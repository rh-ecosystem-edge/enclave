#!/bin/bash
# Install Enclave Lab on Landing Zone VM
#
# This script:
# 1. Copies Enclave Lab repository to Landing Zone VM
# 2. Generates config/global.yaml, config/certificates.yaml and config/cloud_infra.yaml configuration
#    from infrastructure
# 3. Installs any missing dependencies
# 4. Verifies installation

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

# Determine cluster name for dynamic config file
ENCLAVE_CLUSTER_NAME="${ENCLAVE_CLUSTER_NAME:-enclave-test}"

# Source cluster configuration
load_cluster_env

# Configuration
CLUSTER_NAME="${ENCLAVE_CLUSTER_NAME:-enclave-test}"
LZ_VM_NAME="${CLUSTER_NAME}_landingzone_0"
ensure_working_dir

# Landing Zone IP (static DHCP lease, from cluster-env.sh)
CLUSTER_IP="${ENCLAVE_LZ_CLUSTER_IP}"

if [ -z "$CLUSTER_IP" ]; then
    error "Could not determine Landing Zone IP address"
    error "Is the Landing Zone VM running? Run: make verify-landing-zone"
    exit 1
fi

# Setup SSH configuration
setup_ssh_config "$CLUSTER_IP"
LZ_ROOT_DIR="/home/${LZ_USER}"

info "========================================="
info "Enclave Lab Installation on Landing Zone"
info "========================================="
info ""
info "Landing Zone VM: $LZ_VM_NAME"
info "Landing Zone IP: $CLUSTER_IP"
info "Local Enclave Lab: $ENCLAVE_DIR"
info "Remote Enclave Dir: $LZ_ENCLAVE_DIR"
info ""

# Step 1: Verify Landing Zone is accessible
info "Step 1: Verifying Landing Zone VM is accessible..."
if ! ssh_test_connection; then
    error "Cannot connect to Landing Zone VM at $CLUSTER_IP"
    error "Run 'make verify-landing-zone' to check VM status"
    exit 1
fi
success "Landing Zone VM is accessible"

# Step 2: Copy Enclave Lab to Landing Zone
info "Step 3: Copying Enclave Lab repository to Landing Zone..."

# Create directory on Landing Zone
ssh_exec "mkdir -p $LZ_ENCLAVE_DIR" &>/dev/null

# Use rsync to copy Enclave Lab (excluding .git and other unnecessary files)
info "  Syncing files (this may take a minute)..."
rsync -az --delete \
    --exclude='.git' \
    --exclude='*.pyc' \
    --exclude='__pycache__' \
    --exclude='.venv' \
    --exclude='venv' \
    --exclude='*.log' \
    --exclude='.idea' \
    -e "ssh $SSH_OPTS" \
    "$ENCLAVE_DIR/" \
    "${LZ_SSH}:${LZ_ENCLAVE_DIR}/"

success "Enclave Lab copied to Landing Zone"

# Step 4: Install additional dependencies
info "Step 4: Installing additional dependencies on Landing Zone..."

ssh_exec "sudo $LZ_ENCLAVE_DIR/setup_env.sh"
ssh_exec "$LZ_ENCLAVE_DIR/setup_ansible.sh"

success "Dependencies installed"

# Step 5: Generate config/global.yaml, config/certificates.yaml and config/cloud_infra.yaml configuration
info "Step 5: Generating Enclave Lab configuration (config/global.yaml, config/certificates.yaml and config/cloud_infra.yaml)..."

# Generate config files using helper script
"${ENCLAVE_DIR}/scripts/infrastructure/generate_enclave_vars.sh"

# Copy vars files to Landing Zone
ssh $SSH_OPTS "$LZ_SSH" "mkdir -p ${LZ_ENCLAVE_DIR}/config"
scp $SSH_OPTS "${WORKING_DIR}/config/global.yaml" "${LZ_SSH}:${LZ_ENCLAVE_DIR}/config/global.yaml"
scp $SSH_OPTS "${WORKING_DIR}/config/certificates.yaml" "${LZ_SSH}:${LZ_ENCLAVE_DIR}/config/certificates.yaml"
scp $SSH_OPTS "${WORKING_DIR}/config/cloud_infra.yaml" "${LZ_SSH}:${LZ_ENCLAVE_DIR}/config/cloud_infra.yaml"

success "Configuration generated and copied to Landing Zone"

# Cluster DNS (api.<cluster>.<base>, *.apps.<cluster>.<base>, mirror) is baked into the
# libvirt networks' dnsmasq by vm_infra.py (see Config.cluster_dns_addresses), so there
# is no runtime net-update here.

