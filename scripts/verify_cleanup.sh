#!/bin/bash
# Verify infrastructure cleanup
#
# This script checks for leftover resources after cleanup and reports warnings
# if any infrastructure components remain. Supports both local and GitHub Actions execution.
#
# Usage:
#   ./verify_cleanup.sh
#
# Environment Variables:
#   ENCLAVE_CLUSTER_NAME - Cluster name (required)
#   WORKING_DIR - Working directory (optional, defaults to /opt/dev-scripts)
#   GITHUB_STEP_SUMMARY - GitHub Actions summary file (optional)

set -euo pipefail

# Colors for terminal output
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m'

# Required variables
: "${ENCLAVE_CLUSTER_NAME:?ENCLAVE_CLUSTER_NAME must be set}"

# Auto-construct WORKING_DIR if not set
if [ -z "${WORKING_DIR:-}" ]; then
    if [ -n "${BASE_WORKING_DIR:-}" ] && [ -n "${ENCLAVE_CLUSTER_NAME}" ]; then
        WORKING_DIR="${BASE_WORKING_DIR}/clusters/${ENCLAVE_CLUSTER_NAME}"
    else
        # Fallback to default for backward compatibility
        WORKING_DIR="/opt/dev-scripts"
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

# Track warnings
WARNING_COUNT=0

output "## Verifying Infrastructure Cleanup"
output ""

# Check for leftover VMs
output "### Checking for Leftover VMs"
LEFTOVER_VMS=$(sudo virsh list --all --name | grep -E "${ENCLAVE_CLUSTER_NAME}" || echo "")

if [ -n "$LEFTOVER_VMS" ]; then
    WARNING_COUNT=$((WARNING_COUNT + 1))
    output "⚠️ Warning: Found leftover VMs:"
    output "\`\`\`"
    output "$LEFTOVER_VMS"
    output "\`\`\`"
    echo -e "${YELLOW}⚠️ Found leftover VMs:${NC}" >&2
    echo "$LEFTOVER_VMS" >&2
else
    output "✅ No leftover VMs found"
fi

# Check for leftover networks
output ""
output "### Checking for Leftover Networks"
LEFTOVER_NETWORKS=$(sudo virsh net-list --all --name | grep -E "${ENCLAVE_CLUSTER_NAME}" || echo "")

if [ -n "$LEFTOVER_NETWORKS" ]; then
    WARNING_COUNT=$((WARNING_COUNT + 1))
    output "⚠️ Warning: Found leftover networks:"
    output "\`\`\`"
    output "$LEFTOVER_NETWORKS"
    output "\`\`\`"
    echo -e "${YELLOW}⚠️ Found leftover networks:${NC}" >&2
    echo "$LEFTOVER_NETWORKS" >&2
else
    output "✅ No leftover networks found"
fi

# Check for leftover environment files
output ""
output "### Checking for Leftover Files"
LEFTOVER_FILES=""

# Check for environment file
if [ -f "${WORKING_DIR}/environment-${ENCLAVE_CLUSTER_NAME}.json" ]; then
    LEFTOVER_FILES="${LEFTOVER_FILES}${WORKING_DIR}/environment-${ENCLAVE_CLUSTER_NAME}.json\n"
fi

# Check for cluster-specific working directory
if [ -d "${WORKING_DIR}/clusters/${ENCLAVE_CLUSTER_NAME}" ]; then
    LEFTOVER_FILES="${LEFTOVER_FILES}${WORKING_DIR}/clusters/${ENCLAVE_CLUSTER_NAME}/\n"
fi

if [ -n "$LEFTOVER_FILES" ]; then
    WARNING_COUNT=$((WARNING_COUNT + 1))
    output "⚠️ Warning: Found leftover files/directories:"
    output "\`\`\`"
    output "$LEFTOVER_FILES"
    output "\`\`\`"
    echo -e "${YELLOW}⚠️ Found leftover files/directories:${NC}" >&2
    echo -e "$LEFTOVER_FILES" >&2
else
    output "✅ No leftover files found"
fi

# Check for storage pools
output ""
output "### Checking for Leftover Storage Pools"
LEFTOVER_POOLS=$(sudo virsh pool-list --all --name | grep -E "${ENCLAVE_CLUSTER_NAME}" || echo "")

if [ -n "$LEFTOVER_POOLS" ]; then
    WARNING_COUNT=$((WARNING_COUNT + 1))
    output "⚠️ Warning: Found leftover storage pools:"
    output "\`\`\`"
    output "$LEFTOVER_POOLS"
    output "\`\`\`"
    echo -e "${YELLOW}⚠️ Found leftover storage pools:${NC}" >&2
    echo "$LEFTOVER_POOLS" >&2
else
    output "✅ No leftover storage pools found"
fi

# Summary
output ""
if [ $WARNING_COUNT -eq 0 ]; then
    output "✅ Cleanup verification complete - no leftover resources found"
    echo -e "${GREEN}✅ Cleanup verification complete${NC}"
    exit 0
else
    output "⚠️ Cleanup verification complete with $WARNING_COUNT warning(s)"
    output ""
    output "**Note**: Some resources may remain due to cleanup timing or errors."
    output "You may need to manually clean up remaining resources."
    echo -e "${YELLOW}⚠️ Cleanup verification found $WARNING_COUNT warning(s)${NC}"
    exit 0  # Exit 0 as warnings are not fatal
fi
