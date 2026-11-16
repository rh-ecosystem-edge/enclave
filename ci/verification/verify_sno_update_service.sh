#!/bin/bash
# Verify the hub update service policy patched the SNO managed cluster.
#
# Runs `enclave-ci verify sno-update-service` (src/enclave_ci/enclave_ci/verify/sno.py):
# the `update-service` ACM policy (operators/cincinnati-operator/tasks.yaml) sets
# ClusterVersion.spec.upstream on every managed cluster to the hub OpenShift Update
# Service graph. Checks that the policy replicated to the SNO namespace is Compliant,
# that the SNO's ClusterVersion upstream matches the URL in the hub policy, and that
# the ACM config-policy-controller owns that field (both from one ClusterVersion read).
# RetrievedUpdates is reported for information only. CI-only; no-op when no SNO VM
# was created.
#
# Usage: ./verify_sno_update_service.sh

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

info "Verifying the update service patch on: $SNO_CLUSTER_NAME (via $LZ_IP)"
cd "$ENCLAVE_DIR"
# Log file collected under managed_sno/ by collect_ci_artifacts.sh.
set +e
uv run --quiet --frozen --package enclave-ci enclave-ci verify sno-update-service \
    --lz-ip "$LZ_IP" --lz-user "$LZ_USER" --session-dir "/home/${LZ_USER}/sessions/1" \
    --sno-name "$SNO_CLUSTER_NAME" 2>&1 | tee "verify_sno_update_service_${SNO_CLUSTER_NAME}.log"
RC=${PIPESTATUS[0]}
set -e

if [ "$RC" -ne 0 ]; then
    error "Update service patch verification failed: $SNO_CLUSTER_NAME"
    exit 1
fi
success "Update service patch verified: $SNO_CLUSTER_NAME"
