#!/bin/bash
# Provision Landing Zone VM with CentOS Stream 10
#
# This script provisions the existing Landing Zone VM (created by dev-scripts)
# with CentOS Stream 10 cloud image and configures it for Enclave Lab deployment.

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

# Check required environment variables
if [ -z "${DEV_SCRIPTS_PATH:-}" ]; then
    error "DEV_SCRIPTS_PATH environment variable is not set"
    exit 1
fi

# Determine cluster name for dynamic config file
ENCLAVE_CLUSTER_NAME="${ENCLAVE_CLUSTER_NAME:-enclave-test}"

# Source dev-scripts configuration to get network and cluster info
CONFIG_FILE="${DEV_SCRIPTS_PATH}/config_${ENCLAVE_CLUSTER_NAME}.sh"
if [ ! -f "$CONFIG_FILE" ]; then
    error "dev-scripts configuration not found: $CONFIG_FILE"
    error "Expected config file for cluster: $ENCLAVE_CLUSTER_NAME"
    error "Please run 'make environment' first to create infrastructure"
    exit 1
fi

# shellcheck source=/dev/null
source "$CONFIG_FILE"

# Configuration
CLUSTER_NAME="${CLUSTER_NAME:-enclave-test}"
LZ_VM_NAME="${CLUSTER_NAME}_landingzone_0"
WORKING_DIR="${WORKING_DIR:-/opt/dev-scripts}"
LZ_WORKING_DIR="${WORKING_DIR}/landing-zone/${CLUSTER_NAME}"
CLOUD_IMAGE_URL="https://cloud.centos.org/centos/10-stream/x86_64/images/CentOS-Stream-GenericCloud-10-latest.x86_64.qcow2"
CLOUD_IMAGE_NAME="centos-stream-10-cloud.qcow2"
POOL_NAME="${CLUSTER_NAME}-lz"
POOL_PATH="${WORKING_DIR}/pool/${CLUSTER_NAME}"

# Network configuration (from dev-scripts config)
BMC_NETWORK="${PROVISIONING_NETWORK}"
BMC_NETWORK_NAME="${PROVISIONING_NETWORK_NAME:-bmc}"
CLUSTER_NETWORK="${EXTERNAL_SUBNET_V4}"
CLUSTER_NETWORK_NAME="${BAREMETAL_NETWORK_NAME:-cluster}"

# Calculate network prefixes for IP detection
BMC_IP=$(echo "$BMC_NETWORK" | sed 's|/.*||' | awk -F. '{print $1"."$2"."$3".2"}')
BMC_PREFIX=$(echo "$BMC_NETWORK" | sed 's|.*/||')

# Extract cluster network prefix for dynamic IP detection (e.g., "192.168.4" from "192.168.4.0/24")
CLUSTER_NET_PREFIX=$(echo "$CLUSTER_NETWORK" | sed 's|/.*||' | awk -F. '{print $1"."$2"."$3}')
CLUSTER_IP="${CLUSTER_NET_PREFIX}.2"  # Initial guess, will be updated from DHCP

# SSH key
SSH_KEY_FILE="$HOME/.ssh/id_rsa.pub"
if [ ! -f "$SSH_KEY_FILE" ]; then
    error "SSH public key not found: $SSH_KEY_FILE"
    error "Please generate SSH key: ssh-keygen -t rsa -b 4096"
    exit 1
fi
SSH_PUBLIC_KEY=$(cat "$SSH_KEY_FILE")

info "Landing Zone VM Provisioning Configuration:"
info "  VM Name: $LZ_VM_NAME"
info "  BMC Network: $BMC_NETWORK (DHCP)"
info "  Cluster Network: $CLUSTER_NETWORK (DHCP)"
info "  Working Directory: $LZ_WORKING_DIR"
info ""
info "Note: VM will use DHCP for initial boot. Static IPs can be configured later if needed."
echo ""

# Create working directory
info "Creating working directory: $LZ_WORKING_DIR"
sudo mkdir -p "$LZ_WORKING_DIR"
sudo chown $USER:$USER "$LZ_WORKING_DIR"

