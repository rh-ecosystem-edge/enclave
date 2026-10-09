#!/usr/bin/env bash
# Get the Landing Zone VM IP address.
#
# Prefers the authoritative static-lease IP from cluster-env.sh
# (ENCLAVE_LZ_CLUSTER_IP, owned by vm_infra.py); falls back to a live virsh lookup
# on the cluster network if cluster-env.sh is not available.
#
# Usage:
#   ./get_landing_zone_ip.sh
#
# Environment variables:
#   ENCLAVE_CLUSTER_NAME - Cluster name (default: enclave-test)
#   WORKING_DIR / BASE_WORKING_DIR - Used to locate cluster-env.sh

set -euo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" &>/dev/null && pwd)"
ENCLAVE_DIR="$(cd -- "${SCRIPT_DIR}/../.." &>/dev/null && pwd)"

source "${ENCLAVE_DIR}/scripts/lib/config.sh"
source "${ENCLAVE_DIR}/scripts/lib/network.sh"

ENCLAVE_CLUSTER_NAME="${ENCLAVE_CLUSTER_NAME:-enclave-test}"
try_load_cluster_env || true

# Authoritative static-lease IP from cluster-env.sh.
if [ -n "${ENCLAVE_LZ_CLUSTER_IP:-}" ]; then
    echo "${ENCLAVE_LZ_CLUSTER_IP}"
    exit 0
fi

# Fallback: query libvirt directly on the cluster network.
LZ_VM_NAME="${ENCLAVE_LZ_VM_NAME:-${ENCLAVE_CLUSTER_NAME}_landingzone_0}"
CLUSTER_NETWORK="${ENCLAVE_CLUSTER_NETWORK:-192.168.2.0/24}"
get_vm_ip_on_network "$LZ_VM_NAME" "$CLUSTER_NETWORK"
