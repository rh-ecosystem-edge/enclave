#!/bin/bash
# Verify Enclave Lab infrastructure is properly configured
#
# This script performs comprehensive verification of:
# - Networks (BMC and cluster)
# - Master VMs
# - BMC emulation (sushy-tools)
# - Landing Zone VM (if provisioned)

set -euo pipefail

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m' # No Color

info() {
    echo -e "${GREEN}INFO:${NC} $1"
}

error() {
    echo -e "${RED}ERROR:${NC} $1"
}

warning() {
    echo -e "${YELLOW}WARNING:${NC} $1"
}

success() {
    echo -e "${GREEN}✓${NC} $1"
}

fail() {
    echo -e "${RED}✗${NC} $1"
}

# Check required environment variables
if [ -z "${DEV_SCRIPTS_PATH:-}" ]; then
    error "DEV_SCRIPTS_PATH environment variable is not set"
    exit 1
fi

# Determine cluster name for dynamic config file
ENCLAVE_CLUSTER_NAME="${ENCLAVE_CLUSTER_NAME:-enclave-test}"

# Source dev-scripts configuration
CONFIG_FILE="${DEV_SCRIPTS_PATH}/config_${ENCLAVE_CLUSTER_NAME}.sh"
if [ ! -f "$CONFIG_FILE" ]; then
    error "dev-scripts configuration not found: $CONFIG_FILE"
    error "Expected config file for cluster: $ENCLAVE_CLUSTER_NAME"
    exit 1
fi

# shellcheck source=/dev/null
source "$CONFIG_FILE"

# Configuration
CLUSTER_NAME="${CLUSTER_NAME:-enclave-test}"
BMC_NETWORK_NAME="${PROVISIONING_NETWORK_NAME:-bmc}"
CLUSTER_NETWORK_NAME="${BAREMETAL_NETWORK_NAME:-cluster}"
NUM_MASTERS="${NUM_MASTERS:-3}"

info "========================================="
info "Enclave Lab Infrastructure Verification"
info "========================================="
info ""

# Track failures
FAILED=0

# Test 1: Check libvirt networks
info "Test 1: Checking libvirt networks..."
if sudo virsh net-list --all | grep -q "$BMC_NETWORK_NAME"; then
    if sudo virsh net-list | grep -q "$BMC_NETWORK_NAME.*active"; then
        success "BMC network ($BMC_NETWORK_NAME) is active"
    else
        fail "BMC network exists but is not active"
        FAILED=1
        sudo virsh net-start "$BMC_NETWORK_NAME"
    fi
else
    fail "BMC network ($BMC_NETWORK_NAME) not found"
    FAILED=1
fi

if sudo virsh net-list --all | grep -q "$CLUSTER_NETWORK_NAME"; then
    if sudo virsh net-list | grep -q "$CLUSTER_NETWORK_NAME.*active"; then
        success "Cluster network ($CLUSTER_NETWORK_NAME) is active"
    else
        fail "Cluster network exists but is not active"
        FAILED=1
        sudo virsh net-start "$CLUSTER_NETWORK_NAME"
    fi
else
    fail "Cluster network ($CLUSTER_NETWORK_NAME) not found"
    FAILED=1
fi

# Test 2: Check master VMs
info "Test 2: Checking master VMs..."
MASTER_COUNT=0
for i in $(seq 0 $((NUM_MASTERS - 1))); do
    VM_NAME="${CLUSTER_NAME}_master_${i}"
    if sudo virsh list --all | grep -q "$VM_NAME"; then
        success "Master VM $i exists: $VM_NAME"
        MASTER_COUNT=$((MASTER_COUNT + 1))

        # Check network interfaces
        IFACE_COUNT=$(sudo virsh domiflist "$VM_NAME" | grep -c "bridge\|network" || true)
        if [ "$IFACE_COUNT" -eq 1 ]; then
            success "  Master VM $i has 1 network interface (cluster only) ✓"
        else
            fail "  Master VM $i has $IFACE_COUNT interfaces (expected: 1)"
            FAILED=1
        fi
    else
        fail "Master VM $i not found: $VM_NAME"
        FAILED=1
    fi
done

if [ "$MASTER_COUNT" -eq "$NUM_MASTERS" ]; then
    success "All $NUM_MASTERS master VMs found"
