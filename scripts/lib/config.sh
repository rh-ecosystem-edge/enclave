#!/bin/bash
# Shared configuration utilities
#
# Provides functions for loading cluster environment files and
# parsing environment.json files.
#
# Usage:
#   source "${ENCLAVE_DIR}/scripts/lib/config.sh"
#   load_cluster_env
#   value=$(get_env_json_value "networks.cluster.cidr")
#
# Functions:
#   load_cluster_env [CLUSTER_NAME]     - Load cluster-env.sh (required)
#   try_load_cluster_env [CLUSTER_NAME] - Load cluster-env.sh (optional, no error)
#   is_enclave_disconnected             - True if ENCLAVE_DEPLOYMENT_MODE=disconnected
#   get_env_json_value PATH [ENV_FILE]  - Extract value from environment.json using jq

# Load cluster-env.sh for a cluster
# Args: $1 = Cluster name (optional, defaults to ENCLAVE_CLUSTER_NAME or "enclave-test")
# Exits with error if file not found
load_cluster_env() {
    local cluster_name="${1:-${ENCLAVE_CLUSTER_NAME:-enclave-test}}"

    local working_dir="${WORKING_DIR:-}"
    if [ -z "$working_dir" ]; then
        if [ -n "${BASE_WORKING_DIR:-}" ]; then
            working_dir="${BASE_WORKING_DIR}/clusters/${cluster_name}"
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
    _set_compat_vars
}

# Backward-compat alias
load_devscripts_config() { load_cluster_env "$@"; }

# Try to load cluster-env.sh (non-fatal)
# Returns: 0 if loaded successfully, 1 if not found
try_load_cluster_env() {
    local cluster_name="${1:-${ENCLAVE_CLUSTER_NAME:-enclave-test}}"

    local working_dir="${WORKING_DIR:-}"
    if [ -z "$working_dir" ]; then
        [ -n "${BASE_WORKING_DIR:-}" ] || return 1
        working_dir="${BASE_WORKING_DIR}/clusters/${cluster_name}"
    fi

    local env_file="${working_dir}/cluster-env.sh"
    [ -f "$env_file" ] || return 1

    # shellcheck source=/dev/null
    source "$env_file"
    _set_compat_vars
    return 0
}

# Backward-compat alias
try_load_devscripts_config() { try_load_cluster_env "$@"; }

# Export compat variable aliases so scripts that reference old dev-scripts
# variable names continue to work without changes.
_set_compat_vars() {
    CLUSTER_NAME="${ENCLAVE_CLUSTER_NAME}"
    PROVISIONING_NETWORK="${ENCLAVE_BMC_NETWORK}"
    PROVISIONING_NETWORK_NAME="${ENCLAVE_BMC_BRIDGE}"
    EXTERNAL_SUBNET_V4="${ENCLAVE_CLUSTER_NETWORK}"
    BAREMETAL_NETWORK_NAME="${ENCLAVE_CLUSTER_BRIDGE}"
    BASE_DOMAIN="${BASE_DOMAIN:-${ENCLAVE_BASE_DOMAIN:-${ENCLAVE_CLUSTER_NAME}.lab}}"
    export CLUSTER_NAME PROVISIONING_NETWORK PROVISIONING_NETWORK_NAME
    export EXTERNAL_SUBNET_V4 BAREMETAL_NETWORK_NAME BASE_DOMAIN
}

# Return 0 if Enclave is running in disconnected mode.
is_enclave_disconnected() {
    local deployment_mode="${ENCLAVE_DEPLOYMENT_MODE:-}"
    [[ "${deployment_mode,,}" == "disconnected" ]]
}

# Extract a value from environment.json using jq
# Args: $1 = JSON path (e.g., "networks.cluster.cidr")
#       $2 = Environment file path (optional, auto-constructed if not provided)
get_env_json_value() {
    local json_path="$1"
    local env_file="${2:-}"

    if [ -z "$env_file" ]; then
        local cluster_name="${ENCLAVE_CLUSTER_NAME:-enclave-test}"

        if [ -n "${WORKING_DIR:-}" ]; then
            env_file="${WORKING_DIR}/environment-${cluster_name}.json"
        elif [ -n "${BASE_WORKING_DIR:-}" ]; then
            env_file="${BASE_WORKING_DIR}/clusters/${cluster_name}/environment-${cluster_name}.json"
        else
            echo "ERROR: Cannot determine environment.json path (WORKING_DIR not set)" >&2
            return 1
        fi
    fi

    if [ ! -f "$env_file" ]; then
        echo "ERROR: Environment file not found: $env_file" >&2
        return 1
    fi

    jq -r ".$json_path // empty" "$env_file" 2>/dev/null || true
}