# Step 6: Copy pull secret
info "Step 6: Setting up pull secret..."

# Look for pull secret in common locations
PULL_SECRET_FOUND=false
PULL_SECRET_SOURCE=""

# Check common pull secret locations in order of preference
for SECRET_PATH in \
    "${WORKING_DIR}/pull_secret.json" \
    "${HOME}/.pull-secret.json" \
    "${WORKING_DIR}/pull-secret.json" \
    "/root/pull-secret.json"; do

    if [ -f "$SECRET_PATH" ]; then
        PULL_SECRET_SOURCE="$SECRET_PATH"
        PULL_SECRET_FOUND=true
        break
    fi
done

if [ "$PULL_SECRET_FOUND" = true ]; then
    info "  Found pull secret at: $PULL_SECRET_SOURCE"

    # Validate it's valid JSON with required registries
    if ! jq -e '.auths."registry.redhat.io"' "$PULL_SECRET_SOURCE" >/dev/null 2>&1; then
        error "Pull secret at $PULL_SECRET_SOURCE is missing registry.redhat.io credentials"
        exit 1
    fi

    info "  Validated pull secret contains registry.redhat.io credentials"

    # Step 6.5: Update config/global.yaml with actual pull secret content
    # The pull secret file at pullSecretPath will be created by 01-prepare.yaml
    info "Step 6.5: Embedding pull secret in config/global.yaml..."

    # Copy pull secret to a temp file on the LZ, embed it into global.yaml, then remove.
    # This avoids exposing the secret in command arguments or shell history.
    scp $SSH_OPTS "$PULL_SECRET_SOURCE" "${LZ_SSH}:/tmp/_pull_secret.json"
    ssh $SSH_OPTS "$LZ_SSH" python3 - "${LZ_ENCLAVE_DIR}/config/global.yaml" <<'EOPY'
import yaml, json, sys
config_path = sys.argv[1]
with open("/tmp/_pull_secret.json") as f:
    pull_secret = json.load(f)
with open(config_path) as f:
    vars_data = yaml.safe_load(f)
vars_data["pullSecret"] = pull_secret
with open(config_path, "w") as f:
    yaml.dump(vars_data, f, default_flow_style=False, sort_keys=False)
EOPY
    ssh $SSH_OPTS "$LZ_SSH" rm -f /tmp/_pull_secret.json

    success "Pull secret embedded in config/global.yaml"

else
    error "Pull secret not found in any common location"
    info "  Searched:"
    info "    - ${WORKING_DIR}/pull_secret.json"
    info "    - ${HOME}/.pull-secret.json"
    info "    - ${WORKING_DIR}/pull-secret.json"
    info "    - /root/pull-secret.json"
    info ""
    info "  Please:"
    info "    1. Download pull secret from https://console.redhat.com/openshift/install/pull-secret"
    info "    2. Save it to one of the locations above"
    info "    3. Re-run 'make install-enclave'"
    exit 1
fi

# Step 7: Generate SSH key if needed
info "Step 7: Checking SSH key on Landing Zone..."
if ! SSH_KEY_PATH=$(ensure_lz_ssh_public_key); then
    error "Failed to find or generate an SSH key on the Landing Zone"
    exit 1
fi
success "SSH key ready: ${SSH_KEY_PATH}"

# Step 8: Display configuration summary
info "Step 8: Configuration summary..."
echo ""
info "Enclave Lab Installation Summary:"
info "  Enclave Lab Directory: $LZ_ENCLAVE_DIR"
info "  Configuration: $LZ_ENCLAVE_DIR/config/global.yaml"
info "  Certificates: $LZ_ENCLAVE_DIR/config/certificates.yaml"
info "  Cloud Infra: $LZ_ENCLAVE_DIR/config/cloud_infra.yaml"
info "  Working Directory: $LZ_ROOT_DIR"
echo ""

# Step 9: Display next steps
echo ""
info "========================================="
info "✅ Enclave Lab Installation Complete!"
info "========================================="
echo ""
info "Enclave Lab is now installed on Landing Zone VM at: $CLUSTER_IP"
echo ""
info "Next steps:"
info "  1. SSH to Landing Zone: ssh $LZ_SSH"
info "  2. Review configuration: cat $LZ_ENCLAVE_DIR/config/global.yaml"
info "  3. Edit config/global.yaml, config/certificates.yaml and config/cloud_infra.yaml as needed"
info "  4. Run Enclave Lab: cd $LZ_ENCLAVE_DIR && ansible-playbook playbooks/main.yaml"
echo ""
info "To verify installation:"
info "  make verify-enclave-installation"
echo ""