else
    fail "Found $MASTER_COUNT master VMs (expected: $NUM_MASTERS)"
    FAILED=1
fi

# Test 3: Check Landing Zone VM
info "Test 3: Checking Landing Zone VM..."
LZ_VM_NAME="${CLUSTER_NAME}_landingzone_0"
if sudo virsh list --all | grep -q "$LZ_VM_NAME"; then
    success "Landing Zone VM exists: $LZ_VM_NAME"

    # Check network interfaces
    IFACE_COUNT=$(sudo virsh domiflist "$LZ_VM_NAME" | grep -c "bridge\|network" || true)
    if [ "$IFACE_COUNT" -eq 2 ]; then
        success "  Landing Zone VM has 2 network interfaces (BMC + cluster) ✓"
    else
        fail "  Landing Zone VM has $IFACE_COUNT interfaces (expected: 2)"
        FAILED=1
    fi

    # Check if it's running
    if sudo virsh list --state-running | grep -q "$LZ_VM_NAME"; then
        info "  Landing Zone VM is running"

        # Run detailed Landing Zone verification if the VM is provisioned
        SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
        if [ -f "$SCRIPT_DIR/verify_landing_zone.sh" ]; then
            echo ""
            info "Running detailed Landing Zone verification..."
            if "$SCRIPT_DIR/verify_landing_zone.sh"; then
                success "Landing Zone VM is fully provisioned and operational"
            else
                warning "Landing Zone VM exists but may not be fully provisioned"
                info "Run 'make provision-landing-zone' to provision it with CentOS Stream 10"
            fi
        fi
    else
        info "  Landing Zone VM is not running (this is normal before provisioning)"
        info "  Run 'make provision-landing-zone' to provision and start it"
    fi
else
    fail "Landing Zone VM not found: $LZ_VM_NAME"
    FAILED=1
fi

# Test 4: Check BMC emulation (sushy-tools)
info "Test 4: Checking BMC emulation (sushy-tools)..."
BMC_NETWORK="${PROVISIONING_NETWORK}"
BMC_GATEWAY=$(echo "$BMC_NETWORK" | sed 's|/.*||' | awk -F. '{print $1"."$2"."$3".1"}')
SUSHY_ENDPOINT="http://${BMC_GATEWAY}:8000/redfish/v1/Systems"

if curl -s -o /dev/null -w '%{http_code}' --connect-timeout 5 "$SUSHY_ENDPOINT" 2>/dev/null | grep -q "200"; then
    success "sushy-tools is accessible at $SUSHY_ENDPOINT"

    # Count available systems
    SYSTEMS_COUNT=$(curl -s "$SUSHY_ENDPOINT" 2>/dev/null | jq '.Members | length' || echo "0")
    info "  Available systems: $SYSTEMS_COUNT"
else
    warning "sushy-tools not accessible at $SUSHY_ENDPOINT"
    info "  This is normal if sushy-tools container is not running"
fi

echo ""
info "========================================="
info "Verification Summary"
info "========================================="
info ""

if [ "$FAILED" -eq 0 ]; then
    success "Infrastructure verification complete!"
    info ""
    info "Status:"
    info "  Networks: BMC ($BMC_NETWORK_NAME), Cluster ($CLUSTER_NETWORK_NAME)"
    info "  Master VMs: $MASTER_COUNT found"
    info "  Landing Zone: $(sudo virsh list --all | grep -q "$LZ_VM_NAME" && echo "Created" || echo "Not found")"
    info "  BMC Emulation: $(curl -s -o /dev/null -w '%{http_code}' --connect-timeout 5 "$SUSHY_ENDPOINT" 2>/dev/null | grep -q "200" && echo "Running" || echo "Not running")"
    info ""
    info "Next steps:"
    if ! sudo virsh list --state-running | grep -q "$LZ_VM_NAME"; then
        info "  1. Provision Landing Zone: make provision-landing-zone"
        info "  2. Verify Landing Zone: make verify-landing-zone"
    else
        info "  1. Install Enclave Lab on Landing Zone: make install-enclave (Task 3)"
    fi
    info ""
    exit 0
else
    error "Infrastructure verification FAILED!"
    info ""
    error "Please review the errors above and fix the infrastructure issues."
    info ""
    exit 1
fi
