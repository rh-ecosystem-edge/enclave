#!/bin/bash
# Run a bootstrap.sh step on the Landing Zone via SSH
#
# This script connects to the Landing Zone VM and runs a specific
# step of bootstrap.sh in non-interactive mode.
#
# Usage: ./deploy_bootstrap_step.sh <step-name>
# Example: ./deploy_bootstrap_step.sh download-content
# Example: ./deploy_bootstrap_step.sh build-cache

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

# Check arguments
if [ $# -lt 1 ]; then
    error "Usage: $0 <step-name>"
    error "Example: $0 download-content"
    error "Example: $0 build-cache"
    error ""
    error "Available steps: setup validate download-content build-cache"
    error "  acquire-hardware deploy post-install operators day2 discovery"
    exit 1
fi

STEP_NAME="$1"

# Validate step name against allowed values
case "$STEP_NAME" in
    setup|validate|download-content|build-cache|acquire-hardware|deploy|post-install|operators|day2|discovery|partner-overlay)
        ;;
    *)
        error "Unknown bootstrap step: $STEP_NAME"
        error "Valid steps: setup validate download-content build-cache acquire-hardware deploy post-install operators day2 discovery partner-overlay"
        exit 1
        ;;
esac

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

info "Running bootstrap step: $STEP_NAME"
info "Landing Zone: $CLUSTER_IP"
info ""

# Verify SSH connectivity
if ! ssh_test_connection; then
    error "Cannot connect to Landing Zone at $CLUSTER_IP"
    exit 1
fi

# Verify bootstrap.sh exists on Landing Zone
if ! ssh_file_exists "$LZ_ENCLAVE_DIR/bootstrap.sh"; then
    error "bootstrap.sh not found at $LZ_ENCLAVE_DIR"
    error "Run 'make install-enclave' first"
    exit 1
fi

# Run the bootstrap step
LOG_FILE="deployment_bootstrap_${STEP_NAME}.log"
info "Running bootstrap.sh --step $STEP_NAME (logging to $LOG_FILE)..."

# Forward deployment mode environment variable if set
EXPORT_VARS=""
if [ -n "${ENCLAVE_DEPLOYMENT_MODE:-}" ]; then
    EXPORT_VARS="export ENCLAVE_DEPLOYMENT_MODE=${ENCLAVE_DEPLOYMENT_MODE}; "
    info "Deployment mode: $ENCLAVE_DEPLOYMENT_MODE"
fi

# shellcheck disable=SC2086
# Use pipefail on the remote shell so the exit code of bootstrap.sh
# propagates through the tee pipeline (without it, tee always returns 0).
if ssh -t $SSH_OPTS "$LZ_SSH" "set -o pipefail; cd $LZ_ENCLAVE_DIR && ${EXPORT_VARS}./bootstrap.sh --step $STEP_NAME --non-interactive 2>&1 | tee $LOG_FILE"; then
    echo ""
    success "Bootstrap step completed successfully: $STEP_NAME"
else
    STEP_EXIT_CODE=$?
    echo ""
    error "Bootstrap step failed: $STEP_NAME (exit code: $STEP_EXIT_CODE)"
    info "Check logs: ssh ${LZ_SSH} 'cat $LZ_ENCLAVE_DIR/$LOG_FILE'"
    exit $STEP_EXIT_CODE
fi
