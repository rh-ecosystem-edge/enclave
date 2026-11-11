#!/bin/bash
# Verify OpenShift cluster deployment
#
# This script verifies that the OpenShift cluster is successfully deployed
# and all components are healthy. Supports both local and GitHub Actions execution.
#
# It checks once (no waiting; bootstrap waits for the MachineConfig rollout of its
# Quay mirror changes) and fails listing whatever does not hold:
#   - the ClusterVersion Available, and not Progressing or Failing
#   - every node Ready
#   - every cluster operator Available and not Degraded
#   - every MachineConfigPool Updated, and not Updating or Degraded
#   - the hub Quay (QuayRegistry quay-enterprise/registry) Available and its
#     /health/instance answering 200
#   - disconnected only: the Landing Zone mirror registry (quayHostname, default
#     mirror.<baseDomain>) /health/instance answering 200
#
# Usage:
#   ./verify_cluster.sh
#
# Environment Variables:
#   ENCLAVE_CLUSTER_NAME - Cluster name (required)
#   WORKING_DIR - Working directory (required)
#   GITHUB_STEP_SUMMARY - GitHub Actions summary file (optional)

set -euo pipefail

# Detect Enclave repository root
SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" &>/dev/null && pwd)"
ENCLAVE_DIR="$(cd -- "${SCRIPT_DIR}/../.." &>/dev/null && pwd)"

# Source shared utilities
source "${ENCLAVE_DIR}/ci/lib/output.sh"
source "${ENCLAVE_DIR}/ci/lib/config.sh"

# Required variables
: "${ENCLAVE_CLUSTER_NAME:?ENCLAVE_CLUSTER_NAME must be set}"

# Auto-construct WORKING_DIR if not set
if [ -z "${WORKING_DIR:-}" ]; then
    if [ -n "${BASE_WORKING_DIR:-}" ] && [ -n "${ENCLAVE_CLUSTER_NAME}" ]; then
        WORKING_DIR="${BASE_WORKING_DIR}/${ENCLAVE_CLUSTER_NAME}"
    else
        echo "ERROR: WORKING_DIR not set and cannot construct from BASE_WORKING_DIR + ENCLAVE_CLUSTER_NAME" >&2
        exit 1
    fi
fi

# Deployment mode from cluster-env.sh (vm_infra.py); disconnected unless connected.
try_load_cluster_env || true
DEPLOYMENT_MODE="${ENCLAVE_DEPLOYMENT_MODE:-disconnected}"

# Error tracking
VERIFICATION_FAILED=0

# Disable exit on error for verification steps (we collect all failures)
set +e

output "## Verifying Cluster Deployment"
output ""

# Get Landing Zone IP using helper script
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
LZ_IP=$("${SCRIPT_DIR}/../utils/get_landing_zone_ip.sh")

if [ -z "$LZ_IP" ]; then
    output "❌ Cannot find Landing Zone IP"
    echo -e "${RED}ERROR:${NC} Cannot find Landing Zone IP" >&2
    echo "Cluster env file: ${WORKING_DIR}/cluster-env.sh" >&2
    cat "${WORKING_DIR}/cluster-env.sh" 2>/dev/null || echo "cluster-env.sh not found" >&2
    echo "" >&2
    echo "Trying virsh domifaddr:" >&2
    sudo virsh domifaddr "${ENCLAVE_CLUSTER_NAME}_landingzone_0" 2>&1 || true
    exit 1
fi
output "✅ Landing Zone IP: $LZ_IP"

SSH_OPTS="-o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null -o ConnectTimeout=10"

LZ_SESSION_DIR="/home/cloud-user/sessions/1"
# Evaluated on the LZ: \$PATH expands to the remote PATH at runtime
LZ_OC_ENV="export PATH=${LZ_SESSION_DIR}/bin:\$PATH KUBECONFIG=${LZ_SESSION_DIR}/ocp-cluster/auth/kubeconfig"

