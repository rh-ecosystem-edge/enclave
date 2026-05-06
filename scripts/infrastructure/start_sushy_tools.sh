#!/bin/bash
# Start sushy-tools BMC emulator for Enclave Lab
#
# This script starts sushy-tools as a standalone container
# to provide Redfish BMC emulation for worker VMs.

set -euo pipefail

# Detect Enclave repository root
SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" &>/dev/null && pwd)"
ENCLAVE_DIR="$(cd -- "${SCRIPT_DIR}/../.." &>/dev/null && pwd)"

# Source shared utilities
source "${ENCLAVE_DIR}/scripts/lib/output.sh"
source "${ENCLAVE_DIR}/scripts/lib/config.sh"
source "${ENCLAVE_DIR}/scripts/lib/network.sh"
source "${ENCLAVE_DIR}/scripts/lib/common.sh"

# Get cluster name from environment or dev-scripts config
ENCLAVE_CLUSTER_NAME="${ENCLAVE_CLUSTER_NAME:-enclave-test}"

# Try to load dev-scripts config (non-fatal)
try_load_devscripts_config

CLUSTER_NAME="${CLUSTER_NAME:-$ENCLAVE_CLUSTER_NAME}"

# Use cluster-specific container name for parallel execution isolation
CONTAINER_NAME="sushy-tools-${CLUSTER_NAME}"

# Check if sushy-tools is already running for this cluster
if sudo podman ps | grep -q "$CONTAINER_NAME"; then
    success "sushy-tools is already running for cluster $CLUSTER_NAME"
    sudo podman ps | grep "$CONTAINER_NAME"
    exit 0
fi

info "Starting sushy-tools BMC emulator..."

# Configuration
SUSHY_TOOLS_IMAGE="${SUSHY_TOOLS_IMAGE:-quay.io/metal3-io/sushy-tools}"

# Ensure working directory is set
ensure_working_dir

SUSHY_DIR="${WORKING_DIR}/virtualbmc/sushy-tools"

# Calculate BMC gateway and port from PROVISIONING_NETWORK
PROVISIONING_NETWORK="${PROVISIONING_NETWORK:-100.64.1.0/24}"
BMC_GATEWAY=$(get_network_gateway "$PROVISIONING_NETWORK")
BMC_PORT=$(calculate_bmc_port "$PROVISIONING_NETWORK")

# Wait for bridge to have IP assigned
wait_for_bridge_ip() {
    local bridge_name="${CLUSTER_NAME}-p"
    local expected_ip="$BMC_GATEWAY"
    local max_attempts=30
    local wait_seconds=2
    local attempt=1

    info "Waiting for bridge ${bridge_name} to have IP ${expected_ip}..."

    while [ $attempt -le $max_attempts ]; do
        # Check if bridge exists and has the expected IP
        if ip addr show "$bridge_name" 2>/dev/null | grep -q "inet ${expected_ip}/"; then
            success "Bridge ${bridge_name} has IP ${expected_ip}"
            return 0
        fi

        if [ $attempt -eq $max_attempts ]; then
            error "Timeout waiting for bridge ${bridge_name} to have IP ${expected_ip} (waited $((max_attempts * wait_seconds)) seconds)"
            error ""
            error "Bridge diagnostics:"

            # Check if bridge exists
            if ip link show "$bridge_name" >/dev/null 2>&1; then
                error "  Bridge exists but IP not assigned:"
                ip addr show "$bridge_name" 2>&1 | while IFS= read -r line; do
                    error "    $line"
                done

                # Check libvirt network status
                if sudo virsh net-info "$bridge_name" >/dev/null 2>&1; then
                    error ""
                    error "  Libvirt network status:"
                    sudo virsh net-info "$bridge_name" 2>&1 | while IFS= read -r line; do
                        error "    $line"
                    done
                else
                    error "  Libvirt network '${bridge_name}' not found"
                fi
            else
                error "  Bridge ${bridge_name} does not exist at all!"
                error "  This means dev-scripts failed to create the network."
            fi

            return 1
        fi

        info "  Attempt $attempt/$max_attempts: Bridge not ready yet, waiting ${wait_seconds}s..."

        # Show bridge state every 10 attempts for debugging
        if [ $((attempt % 10)) -eq 0 ]; then
            info "    Bridge state at attempt $attempt:"
            if ip link show "$bridge_name" >/dev/null 2>&1; then
                ip addr show "$bridge_name" 2>&1 | grep "inet " | while IFS= read -r line; do
                    info "      $line"
                done
                if ! ip addr show "$bridge_name" 2>&1 | grep -q "inet "; then
                    info "      Bridge exists but has no IP assigned yet"
                fi
            else
                info "      Bridge does not exist yet"
            fi
        fi

        sleep $wait_seconds
        attempt=$((attempt + 1))
    done

    return 1
}

# Wait for bridge to be ready before starting sushy-tools
wait_for_bridge_ip

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
# Use cluster-specific BMC IP and port for parallel execution isolation
sudo tee "${SUSHY_DIR}/conf.py" > /dev/null <<EOF
# Sushy-tools emulator configuration for Enclave Lab

# Use libvirt as the backend
SUSHY_EMULATOR_LIBVIRT_URI = "qemu:///system"

