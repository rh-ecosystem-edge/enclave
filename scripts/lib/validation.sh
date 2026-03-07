#!/bin/bash
# Shared validation utilities
#
# Provides functions for validating environment variables, checking prerequisites,
# and verifying system requirements.
#
# Usage:
#   source "${ENCLAVE_DIR}/scripts/lib/validation.sh"
#   require_env_var "DEV_SCRIPTS_PATH"
#   require_env_vars "DEV_SCRIPTS_PATH" "WORKING_DIR" "ENCLAVE_CLUSTER_NAME"
#   require_command "jq"
#   require_file "/path/to/required/file"
#
# Functions:
#   require_env_var VAR_NAME [ERROR_MSG]      - Require environment variable to be set
#   require_env_vars VAR1 [VAR2...]           - Require multiple environment variables
#   require_command COMMAND [ERROR_MSG]       - Require command to be available in PATH
#   require_file FILE_PATH [ERROR_MSG]        - Require file to exist
#   require_dir DIR_PATH [ERROR_MSG]          - Require directory to exist
#   validate_ip IP_ADDRESS                    - Validate IP address format

# Require an environment variable to be set
# Args: $1 = Variable name
#       $2 = Custom error message (optional)
# Exits with error if variable is not set or empty
# Example: require_env_var "DEV_SCRIPTS_PATH"
require_env_var() {
    local var_name="$1"
    local error_msg="${2:-${var_name} environment variable is not set}"

    # Use indirect variable expansion to check if variable is set
    if [ -z "${!var_name:-}" ]; then
        echo "ERROR: $error_msg" >&2
        exit 1
    fi
}

# Require multiple environment variables to be set
# Args: $@ = Variable names
# Exits with error if any variable is not set or empty
# Example: require_env_vars "DEV_SCRIPTS_PATH" "WORKING_DIR" "CLUSTER_NAME"
require_env_vars() {
    local failed=0

    for var_name in "$@"; do
        if [ -z "${!var_name:-}" ]; then
            echo "ERROR: ${var_name} environment variable is not set" >&2
            failed=1
        fi
    done

    if [ $failed -eq 1 ]; then
        exit 1
    fi
}

# Require a command to be available in PATH
# Args: $1 = Command name
#       $2 = Custom error message (optional)
# Exits with error if command is not found
# Example: require_command "jq" "jq is required but not installed"
require_command() {
    local command_name="$1"
    local error_msg="${2:-Command '${command_name}' is required but not found in PATH}"

    if ! command -v "$command_name" &>/dev/null; then
        echo "ERROR: $error_msg" >&2
        exit 1
    fi
}

# Require a file to exist
# Args: $1 = File path
#       $2 = Custom error message (optional)
# Exits with error if file does not exist
# Example: require_file "/path/to/config.yaml"
require_file() {
    local file_path="$1"
    local error_msg="${2:-Required file not found: ${file_path}}"

    if [ ! -f "$file_path" ]; then
        echo "ERROR: $error_msg" >&2
        exit 1
    fi
}

# Require a directory to exist
# Args: $1 = Directory path
#       $2 = Custom error message (optional)
# Exits with error if directory does not exist
# Example: require_dir "/opt/dev-scripts"
require_dir() {
    local dir_path="$1"
    local error_msg="${2:-Required directory not found: ${dir_path}}"

    if [ ! -d "$dir_path" ]; then
        echo "ERROR: $error_msg" >&2
        exit 1
    fi
}

# Validate IP address format
# Args: $1 = IP address string
# Returns: 0 if valid IPv4 address, 1 otherwise
# Example: if validate_ip "192.168.1.1"; then echo "Valid"; fi
validate_ip() {
    local ip="$1"

    # Simple IPv4 validation regex
    if [[ $ip =~ ^[0-9]{1,3}\.[0-9]{1,3}\.[0-9]{1,3}\.[0-9]{1,3}$ ]]; then
        # Check each octet is <= 255
        local IFS='.'
        # shellcheck disable=SC2206  # We want word splitting here
        local -a octets=($ip)
        for octet in "${octets[@]}"; do
            if [ "$octet" -gt 255 ]; then
                return 1
            fi
        done
        return 0
    fi
    return 1
}
