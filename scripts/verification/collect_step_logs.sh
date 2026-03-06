#!/bin/bash
# Collect step logs from dev-scripts and cluster directories
#
# This script collects logs from various sources into a structured directory
# for artifact collection. Supports both local and GitHub Actions execution.
#
# Usage:
#   ./collect_step_logs.sh [output-directory]
#
# Arguments:
#   output-directory - Directory to collect logs into (default: step-logs)
#
# Environment Variables:
#   DEV_SCRIPTS_PATH - Path to dev-scripts installation (required)
#   WORKING_DIR - Cluster working directory (required)

set -euo pipefail

# Colors for terminal output
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m'

# Required variables
: "${DEV_SCRIPTS_PATH:?DEV_SCRIPTS_PATH must be set}"

# Auto-construct WORKING_DIR if not set
if [ -z "${WORKING_DIR:-}" ]; then
    ENCLAVE_CLUSTER_NAME="${ENCLAVE_CLUSTER_NAME:-enclave-test}"
    if [ -n "${BASE_WORKING_DIR:-}" ] && [ -n "${ENCLAVE_CLUSTER_NAME}" ]; then
        WORKING_DIR="${BASE_WORKING_DIR}/clusters/${ENCLAVE_CLUSTER_NAME}"
    else
        echo "ERROR: WORKING_DIR not set and cannot construct from BASE_WORKING_DIR + ENCLAVE_CLUSTER_NAME"
        exit 1
    fi
fi

# Optional variables
OUTPUT_DIR="${1:-step-logs}"

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

echo -e "${GREEN}Collecting step logs...${NC}"

# Create output directory if it doesn't exist
mkdir -p "$OUTPUT_DIR"

# Collect dev-scripts logs if they exist
if [ -d "${DEV_SCRIPTS_PATH}/logs" ]; then
    echo "Collecting dev-scripts logs..."
    mkdir -p "${OUTPUT_DIR}/dev-scripts"
    if cp -r "${DEV_SCRIPTS_PATH}/logs"/* "${OUTPUT_DIR}/dev-scripts/" 2>/dev/null; then
        echo -e "${GREEN}✅ Collected dev-scripts logs${NC}"
        output "✅ Collected dev-scripts logs"
    else
        echo -e "${YELLOW}⚠️  dev-scripts logs directory exists but is empty${NC}"
    fi
else
    echo "No dev-scripts logs found at ${DEV_SCRIPTS_PATH}/logs"
fi

# Collect cluster-specific logs if they exist
if [ -d "${WORKING_DIR}/logs" ]; then
    echo "Collecting cluster-specific logs..."
    mkdir -p "${OUTPUT_DIR}/cluster-logs"
    if cp -r "${WORKING_DIR}/logs"/* "${OUTPUT_DIR}/cluster-logs/" 2>/dev/null; then
        echo -e "${GREEN}✅ Collected cluster logs${NC}"
        output "✅ Collected cluster logs"
    else
        echo -e "${YELLOW}⚠️  cluster logs directory exists but is empty${NC}"
    fi
else
    echo "No cluster logs found at ${WORKING_DIR}/logs"
fi

# List what we collected
echo ""
echo "Step logs collected in: ${OUTPUT_DIR}"
if [ -d "$OUTPUT_DIR" ]; then
    echo "Contents:"
    ls -lah "$OUTPUT_DIR" || echo "Directory is empty"
fi

echo ""
echo -e "${GREEN}✅ Log collection complete${NC}"
