#!/bin/bash
# Verify the hub update service policy patched the SNO managed cluster.
#
# The `update-service` ACM policy (operators/cincinnati-operator/tasks.yaml) sets
# ClusterVersion.spec.upstream on every managed cluster to the hub OpenShift Update
# Service graph. Checks that the policy replicated to the SNO namespace is Compliant,
# that the SNO's ClusterVersion upstream matches the URL in the hub policy, and that
# the ACM config-policy-controller is the field manager that set it.
# RetrievedUpdates is reported for information only. CI-only; no-op when no SNO VM
# was created.
#
# Usage: ./verify_sno_update_service.sh

set -euo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" &>/dev/null && pwd)"
ENCLAVE_DIR="$(cd -- "${SCRIPT_DIR}/../.." &>/dev/null && pwd)"

source "${ENCLAVE_DIR}/ci/lib/output.sh"
source "${ENCLAVE_DIR}/ci/lib/config.sh"

load_cluster_env

case "${ENCLAVE_SNO_VM_ENABLED:-false}" in
    false) info "ENCLAVE_SNO_VM_ENABLED=false; no SNO to verify, skipping"; exit 0 ;;
    true) ;;
    *) error "ENCLAVE_SNO_VM_ENABLED must be true or false, got: '${ENCLAVE_SNO_VM_ENABLED}'"; exit 1 ;;
esac

SNO_CLUSTER_NAME="${ENCLAVE_SNO_VM_CLUSTER_NAME:?ENCLAVE_SNO_VM_CLUSTER_NAME not set}"
LZ_IP="$("${ENCLAVE_DIR}/ci/utils/get_landing_zone_ip.sh")"
if [ -z "$LZ_IP" ]; then
    error "Cannot find Landing Zone IP"
    exit 1
fi

SSH_OPTS="-o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null -o ConnectTimeout=10"
LZ_SESSION_DIR="/home/cloud-user/sessions/1"
SNO_KUBECONFIG="${LZ_SESSION_DIR}/${SNO_CLUSTER_NAME}-kubeconfig"
SUMMARY="${GITHUB_STEP_SUMMARY:-/dev/null}"
LOG_FILE="verify_sno_update_service_${SNO_CLUSTER_NAME}.log"

info "Verifying the update service patch on: $SNO_CLUSTER_NAME (via $LZ_IP)"
{
    echo "## Verifying Update Service Patch on the SNO"
    echo ""
} | tee -a "$SUMMARY"

set +e
# shellcheck disable=SC2086  # SSH_OPTS needs word splitting
ssh $SSH_OPTS "cloud-user@${LZ_IP}" \
    "KUBECONFIG=${SNO_KUBECONFIG} HUB_KUBECONFIG=${LZ_SESSION_DIR}/ocp-cluster/auth/kubeconfig SNO=${SNO_CLUSTER_NAME} PATH=${LZ_SESSION_DIR}/bin:\$PATH bash -s" \
    2>&1 <<'REMOTE' | tee "$LOG_FILE" | tee -a "$SUMMARY"
set -uo pipefail

ocq() { oc --request-timeout=30s "$@" 2>/dev/null; }
hubq() { ocq --kubeconfig "$HUB_KUBECONFIG" "$@"; }
POLICY_NS=openshift-acm-policies
POLICY=update-service
REPLICATED="${POLICY_NS}.${POLICY}"
# The ConfigurationPolicy controller updates objects without an explicit field
# manager, so the API server records its binary name.
ACM_MANAGER=config-policy-controller

EXPECTED=$(hubq -n "$POLICY_NS" get policy "$POLICY" -o json | jq -r \
    '[.spec["policy-templates"][].objectDefinition.spec["object-templates"][]?.objectDefinition
      | select(.kind == "ClusterVersion") | .spec.upstream][0] // empty')
if [ -z "$EXPECTED" ]; then
    echo "ERROR: could not read the expected upstream from policy ${POLICY_NS}/${POLICY} on the hub"
    hubq -n "$POLICY_NS" get policy "$POLICY" -o yaml || true
    exit 1
fi
echo "Expected upstream (from the hub policy): $EXPECTED"

# The policy re-evaluates every 45s while NonCompliant; allow ~10 minutes.
ok=""
for i in $(seq 1 20); do
    COMPLIANT=$(hubq -n "$SNO" get policy "$REPLICATED" -o jsonpath='{.status.compliant}')
    UPSTREAM=$(ocq get clusterversion version -o jsonpath='{.spec.upstream}')
    if [ "$COMPLIANT" = "Compliant" ] && [ "$UPSTREAM" = "$EXPECTED" ]; then
        ok=1; break
    fi
    echo "  [$i/20] policy=${COMPLIANT:-not replicated yet} upstream='${UPSTREAM}' — waiting 30s"
    sleep 30
done

RETRIEVED=$(ocq get clusterversion version \
    -o jsonpath='{range .status.conditions[?(@.type=="RetrievedUpdates")]}{.status} ({.reason}) {.message}{end}')
echo "Replicated policy ${SNO}/${REPLICATED}: ${COMPLIANT:-not found}"
echo "SNO ClusterVersion upstream: ${UPSTREAM:-unset}"
echo "RetrievedUpdates (informational): ${RETRIEVED:-unknown}"

# Who last set spec.upstream: "<manager> <operation> <time>".
SETTER=$(ocq get clusterversion version --show-managed-fields -o json | jq -r \
    '[.metadata.managedFields[]? | select(.fieldsV1["f:spec"]["f:upstream"] != null)
      | "\(.manager) \(.operation) \(.time)"] | last // empty')
echo "spec.upstream set by: ${SETTER:-unknown}"
if [ -n "$ok" ] && [ "${SETTER%% *}" != "$ACM_MANAGER" ]; then
    echo "❌ spec.upstream matches, but it was not set by ${ACM_MANAGER}"
    ok=""
fi

if [ -n "$ok" ]; then
    echo "✅ Update service policy patched the SNO ClusterVersion"
    exit 0
fi

echo "❌ Update service patch not applied — diagnostics:"
echo "----- Replicated policy status (hub) -----"
hubq -n "$SNO" get policy "$REPLICATED" -o jsonpath='{range .status.details[*]}{.templateMeta.name}: {.compliant}{"\n"}{range .history[0:3]}  {.lastTimestamp} {.message}{"\n"}{end}{end}' || true
echo "----- Policies replicated to ${SNO} (hub) -----"
hubq -n "$SNO" get policy || true
echo "----- PlacementRule ${POLICY_NS}/${POLICY} decisions (hub) -----"
hubq -n "$POLICY_NS" get placementrule "$POLICY" -o jsonpath='{range .status.decisions[*]}{.clusterName}{"\n"}{end}' || true
echo "----- ClusterVersion spec (SNO) -----"
ocq get clusterversion version -o jsonpath='{.spec}' || true
echo ""
echo "----- ClusterVersion managedFields (SNO) -----"
ocq get clusterversion version --show-managed-fields -o jsonpath='{range .metadata.managedFields[*]}{.manager} {.operation} {.time}{"\n"}{end}' || true
echo "----- ConfigurationPolicies (SNO) -----"
ocq get configurationpolicy -A || true
exit 1
REMOTE
RC=${PIPESTATUS[0]}
set -e

echo ""
if [ "$RC" -ne 0 ]; then
    error "Update service patch verification failed: $SNO_CLUSTER_NAME"
    exit 1
fi
success "Update service patch verified: $SNO_CLUSTER_NAME"