# Use MAC address as system identifier instead of UUID
SUSHY_EMULATOR_LIBVIRT_MAC_AS_ID = True

# Bind to cluster-specific BMC IP and port for parallel execution isolation
SUSHY_EMULATOR_LISTEN_IP = "${BMC_GATEWAY}"
SUSHY_EMULATOR_LISTEN_PORT = ${BMC_PORT}

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

# Start sushy-tools container with cluster-specific name, IP binding, and port
info "Starting sushy-tools container for cluster $CLUSTER_NAME..."
info "  Container: $CONTAINER_NAME"
info "  Binding to: ${BMC_GATEWAY}:${BMC_PORT}"
sudo podman run -d \
    --net host \
    --privileged \
    --name "$CONTAINER_NAME" \
    -v "$SUSHY_DIR:/root/sushy:z" \
    -v "/root/.ssh:/root/.ssh:ro,z" \
    -v "/var/run/libvirt:/var/run/libvirt:z" \
    "${SUSHY_TOOLS_IMAGE}" \
    sushy-emulator --config /root/sushy/conf.py

# Open firewall port for sushy-tools
info "Configuring firewall to allow sushy-tools traffic..."
# Allow incoming traffic on the BMC port from the BMC network
if sudo firewall-cmd --state >/dev/null 2>&1; then
    # Get the zone for the BMC bridge interface
    BMC_BRIDGE="${CLUSTER_NAME}-p"
    BMC_ZONE=$(sudo firewall-cmd --get-zone-of-interface="$BMC_BRIDGE" 2>/dev/null || echo "")

    if [ -n "$BMC_ZONE" ]; then
        info "  Adding port ${BMC_PORT}/tcp to zone: $BMC_ZONE (bridge: $BMC_BRIDGE)"
        # Add to runtime configuration (immediate)
        sudo firewall-cmd --zone="$BMC_ZONE" --add-port="${BMC_PORT}/tcp" >/dev/null 2>&1 || true
        # Add to permanent configuration (survives reboot)
        sudo firewall-cmd --zone="$BMC_ZONE" --add-port="${BMC_PORT}/tcp" --permanent >/dev/null 2>&1 || true
    else
        # Fallback: add to libvirt zone (common for libvirt bridges)
        info "  Adding port ${BMC_PORT}/tcp to libvirt zone (BMC bridge not in a zone yet)"
        sudo firewall-cmd --zone=libvirt --add-port="${BMC_PORT}/tcp" >/dev/null 2>&1 || true
        sudo firewall-cmd --zone=libvirt --add-port="${BMC_PORT}/tcp" --permanent >/dev/null 2>&1 || true
    fi
    info "  ✓ Firewall configured for port ${BMC_PORT}/tcp"
else
    warning "Firewalld not running, skipping firewall configuration"
fi

# Verify sushy-tools endpoint is responding
info "Verifying sushy-tools endpoint accessibility..."
MAX_WAIT=90
COUNTER=0
ENDPOINT_READY=false

while [ $COUNTER -lt $MAX_WAIT ]; do
    if curl -k -s -o /dev/null -w '%{http_code}' --connect-timeout 2 "https://${BMC_GATEWAY}:${BMC_PORT}/redfish/v1/Systems" 2>/dev/null | grep -q "200"; then
        ENDPOINT_READY=true
        info "✓ Sushy-tools endpoint is responding (${COUNTER}s)"
        break
    fi
    sleep 1
    COUNTER=$((COUNTER + 1))
done

if [ "$ENDPOINT_READY" = false ]; then
    error "Sushy-tools endpoint is not responding after ${MAX_WAIT}s"
    error "Endpoint: https://${BMC_GATEWAY}:${BMC_PORT}/redfish/v1/Systems"
    error "Check container logs: sudo podman logs $CONTAINER_NAME"
    exit 1
fi

# Install Ironic ISO server CA into the container trust store so that
# sushy-tools trusts CA-signed certs when fetching virtual media ISOs.
if sudo podman exec "$CONTAINER_NAME" test -f /root/sushy/ca.crt; then
    info "Installing Ironic ISO server CA into sushy-tools container trust store..."
    sudo podman exec "$CONTAINER_NAME" bash -c "
        mkdir -p /etc/pki/ca-trust/source/anchors /usr/local/share/ca-certificates &&
        cp /root/sushy/ca.crt /etc/pki/ca-trust/source/anchors/ironic-iso-ca.crt &&
        cp /root/sushy/ca.crt /usr/local/share/ca-certificates/ironic-iso-ca.crt &&
        { update-ca-trust || update-ca-certificates; }"
    success "Ironic ISO server CA installed in sushy-tools trust store"
else
    info "No Ironic ISO server CA found; skipping trust store update (HTTPS not configured)"
fi

info ""
success "sushy-tools BMC emulation started successfully for cluster ${CLUSTER_NAME}!"
info "  Endpoint: https://${BMC_GATEWAY}:${BMC_PORT}/redfish/v1/Systems (HTTPS with self-signed cert)"
info "  Container: $CONTAINER_NAME"
info ""
info "To stop: sudo podman stop $CONTAINER_NAME"
info "To restart: sudo podman restart $CONTAINER_NAME"
info "To view logs: sudo podman logs $CONTAINER_NAME"
info ""
