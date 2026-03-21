#!/bin/bash
# Deploy OpenShift cluster using Enclave Lab
#
# This script connects to the Landing Zone VM and runs the Enclave Lab
# ansible playbook to deploy the OpenShift cluster.

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

# Deployment mode: connected or disconnected (default: disconnected)
DEPLOYMENT_MODE="${ENCLAVE_DEPLOYMENT_MODE:-disconnected}"

# Custom vars file (optional)
VARS_FILE="${VARS_FILE:-}"

info "========================================="
info "OpenShift Cluster Deployment"
info "========================================="
info ""
info "Landing Zone: $CLUSTER_IP"
info "Enclave Lab Directory: $LZ_ENCLAVE_DIR"
info ""

# Verify SSH connectivity
info "Verifying Landing Zone connectivity..."
if ! ssh_test_connection; then
    error "Cannot connect to Landing Zone at $CLUSTER_IP"
    exit 1
fi
success "Landing Zone accessible"

# Verify Enclave Lab is installed
info "Verifying Enclave Lab installation..."
if ! ssh_file_exists "$LZ_ENCLAVE_DIR/playbooks/main.yaml"; then
    error "Enclave Lab not found at $LZ_ENCLAVE_DIR"
    error "Run 'make install-enclave' first"
    exit 1
fi

if ! ssh_file_exists "$LZ_ENCLAVE_DIR/config/global.yaml"; then
    error "config/global.yaml not found at $LZ_ENCLAVE_DIR"
    error "Run 'make install-enclave' first"
    exit 1
fi
success "Enclave Lab installation verified"

# Show configuration summary
info ""
info "Deployment Configuration:"
CLUSTER_NAME_VAR=$(ssh_exec "grep '^clusterName:' $LZ_ENCLAVE_DIR/config/global.yaml | awk '{print \$2}'" 2>/dev/null)
BASE_DOMAIN=$(ssh_exec "grep '^baseDomain:' $LZ_ENCLAVE_DIR/config/global.yaml | awk '{print \$2}'" 2>/dev/null)
NODE_COUNT=$(ssh_exec "grep -c '^  - name:' $LZ_ENCLAVE_DIR/config/global.yaml 2>/dev/null || true")

info "  Cluster: ${CLUSTER_NAME_VAR}.${BASE_DOMAIN}"
info "  Cluster Nodes: ${NODE_COUNT}"
info "  Landing Zone: ${CLUSTER_IP}"
info ""

warning "==========================================="
warning "This will deploy OpenShift cluster"
warning "==========================================="
info ""
info "What will happen:"
info "  1. Download OpenShift images and binaries"
info "  2. Create local mirror registry (Quay)"
info "  3. Mirror required images"
info "  4. Generate Agent-Based Installer ISO"
info "  5. Power on worker VMs via Redfish"
info "  6. Boot workers from ISO and install OpenShift"
info "  7. Wait for cluster to be ready"
info "  8. Configure operators and post-install"
info ""
warning "This process will take 30-60 minutes or more"

if [ "$DEPLOYMENT_MODE" = "connected" ]; then
    info "Connected Mode - What will happen:"
    info "  1. Download OpenShift images and binaries"
    info "  2. Generate Agent-Based Installer ISO (pulls from upstream)"
    info "  3. Power on cluster VMs via Redfish"
    info "  4. Boot nodes from ISO and install OpenShift"
    info "  5. Wait for cluster to be ready"
    info "  6. Configure operators and post-install"
    info ""
    warning "Estimated time: 20-30 minutes (no mirroring)"
else
    info "Disconnected Mode - What will happen:"
    info "  1. Download OpenShift images and binaries"
    info "  2. Create local mirror registry (Quay)"
    info "  3. Mirror required images (~30+ minutes)"
    info "  4. Generate Agent-Based Installer ISO"
    info "  5. Power on cluster VMs via Redfish"
    info "  6. Boot nodes from ISO and install OpenShift"
    info "  7. Wait for cluster to be ready"
    info "  8. Configure operators and post-install"
    info ""
    warning "Estimated time: 45-90 minutes (includes mirroring)"
fi
info ""
info "========================================="
info "Starting OpenShift Deployment"
info "========================================="
info ""
info "Connecting to Landing Zone and running Enclave Lab playbook..."
info "You can monitor progress in real-time below"
info ""
info "To monitor from another terminal:"
info "  ssh ${LZ_SSH}"
info "  tail -f /home/${LZ_USER}/enclave/deployment.log"
info ""

