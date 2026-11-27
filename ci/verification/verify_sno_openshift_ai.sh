#!/bin/bash
# Verify OpenShift AI on the SNO managed cluster.
#
# Runs `enclave-ci verify sno-openshift-ai` (src/enclave_ci/enclave_ci/verify/openshift_ai.py):
# once the SNO is labeled for openshift-ai, the plugin's ACM policies install it there.
# Checks that the model-* and plugin-mirrors-openshift-ai policies replicated to the SNO
# namespace are Compliant, that every Subscription on the SNO has a Succeeded CSV, and
# that the DataScienceCluster default-dsc is Ready with KServe Managed. CI-only; no-op
# when no SNO VM was created.
#
# Usage: ./verify_sno_openshift_ai.sh

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

info "Verifying OpenShift AI on the SNO: $SNO_CLUSTER_NAME"
cd "$ENCLAVE_DIR"
# Log file collected under managed_sno/ by collect_ci_artifacts.sh.
set +e
uv run --quiet --frozen --package enclave-ci enclave-ci verify sno-openshift-ai \
    --lz-ip "$LZ_IP" --lz-user "$LZ_USER" --session-dir "/home/${LZ_USER}/sessions/1" \
    --sno-name "$SNO_CLUSTER_NAME" 2>&1 | tee "verify_sno_openshift_ai_${SNO_CLUSTER_NAME}.log"
RC=${PIPESTATUS[0]}
set -e

if [ "$RC" -ne 0 ]; then
    error "OpenShift AI verification failed: $SNO_CLUSTER_NAME"
    exit 1
fi
success "OpenShift AI verified on the SNO: $SNO_CLUSTER_NAME"
