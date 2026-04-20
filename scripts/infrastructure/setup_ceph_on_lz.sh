#!/bin/bash
# Set up a Ceph cluster on the Landing Zone VM for ODF CI testing
#
# This script SSHs to the Landing Zone and runs setup_ceph.sh remotely.
# The LZ is on the same libvirt network as the OpenShift nodes, so
# Ceph is directly reachable without firewall or routing workarounds.
#
# After setup, config files are written to ~/ceph-config/ on the LZ
# and consumed by deploy scripts via scripts/lib/odf.sh.
#
# Usage: ./setup_ceph_on_lz.sh
# Environment:
#   DEV_SCRIPTS_PATH       - Path to dev-scripts (required)
#   OSD_SIZE_GB            - Size per OSD in GB (default: 20)
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
source "${ENCLAVE_DIR}/scripts/lib/common.sh"

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

OSD_SIZE_GB="${OSD_SIZE_GB:-200}"

info "========================================="
info "Ceph Setup on Landing Zone"
info "========================================="
info ""
info "Landing Zone VM: $LZ_VM_NAME"
info "Landing Zone IP: $CLUSTER_IP"
info "OSD size: ${OSD_SIZE_GB}GB"
info ""

# Step 1: Verify SSH connectivity
info "Step 1: Verifying Landing Zone connectivity..."
if ! ssh_test_connection; then
    error "Cannot connect to Landing Zone at $CLUSTER_IP"
    exit 1
fi
success "Landing Zone accessible"

# Resolve actual home directory on LZ (handles root, non-standard homes)
resolve_lz_home

# Step 2: Ensure lvm2 is installed on LZ (required for OSD creation)
info "Step 2: Ensuring lvm2 is installed on Landing Zone..."
# shellcheck disable=SC2086
ssh $SSH_OPTS "$LZ_SSH" "rpm -q lvm2 &>/dev/null || sudo dnf install -y lvm2"
success "lvm2 available"

# Step 3: Run setup_ceph.sh on the Landing Zone
info "Step 3: Running Ceph setup on Landing Zone..."
info "  This will bootstrap a single-node Ceph cluster with 3 loopback OSDs"
info ""

CEPH_CONFIG_DIR="${LZ_HOME}/ceph-config"

# shellcheck disable=SC2086
ssh -t $SSH_OPTS "$LZ_SSH" "sudo CEPH_HOST_IP=${CLUSTER_IP} \
    LOOP_DIR=/var/lib/ceph-loops \
    OSD_SIZE_GB=${OSD_SIZE_GB} \
    SKIP_LOOPBACK_SERVICE=true \
    SKIP_FIREWALL=true \
    CEPH_CONFIG_DIR=${CEPH_CONFIG_DIR} \
    bash ${LZ_ENCLAVE_DIR}/scripts/infrastructure/setup_ceph.sh"

# Step 4: Verify config files were created
info ""
info "Step 4: Verifying config files on Landing Zone..."

if ! ssh_file_exists "${CEPH_CONFIG_DIR}/odf_external_config.json"; then
    error "ODF external config not found at ${CEPH_CONFIG_DIR}/odf_external_config.json"
    exit 1
fi

if ! ssh_file_exists "${CEPH_CONFIG_DIR}/quay_backend_rgw_config.yaml"; then
    error "Quay RGW config not found at ${CEPH_CONFIG_DIR}/quay_backend_rgw_config.yaml"
    exit 1
fi

success "Config files verified on Landing Zone"

# Step 5: Merge Ceph configs into global.yaml so bootstrap steps can use them
# bootstrap.sh reads global.yaml directly and does not source odf.sh
info ""
info "Step 5: Updating global.yaml with Ceph/ODF configuration..."
GLOBAL_VARS="${LZ_ENCLAVE_DIR}/config/global.yaml"

# Append both configs on the LZ side to avoid shell quoting issues with JSON
# RGW config for Quay S3 validation, ODF config for operator deployment
# shellcheck disable=SC2086
ssh $SSH_OPTS "$LZ_SSH" bash -s -- "${GLOBAL_VARS}" "${CEPH_CONFIG_DIR}" <<'REMOTE_SCRIPT'
GLOBAL_VARS="$1"
CEPH_CONFIG_DIR="$2"
RGW_CONFIG=$(cat "${CEPH_CONFIG_DIR}/quay_backend_rgw_config.yaml")
ODF_CONFIG=$(cat "${CEPH_CONFIG_DIR}/odf_external_config.json")
printf '\nquayBackendRGWConfiguration: %s\n' "${RGW_CONFIG}" >> "${GLOBAL_VARS}"
printf "odfExternalConfig: '%s'\n" "${ODF_CONFIG}" >> "${GLOBAL_VARS}"
REMOTE_SCRIPT
success "global.yaml updated with quayBackendRGWConfiguration and odfExternalConfig"

info ""
info "========================================="
info "Ceph Setup Complete"
info "========================================="
info ""
info "Ceph cluster running on Landing Zone at $CLUSTER_IP"
info "Config files: ${CEPH_CONFIG_DIR}/"
info "  - odf_external_config.json"
info "  - quay_backend_rgw_config.yaml"
info ""