# Run ansible playbook on Landing Zone
# Note: Pass workingDir as extra var because it's used in playbook's environment block
# before config/global.yaml is loaded
# Use bash -c with PIPESTATUS to capture ansible-playbook exit code correctly
# even when piping through tee

# Build ansible-playbook command with extra vars file
# Create a temporary extra vars YAML file on the Landing Zone
EXTRA_VARS_CONTENT="workingDir: /home/${LZ_USER}
"

# Set disconnected mode based on DEPLOYMENT_MODE
if [ "$DEPLOYMENT_MODE" = "connected" ]; then
    EXTRA_VARS_CONTENT="${EXTRA_VARS_CONTENT}disconnected: false
"
    info "Deploying in CONNECTED mode (no mirroring)"
else
    EXTRA_VARS_CONTENT="${EXTRA_VARS_CONTENT}disconnected: true
"
    info "Deploying in DISCONNECTED mode (with mirroring)"
fi

if [ -n "$VARS_FILE" ]; then
    info "Using custom vars file: $VARS_FILE"
    EXTRA_VARS_CONTENT="${EXTRA_VARS_CONTENT}vars_file: $VARS_FILE
"
fi

# Set storage plugin from STORAGE_PLUGIN env var (overrides default in config)
if [ -n "${STORAGE_PLUGIN:-}" ]; then
    EXTRA_VARS_CONTENT="${EXTRA_VARS_CONTENT}storage_plugin: ${STORAGE_PLUGIN}
"
    info "Storage plugin: $STORAGE_PLUGIN"
fi

# Set enabled plugins from ENABLED_PLUGINS env var (comma-separated)
if [ -n "${ENABLED_PLUGINS:-}" ]; then
    EXTRA_VARS_CONTENT="${EXTRA_VARS_CONTENT}enabled_plugins:
"
    IFS=',' read -ra _plugins <<< "$ENABLED_PLUGINS"
    for _plugin in "${_plugins[@]}"; do
        _plugin="${_plugin// /}"  # trim whitespace
        EXTRA_VARS_CONTENT="${EXTRA_VARS_CONTENT}  - ${_plugin}
"
    done
    info "Enabled plugins: $ENABLED_PLUGINS"
fi

# Create the extra vars file on Landing Zone
ssh_exec "mkdir -p $LZ_ENCLAVE_DIR/config"
# shellcheck disable=SC2087,SC2086  # We want client-side expansion of $EXTRA_VARS_CONTENT
ssh $SSH_OPTS "$LZ_SSH" "cat > $LZ_ENCLAVE_DIR/config/extra_vars.yaml" <<EOF
$EXTRA_VARS_CONTENT
EOF

# Run ansible-playbook with the extra vars file
# shellcheck disable=SC2086  # SSH_OPTS needs word splitting
ssh -t $SSH_OPTS "$LZ_SSH" "cd $LZ_ENCLAVE_DIR && bash -c 'set -o pipefail; ansible-playbook playbooks/main.yaml -e @config/extra_vars.yaml 2>&1 | tee deployment.log'"

DEPLOYMENT_EXIT_CODE=$?

echo ""
info "========================================="
if [ $DEPLOYMENT_EXIT_CODE -eq 0 ]; then
    info "✅ Deployment Complete!"
    info "========================================="
    info ""
    success "OpenShift cluster deployed successfully!"
    info ""
    info "Cluster Access:"
    info "  Web Console: https://console-openshift-console.apps.${CLUSTER_NAME_VAR}.${BASE_DOMAIN}"
    info "  API: https://api.${CLUSTER_NAME_VAR}.${BASE_DOMAIN}:6443"
    info ""
    info "Get kubeconfig:"
    info "  ssh ${LZ_SSH}"
    info "  export KUBECONFIG=\$HOME/ocp-cluster/auth/kubeconfig"
    info "  oc get nodes"
    info ""
    info "View deployment logs:"
    info "  ssh ${LZ_SSH} 'cat ~/enclave/deployment.log'"
    info ""
else
    error "========================================="
    error "Deployment Failed!"
    error "========================================="
    error ""
    error "Deployment exited with code: $DEPLOYMENT_EXIT_CODE"
    info ""
    info "To troubleshoot:"
    info "  1. SSH to Landing Zone: ssh ${LZ_SSH}"
    info "  2. Check logs: cat ~/enclave/deployment.log"
    info "  3. Re-run manually: cd ~/enclave && ansible-playbook playbooks/main.yaml -e \"$EXTRA_VARS\""
    info ""
    exit 1
fi
