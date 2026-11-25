#!/bin/bash
# Label the SNO ManagedCluster on the hub, e.g. to select it for a plugin's ACM
# policies (sovcloud.open-cluster-management.io/openshift-ai=true). Idempotent.
# CI-only; consumes cluster-env.sh; no-op when no SNO VM was created.
#
# Usage: ./label_sno.sh <key>=<value> [<key>=<value> ...]

set -euo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" &>/dev/null && pwd)"
ENCLAVE_DIR="$(cd -- "${SCRIPT_DIR}/../.." &>/dev/null && pwd)"

source "${ENCLAVE_DIR}/ci/lib/output.sh"
source "${ENCLAVE_DIR}/ci/lib/config.sh"
source "${ENCLAVE_DIR}/ci/lib/ssh.sh"

if [ $# -lt 1 ]; then
    error "Usage: $0 <key>=<value> [<key>=<value> ...]"
    exit 1
fi
for label in "$@"; do
    # Kubernetes label: optional DNS prefix, name and value of [A-Za-z0-9._-].
    if [[ ! "$label" =~ ^([a-z0-9.-]+/)?[A-Za-z0-9._-]+=[A-Za-z0-9._-]*$ ]]; then
        error "Invalid label: '$label'"
        exit 1
    fi
done

load_cluster_env

case "${ENCLAVE_SNO_VM_ENABLED:-false}" in
    false) info "ENCLAVE_SNO_VM_ENABLED=false; no SNO to label, skipping"; exit 0 ;;
    true) ;;
    *) error "ENCLAVE_SNO_VM_ENABLED must be true or false, got: '${ENCLAVE_SNO_VM_ENABLED}'"; exit 1 ;;
esac

SNO_CLUSTER_NAME="${ENCLAVE_SNO_VM_CLUSTER_NAME:?ENCLAVE_SNO_VM_CLUSTER_NAME not set}"
LZ_IP="${ENCLAVE_LZ_CLUSTER_IP:-}"
if [ -z "$LZ_IP" ]; then
    error "Cannot find Landing Zone IP"
    exit 1
fi
setup_ssh_config "$LZ_IP"

info "Labeling ManagedCluster ${SNO_CLUSTER_NAME}: $*"
LZ_WORKDIR="/home/${LZ_USER}/sessions/1"
# Quoted for the remote shell; $PATH expands there.
oc_env=$(printf 'PATH=%q/bin:"$PATH" KUBECONFIG=%q' "$LZ_WORKDIR" "${LZ_WORKDIR}/ocp-cluster/auth/kubeconfig")
mc=$(printf '%q' "$SNO_CLUSTER_NAME")
remote_cmd="${oc_env} oc label managedcluster ${mc}$(printf ' %q' "$@") --overwrite"
remote_cmd+=" && ${oc_env} oc get managedcluster ${mc} --show-labels"
# shellcheck disable=SC2086  # SSH_OPTS needs word splitting
ssh -n $SSH_OPTS "$LZ_SSH" "$remote_cmd"
success "Labeled ManagedCluster ${SNO_CLUSTER_NAME}"
