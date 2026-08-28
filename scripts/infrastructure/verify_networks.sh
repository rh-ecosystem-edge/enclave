#!/bin/bash
# Verify that vm_infra.py created the required libvirt networks

set -euo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" &>/dev/null && pwd)"
ENCLAVE_DIR="$(cd -- "${SCRIPT_DIR}/../.." &>/dev/null && pwd)"

source "${ENCLAVE_DIR}/scripts/lib/output.sh"
source "${ENCLAVE_DIR}/scripts/lib/config.sh"
source "${ENCLAVE_DIR}/scripts/lib/common.sh"

ENCLAVE_CLUSTER_NAME="${ENCLAVE_CLUSTER_NAME:-enclave-test}"
ensure_working_dir

load_cluster_env

info "Verifying networks for cluster: ${ENCLAVE_CLUSTER_NAME}"

VERIFICATION_FAILED=0

# Verify a single libvirt network: existence, active state, bridge IP
verify_network() {
    local net_name="$1"
    local expected_ip="${2:-}"

    info ""
    info "Checking network: ${net_name}..."

    if ! sudo virsh net-info "${net_name}" >/dev/null 2>&1; then
        error "Network ${net_name} does not exist"
        error "Run 'make -f Makefile.ci environment' to create the infrastructure"
        VERIFICATION_FAILED=1
        return
    fi

    NET_STATE=$(sudo virsh net-info "${net_name}" | grep "^Active:" | awk '{print $2}')
    success "Network ${net_name} exists (active: ${NET_STATE})"

    if [ "$NET_STATE" != "yes" ]; then
        error "  Network ${net_name} is not active — attempting to start it..."
        if sudo virsh net-start "${net_name}" 2>&1; then
            success "  Network ${net_name} started"
        else
            error "  Failed to start network ${net_name}"
            VERIFICATION_FAILED=1
        fi
    fi

    if ! ip link show "${net_name}" >/dev/null 2>&1; then
        error "  Bridge interface ${net_name} does not exist"
        VERIFICATION_FAILED=1
        return
    fi
    success "  Bridge interface ${net_name} exists"

    if [ -n "$expected_ip" ]; then
        if ip addr show "${net_name}" | grep -q "inet ${expected_ip}/"; then
            success "  Bridge has expected IP ${expected_ip}"
        else
            error "  Bridge ${net_name} does not have IP ${expected_ip}"
            VERIFICATION_FAILED=1
            info "  Actual addresses:"
            ip addr show "${net_name}" | grep "inet " | while IFS= read -r line; do
                info "    $line"
            done
        fi
    fi
}

# Derive gateway IPs from network CIDRs (e.g. "100.64.5.0/24" → "100.64.5.1")
_gateway() { echo "$1" | awk -F'[./]' '{print $1"."$2"."$3".1"}'; }

verify_network "${ENCLAVE_BMC_BRIDGE}" "$(_gateway "${ENCLAVE_BMC_NETWORK}")"
verify_network "${ENCLAVE_CLUSTER_BRIDGE}" "$(_gateway "${ENCLAVE_CLUSTER_NETWORK}")"

# Disconnected mode: also verify LZ uplink bridge
if [ "${ENCLAVE_DEPLOYMENT_MODE:-}" = "disconnected" ] && [ -n "${ENCLAVE_LZ_NETWORK:-}" ]; then
    UPLINK_BRIDGE="${ENCLAVE_CLUSTER_NAME}-u"
    verify_network "${UPLINK_BRIDGE}" "$(_gateway "${ENCLAVE_LZ_NETWORK}")"
fi

if [ $VERIFICATION_FAILED -eq 0 ]; then
    info ""
    success "All networks verified successfully"
    exit 0
else
    error ""
    error "Network verification failed"
    exit 1
fi
