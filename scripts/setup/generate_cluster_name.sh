#!/bin/bash
# Generate unique cluster name for local or CI execution
#
# This script generates a unique cluster name using either hash or date-based
# strategies. If ENCLAVE_CLUSTER_NAME is already set, it preserves it.
#
# Usage:
#   ./generate_cluster_name.sh [OPTIONS]
#
# Options:
#   --strategy STRATEGY  - Naming strategy: "hash" or "date" (default: hash)
#   --prefix PREFIX      - Cluster name prefix (default: eci)
#
# Environment Variables:
#   ENCLAVE_CLUSTER_NAME - If set, this script does nothing (preserves existing name)
#   GITHUB_ENV - GitHub Actions environment file (optional)
#
# Outputs:
#   Sets ENCLAVE_CLUSTER_NAME and exports it
#   Writes to GITHUB_ENV if in GitHub Actions
#   Writes to /tmp/cluster_name for local use

set -euo pipefail

# Parse command-line arguments
STRATEGY="hash"
PREFIX="eci"

while [[ $# -gt 0 ]]; do
    case $1 in
        --strategy)
            STRATEGY="$2"
            shift 2
            ;;
        --prefix)
            PREFIX="$2"
            shift 2
            ;;
        *)
            echo "Unknown option: $1"
            echo "Usage: $0 [--strategy hash|date] [--prefix PREFIX]"
            exit 1
            ;;
    esac
done

# If cluster name is already set, preserve it
if [ -n "${ENCLAVE_CLUSTER_NAME:-}" ]; then
    echo "Using existing cluster name: ${ENCLAVE_CLUSTER_NAME}"
    exit 0
fi

# Generate cluster name based on strategy
if [ "$STRATEGY" = "hash" ]; then
    # Hash-based naming for local/CI runs
    # For local: use timestamp + PID for uniqueness
    # For GitHub Actions: use run_id if available
    if [ -n "${GITHUB_RUN_ID:-}" ]; then
        # GitHub Actions - use run ID
        SHORT_ID=$(echo "${GITHUB_RUN_ID}" | sha256sum | cut -c1-8)
    else
        # Local execution - use timestamp + PID
        UNIQUE_STRING="$(date +%s)-$$"
        SHORT_ID=$(echo "${UNIQUE_STRING}" | sha256sum | cut -c1-8)
    fi
    CLUSTER_NAME="${PREFIX}-${SHORT_ID}"
    echo "Generated cluster name (hash): ${CLUSTER_NAME}"
elif [ "$STRATEGY" = "date" ]; then
    # Date-based naming for nightly runs
    DATE_STAMP=$(date +%Y%m%d)
    CLUSTER_NAME="${PREFIX}-${DATE_STAMP}"
    echo "Generated cluster name (date): ${CLUSTER_NAME}"
else
    echo "ERROR: Invalid naming-strategy: ${STRATEGY}"
    echo "Valid strategies: hash, date"
    exit 1
fi

# Export to environment
export ENCLAVE_CLUSTER_NAME="${CLUSTER_NAME}"

# Write to GitHub Actions environment if available
if [ -n "${GITHUB_ENV:-}" ]; then
    echo "ENCLAVE_CLUSTER_NAME=${CLUSTER_NAME}" >> "$GITHUB_ENV"
    echo "✅ Cluster name exported to GitHub Actions: ${CLUSTER_NAME}"
fi

# Write to temp file for local execution (can be sourced by other scripts)
echo "${CLUSTER_NAME}" > /tmp/cluster_name
echo "export ENCLAVE_CLUSTER_NAME='${CLUSTER_NAME}'" > /tmp/cluster_name.env

echo "✅ Cluster name set: ${CLUSTER_NAME}"
echo "   (exported as ENCLAVE_CLUSTER_NAME environment variable)"