# Download CentOS Stream 10 cloud image
if [ ! -f "${LZ_WORKING_DIR}/${CLOUD_IMAGE_NAME}" ]; then
    info "Downloading CentOS Stream 10 cloud image..."
    wget -O "${LZ_WORKING_DIR}/${CLOUD_IMAGE_NAME}" "$CLOUD_IMAGE_URL"
    info "✓ Cloud image downloaded"
else
    info "Cloud image already exists, skipping download"
fi

# Create cloud-init configuration
info "Creating cloud-init configuration..."

cat > "${LZ_WORKING_DIR}/meta-data" <<EOF
instance-id: ${LZ_VM_NAME}
local-hostname: enclave-lz
EOF

cat > "${LZ_WORKING_DIR}/user-data" <<EOF
#cloud-config
users:
  - name: cloud-user
    sudo: ALL=(ALL) NOPASSWD:ALL
    groups: wheel
    shell: /bin/bash
    ssh_authorized_keys:
      - ${SSH_PUBLIC_KEY}

hostname: enclave-lz
fqdn: enclave-lz.${CLUSTER_DOMAIN:-enclave-test.lab}

packages:
  - git
  - vim
  - curl
  - wget
  - jq
  - python3
  - python3-pip
  - ansible-core

runcmd:
  - systemctl disable cloud-init
  - touch /etc/cloud/cloud-init.disabled

timezone: UTC

ssh_pwauth: false
disable_root: true

final_message: "Enclave Landing Zone VM is ready. Time: \$UPTIME"
EOF

# Note: Skipping network-config - let cloud-init use DHCP from libvirt networks
# This ensures the VM gets network connectivity quickly
# Static IPs can be configured later if needed via Task 3

info "✓ cloud-init configuration created"

# Create cloud-init ISO
info "Creating cloud-init ISO..."
sudo genisoimage -output "${LZ_WORKING_DIR}/cloud-init.iso" \
    -volid cidata -joliet -rock \
    "${LZ_WORKING_DIR}/user-data" \
    "${LZ_WORKING_DIR}/meta-data" 2>&1 | grep -v "Warning: creating filesystem"
info "✓ cloud-init ISO created"

# Stop and remove existing VM if it exists
if sudo virsh list --all | grep -q "$LZ_VM_NAME"; then
    info "Removing existing Landing Zone VM..."
    if sudo virsh list --state-running | grep -q "$LZ_VM_NAME"; then
        sudo virsh destroy "$LZ_VM_NAME"
    fi
    sudo virsh undefine "$LZ_VM_NAME" --nvram 2>/dev/null || sudo virsh undefine "$LZ_VM_NAME"
    info "✓ Existing VM removed"
fi

# Create cluster-specific libvirt storage pool if it doesn't exist
if ! sudo virsh pool-uuid "$POOL_NAME" > /dev/null 2>&1; then
    info "Creating libvirt storage pool: $POOL_NAME"

    # Create pool directory if it doesn't exist
    if [ ! -d "$POOL_PATH" ]; then
        sudo mkdir -p "$POOL_PATH"
    fi

    # Define the pool
    sudo virsh pool-define /dev/stdin <<EOF
<pool type='dir'>
  <name>$POOL_NAME</name>
  <target>
    <path>$POOL_PATH</path>
  </target>
</pool>
EOF

    # Start and autostart the pool
    sudo virsh pool-start "$POOL_NAME"
    sudo virsh pool-autostart "$POOL_NAME"
    info "✓ Created storage pool: $POOL_NAME at $POOL_PATH"
else
    info "Using existing storage pool: $POOL_NAME"

    # Check if pool is active, start it if not
    if ! sudo virsh pool-info "$POOL_NAME" 2>/dev/null | grep -qE "State:[[:space:]]+running$"; then
        info "Storage pool is inactive, starting it..."
        sudo virsh pool-start "$POOL_NAME"
        info "✓ Storage pool started"
    fi
fi

# Get pool path and prepare disk
info "Preparing VM disk in libvirt pool..."
POOL_PATH=$(sudo virsh pool-dumpxml "$POOL_NAME" | grep "<path>" | sed 's/.*<path>\(.*\)<\/path>.*/\1/')
info "Pool path: $POOL_PATH"

