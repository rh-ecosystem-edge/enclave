#!/bin/bash
# Shared common utilities
#
# Provides common helper functions used across multiple scripts for
# directory detection, working directory construction, and script path resolution.
#
# Usage:
#   source "${ENCLAVE_DIR}/ci/lib/common.sh"
#   detect_script_dir
#   detect_enclave_dir
#   ensure_working_dir
#
# Functions:
#   detect_script_dir                 - Set SCRIPT_DIR to the directory containing the calling script
#   detect_enclave_dir                - Set ENCLAVE_DIR to repository root
#   ensure_working_dir                - Ensure WORKING_DIR is set (auto-construct if needed)
#   get_cluster_name                  - Get cluster name (ENCLAVE_CLUSTER_NAME with fallback)
#   enclave_ci_environment_bin        - Print path to an enclave-ci that can import libvirt

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

    # Navigate up two levels to the repository root (callers live in <area>/<dir>/)
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
        WORKING_DIR="${BASE_WORKING_DIR}/${ENCLAVE_CLUSTER_NAME}"
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

# Print the path of an enclave-ci executable whose venv is built on the host python3
# with system site-packages, so `import libvirt` resolves to the python3-libvirt RPM
# instead of compiling libvirt-python. Run it as the unprivileged user (the venv lives
# in the repo checkout); the printed path can then be executed under sudo.
# Requires: ENCLAVE_DIR, uv, /usr/bin/python3 with python3-libvirt
# Example: sudo -E "$(enclave_ci_environment_bin)" environment create
enclave_ci_environment_bin() {
    local venv="${ENCLAVE_DIR}/.venv-enclave-ci"
    # Explicit `|| return 1`: errexit does not apply inside $(...) or if-conditions.
    if [ ! -x "${venv}/bin/python" ]; then
        uv venv --quiet --python /usr/bin/python3 --system-site-packages "${venv}" >&2 \
            || return 1
    fi
    UV_PROJECT_ENVIRONMENT="${venv}" uv sync --quiet --frozen --package enclave-ci \
        --directory "${ENCLAVE_DIR}" >&2 || return 1
    echo "${venv}/bin/enclave-ci"
}

