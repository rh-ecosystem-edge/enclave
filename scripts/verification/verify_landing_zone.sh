#!/bin/bash
# Verify Landing Zone VM is properly provisioned and accessible
#
# This script verifies that the Landing Zone VM is:
# - Running
# - Accessible via SSH
# - Has both network interfaces configured
# - Has required dependencies installed
# - Can reach both BMC and cluster networks

set -euo pipefail

# Detect Enclave repository root
SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" &>/dev/null && pwd)"
ENCLAVE_DIR="$(cd -- "${SCRIPT_DIR}/../.." &>/dev/null && pwd)"

# Source shared utilities
source "${ENCLAVE_DIR}/scripts/lib/output.sh"
source "${ENCLAVE_DIR}/scripts/lib/validation.sh"
source "${ENCLAVE_DIR}/scripts/lib/config.sh"
source "${ENCLAVE_DIR}/scripts/lib/network.sh"

# Custom fail function for this script
fail() {
    echo -e "${RED}✗${NC} $1"
}

# Validate required environment variables
require_env_var "DEV_SCRIPTS_PATH"

# Determine cluster name for dynamic config file
ENCLAVE_CLUSTER_NAME="${ENCLAVE_CLUSTER_NAME:-enclave-test}"

# Source dev-scripts configuration
load_devscripts_config

# Configuration
CLUSTER_NAME="${CLUSTER_NAME:-enclave-test}"
LZ_VM_NAME="${CLUSTER_NAME}_landingzone_0"
BMC_NETWORK="${PROVISIONING_NETWORK}"
CLUSTER_NETWORK="${EXTERNAL_SUBNET_V4}"

# Extract network prefixes for dynamic IP detection
CLUSTER_NET_PREFIX=$(get_network_prefix "$CLUSTER_NETWORK")
BMC_NET_PREFIX=$(get_network_prefix "$BMC_NETWORK")

# Get actual IP from libvirt (VM uses DHCP) - dynamic subnet detection
CLUSTER_IP=$(get_vm_ip_on_network "$LZ_VM_NAME" "$CLUSTER_NETWORK")

if [ -z "$CLUSTER_IP" ]; then
    error "Could not determine Landing Zone IP address"
    error "VM may not have network connectivity yet"
    exit 1
fi

# Calculate other IPs
BMC_IP=$(echo "$BMC_NETWORK" | sed 's|/.*||' | awk -F. '{print $1"."$2"."$3".2"}')
BMC_GATEWAY=$(get_network_gateway "$BMC_NETWORK")
CLUSTER_GATEWAY=$(get_network_gateway "$CLUSTER_NETWORK")

# Calculate cluster-specific BMC port
BMC_PORT=$(calculate_bmc_port "$BMC_NETWORK")

SSH_OPTS="-o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null -o ConnectTimeout=5 -q"

info "========================================="
info "Landing Zone VM Verification"
info "========================================="
info ""
info "VM Name: $LZ_VM_NAME"
info "BMC Network IP: $BMC_IP"
info "Cluster Network IP: $CLUSTER_IP"
info ""

# Test 1: Check if VM exists and is running
info "Test 1: Checking if Landing Zone VM exists and is running..."
if sudo virsh list --state-running | grep -q "$LZ_VM_NAME"; then
    success "Landing Zone VM is running"
else
    fail "Landing Zone VM is not running"
    if sudo virsh list --all | grep -q "$LZ_VM_NAME"; then
        error "VM exists but is not running. Start it with: sudo virsh start $LZ_VM_NAME"
    else
        error "VM does not exist. Run 'make environment' first"
    fi
    exit 1
fi

# Test 2: Check SSH connectivity
info "Test 2: Checking SSH connectivity to Landing Zone..."
if ssh $SSH_OPTS cloud-user@${CLUSTER_IP} "echo 'SSH test successful'" &>/dev/null; then
    success "SSH connection successful (via cluster network: $CLUSTER_IP)"
else
    fail "Cannot establish SSH connection to $CLUSTER_IP"
    error "Landing Zone VM may still be booting. Wait and try again."
    exit 1
fi

# Test 3: Verify network interfaces
info "Test 3: Verifying network interfaces..."
INTERFACES=$(ssh $SSH_OPTS cloud-user@${CLUSTER_IP} "ip -br addr show" 2>/dev/null)

# Count non-loopback interfaces
IFACE_COUNT=$(echo "$INTERFACES" | grep -v "lo " | grep -v "^$" | wc -l)
if [ "$IFACE_COUNT" -ge 2 ]; then
    success "VM has $IFACE_COUNT network interfaces (expected: 2+)"

    # Show interface details - use dynamic network detection
    BMC_IFACE=$(echo "$INTERFACES" | grep "$BMC_NET_PREFIX" || true)
    CLUSTER_IFACE=$(echo "$INTERFACES" | grep "$CLUSTER_NET_PREFIX" || true)

    if [ -n "$BMC_IFACE" ]; then
        BMC_IFACE_NAME=$(echo "$BMC_IFACE" | awk '{print $1}')
        BMC_IFACE_IP=$(echo "$BMC_IFACE" | awk '{print $3}' | cut -d'/' -f1)
        success "  BMC network interface: $BMC_IFACE_NAME ($BMC_IFACE_IP)"
    else
        warning "  No interface found on BMC network ($BMC_NETWORK)"
    fi

    if [ -n "$CLUSTER_IFACE" ]; then
        CLUSTER_IFACE_NAME=$(echo "$CLUSTER_IFACE" | awk '{print $1}')
        CLUSTER_IFACE_IP=$(echo "$CLUSTER_IFACE" | awk '{print $3}' | cut -d'/' -f1)
        success "  Cluster network interface: $CLUSTER_IFACE_NAME ($CLUSTER_IFACE_IP)"
    else
        fail "  No interface found on cluster network ($CLUSTER_NETWORK)"
    fi
