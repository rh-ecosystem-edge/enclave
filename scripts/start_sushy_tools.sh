#!/bin/bash
# Start sushy-tools BMC emulator for Enclave Lab
#
# This script starts sushy-tools as a standalone container
# to provide Redfish BMC emulation for worker VMs.

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
    echo -e "${RED}ERROR:${NC} $1" >&2
    exit 1
}

warning() {
    echo -e "${YELLOW}WARNING:${NC} $1"
}

success() {
    echo -e "${GREEN}✓${NC} $1"
}

# Check if sushy-tools is already running
if sudo podman ps | grep -q sushy-tools; then
    success "sushy-tools is already running"
    sudo podman ps | grep sushy-tools
    exit 0
fi

info "Starting sushy-tools BMC emulator..."

# Configuration
SUSHY_TOOLS_IMAGE="${SUSHY_TOOLS_IMAGE:-quay.io/metal3-io/sushy-tools}"
WORKING_DIR="${WORKING_DIR:-/opt/dev-scripts}"
SUSHY_DIR="${WORKING_DIR}/virtualbmc/sushy-tools"

# Get BMC network gateway for dynamic configuration
# Try to source dev-scripts config if available
if [ -n "${DEV_SCRIPTS_PATH:-}" ] && [ -n "${ENCLAVE_CLUSTER_NAME:-}" ]; then
    CONFIG_FILE="${DEV_SCRIPTS_PATH}/config_${ENCLAVE_CLUSTER_NAME}.sh"
    if [ -f "$CONFIG_FILE" ]; then
        # shellcheck source=/dev/null
        source "$CONFIG_FILE"
    fi
fi

# Calculate BMC gateway from PROVISIONING_NETWORK
PROVISIONING_NETWORK="${PROVISIONING_NETWORK:-100.64.1.0/24}"
BMC_GATEWAY=$(echo "$PROVISIONING_NETWORK" | sed 's|/.*||' | awk -F. '{print $1"."$2"."$3".1"}')

# Create sushy-tools directory if it doesn't exist
if [ ! -d "$SUSHY_DIR" ]; then
    info "Creating sushy-tools directory: $SUSHY_DIR"
    sudo mkdir -p "$SUSHY_DIR"
fi

# Generate self-signed SSL certificate if it doesn't exist
if [ ! -f "${SUSHY_DIR}/sushy.crt" ] || [ ! -f "${SUSHY_DIR}/sushy.key" ]; then
    info "Generating self-signed SSL certificate for sushy-tools..."
    sudo openssl req -new -x509 -nodes -days 3650 \
        -subj "/C=US/ST=State/L=City/O=Enclave-Lab/CN=sushy-tools" \
        -keyout "${SUSHY_DIR}/sushy.key" \
        -out "${SUSHY_DIR}/sushy.crt" \
        -addext "subjectAltName=IP:${BMC_GATEWAY},IP:127.0.0.1" \
        > /dev/null 2>&1
    success "SSL certificate generated"
else
    info "Using existing SSL certificate"
fi

# Create sushy-tools configuration
info "Creating sushy-tools configuration..."
sudo tee "${SUSHY_DIR}/conf.py" > /dev/null <<'EOF'
# Sushy-tools emulator configuration for Enclave Lab

# Use libvirt as the backend
SUSHY_EMULATOR_LIBVIRT_URI = "qemu:///system"

# Use MAC address as system identifier instead of UUID
SUSHY_EMULATOR_LIBVIRT_MAC_AS_ID = True

# Listen on all IPv4 addresses
SUSHY_EMULATOR_LISTEN_IP = "0.0.0.0"
SUSHY_EMULATOR_LISTEN_PORT = 8000

# Use HTTPS with self-signed certificate
SUSHY_EMULATOR_SSL_CERT = "/root/sushy/sushy.crt"
SUSHY_EMULATOR_SSL_KEY = "/root/sushy/sushy.key"

# Enable authentication (optional - can be disabled)
SUSHY_EMULATOR_AUTH_FILE = None

# Boot device persistence
SUSHY_EMULATOR_IGNORE_BOOT_DEVICE = True

# Do not set ALLOWED_INSTANCES - when not set, all VMs are allowed
EOF

success "sushy-tools configuration created"

# Pull sushy-tools image
info "Pulling sushy-tools image: $SUSHY_TOOLS_IMAGE"
sudo podman pull "$SUSHY_TOOLS_IMAGE"

# Start sushy-tools container
info "Starting sushy-tools container..."
sudo podman run -d \
    --net host \
    --privileged \
    --name sushy-tools \
    -v "$SUSHY_DIR:/root/sushy" \
    -v "/root/.ssh:/root/ssh" \
    -v "/var/run/libvirt:/var/run/libvirt" \
    "${SUSHY_TOOLS_IMAGE}"

# Wait for sushy-tools to be ready
info "Waiting for sushy-tools to be ready..."
sleep 3

# Verify it's running
if sudo podman ps | grep -q sushy-tools; then
    success "sushy-tools is now running"

    # Test endpoint (using HTTPS with -k for self-signed cert)
    if curl -k -s -o /dev/null -w '%{http_code}' --connect-timeout 5 "https://${BMC_GATEWAY}:8000/redfish/v1/Systems" 2>/dev/null | grep -q "200"; then
        success "Redfish endpoint is accessible at https://${BMC_GATEWAY}:8000/redfish/v1/Systems"
    else
        warning "sushy-tools is running but endpoint not accessible yet (may need a few more seconds)"
    fi
else
    error "Failed to start sushy-tools"
fi

info ""
info "sushy-tools BMC emulation started successfully!"
info "  Endpoint: https://${BMC_GATEWAY}:8000/redfish/v1/Systems (HTTPS with self-signed cert)"
info "  Container: sushy-tools"
info ""
info "To stop: sudo podman stop sushy-tools"
info "To restart: sudo podman restart sushy-tools"
info "To view logs: sudo podman logs sushy-tools"
info ""
