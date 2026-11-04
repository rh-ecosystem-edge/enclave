#!/bin/bash
# Verify the SNO spoke cluster deployment.
#
# Using the spoke kubeconfig provision_spoke.sh wrote on the Landing Zone, checks
# that the spoke is healthy: ClusterVersion Available, all nodes Ready, and all
# cluster operators Available and not Degraded. Allows a short settle window, and
# dumps diagnostics on failure. CI-only; no-op when no spoke was created.
#
# Usage: ./verify_spoke.sh

set -euo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" &>/dev/null && pwd)"
ENCLAVE_DIR="$(cd -- "${SCRIPT_DIR}/../.." &>/dev/null && pwd)"

source "${ENCLAVE_DIR}/scripts/lib/output.sh"
source "${ENCLAVE_DIR}/scripts/lib/config.sh"

load_cluster_env

# No-op when no spoke was created (vm_infra.py emits only 0 or 1).
SPOKE_COUNT="${ENCLAVE_SPOKE_COUNT:-0}"
case "$SPOKE_COUNT" in
    0) info "ENCLAVE_SPOKE_COUNT=0; no spoke to verify, skipping"; exit 0 ;;
    1) ;;
    *) error "ENCLAVE_SPOKE_COUNT must be 0 or 1, got: '${SPOKE_COUNT}'"; exit 1 ;;
esac

SPOKE_CLUSTER_NAME="${ENCLAVE_SPOKE_CLUSTER_NAME:?ENCLAVE_SPOKE_CLUSTER_NAME not set}"
LZ_IP="$("${ENCLAVE_DIR}/scripts/utils/get_landing_zone_ip.sh")"
if [ -z "$LZ_IP" ]; then
    error "Cannot find Landing Zone IP"
    exit 1
fi

SSH_OPTS="-o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null -o ConnectTimeout=10"
LZ_SESSION_DIR="/home/cloud-user/sessions/1"
SPOKE_KUBECONFIG="${LZ_SESSION_DIR}/${SPOKE_CLUSTER_NAME}-kubeconfig"
SUMMARY="${GITHUB_STEP_SUMMARY:-/dev/null}"

info "Verifying spoke cluster: $SPOKE_CLUSTER_NAME (via $LZ_IP)"
{
    echo "## Verifying Spoke Cluster Deployment"
    echo ""
    echo "Spoke cluster: ${SPOKE_CLUSTER_NAME}"
} | tee -a "$SUMMARY"

# All checks run on the LZ against the spoke kubeconfig in one pass. A short retry
# absorbs post-install operator settling; on failure, diagnostics are dumped.
set +e
# shellcheck disable=SC2086  # SSH_OPTS needs word splitting
ssh $SSH_OPTS "cloud-user@${LZ_IP}" \
    "KUBECONFIG=${SPOKE_KUBECONFIG} SPOKE=${SPOKE_CLUSTER_NAME} PATH=${LZ_SESSION_DIR}/bin:\$PATH bash -s" \
    2>&1 <<'REMOTE' | tee -a "$SUMMARY"
set -uo pipefail

if [ ! -f "$KUBECONFIG" ]; then
    echo "ERROR: spoke kubeconfig not found: $KUBECONFIG"
    exit 1
fi

ocq() { oc --request-timeout=30s "$@" 2>/dev/null; }
unhealthy_cos() { ocq get clusteroperators -o json | jq -r ".items[] | select(any(.status.conditions[]; .type==\"$1\" and .status==\"$2\")) | .metadata.name" | paste -sd' ' -; }

ok=""
for i in $(seq 1 10); do
    CV=$(ocq get clusterversion version -o jsonpath='{range .status.conditions[?(@.type=="Available")]}{.status}{end}')
    TOTAL=$(ocq get nodes --no-headers | grep -c . || true)
    READY=$(ocq get nodes --no-headers | awk '$2 ~ /(^|,)Ready(,|$)/ {c++} END{print c+0}')
    UNAVAIL=$(unhealthy_cos Available False)
    DEGRADED=$(unhealthy_cos Degraded True)
    if [ "$CV" = "True" ] && [ "${TOTAL:-0}" -ge 1 ] && [ "${READY:-0}" = "${TOTAL:-0}" ] \
       && [ -z "$UNAVAIL" ] && [ -z "$DEGRADED" ]; then
        ok=1; break
    fi
    echo "  [$i/10] ClusterVersion=${CV:-?} nodesReady=${READY:-0}/${TOTAL:-0} unavailable='${UNAVAIL}' degraded='${DEGRADED}' — waiting 30s"
    sleep 30
done

echo "ClusterVersion Available: ${CV:-unknown}"
echo "Nodes Ready: ${READY:-0}/${TOTAL:-0}"
echo "Unavailable operators: ${UNAVAIL:-none}"
echo "Degraded operators: ${DEGRADED:-none}"

if [ -n "$ok" ]; then
    echo "✅ Spoke cluster is healthy"
    exit 0
fi

echo "❌ Spoke cluster is not healthy — diagnostics:"
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
    error "Spoke cluster verification failed: $SPOKE_CLUSTER_NAME"
    exit 1
fi
success "Spoke cluster verified: $SPOKE_CLUSTER_NAME"
