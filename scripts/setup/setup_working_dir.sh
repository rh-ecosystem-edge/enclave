#!/bin/bash
# Setup cluster-specific working directory
#
# Creates a unique working directory for the cluster to enable parallel execution.
# Outputs the working directory path for GitHub Actions to consume.
#
# Usage:
#   ./setup_working_dir.sh
#
# Environment Variables:
#   BASE_WORKING_DIR - Base directory for all clusters (required)
#   ENCLAVE_CLUSTER_NAME - Cluster name (required)
#   GITHUB_ENV - GitHub Actions environment file (optional)

set -euo pipefail

# Required variables
: "${BASE_WORKING_DIR:?BASE_WORKING_DIR must be set}"

# Generate cluster name if not already set
if [ -z "${ENCLAVE_CLUSTER_NAME:-}" ]; then
    SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
    echo "No cluster name provided, generating one..."
    "${SCRIPT_DIR}/generate_cluster_name.sh"
    # Source the generated environment
    if [ -f /tmp/cluster_name.env ]; then
        source /tmp/cluster_name.env
    fi
fi

# Verify cluster name is now set
: "${ENCLAVE_CLUSTER_NAME:?ENCLAVE_CLUSTER_NAME must be set}"

# Create cluster-specific working directory
CLUSTER_WORKING_DIR="${BASE_WORKING_DIR}/clusters/${ENCLAVE_CLUSTER_NAME}"

echo "Creating cluster-specific working directory: ${CLUSTER_WORKING_DIR}"
mkdir -p "$CLUSTER_WORKING_DIR"

# Always write to temp file for workflow steps to read
echo "${CLUSTER_WORKING_DIR}" > /tmp/working_dir

# Export to GitHub Actions environment if available
if [ -n "${GITHUB_ENV:-}" ]; then
    echo "WORKING_DIR=${CLUSTER_WORKING_DIR}" >> "$GITHUB_ENV"
    echo "✅ Working directory exported to GitHub Actions: ${CLUSTER_WORKING_DIR}"
else
    # For local execution, export to current shell
    export WORKING_DIR="${CLUSTER_WORKING_DIR}"
    echo "✅ Working directory set: ${CLUSTER_WORKING_DIR}"
    echo "   (exported as WORKING_DIR environment variable)"
fi

echo "Cluster working directory: ${CLUSTER_WORKING_DIR}"
