#!/bin/bash
# Shared configuration utilities
#
# Provides functions for loading dev-scripts configuration files and
# parsing environment.json files.
#
# Usage:
#   source "${ENCLAVE_DIR}/scripts/lib/config.sh"
#   load_devscripts_config
#   value=$(get_env_json_value "networks.cluster.cidr")
#
# Functions:
#   load_devscripts_config [CLUSTER_NAME]  - Load dev-scripts config file (required)
#   try_load_devscripts_config [CLUSTER_NAME] - Load dev-scripts config (optional, no error)
#   get_env_json_value PATH [ENV_FILE]     - Extract value from environment.json using jq

# Load dev-scripts configuration file for a cluster
# Args: $1 = Cluster name (optional, defaults to ENCLAVE_CLUSTER_NAME or "enclave-test")
# Exits with error if config file not found
# Sets: All variables from the config file
load_devscripts_config() {
    local cluster_name="${1:-${ENCLAVE_CLUSTER_NAME:-enclave-test}}"

    if [ -z "${DEV_SCRIPTS_PATH:-}" ]; then
        echo "ERROR: DEV_SCRIPTS_PATH environment variable is not set" >&2
        exit 1
    fi

    local config_file="${DEV_SCRIPTS_PATH}/config_${cluster_name}.sh"

    if [ ! -f "$config_file" ]; then
        echo "ERROR: dev-scripts configuration not found: $config_file" >&2
        echo "ERROR: Expected config file for cluster: $cluster_name" >&2
        exit 1
    fi

    # shellcheck source=/dev/null
    source "$config_file"
}

# Try to load dev-scripts configuration file (non-fatal)
# Args: $1 = Cluster name (optional, defaults to ENCLAVE_CLUSTER_NAME or "enclave-test")
# Returns: 0 if loaded successfully, 1 if not found
# Sets: All variables from the config file (if found)
try_load_devscripts_config() {
    local cluster_name="${1:-${ENCLAVE_CLUSTER_NAME:-enclave-test}}"

    if [ -z "${DEV_SCRIPTS_PATH:-}" ]; then
        return 1
    fi

    local config_file="${DEV_SCRIPTS_PATH}/config_${cluster_name}.sh"

    if [ ! -f "$config_file" ]; then
        return 1
    fi

    # shellcheck source=/dev/null
    source "$config_file"
    return 0
}

# Extract a value from environment.json using jq
# Args: $1 = JSON path (e.g., "networks.cluster.cidr")
#       $2 = Environment file path (optional, auto-constructed if not provided)
# Returns: Extracted value or empty string if not found
# Example: CLUSTER_CIDR=$(get_env_json_value "networks.cluster.cidr")
get_env_json_value() {
    local json_path="$1"
    local env_file="${2:-}"

    # Auto-construct environment file path if not provided
    if [ -z "$env_file" ]; then
        local cluster_name="${ENCLAVE_CLUSTER_NAME:-enclave-test}"

        # Try to construct from WORKING_DIR
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
