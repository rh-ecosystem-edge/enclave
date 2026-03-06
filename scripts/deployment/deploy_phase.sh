#!/bin/bash
# Deploy a specific phase of the OpenShift cluster deployment
#
# This script connects to the Landing Zone VM and runs a specific
# Enclave Lab ansible playbook phase.
#
# Usage: ./deploy_phase.sh <playbook-file>
# Example: ./deploy_phase.sh 04-post-install.yaml

set -euo pipefail

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m' # No Color

info() {
    echo -e "${GREEN}INFO:${NC} $1"
}

error() {
    echo -e "${RED}ERROR:${NC} $1"
}

warning() {
    echo -e "${YELLOW}WARNING:${NC} $1"
}

success() {
    echo -e "${GREEN}✓${NC} $1"
}

# Check arguments
if [ $# -ne 1 ]; then
    error "Usage: $0 <playbook-file>"
    error "Example: $0 04-post-install.yaml"
    exit 1
fi

PLAYBOOK_FILE="$1"

# Check required environment variables
if [ -z "${DEV_SCRIPTS_PATH:-}" ]; then
    error "DEV_SCRIPTS_PATH environment variable is not set"
    exit 1
fi

# Determine cluster name for dynamic config file
ENCLAVE_CLUSTER_NAME="${ENCLAVE_CLUSTER_NAME:-enclave-test}"

# Source dev-scripts configuration
CONFIG_FILE="${DEV_SCRIPTS_PATH}/config_${ENCLAVE_CLUSTER_NAME}.sh"
if [ ! -f "$CONFIG_FILE" ]; then
    error "dev-scripts configuration not found: $CONFIG_FILE"
    error "Expected config file for cluster: $ENCLAVE_CLUSTER_NAME"
    exit 1
fi

# shellcheck source=/dev/null
source "$CONFIG_FILE"

# Configuration
CLUSTER_NAME="${CLUSTER_NAME:-enclave-test}"
LZ_VM_NAME="${CLUSTER_NAME}_landingzone_0"

# Extract cluster network prefix for dynamic IP detection
CLUSTER_NETWORK="${EXTERNAL_SUBNET_V4}"
CLUSTER_NET_PREFIX=$(echo "$CLUSTER_NETWORK" | sed 's|/.*||' | awk -F. '{print $1"."$2"."$3}')
ESCAPED_CLUSTER_PREFIX=$(echo "$CLUSTER_NET_PREFIX" | sed 's/\./\\./g')

# Get Landing Zone IP - dynamic subnet detection
CLUSTER_IP=$(sudo virsh domifaddr "$LZ_VM_NAME" 2>/dev/null | grep -E "${ESCAPED_CLUSTER_PREFIX}\." | awk '{print $4}' | cut -d'/' -f1 | head -1)

if [ -z "$CLUSTER_IP" ]; then
    error "Could not determine Landing Zone IP address"
    exit 1
fi

SSH_OPTS="-o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null -o ConnectTimeout=5 -q"
LZ_USER="cloud-user"
LZ_SSH="${LZ_USER}@${CLUSTER_IP}"
LZ_ENCLAVE_DIR="/home/${LZ_USER}/enclave"

# Deployment mode: connected or disconnected (default: disconnected)
DEPLOYMENT_MODE="${ENCLAVE_DEPLOYMENT_MODE:-disconnected}"

info "Running phase: $PLAYBOOK_FILE"
info "Landing Zone: $CLUSTER_IP"
info "Deployment mode: $DEPLOYMENT_MODE"
info ""

# Verify SSH connectivity
if ! ssh $SSH_OPTS "$LZ_SSH" "echo 'SSH test successful'" &>/dev/null; then
    error "Cannot connect to Landing Zone at $CLUSTER_IP"
    exit 1
fi

# Verify Enclave Lab is installed
if ! ssh $SSH_OPTS "$LZ_SSH" "test -f $LZ_ENCLAVE_DIR/playbooks/$PLAYBOOK_FILE"; then
    error "Playbook not found: $LZ_ENCLAVE_DIR/playbooks/$PLAYBOOK_FILE"
    exit 1
fi

if ! ssh $SSH_OPTS "$LZ_SSH" "test -f $LZ_ENCLAVE_DIR/config/global.yaml"; then
    error "config/global.yaml not found at $LZ_ENCLAVE_DIR"
    error "Run 'make install-enclave' first"
    exit 1
fi

# Build ansible-playbook command with extra vars
EXTRA_VARS_CONTENT="workingDir: /home/${LZ_USER}
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
# shellcheck disable=SC2087  # We want client-side expansion of $EXTRA_VARS_CONTENT
ssh $SSH_OPTS "$LZ_SSH" "cat > $LZ_ENCLAVE_DIR/phase_vars.yaml" <<EOF
$EXTRA_VARS_CONTENT
EOF

# Run ansible-playbook with the extra vars file
LOG_FILE="deployment_$(basename "$PLAYBOOK_FILE" .yaml).log"
info "Running playbook (logging to $LOG_FILE)..."
ssh -t $SSH_OPTS "$LZ_SSH" "cd $LZ_ENCLAVE_DIR && bash -c 'set -o pipefail; ansible-playbook playbooks/$PLAYBOOK_FILE -e @phase_vars.yaml 2>&1 | tee $LOG_FILE'"

PHASE_EXIT_CODE=$?

echo ""
if [ $PHASE_EXIT_CODE -eq 0 ]; then
    success "Phase completed successfully: $PLAYBOOK_FILE"
else
    error "Phase failed: $PLAYBOOK_FILE (exit code: $PHASE_EXIT_CODE)"
    info "Check logs: ssh ${LZ_SSH} 'cat ~/$LOG_FILE'"
    exit 1
fi