else
    fail "VM has only $IFACE_COUNT network interface(s) (expected: 2)"
fi

# Test 4: Test BMC network connectivity
info "Test 4: Testing BMC network connectivity..."
if ssh $SSH_OPTS cloud-user@${CLUSTER_IP} "ping -c 1 -W 2 $BMC_GATEWAY &>/dev/null"; then
    success "Can reach BMC network gateway: $BMC_GATEWAY"
else
    fail "Cannot reach BMC network gateway: $BMC_GATEWAY"
fi

# Test 5: Test cluster network connectivity
info "Test 5: Testing cluster network connectivity..."
if ssh $SSH_OPTS cloud-user@${CLUSTER_IP} "ping -c 1 -W 2 $CLUSTER_GATEWAY &>/dev/null"; then
    success "Can reach cluster network gateway: $CLUSTER_GATEWAY"
else
    fail "Cannot reach cluster network gateway: $CLUSTER_GATEWAY"
fi

# Test 6: Verify sushy-tools (BMC emulator) accessibility
info "Test 6: Testing sushy-tools (BMC emulator) accessibility..."
SUSHY_ENDPOINT="https://${BMC_GATEWAY}:${BMC_PORT}/redfish/v1/Systems"

# First check from the host (where sushy-tools should be running)
if curl -k -s -o /dev/null -w '%{http_code}' --connect-timeout 5 "$SUSHY_ENDPOINT" 2>/dev/null | grep -q "200"; then
    # Check if Landing Zone can also reach it
    if ssh $SSH_OPTS cloud-user@${CLUSTER_IP} "curl -k -s -o /dev/null -w '%{http_code}' --connect-timeout 5 $SUSHY_ENDPOINT" 2>/dev/null | grep -q "200"; then
        success "Can reach sushy-tools at $SUSHY_ENDPOINT from Landing Zone"
    else
        warning "sushy-tools is running on host but NOT reachable from Landing Zone"
        info "  This may indicate BMC network routing issue"
    fi
else
    fail "sushy-tools is NOT running on host ($SUSHY_ENDPOINT)"
    info "  dev-scripts should have started it. Check: sudo podman ps | grep sushy"
fi

# Test 7: Verify required packages are installed
info "Test 7: Verifying required packages..."
REQUIRED_PACKAGES=("git" "ansible" "python3" "curl" "jq")
for pkg in "${REQUIRED_PACKAGES[@]}"; do
    if ssh $SSH_OPTS cloud-user@${CLUSTER_IP} "command -v $pkg &>/dev/null"; then
        success "$pkg is installed"
    else
        fail "$pkg is NOT installed"
    fi
done

# Test 8: Verify disk space
info "Test 8: Checking available disk space..."
DISK_AVAIL=$(ssh $SSH_OPTS cloud-user@${CLUSTER_IP} "df -h / | tail -1 | awk '{print \$4}'" 2>/dev/null)
success "Available disk space: $DISK_AVAIL"

# Test 9: Verify cloud-init completed successfully
info "Test 9: Verifying cloud-init completion..."
if ssh $SSH_OPTS cloud-user@${CLUSTER_IP} "test -f /var/lib/cloud/instance/boot-finished"; then
    success "cloud-init completed successfully"
else
    warning "cloud-init may not have completed yet"
fi

# Test 10: Verify hostname
info "Test 10: Verifying hostname configuration..."
HOSTNAME=$(ssh $SSH_OPTS cloud-user@${CLUSTER_IP} "hostname" 2>/dev/null)
SHORT_HOSTNAME=$(ssh $SSH_OPTS cloud-user@${CLUSTER_IP} "hostname -s" 2>/dev/null)

if [[ "$HOSTNAME" =~ ^enclave-lz ]]; then
    success "Hostname correctly set to: $HOSTNAME"
    if [ "$SHORT_HOSTNAME" == "enclave-lz" ]; then
        success "  Short hostname: $SHORT_HOSTNAME"
    fi
else
    fail "Unexpected hostname: $HOSTNAME (expected: enclave-lz or enclave-lz.domain)"
fi

echo ""
info "========================================="
info "Verification Summary"
info "========================================="
info ""
success "Landing Zone VM is properly provisioned and ready!"
info ""
info "Access Information:"
info "  SSH: ssh cloud-user@${CLUSTER_IP}"
info "  BMC Network: ${BMC_IP} (can reach ${BMC_GATEWAY}:8000)"
info "  Cluster Network: ${CLUSTER_IP}"
info ""
info "Next Steps:"
info "  - Install Enclave Lab: make install-enclave (Task 3)"
info "  - Verify full infrastructure: make verify"
info ""
