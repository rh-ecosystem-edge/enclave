#!/bin/bash
# Verify the SNO managed cluster.
#
# Runs `enclave-ci verify sno` (src/enclave_ci/enclave_ci/verify/sno.py): using the SNO
# kubeconfig deploy_sno.sh wrote on the Landing Zone, checks the SNO the same way as
# the hub (ClusterVersion, nodes, cluster operators, MachineConfigPools) and, on the
# hub, that it is a Joined and Available ManagedCluster. Allows a short settle window
# and prints diagnostics on failure. CI-only; no-op when no SNO VM was created.
#
# Usage: ./verify_sno.sh

set -euo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" &>/dev/null && pwd)"
ENCLAVE_DIR="$(cd -- "${SCRIPT_DIR}/../.." &>/dev/null && pwd)"

source "${ENCLAVE_DIR}/ci/lib/output.sh"
source "${ENCLAVE_DIR}/ci/lib/config.sh"
source "${ENCLAVE_DIR}/ci/lib/ssh.sh"

load_cluster_env

case "${ENCLAVE_SNO_VM_ENABLED:-false}" in
    false) info "ENCLAVE_SNO_VM_ENABLED=false; no SNO to verify, skipping"; exit 0 ;;
    true) ;;
    *) error "ENCLAVE_SNO_VM_ENABLED must be true or false, got: '${ENCLAVE_SNO_VM_ENABLED}'"; exit 1 ;;
esac

SNO_CLUSTER_NAME="${ENCLAVE_SNO_VM_CLUSTER_NAME:?ENCLAVE_SNO_VM_CLUSTER_NAME not set}"
# Landing Zone IP (static DHCP lease, from cluster-env.sh)
LZ_IP="${ENCLAVE_LZ_CLUSTER_IP:-}"
if [ -z "$LZ_IP" ]; then
    error "Cannot find Landing Zone IP"
    exit 1
fi
setup_ssh_config "$LZ_IP"

info "Verifying SNO managed cluster: $SNO_CLUSTER_NAME (via $LZ_IP)"
cd "$ENCLAVE_DIR"
# Log file collected under managed_sno/ by collect_ci_artifacts.sh.
set +e
uv run --quiet --frozen --package enclave-ci enclave-ci verify sno \
    --lz-ip "$LZ_IP" --lz-user "$LZ_USER" --session-dir "/home/${LZ_USER}/sessions/1" \
    --sno-name "$SNO_CLUSTER_NAME" 2>&1 | tee "verify_sno_${SNO_CLUSTER_NAME}.log"
RC=${PIPESTATUS[0]}
set -e

if [ "$RC" -ne 0 ]; then
    error "SNO managed cluster verification failed: $SNO_CLUSTER_NAME"
    exit 1
fi
success "SNO managed cluster verified: $SNO_CLUSTER_NAME"