# Delete old disk volume if exists
if sudo virsh vol-list "$POOL_NAME" | grep -q "${LZ_VM_NAME}.qcow2"; then
    info "Removing old disk volume..."
    sudo virsh vol-delete "${LZ_VM_NAME}.qcow2" --pool "$POOL_NAME"
fi

# Convert cloud image to pool location
info "Converting cloud image to pool volume..."
sudo qemu-img convert -f qcow2 -O qcow2 \
    "${LZ_WORKING_DIR}/${CLOUD_IMAGE_NAME}" \
    "${POOL_PATH}/${LZ_VM_NAME}.qcow2"

# Resize to 600GB (needed for OpenShift image mirroring)
# Provides ample space for: Quay storage ~130GB, oc-mirror workspace ~7GB,
# ISO files ~3GB, binaries ~3GB, OS and other ~7GB, plus large buffer
info "Resizing disk to 600GB..."
sudo qemu-img resize "${POOL_PATH}/${LZ_VM_NAME}.qcow2" 600G

# Refresh pool so libvirt sees the new volume
info "Refreshing libvirt pool..."
sudo virsh pool-refresh "$POOL_NAME"
info "✓ Disk prepared in pool"

# Create VM using virt-install with BIOS boot
info "Creating Landing Zone VM with virt-install..."
sudo virt-install \
    --name "$LZ_VM_NAME" \
    --memory 8192 \
    --vcpus 4 \
    --disk vol=${POOL_NAME}/${LZ_VM_NAME}.qcow2,bus=virtio \
    --disk "${LZ_WORKING_DIR}/cloud-init.iso,device=cdrom,bus=sata" \
    --network network=${BMC_NETWORK_NAME} \
    --network network=${CLUSTER_NETWORK_NAME} \
    --boot hd,cdrom \
    --os-variant centos-stream10 \
    --graphics vnc \
    --noautoconsole \
    --import

info "✓ Landing Zone VM created and started"

# Wait for VM to boot and get IP via DHCP
info "Waiting for Landing Zone VM to boot and get IP (this may take 2-3 minutes)..."
MAX_WAIT=180
COUNTER=0
BOOT_COMPLETE=false
SSH_READY=false
VM_IP=""

while [ $COUNTER -lt $MAX_WAIT ]; do
    # Try to get VM IP from libvirt (use dynamic network prefix from allocated subnet)
    if [ -z "$VM_IP" ]; then
        # Escape dots for grep regex (e.g., "192\.168\.4\.")
        ESCAPED_PREFIX=$(echo "$CLUSTER_NET_PREFIX" | sed 's/\./\\./g')
        VM_IP=$(sudo virsh domifaddr "$LZ_VM_NAME" 2>/dev/null | grep -E "${ESCAPED_PREFIX}\." | awk '{print $4}' | cut -d'/' -f1 | head -1 || true)
        if [ -n "$VM_IP" ]; then
            info "  VM got IP address: $VM_IP (${COUNTER}s)"
            CLUSTER_IP="$VM_IP"  # Update with actual IP from DHCP
        fi
    fi

    # Once we have an IP, check if SSH port is open
    if [ -n "$VM_IP" ] && ! $SSH_READY; then
        if nc -z -w 2 ${VM_IP} 22 2>/dev/null; then
            info "  SSH port is now open (${COUNTER}s)"
            SSH_READY=true
        fi
    fi

    # Try to SSH and check for cloud-init completion
    if [ -n "$VM_IP" ]; then
        if ssh -o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null \
               -o ConnectTimeout=3 -o BatchMode=yes -q cloud-user@${VM_IP} \
               "cloud-init status --wait" 2>/dev/null; then
            BOOT_COMPLETE=true
            break
        fi
    fi

    # Show progress
    if [ $((COUNTER % 30)) -eq 0 ]; then
        if [ -z "$VM_IP" ]; then
            info "  Waiting for VM to get IP address... (${COUNTER}s elapsed)"
        elif $SSH_READY; then
            info "  Waiting for cloud-init to complete... (${COUNTER}s elapsed)"
        else
            info "  Waiting for SSH service... (${COUNTER}s elapsed)"
        fi
    fi

    sleep 3
    COUNTER=$((COUNTER + 3))
done

