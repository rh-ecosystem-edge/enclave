#!/bin/bash
# Deploy a plugin to the OpenShift cluster
#
# This script connects to the Landing Zone VM and runs the
# deploy-plugin.yaml playbook for the specified plugin.
#
# Usage: ./deploy_plugin.sh <plugin-name>
# Example: ./deploy_plugin.sh openshift-ai

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
if [ $# -ne 1 ]; then
    error "Usage: $0 <plugin-name>"
    error "Example: $0 openshift-ai"
    exit 1
fi

PLUGIN_NAME="$1"

# Validate plugin name format (prevent path traversal)
if [[ ! "$PLUGIN_NAME" =~ ^[A-Za-z0-9][A-Za-z0-9._-]*$ ]]; then
    error "Invalid plugin name: '$PLUGIN_NAME'"
    error "Plugin name must match: ^[A-Za-z0-9][A-Za-z0-9._-]*$"
    exit 1
fi

# Validate plugin exists locally
if [ ! -f "${ENCLAVE_DIR}/plugins/${PLUGIN_NAME}/plugin.yaml" ]; then
    error "Plugin not found: plugins/${PLUGIN_NAME}/plugin.yaml"
    exit 1
fi

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

# Deployment mode: connected or disconnected (default: disconnected)
DEPLOYMENT_MODE="${ENCLAVE_DEPLOYMENT_MODE:-disconnected}"

info "Deploying plugin: $PLUGIN_NAME"
info "Landing Zone: $CLUSTER_IP"
info "Deployment mode: $DEPLOYMENT_MODE"
info ""

# Verify SSH connectivity
if ! ssh_test_connection; then
    error "Cannot connect to Landing Zone at $CLUSTER_IP"
    exit 1
fi

# Verify Enclave Lab is installed
if ! ssh_file_exists "$LZ_ENCLAVE_DIR/plugins/${PLUGIN_NAME}/plugin.yaml"; then
    error "Plugin not found on Landing Zone: $LZ_ENCLAVE_DIR/plugins/${PLUGIN_NAME}/plugin.yaml"
    exit 1
fi

if ! ssh_file_exists "$LZ_ENCLAVE_DIR/config/global.yaml"; then
    error "config/global.yaml not found at $LZ_ENCLAVE_DIR"
    error "Run 'make install-enclave' first"
    exit 1
fi

# Build ansible-playbook command with extra vars
EXTRA_VARS_CONTENT="workingDir: /home/${LZ_USER}
plugin_name: ${PLUGIN_NAME}
"

# Set disconnected mode based on DEPLOYMENT_MODE
if [ "$DEPLOYMENT_MODE" = "connected" ]; then
    EXTRA_VARS_CONTENT="${EXTRA_VARS_CONTENT}disconnected: false
"
else
    EXTRA_VARS_CONTENT="${EXTRA_VARS_CONTENT}disconnected: true
"
fi

# Create the extra vars file on Landing Zone
# shellcheck disable=SC2087,SC2086  # We want client-side expansion of $EXTRA_VARS_CONTENT
ssh $SSH_OPTS "$LZ_SSH" "cat > $LZ_ENCLAVE_DIR/phase_vars.yaml" <<EOF
$EXTRA_VARS_CONTENT
EOF

# Run ansible-playbook with the extra vars file
LOG_FILE="deployment_plugin_${PLUGIN_NAME}.log"
info "Running playbook (logging to $LOG_FILE)..."
# shellcheck disable=SC2086  # SSH_OPTS needs word splitting
set +e
ssh -t $SSH_OPTS "$LZ_SSH" "cd $LZ_ENCLAVE_DIR && bash -c 'set -o pipefail; ansible-playbook playbooks/deploy-plugin.yaml -e @phase_vars.yaml 2>&1 | tee $LOG_FILE'"
PLUGIN_EXIT_CODE=$?
set -e

echo ""
if [ $PLUGIN_EXIT_CODE -eq 0 ]; then
    success "Plugin deployed successfully: $PLUGIN_NAME"
else
    error "Plugin deployment failed: $PLUGIN_NAME (exit code: $PLUGIN_EXIT_CODE)"
    info "Check logs: ssh ${LZ_SSH} 'cat ~/$LOG_FILE'"
    exit 1
fi
