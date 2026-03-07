#!/bin/bash
# Verify that dev-scripts created the required networks
#
# This script checks that libvirt networks exist and are active
# after running dev-scripts make infra_only.

set -euo pipefail

# Detect Enclave repository root
SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" &>/dev/null && pwd)"
ENCLAVE_DIR="$(cd -- "${SCRIPT_DIR}/../.." &>/dev/null && pwd)"

# Source shared utilities
source "${ENCLAVE_DIR}/scripts/lib/output.sh"

# Get cluster name
ENCLAVE_CLUSTER_NAME="${ENCLAVE_CLUSTER_NAME:-enclave-test}"
DEV_SCRIPTS_CONFIG="${DEV_SCRIPTS_PATH:-}/config_${ENCLAVE_CLUSTER_NAME}.sh"

# Source dev-scripts config if it exists
if [ -f "$DEV_SCRIPTS_CONFIG" ]; then
    # shellcheck source=/dev/null
    source "$DEV_SCRIPTS_CONFIG"
fi

CLUSTER_NAME="${CLUSTER_NAME:-$ENCLAVE_CLUSTER_NAME}"

# Expected network names
BMC_NETWORK="${CLUSTER_NAME}-p"
CLUSTER_NETWORK="${CLUSTER_NAME}-e"

info "Verifying networks for cluster: ${CLUSTER_NAME}"
info "  Expected BMC network: ${BMC_NETWORK}"
info "  Expected cluster network: ${CLUSTER_NETWORK}"

VERIFICATION_FAILED=0

# Check BMC network (provisioning)
info ""
info "Checking BMC network (${BMC_NETWORK})..."
if sudo virsh net-info "${BMC_NETWORK}" >/dev/null 2>&1; then
    NET_STATE=$(sudo virsh net-info "${BMC_NETWORK}" | grep "^Active:" | awk '{print $2}')
    NET_AUTOSTART=$(sudo virsh net-info "${BMC_NETWORK}" | grep "^Autostart:" | awk '{print $2}')
    NET_PERSISTENT=$(sudo virsh net-info "${BMC_NETWORK}" | grep "^Persistent:" | awk '{print $2}')

    success "Network ${BMC_NETWORK} exists"
    info "  State: ${NET_STATE}"
    info "  Autostart: ${NET_AUTOSTART}"
    info "  Persistent: ${NET_PERSISTENT}"

    if [ "$NET_STATE" != "yes" ]; then
        error "  Network ${BMC_NETWORK} is not active!"
        error "  Attempting to start it..."
        if sudo virsh net-start "${BMC_NETWORK}" 2>&1; then
            success "  Network ${BMC_NETWORK} started"
        else
            error "  Failed to start network ${BMC_NETWORK}"
            VERIFICATION_FAILED=1
        fi
    fi

    # Check if bridge interface exists
    BRIDGE_IP=$(sudo virsh net-dumpxml "${BMC_NETWORK}" | grep -oP '(?<=<ip address=")[^"]*' || echo "unknown")
    info "  Bridge IP: ${BRIDGE_IP}"

    if ip link show "${BMC_NETWORK}" >/dev/null 2>&1; then
        success "  Bridge interface ${BMC_NETWORK} exists"

        # Check if bridge has IP
        if ip addr show "${BMC_NETWORK}" | grep -q "inet ${BRIDGE_IP}/"; then
            success "  Bridge has IP ${BRIDGE_IP}"
        else
            warning "  Bridge exists but does not have expected IP ${BRIDGE_IP}"
            info "  Actual IPs:"
            ip addr show "${BMC_NETWORK}" | grep "inet " | while IFS= read -r line; do
                info "    $line"
            done
        fi
    else
        error "  Bridge interface ${BMC_NETWORK} does not exist!"
        error "  This means libvirt network is defined but bridge wasn't created"
        VERIFICATION_FAILED=1
    fi
else
    error "Network ${BMC_NETWORK} does not exist!"
    error "Dev-scripts failed to create the BMC network"
    VERIFICATION_FAILED=1
fi

# Check cluster network (external)
info ""
info "Checking cluster network (${CLUSTER_NETWORK})..."
if sudo virsh net-info "${CLUSTER_NETWORK}" >/dev/null 2>&1; then
    NET_STATE=$(sudo virsh net-info "${CLUSTER_NETWORK}" | grep "^Active:" | awk '{print $2}')
    NET_AUTOSTART=$(sudo virsh net-info "${CLUSTER_NETWORK}" | grep "^Autostart:" | awk '{print $2}')
    NET_PERSISTENT=$(sudo virsh net-info "${CLUSTER_NETWORK}" | grep "^Persistent:" | awk '{print $2}')

    success "Network ${CLUSTER_NETWORK} exists"
    info "  State: ${NET_STATE}"
    info "  Autostart: ${NET_AUTOSTART}"
    info "  Persistent: ${NET_PERSISTENT}"

    if [ "$NET_STATE" != "yes" ]; then
        error "  Network ${CLUSTER_NETWORK} is not active!"
        error "  Attempting to start it..."
        if sudo virsh net-start "${CLUSTER_NETWORK}" 2>&1; then
            success "  Network ${CLUSTER_NETWORK} started"
        else
            error "  Failed to start network ${CLUSTER_NETWORK}"
            VERIFICATION_FAILED=1
        fi
    fi

    # Check if bridge interface exists
    BRIDGE_IP=$(sudo virsh net-dumpxml "${CLUSTER_NETWORK}" | grep -oP '(?<=<ip address=")[^"]*' || echo "unknown")
    info "  Bridge IP: ${BRIDGE_IP}"

    if ip link show "${CLUSTER_NETWORK}" >/dev/null 2>&1; then
        success "  Bridge interface ${CLUSTER_NETWORK} exists"

        # Check if bridge has IP
        if ip addr show "${CLUSTER_NETWORK}" | grep -q "inet ${BRIDGE_IP}/"; then
            success "  Bridge has IP ${BRIDGE_IP}"
        else
            warning "  Bridge exists but does not have expected IP ${BRIDGE_IP}"
            info "  Actual IPs:"
            ip addr show "${CLUSTER_NETWORK}" | grep "inet " | while IFS= read -r line; do
                info "    $line"
            done
        fi
    else
        error "  Bridge interface ${CLUSTER_NETWORK} does not exist!"
        error "  This means libvirt network is defined but bridge wasn't created"
        VERIFICATION_FAILED=1
    fi
else
    error "Network ${CLUSTER_NETWORK} does not exist!"
    error "Dev-scripts failed to create the cluster network"
    VERIFICATION_FAILED=1
fi

if [ $VERIFICATION_FAILED -eq 0 ]; then
    info ""
    success "All networks verified successfully"
    exit 0
else
    error ""
    error "Network verification failed!"
    error "Dev-scripts may have encountered errors during network creation"
    exit 1
fi
