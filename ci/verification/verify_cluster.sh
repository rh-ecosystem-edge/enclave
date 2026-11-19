#!/bin/bash
# Verify the hub cluster right after bootstrap.
#
# Reads the Landing Zone IP from cluster-env.sh and runs `enclave-ci verify cluster`
# (src/enclave_ci/enclave_ci/verify/), which checks once, over SSH, the
# ClusterVersion, nodes, cluster operators, MachineConfigPools, the hub Quay and, in
# disconnected mode, the Landing Zone mirror registry, and lists every problem.
#
# Usage:
#   ./verify_cluster.sh
#
# Environment Variables:
#   WORKING_DIR - Working directory holding cluster-env.sh (or BASE_WORKING_DIR +
#                 ENCLAVE_CLUSTER_NAME)
#   GITHUB_STEP_SUMMARY - GitHub Actions summary file (optional)

set -euo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" &>/dev/null && pwd)"
ENCLAVE_DIR="$(cd -- "${SCRIPT_DIR}/../.." &>/dev/null && pwd)"

source "${ENCLAVE_DIR}/ci/lib/output.sh"
source "${ENCLAVE_DIR}/ci/lib/config.sh"

# Cluster environment (single source of truth from vm_infra.py).
load_cluster_env
DEPLOYMENT_MODE="${ENCLAVE_DEPLOYMENT_MODE:-disconnected}"

# Landing Zone IP (static DHCP lease, from cluster-env.sh)
LZ_IP="${ENCLAVE_LZ_CLUSTER_IP:-}"
if [ -z "$LZ_IP" ]; then
    output "❌ Cannot find Landing Zone IP"
    error "ENCLAVE_LZ_CLUSTER_IP not set in cluster-env.sh"
    exit 1
fi

cd "$ENCLAVE_DIR"
exec uv run --quiet --frozen --package enclave-ci \
    enclave-ci verify cluster --lz-ip "$LZ_IP" --mode "$DEPLOYMENT_MODE"
