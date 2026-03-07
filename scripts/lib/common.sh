#!/bin/bash
# Shared common utilities
#
# Provides common helper functions used across multiple scripts for
# directory detection, working directory construction, and script path resolution.
#
# Usage:
#   source "${ENCLAVE_DIR}/scripts/lib/common.sh"
#   detect_script_dir
#   detect_enclave_dir
#   ensure_working_dir
#
# Functions:
#   detect_script_dir                 - Set SCRIPT_DIR to the directory containing the calling script
#   detect_enclave_dir                - Set ENCLAVE_DIR to repository root
#   ensure_working_dir                - Ensure WORKING_DIR is set (auto-construct if needed)
#   get_cluster_name                  - Get cluster name (ENCLAVE_CLUSTER_NAME with fallback)

# Detect the directory containing the calling script
# Sets: SCRIPT_DIR
# Example: detect_script_dir
detect_script_dir() {
    SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[1]}")" &>/dev/null && pwd)"
    export SCRIPT_DIR
}

# Detect the Enclave repository root directory
# Assumes: Script is in a subdirectory of the repository
# Sets: ENCLAVE_DIR
# Example: detect_enclave_dir
detect_enclave_dir() {
    # Get the script directory first
    local script_dir
    script_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[1]}")" &>/dev/null && pwd)"

    # Navigate up to repository root (scripts are in scripts/*)
    ENCLAVE_DIR="$(cd -- "${script_dir}/../.." &>/dev/null && pwd)"
    export ENCLAVE_DIR
}

# Ensure WORKING_DIR is set
# Auto-constructs from BASE_WORKING_DIR and ENCLAVE_CLUSTER_NAME if not set
# Exits with error if WORKING_DIR cannot be determined
# Sets: WORKING_DIR
# Example: ensure_working_dir
ensure_working_dir() {
    if [ -n "${WORKING_DIR:-}" ]; then
        # Already set, nothing to do
        return 0
    fi

    # Try to auto-construct
    if [ -n "${BASE_WORKING_DIR:-}" ] && [ -n "${ENCLAVE_CLUSTER_NAME:-}" ]; then
        WORKING_DIR="${BASE_WORKING_DIR}/clusters/${ENCLAVE_CLUSTER_NAME}"
        export WORKING_DIR
        return 0
    fi

    # Cannot determine WORKING_DIR
    echo "ERROR: WORKING_DIR not set and cannot construct from BASE_WORKING_DIR + ENCLAVE_CLUSTER_NAME" >&2
    exit 1
}

# Get cluster name with fallback to default
# Returns: Cluster name (ENCLAVE_CLUSTER_NAME or "enclave-test")
# Example: CLUSTER_NAME=$(get_cluster_name)
get_cluster_name() {
    echo "${ENCLAVE_CLUSTER_NAME:-enclave-test}"
}

# Get environment.json path for the current cluster
# Args: $1 = Cluster name (optional, uses ENCLAVE_CLUSTER_NAME if not provided)
# Returns: Path to environment.json
# Example: ENV_FILE=$(get_environment_json_path)
get_environment_json_path() {
    local cluster_name="${1:-${ENCLAVE_CLUSTER_NAME:-enclave-test}}"

    # Ensure WORKING_DIR is set
    ensure_working_dir

    echo "${WORKING_DIR}/environment-${cluster_name}.json"
}
