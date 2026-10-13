#!/bin/bash
# Shared configuration utilities
#
# Provides functions for loading the cluster-env.sh file written by vm_infra.py.
#
# Usage:
#   source "${ENCLAVE_DIR}/scripts/lib/config.sh"
#   load_cluster_env
#
# Functions:
#   load_cluster_env [CLUSTER_NAME]     - Load cluster-env.sh (required)
#   try_load_cluster_env [CLUSTER_NAME] - Load cluster-env.sh (optional, no error)
#   is_enclave_disconnected             - True if ENCLAVE_DEPLOYMENT_MODE=disconnected

# Load cluster-env.sh for a cluster
# Args: $1 = Cluster name (optional, defaults to ENCLAVE_CLUSTER_NAME or "enclave-test")
# Exits with error if file not found
load_cluster_env() {
    local cluster_name="${1:-${ENCLAVE_CLUSTER_NAME:-enclave-test}}"

    local working_dir="${WORKING_DIR:-}"
    if [ -z "$working_dir" ]; then
        if [ -n "${BASE_WORKING_DIR:-}" ]; then
            working_dir="${BASE_WORKING_DIR}/${cluster_name}"
        else
            echo "ERROR: WORKING_DIR not set" >&2
            exit 1
        fi
    fi

    local env_file="${working_dir}/cluster-env.sh"

    if [ ! -f "$env_file" ]; then
        echo "ERROR: cluster-env.sh not found: $env_file" >&2
        echo "ERROR: Run 'make -f Makefile.ci environment' first" >&2
        exit 1
    fi

    # shellcheck source=/dev/null
    source "$env_file"
}

# Try to load cluster-env.sh (non-fatal)
# Returns: 0 if loaded successfully, 1 if not found
try_load_cluster_env() {
    local cluster_name="${1:-${ENCLAVE_CLUSTER_NAME:-enclave-test}}"

    local working_dir="${WORKING_DIR:-}"
    if [ -z "$working_dir" ]; then
        [ -n "${BASE_WORKING_DIR:-}" ] || return 1
        working_dir="${BASE_WORKING_DIR}/${cluster_name}"
    fi

    local env_file="${working_dir}/cluster-env.sh"
    [ -f "$env_file" ] || return 1

    # shellcheck source=/dev/null
    source "$env_file"
    return 0
}

# Return 0 if Enclave is running in disconnected mode.
is_enclave_disconnected() {
    local deployment_mode="${ENCLAVE_DEPLOYMENT_MODE:-}"
    [[ "${deployment_mode,,}" == "disconnected" ]]
}