if [ "$BOOT_COMPLETE" = true ]; then
    info "✓ Landing Zone VM boot complete! (${COUNTER}s)"

    # Configure BMC network interface
    echo ""
    info "Configuring BMC network interface..."
    SSH_OPTS="-o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null -o ConnectTimeout=5 -q"

    # Check if BMC interface already has an IP
    BMC_CHECK=$(ssh $SSH_OPTS cloud-user@${CLUSTER_IP} "ip addr show enp1s0 | grep 'inet ${BMC_IP}'" 2>/dev/null || echo "")

    if [ -z "$BMC_CHECK" ]; then
        info "  Configuring enp1s0 with IP ${BMC_IP}/${BMC_PREFIX}..."

        # Configure BMC interface using nmcli
        ssh $SSH_OPTS cloud-user@${CLUSTER_IP} "sudo nmcli con add type ethernet ifname enp1s0 con-name bmc ipv4.addresses ${BMC_IP}/${BMC_PREFIX} ipv4.method manual" 2>/dev/null || {
            # Connection may already exist, modify it
            ssh $SSH_OPTS cloud-user@${CLUSTER_IP} "sudo nmcli con mod bmc ipv4.addresses ${BMC_IP}/${BMC_PREFIX} ipv4.method manual" 2>/dev/null || true
        }

        # Activate the connection
        ssh $SSH_OPTS cloud-user@${CLUSTER_IP} "sudo nmcli con up bmc" 2>/dev/null || {
            # Fallback to manual IP configuration
            ssh $SSH_OPTS cloud-user@${CLUSTER_IP} "sudo ip addr add ${BMC_IP}/${BMC_PREFIX} dev enp1s0 2>/dev/null || true"
            ssh $SSH_OPTS cloud-user@${CLUSTER_IP} "sudo ip link set enp1s0 up"
        }

        # Wait a moment for interface to come up
        sleep 2

        # Verify configuration
        if ssh $SSH_OPTS cloud-user@${CLUSTER_IP} "ip addr show enp1s0 | grep -q '${BMC_IP}'" 2>/dev/null; then
            info "✓ BMC network configured: enp1s0 (${BMC_IP}/${BMC_PREFIX})"
        else
            warning "BMC network configuration may have failed - verify manually"
        fi
    else
        info "✓ BMC network already configured: enp1s0 (${BMC_IP}/${BMC_PREFIX})"
    fi

    # Add mirror -> LZ cluster IP to virsh network DNS so master VMs can resolve mirror.<domain>
    info "Adding DNS entry: mirror -> ${CLUSTER_IP} (Landing Zone) on network ${CLUSTER_NETWORK_NAME}..."
    if ! sudo virsh net-update "${CLUSTER_NETWORK_NAME}" add dns-host "<host ip='${CLUSTER_IP}'><hostname>mirror</hostname></host>" --live --config 2>/dev/null; then
        warning "Could not add mirror DNS entry (network may not support live update)"
    else
        info "✓ DNS entry added: mirror -> ${CLUSTER_IP}"
    fi

    echo ""
    info "========================================="
    info "Landing Zone VM Provisioned Successfully"
    info "========================================="
    info ""
    info "Access Information:"
    info "  SSH: ssh cloud-user@${CLUSTER_IP}"
    info "  BMC Network IP: ${BMC_IP} (enp1s0)"
    info "  Cluster Network IP: ${CLUSTER_IP} (enp2s0)"
    info ""
    info "Next steps:"
    info "  1. Verify connectivity: make verify-landing-zone"
    info "  2. Install Enclave Lab: make install-enclave (Task 3)"
    info ""
else
    warning "Timeout after ${COUNTER}s - VM is running but SSH not ready"
    info ""
    info "Debugging steps:"
    info "  1. Check console: sudo virsh console $LZ_VM_NAME"
    info "  2. Check if VM got IP: sudo virsh domifaddr $LZ_VM_NAME"
    info "  3. Try SSH manually: ssh cloud-user@${CLUSTER_IP}"
    info "  4. Check network: ping ${CLUSTER_IP}"
    info ""
    warning "The VM may need more time to boot. Wait 1-2 minutes and run:"
    warning "  make verify-landing-zone"
    info ""
    exit 1
fi
