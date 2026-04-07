#!/bin/bash
# Shared SSH utilities
#
# Provides SSH configuration and helper functions for connecting to
# Landing Zone VMs and executing remote commands.
#
# Usage:
#   source "${ENCLAVE_DIR}/scripts/lib/ssh.sh"
#   setup_ssh_config "192.168.1.10"
#   ssh_exec "ls -la"
#   ssh_test_connection || exit 1
#
# Functions:
#   setup_ssh_config IP_ADDRESS       - Set SSH variables for Landing Zone (LZ_SSH, SSH_OPTS, etc.)
#   resolve_lz_home                   - Resolve LZ_HOME via SSH (call after ssh_test_connection)
#   ssh_exec COMMAND                  - Execute command on Landing Zone
#   ssh_test_connection               - Test SSH connectivity to Landing Zone
#   ssh_file_exists REMOTE_PATH       - Check if file exists on Landing Zone

# Standard SSH options for Landing Zone connections
SSH_OPTS="-o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null -o ConnectTimeout=5 -q"

# Landing Zone user (default)
LZ_USER="cloud-user"

# Setup SSH configuration for Landing Zone connection
# Args: $1 = Landing Zone IP address
# Sets: LZ_SSH, LZ_HOME, LZ_ENCLAVE_DIR, CLUSTER_IP
# Example: setup_ssh_config "192.168.1.10"
setup_ssh_config() {
    local lz_ip="$1"

    if [ -z "$lz_ip" ]; then
        echo "ERROR: Landing Zone IP address is required" >&2
        return 1
    fi

    # Export for use in scripts (LZ_HOME/LZ_ENCLAVE_DIR can be overridden via env)
    export CLUSTER_IP="$lz_ip"
    export LZ_SSH="${LZ_USER}@${lz_ip}"
    export LZ_HOME="${LZ_HOME:-/home/${LZ_USER}}"
    export LZ_ENCLAVE_DIR="${LZ_ENCLAVE_DIR:-${LZ_HOME}/enclave}"
}

# Resolve LZ_HOME by querying the remote user's home directory via SSH.
# Call this after ssh_test_connection to get the actual home path
# (handles root, non-standard homes, etc.)
# Sets: LZ_HOME, LZ_ENCLAVE_DIR (preserves LZ_ENCLAVE_DIR if already overridden)
# Example: resolve_lz_home
resolve_lz_home() {
    if [ -z "${LZ_SSH:-}" ]; then
        echo "ERROR: SSH not configured. Call setup_ssh_config first." >&2
        return 1
    fi

    # shellcheck disable=SC2086
    local remote_home
    remote_home=$(ssh $SSH_OPTS "$LZ_SSH" 'echo $HOME')

    if [ -n "$remote_home" ]; then
        export LZ_HOME="$remote_home"
        export LZ_ENCLAVE_DIR="${LZ_HOME}/enclave"
    fi
}

# Execute a command on the Landing Zone via SSH
# Args: $1 = Command to execute
# Returns: Command exit code
# Example: ssh_exec "ls -la /home/cloud-user"
ssh_exec() {
    local command="$1"

    if [ -z "${LZ_SSH:-}" ]; then
        echo "ERROR: SSH not configured. Call setup_ssh_config first." >&2
        return 1
    fi

    # shellcheck disable=SC2086
    ssh $SSH_OPTS "$LZ_SSH" "$command"
}

# Test SSH connectivity to Landing Zone
# Returns: 0 if connection successful, 1 otherwise
# Example: if ssh_test_connection; then echo "Connected"; fi
ssh_test_connection() {
    if [ -z "${LZ_SSH:-}" ]; then
        echo "ERROR: SSH not configured. Call setup_ssh_config first." >&2
        return 1
    fi

    # shellcheck disable=SC2086
    ssh $SSH_OPTS "$LZ_SSH" "echo 'SSH test successful'" &>/dev/null
}

# Check if a file exists on the Landing Zone
# Args: $1 = Remote file path
# Returns: 0 if file exists, 1 otherwise
# Example: if ssh_file_exists "/etc/hosts"; then echo "File exists"; fi
ssh_file_exists() {
    local remote_path="$1"

    if [ -z "${LZ_SSH:-}" ]; then
        echo "ERROR: SSH not configured. Call setup_ssh_config first." >&2
        return 1
    fi

    # shellcheck disable=SC2086
    ssh $SSH_OPTS "$LZ_SSH" "test -f $remote_path"
}
