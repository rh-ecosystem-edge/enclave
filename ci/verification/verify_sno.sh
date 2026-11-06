#!/bin/bash
# Verify the SNO managed cluster.
#
# Using the SNO kubeconfig deploy_sno.sh wrote on the Landing Zone, checks that the
# SNO is healthy: ClusterVersion Available, all nodes Ready, and all cluster
# operators Available and not Degraded. On the hub, checks that the SNO is a Joined
# and Available ManagedCluster. Allows a short settle window, and dumps diagnostics
# on failure. CI-only; no-op when no SNO VM was created.
#
# Usage: ./verify_sno.sh

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
LOG_FILE="verify_sno_${SNO_CLUSTER_NAME}.log"

info "Verifying SNO managed cluster: $SNO_CLUSTER_NAME (via $LZ_IP)"
{
    echo "## Verifying SNO Managed Cluster"
    echo ""
    echo "SNO cluster: ${SNO_CLUSTER_NAME}"
} | tee -a "$SUMMARY"

# All checks run on the LZ against the SNO and hub kubeconfigs in one pass. A short
# retry absorbs post-install operator settling; on failure, diagnostics are dumped.
set +e
# shellcheck disable=SC2086  # SSH_OPTS needs word splitting
ssh $SSH_OPTS "cloud-user@${LZ_IP}" \
    "KUBECONFIG=${SNO_KUBECONFIG} HUB_KUBECONFIG=${LZ_SESSION_DIR}/ocp-cluster/auth/kubeconfig SNO=${SNO_CLUSTER_NAME} PATH=${LZ_SESSION_DIR}/bin:\$PATH bash -s" \
    2>&1 <<'REMOTE' | tee "$LOG_FILE" | tee -a "$SUMMARY"
set -uo pipefail

if [ ! -f "$KUBECONFIG" ]; then
    echo "ERROR: SNO kubeconfig not found: $KUBECONFIG"
    exit 1
fi

ocq() { oc --request-timeout=30s "$@" 2>/dev/null; }
hubq() { ocq --kubeconfig "$HUB_KUBECONFIG" "$@"; }
unhealthy_cos() { ocq get clusteroperators -o json | jq -r ".items[] | select(any(.status.conditions[]; .type==\"$1\" and .status==\"$2\")) | .metadata.name" | paste -sd' ' -; }
mc_condition() { hubq get managedcluster "$SNO" -o jsonpath="{range .status.conditions[?(@.type==\"$1\")]}{.status}{end}"; }

ok=""
for i in $(seq 1 10); do
    CV=$(ocq get clusterversion version -o jsonpath='{range .status.conditions[?(@.type=="Available")]}{.status}{end}')
    TOTAL=$(ocq get nodes --no-headers | grep -c . || true)
    READY=$(ocq get nodes --no-headers | awk '$2 ~ /(^|,)Ready(,|$)/ {c++} END{print c+0}')
    UNAVAIL=$(unhealthy_cos Available False)
    DEGRADED=$(unhealthy_cos Degraded True)
    MC_JOINED=$(mc_condition ManagedClusterJoined)
    MC_AVAIL=$(mc_condition ManagedClusterConditionAvailable)
    if [ "$CV" = "True" ] && [ "${TOTAL:-0}" -ge 1 ] && [ "${READY:-0}" = "${TOTAL:-0}" ] \
       && [ -z "$UNAVAIL" ] && [ -z "$DEGRADED" ] \
       && [ "$MC_JOINED" = "True" ] && [ "$MC_AVAIL" = "True" ]; then
        ok=1; break
    fi
    echo "  [$i/10] ClusterVersion=${CV:-?} nodesReady=${READY:-0}/${TOTAL:-0} unavailable='${UNAVAIL}' degraded='${DEGRADED}' managedCluster joined=${MC_JOINED:-?} available=${MC_AVAIL:-?} — waiting 30s"
    sleep 30
done

echo "ClusterVersion Available: ${CV:-unknown}"
echo "Nodes Ready: ${READY:-0}/${TOTAL:-0}"
echo "Unavailable operators: ${UNAVAIL:-none}"
echo "Degraded operators: ${DEGRADED:-none}"
echo "ManagedCluster Joined (hub): ${MC_JOINED:-unknown}"
echo "ManagedCluster Available (hub): ${MC_AVAIL:-unknown}"

if [ -n "$ok" ]; then
    echo "✅ SNO managed cluster is healthy"
    exit 0
fi

echo "❌ SNO managed cluster is not healthy — diagnostics:"
echo "----- ClusterVersion -----"
ocq get clusterversion || true
echo "----- Nodes -----"
ocq get nodes -o wide || true
for n in $(ocq get nodes --no-headers | awk '$2 !~ /(^|,)Ready(,|$)/ {print $1}'); do
    echo "--- NotReady node: $n ---"
    ocq get node "$n" -o jsonpath='{range .status.conditions[*]}{.type}={.status} ({.reason}) {.message}{"\n"}{end}' || true
done
echo "----- Cluster operators -----"
ocq get clusteroperators || true
ocq get clusteroperators -o json | jq -r '.items[] | select(any(.status.conditions[]; (.type=="Available" and .status!="True") or (.type=="Degraded" and .status=="True"))) | .metadata.name + " -> " + ([.status.conditions[] | select(.type=="Available" or .type=="Degraded" or .type=="Progressing") | "\(.type)=\(.status) \(.message // "")"] | join(" | "))' || true
echo "----- ManagedCluster (hub) -----"
hubq get managedcluster "$SNO" --show-labels || true
hubq get managedcluster "$SNO" -o jsonpath='{range .status.conditions[*]}{.type}={.status} ({.reason}) {.message}{"\n"}{end}' || true
hubq get managedclusteraddons -n "$SNO" || true
echo "----- Non-running pods -----"
ocq get pods -A | grep -vE 'Running|Completed' | head -40 || true
echo "----- Recent events -----"
ocq get events -A --sort-by=.lastTimestamp | tail -30 || true
exit 1
REMOTE
RC=${PIPESTATUS[0]}
set -e

echo ""
if [ "$RC" -ne 0 ]; then
    error "SNO managed cluster verification failed: $SNO_CLUSTER_NAME"
    exit 1
fi
success "SNO managed cluster verified: $SNO_CLUSTER_NAME"
