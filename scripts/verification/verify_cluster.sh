#!/bin/bash
# Verify OpenShift cluster deployment
#
# This script verifies that the OpenShift cluster is successfully deployed
# and all components are healthy. Supports both local and GitHub Actions execution.
#
# Usage:
#   ./verify_cluster.sh
#
# Environment Variables:
#   ENCLAVE_CLUSTER_NAME - Cluster name (required)
#   WORKING_DIR - Working directory (required)
#   GITHUB_STEP_SUMMARY - GitHub Actions summary file (optional)

set -euo pipefail

# Colors for terminal output
GREEN='\033[0;32m'
RED='\033[0;31m'
YELLOW='\033[1;33m'
NC='\033[0m'

# Required variables
: "${ENCLAVE_CLUSTER_NAME:?ENCLAVE_CLUSTER_NAME must be set}"

# Auto-construct WORKING_DIR if not set
if [ -z "${WORKING_DIR:-}" ]; then
    if [ -n "${BASE_WORKING_DIR:-}" ] && [ -n "${ENCLAVE_CLUSTER_NAME}" ]; then
        WORKING_DIR="${BASE_WORKING_DIR}/clusters/${ENCLAVE_CLUSTER_NAME}"
    else
        echo "ERROR: WORKING_DIR not set and cannot construct from BASE_WORKING_DIR + ENCLAVE_CLUSTER_NAME" >&2
        exit 1
    fi
fi

# Detect GitHub Actions environment
if [ -n "${GITHUB_STEP_SUMMARY:-}" ]; then
    USE_GITHUB=true
else
    USE_GITHUB=false
fi

# Output helper that works both locally and in CI
output() {
    local msg="$1"
    echo -e "$msg"
    if [ "$USE_GITHUB" = true ]; then
        echo "$msg" >> "$GITHUB_STEP_SUMMARY"
    fi
}

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
    echo "Environment file: ${WORKING_DIR}/environment-${ENCLAVE_CLUSTER_NAME}.json" >&2
    cat "${WORKING_DIR}/environment-${ENCLAVE_CLUSTER_NAME}.json" 2>/dev/null || echo "Environment file not found" >&2
    echo "" >&2
    echo "Trying virsh domifaddr:" >&2
    sudo virsh domifaddr "${ENCLAVE_CLUSTER_NAME}_landingzone_0" 2>&1 || true
    exit 1
fi
output "✅ Landing Zone IP: $LZ_IP"

SSH_OPTS="-o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null -o ConnectTimeout=10"

# Check if kubeconfig exists
if ! ssh $SSH_OPTS cloud-user@$LZ_IP "test -f /home/cloud-user/ocp-cluster/auth/kubeconfig" 2>&1; then
    output "❌ Kubeconfig not found on Landing Zone"
    echo -e "${RED}ERROR:${NC} Kubeconfig not found at /home/cloud-user/ocp-cluster/auth/kubeconfig" >&2
    ssh $SSH_OPTS cloud-user@$LZ_IP "ls -la /home/cloud-user/ocp-cluster/auth/ 2>&1 || echo 'ocp-cluster/auth directory not found'"
    exit 1
fi
output "✅ Kubeconfig found"

# Verify nodes are ready
output ""
output "### Cluster Nodes"
NODES_FILE=$(mktemp)
if ! ssh $SSH_OPTS cloud-user@$LZ_IP "export KUBECONFIG=/home/cloud-user/ocp-cluster/auth/kubeconfig && oc get nodes" 2>&1 | tee "$NODES_FILE"; then
    output "❌ Failed to get cluster nodes"
    echo -e "${RED}ERROR:${NC} Failed to run 'oc get nodes'" >&2
    echo "Command output:" >&2
    cat "$NODES_FILE" >&2
    echo "" >&2
    echo "Checking oc binary and PATH:" >&2
    ssh $SSH_OPTS cloud-user@$LZ_IP "which oc && oc version || echo 'oc command not found or failed'"
    rm -f "$NODES_FILE"
    exit 1
fi

# Output to summary
if [ "$USE_GITHUB" = true ]; then
    cat "$NODES_FILE" >> "$GITHUB_STEP_SUMMARY"
fi
rm -f "$NODES_FILE"

# Count ready nodes
READY_NODES=$(ssh $SSH_OPTS cloud-user@$LZ_IP "export KUBECONFIG=/home/cloud-user/ocp-cluster/auth/kubeconfig && oc get nodes --no-headers 2>/dev/null | grep -c Ready" || echo "0")
output ""
output "✅ Ready nodes: $READY_NODES"

# Verify cluster operators
output ""
output "### Cluster Operators"
OPERATORS_FILE=$(mktemp)
if ! ssh $SSH_OPTS cloud-user@$LZ_IP "export KUBECONFIG=/home/cloud-user/ocp-cluster/auth/kubeconfig && oc get co" 2>&1 | tee "$OPERATORS_FILE"; then
    output "❌ Failed to get cluster operators"
    echo -e "${RED}ERROR:${NC} Failed to run 'oc get co'" >&2
    echo "Command output:" >&2
    cat "$OPERATORS_FILE" >&2
    rm -f "$OPERATORS_FILE"
    exit 1
fi

# Output to summary
if [ "$USE_GITHUB" = true ]; then
    cat "$OPERATORS_FILE" >> "$GITHUB_STEP_SUMMARY"
fi
rm -f "$OPERATORS_FILE"

# Check for degraded operators
output ""
DEGRADED=$(ssh $SSH_OPTS cloud-user@$LZ_IP "export KUBECONFIG=/home/cloud-user/ocp-cluster/auth/kubeconfig && oc get co -o json 2>/dev/null | jq -r '.items[] | select(.status.conditions[] | select(.type==\"Degraded\" and .status==\"True\")) | .metadata.name'" || echo "")

if [ -n "$DEGRADED" ]; then
    output "⚠️ Warning: Some cluster operators are degraded:"
    output "\`\`\`"
    output "$DEGRADED"
    output "\`\`\`"
    VERIFICATION_FAILED=1
else
    output "✅ All cluster operators are healthy"
fi

# Get cluster version
output ""
output "### Cluster Version"
CLUSTERVERSION_FILE=$(mktemp)
if ! ssh $SSH_OPTS cloud-user@$LZ_IP "export KUBECONFIG=/home/cloud-user/ocp-cluster/auth/kubeconfig && oc get clusterversion" > "$CLUSTERVERSION_FILE" 2>&1; then
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
    output "⚠️ Cluster verification complete with warnings"
    echo -e "${YELLOW}⚠️ Cluster verification complete with warnings${NC}"
    exit 1
fi
