#!/bin/bash
# Verify Ceph cluster health on the Landing Zone VM
#
# SSHs to the Landing Zone and runs verify_ceph.sh against localhost,
# plus checks that the CI config files exist.
#
# Usage: ./verify_ceph_on_lz.sh
# Environment:
#   DEV_SCRIPTS_PATH       - Path to dev-scripts (required)
#   ENCLAVE_CLUSTER_NAME   - Cluster name (default: enclave-test)

set -euo pipefail

# Detect Enclave repository root
SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" &>/dev/null && pwd)"
ENCLAVE_DIR="$(cd -- "${SCRIPT_DIR}/../.." &>/dev/null && pwd)"

# Source shared utilities
source "${ENCLAVE_DIR}/scripts/lib/output.sh"
source "${ENCLAVE_DIR}/scripts/lib/validation.sh"
source "${ENCLAVE_DIR}/scripts/lib/config.sh"
source "${ENCLAVE_DIR}/scripts/lib/network.sh"
source "${ENCLAVE_DIR}/scripts/lib/ssh.sh"

# Validate required environment variables
require_env_var "DEV_SCRIPTS_PATH"

# Determine cluster name for dynamic config file
ENCLAVE_CLUSTER_NAME="${ENCLAVE_CLUSTER_NAME:-enclave-test}"

# Source dev-scripts configuration
load_devscripts_config

# Configuration
CLUSTER_NAME="${CLUSTER_NAME:-enclave-test}"
LZ_VM_NAME="${CLUSTER_NAME}_landingzone_0"

# Get Landing Zone IP using network utility
CLUSTER_NETWORK="${EXTERNAL_SUBNET_V4}"
CLUSTER_IP=$(get_vm_ip_on_network "$LZ_VM_NAME" "$CLUSTER_NETWORK")

if [ -z "$CLUSTER_IP" ]; then
    error "Could not determine Landing Zone IP address"
    exit 1
fi

# Setup SSH configuration
setup_ssh_config "$CLUSTER_IP"

CEPH_CONFIG_DIR="${LZ_HOME}/ceph-config"

info "========================================="
info "Verifying Ceph on Landing Zone"
info "========================================="
info ""
info "Landing Zone: $CLUSTER_IP"
info ""

# Verify SSH connectivity
if ! ssh_test_connection; then
    error "Cannot connect to Landing Zone at $CLUSTER_IP"
    exit 1
fi

# Resolve actual home directory on LZ (handles root, non-standard homes)
resolve_lz_home

# Run verify_ceph.sh on the LZ with CEPH_HOST_IP pointing to itself
# shellcheck disable=SC2086
ssh $SSH_OPTS "$LZ_SSH" "CEPH_HOST_IP=${CLUSTER_IP} bash ${LZ_ENCLAVE_DIR}/scripts/infrastructure/verify_ceph.sh"

VERIFY_EXIT=$?

# Check config files
info ""
info "Checking CI config files..."

FAILED=0
if ssh_file_exists "${CEPH_CONFIG_DIR}/odf_external_config.json"; then
    success "odf_external_config.json exists"
else
    error "odf_external_config.json NOT found at ${CEPH_CONFIG_DIR}/"
    FAILED=1
fi

if ssh_file_exists "${CEPH_CONFIG_DIR}/quay_backend_rgw_config.yaml"; then
    success "quay_backend_rgw_config.yaml exists"
else
    error "quay_backend_rgw_config.yaml NOT found at ${CEPH_CONFIG_DIR}/"
    FAILED=1
fi

if [ "$VERIFY_EXIT" -ne 0 ] || [ "$FAILED" -ne 0 ]; then
    error "Ceph verification failed"
    exit 1
fi

success "Ceph verification passed"