# Check if kubeconfig exists
if ! ssh $SSH_OPTS cloud-user@$LZ_IP "test -f ${LZ_SESSION_DIR}/ocp-cluster/auth/kubeconfig" 2>&1; then
    output "❌ Kubeconfig not found on Landing Zone"
    echo -e "${RED}ERROR:${NC} Kubeconfig not found at ${LZ_SESSION_DIR}/ocp-cluster/auth/kubeconfig" >&2
    ssh $SSH_OPTS cloud-user@$LZ_IP "ls -la ${LZ_SESSION_DIR}/ocp-cluster/auth/ 2>&1 || echo 'ocp-cluster/auth directory not found'"
    exit 1
fi
output "✅ Kubeconfig found"

# Health problems, one per line ("healthy" when there are none). Evaluated on the LZ.
health_problems() {
    # shellcheck disable=SC2086 # SSH_OPTS must word-split into separate flags
    ssh $SSH_OPTS cloud-user@$LZ_IP "${LZ_OC_ENV} && MODE=${DEPLOYMENT_MODE} CFG=/home/cloud-user/enclave/config/global.yaml bash -s" <<'REMOTE' 2>"$HEALTH_ERR"
set -o pipefail
d=$(mktemp -d); trap 'rm -rf "$d"' EXIT
p=()
for r in clusterversion nodes co mcp; do
    oc get "$r" -o json > "$d/$r.json" 2>"$d/$r.err" \
        || p+=("query-failed: oc get $r: $(tail -1 "$d/$r.err" | cut -c1-200)")
done
if [ "${#p[@]}" -eq 0 ]; then
    while IFS= read -r line; do p+=("$line"); done < <(jq -rn --slurpfile n "$d/nodes.json" \
        --slurpfile c "$d/co.json" --slurpfile m "$d/mcp.json" \
        --slurpfile v "$d/clusterversion.json" '
        # " (reason) message": message on one line and trimmed; "-" for no reason
        def why: ((.reason // "") | if . == "" then "-" else . end) as $r
            | (.message // "" | gsub("\\s+"; " ") | ltrimstr(" ") | .[0:200]) as $m
            | " (\($r))" + (if $m == "" then "" else " " + $m end);
        ($v[0].items[] | .metadata.name as $x | .status.conditions[]?
            | select((.type == "Available" and .status != "True")
                     or ((.type == "Progressing" or .type == "Failing") and .status == "True"))
            | "clusterversion/\($x): \(.type)=\(.status)\(why)"),
        (if ($n[0].items | length) == 0 then "nodes: none found" else empty end),
        ($n[0].items[] | .metadata.name as $x
            | ([.status.conditions[]? | select(.type == "Ready")][0] // {status: "Unknown"}) as $r
            | select($r.status != "True") | "node/\($x): Ready=\($r.status)\($r | why)"),
        ($c[0].items[] | .metadata.name as $x | .status.conditions[]?
            | select((.type == "Available" and .status != "True")
                     or (.type == "Degraded" and .status == "True"))
            | "co/\($x): \(.type)=\(.status)\(why)"),
        ($m[0].items[] | .metadata.name as $x | .status.conditions[]?
            | select((.type == "Updated" and .status != "True")
                     or ((.type == "Updating" or .type == "Degraded") and .status == "True"))
            | "mcp/\($x): \(.type)=\(.status)\(why)")')
fi
qa=$(oc -n quay-enterprise get quayregistry registry \
    -o jsonpath='{range .status.conditions[?(@.type=="Available")]}{.status}{end}' 2>/dev/null)
[ "$qa" = "True" ] || p+=("quay/hub: Available=${qa:-unknown}")
# health <label> <url>: report a non-200 /health/instance with its (trimmed) body,
# which names the failing Quay component.
health() {
    local out code body
    out=$(curl -sk -m 10 -w '\n%{http_code}' "$2" 2>&1)
    code=${out##*$'\n'}; body=${out%$'\n'*}
    [ "$code" = "200" ] && return
    [ "$body" = "$out" ] && body=""
    body=$(printf '%s' "$body" | tr -s '[:space:]' ' ' | cut -c1-300)
    p+=("$1: /health/instance=${code:-no answer}${body:+ $body}")
}
host=$(oc -n quay-enterprise get route registry-quay -o jsonpath='{.spec.host}' 2>/dev/null)
health quay/hub "https://${host}/health/instance"
if [ "$MODE" = "disconnected" ]; then
    # The LZ mirror answers on its hostname (quayHostname, default mirror.<baseDomain>),
    # not on localhost.
    cfg() { sed -n "s/^$1:[[:space:]]*//p" "$CFG" 2>/dev/null | head -1 | tr -d "\"'"; }
    mirror=$(cfg quayHostname)
    [ -n "$mirror" ] || { base=$(cfg baseDomain); [ -n "$base" ] && mirror="mirror.${base}"; }
    if [ -n "$mirror" ]; then
        health quay/lz-mirror "https://${mirror}:8443/health/instance"
    else
        p+=("quay/lz-mirror: no quayHostname or baseDomain in $CFG")
    fi
fi
if [ "${#p[@]}" -eq 0 ]; then echo "healthy"; else printf '%s\n' "${p[@]}"; fi
REMOTE
}

output ""
output "### Cluster Health (${DEPLOYMENT_MODE})"
HEALTH_ERR=$(mktemp)
PROBLEMS=$(health_problems)
if [ "$PROBLEMS" = "healthy" ]; then
    output "✅ ClusterVersion Available, not Progressing or Failing"
    output "✅ All nodes Ready"
    output "✅ All cluster operators Available, none Degraded"
    output "✅ All MachineConfigPools updated"
    output "✅ Hub Quay healthy"
    [ "$DEPLOYMENT_MODE" = "disconnected" ] && output "✅ Landing Zone mirror registry healthy"
else
    output "❌ Cluster not healthy:"
    output "\`\`\`"
    SSH_ERR=$(tail -5 "$HEALTH_ERR" | tr -s '[:space:]' ' ' | sed 's/ $//')
    output "${PROBLEMS:-no answer from the Landing Zone${SSH_ERR:+: $SSH_ERR}}"
    output "\`\`\`"
    VERIFICATION_FAILED=1
fi
rm -f "$HEALTH_ERR"

# Snapshot tables for the summary (informational; health was checked above).
for entry in "nodes:Cluster Nodes" "co:Cluster Operators" "mcp:Machine Config Pools"; do
    res="${entry%%:*}"
    output ""
    output "### ${entry#*:}"
    SNAP_FILE=$(mktemp)
    if ssh $SSH_OPTS cloud-user@$LZ_IP "${LZ_OC_ENV} && oc get ${res}" > "$SNAP_FILE" 2>&1; then
        if [ "$USE_GITHUB" = true ]; then
            cat "$SNAP_FILE" >> "$GITHUB_STEP_SUMMARY"
        fi
        cat "$SNAP_FILE"
    else
        output "⚠️ Could not run 'oc get ${res}'"
        cat "$SNAP_FILE" >&2
    fi
    rm -f "$SNAP_FILE"
done

# Get cluster version
output ""
output "### Cluster Version"
CLUSTERVERSION_FILE=$(mktemp)
if ! ssh $SSH_OPTS cloud-user@$LZ_IP "${LZ_OC_ENV} && oc get clusterversion" > "$CLUSTERVERSION_FILE" 2>&1; then
    output "⚠️ Could not get cluster version"
else
    if [ "$USE_GITHUB" = true ]; then
        cat "$CLUSTERVERSION_FILE" >> "$GITHUB_STEP_SUMMARY"
    fi
    cat "$CLUSTERVERSION_FILE"
fi
rm -f "$CLUSTERVERSION_FILE"

output ""
if [ $VERIFICATION_FAILED -eq 0 ]; then
    output "✅ Cluster verification complete"
    echo -e "${GREEN}✅ Cluster verification complete${NC}"
    exit 0
else
    output "❌ Cluster verification failed"
    echo -e "${RED}❌ Cluster verification failed${NC}"
    exit 1
fi
