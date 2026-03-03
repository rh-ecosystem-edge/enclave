#!/bin/bash
# Install Enclave Lab on Landing Zone VM
#
# This script:
# 1. Copies Enclave Lab repository to Landing Zone VM
# 2. Generates config/global.yaml and config/certificates.yaml configuration
#    from infrastructure
# 3. Installs any missing dependencies
# 4. Verifies installation

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
WORKING_DIR="${WORKING_DIR:-/opt/dev-scripts}"
CLUSTER_NAME="${ENCLAVE_CLUSTER_NAME:-enclave-test}"

# Try cluster-specific environment file first, fall back to legacy location
ENVIRONMENT_JSON="${WORKING_DIR}/environment-${CLUSTER_NAME}.json"
if [ ! -f "$ENVIRONMENT_JSON" ]; then
    ENVIRONMENT_JSON="${WORKING_DIR}/environment.json"
fi

ENCLAVE_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

# Extract cluster network prefix for dynamic IP detection
CLUSTER_NETWORK="${EXTERNAL_SUBNET_V4}"
CLUSTER_NET_PREFIX=$(echo "$CLUSTER_NETWORK" | sed 's|/.*||' | awk -F. '{print $1"."$2"."$3}')
ESCAPED_CLUSTER_PREFIX=$(echo "$CLUSTER_NET_PREFIX" | sed 's/\./\\./g')

# Get Landing Zone IP - dynamic subnet detection
CLUSTER_IP=$(sudo virsh domifaddr "$LZ_VM_NAME" 2>/dev/null | grep -E "${ESCAPED_CLUSTER_PREFIX}\." | awk '{print $4}' | cut -d'/' -f1 | head -1)

if [ -z "$CLUSTER_IP" ]; then
    error "Could not determine Landing Zone IP address"
    error "Is the Landing Zone VM running? Run: make verify-landing-zone"
    exit 1
fi

SSH_OPTS="-o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null -o ConnectTimeout=5 -q"
LZ_USER="cloud-user"
LZ_SSH="${LZ_USER}@${CLUSTER_IP}"
LZ_ENCLAVE_DIR="/home/${LZ_USER}/enclave"
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
if ! ssh $SSH_OPTS "$LZ_SSH" "echo 'SSH test successful'" &>/dev/null; then
    error "Cannot connect to Landing Zone VM at $CLUSTER_IP"
    error "Run 'make verify-landing-zone' to check VM status"
    exit 1
fi
success "Landing Zone VM is accessible"

# Step 2: Check if environment.json exists
info "Step 2: Checking environment metadata..."
if [ ! -f "$ENVIRONMENT_JSON" ]; then
    error "Environment metadata not found: $ENVIRONMENT_JSON"
    error "Run 'make environment' to create infrastructure first"
    exit 1
fi
success "Environment metadata found"

# Step 3: Copy Enclave Lab to Landing Zone
info "Step 3: Copying Enclave Lab repository to Landing Zone..."

# Create directory on Landing Zone
ssh $SSH_OPTS "$LZ_SSH" "mkdir -p $LZ_ENCLAVE_DIR" 2>/dev/null

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

ssh $SSH_OPTS "$LZ_SSH" bash <<'EOSSH'
# Check if podman is installed
if ! command -v podman &>/dev/null; then
    echo "  Installing podman..."
    sudo dnf install -y podman podman-docker
else
    echo "  podman is already installed"
fi

# Check if httpd is installed (needed to serve ISO images)
if ! command -v httpd &>/dev/null; then
    echo "  Installing httpd (Apache web server)..."
    sudo dnf install -y httpd
    sudo systemctl enable httpd
    sudo systemctl start httpd
    echo "  Creating /var/www/html directory..."
    sudo mkdir -p /var/www/html
    sudo chown -R cloud-user:cloud-user /var/www/html
    sudo chmod 755 /var/www/html
else
    echo "  httpd is already installed"
    # Ensure directory exists and has correct permissions
    sudo mkdir -p /var/www/html
    sudo chown -R cloud-user:cloud-user /var/www/html
    sudo chmod 755 /var/www/html
fi

# Check if oc client is installed
if ! command -v oc &>/dev/null; then
    echo "  OpenShift client (oc) not installed - will be downloaded by Enclave Lab"
else
    echo "  oc client is already installed"
fi

# Check if kubectl is installed
if ! command -v kubectl &>/dev/null; then
    echo "  Creating kubectl symlink to oc..."
    if command -v oc &>/dev/null; then
        sudo ln -sf $(which oc) /usr/local/bin/kubectl 2>/dev/null || true
    fi
else
    echo "  kubectl is already installed"
fi

# Install Python YAML library (needed for updating config/global.yaml)
if ! python3 -c "import yaml" 2>/dev/null; then
    echo "  Installing python3-pyyaml..."
    sudo dnf install -y python3-pyyaml
else
    echo "  python3-pyyaml is already installed"
fi

# Install Python jsonschema library (needed for ansible.utils.jsonschema validation)
if ! python3 -c "import jsonschema" 2>/dev/null; then
    echo "  Installing python3-jsonschema..."
    sudo dnf install -y python3-jsonschema
else
    echo "  python3-jsonschema is already installed"
fi

# Install nmstate (needed for openshift-install network validation)
if ! command -v nmstatectl &>/dev/null; then
    echo "  Installing nmstate..."
    sudo dnf install -y nmstate
else
    echo "  nmstate is already installed"
fi

# Install Python kubernetes library (needed for kubernetes.core collection)
if ! python3 -c "import kubernetes" 2>/dev/null; then
    echo "  Installing Python kubernetes library..."
    sudo dnf install -y python3-kubernetes || sudo pip3 install kubernetes
else
    echo "  Python kubernetes library is already installed"
fi

# Install required Ansible collections
echo "  Installing required Ansible collections..."
cd /home/cloud-user/enclave
ansible-galaxy collection install -r requirements.yml --force 2>/dev/null || echo "  Note: Some collections may already be installed"
EOSSH

success "Dependencies installed"

# Step 5: Generate config/global.yaml and config/certificates.yaml configuration
info "Step 5: Generating Enclave Lab configuration (config/global.yaml and config/certificates.yaml)..."

# Generate config/global.yaml and config/certificates.yaml using helper script
"${ENCLAVE_DIR}/scripts/generate_enclave_vars.sh"

# Copy vars files to Landing Zone
ssh $SSH_OPTS "$LZ_SSH" "mkdir -p ${LZ_ENCLAVE_DIR}/config"
scp $SSH_OPTS "${WORKING_DIR}/config/global.yaml" "${LZ_SSH}:${LZ_ENCLAVE_DIR}/config/global.yaml"
scp $SSH_OPTS "${WORKING_DIR}/config/certificates.yaml" "${LZ_SSH}:${LZ_ENCLAVE_DIR}/config/certificates.yaml"

success "Configuration generated and copied to Landing Zone"

# Step 5.5: Configure DNS resolution for mirror registry
info "Step 5.5: Configuring DNS resolution for mirror registry..."

# Extract baseDomain from config/global.yaml
BASE_DOMAIN=$(grep '^baseDomain:' "${WORKING_DIR}/config/global.yaml" | awk '{print $2}')
MIRROR_HOSTNAME="mirror.${BASE_DOMAIN}"

# Add mirror hostname to /etc/hosts on Landing Zone
ssh $SSH_OPTS "$LZ_SSH" bash -s <<'EOSSH' "${MIRROR_HOSTNAME}"
MIRROR_HOSTNAME="$1"
# Check if mirror hostname is already in /etc/hosts
if ! grep -q "${MIRROR_HOSTNAME}" /etc/hosts; then
    echo "  Adding ${MIRROR_HOSTNAME} to /etc/hosts..."
    echo "127.0.0.1 ${MIRROR_HOSTNAME}" | sudo tee -a /etc/hosts > /dev/null
    echo "  ✓ Added ${MIRROR_HOSTNAME} -> 127.0.0.1"
else
    echo "  ${MIRROR_HOSTNAME} already in /etc/hosts"
fi
EOSSH

success "DNS resolution configured for ${MIRROR_HOSTNAME}"

# Step 6: Copy pull secret
info "Step 6: Setting up pull secret..."

# Look for pull secret in common locations
PULL_SECRET_FOUND=false
PULL_SECRET_SOURCE=""

# Check common pull secret locations in order of preference
for SECRET_PATH in \
    "${DEV_SCRIPTS_PATH}/pull_secret.json" \
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

    # Copy to Landing Zone
    ssh $SSH_OPTS "$LZ_SSH" "mkdir -p ${LZ_ROOT_DIR}/.config"
    scp $SSH_OPTS "$PULL_SECRET_SOURCE" "${LZ_SSH}:${LZ_ROOT_DIR}/.config/pull-secret.json"

    success "Pull secret copied to Landing Zone"

    # Step 6.5: Update config/global.yaml with actual pull secret content
    info "Step 6.5: Embedding pull secret in config/global.yaml..."

    # Read pull secret from Landing Zone and update config/global.yaml
    ssh $SSH_OPTS "$LZ_SSH" bash <<'EOSSH'
# Use Python to update config/global.yaml with actual pull secret
python3 <<'PYEOF'
import yaml
import json

# Read the pull secret
with open('/home/cloud-user/.config/pull-secret.json', 'r') as f:
    pull_secret = json.load(f)

# Read config/global.yaml
with open('/home/cloud-user/enclave/config/global.yaml', 'r') as f:
    vars_data = yaml.safe_load(f)

# Update pullSecret with actual credentials
vars_data['pullSecret'] = pull_secret

# Write back to config/global.yaml
with open('/home/cloud-user/enclave/config/global.yaml', 'w') as f:
    yaml.dump(vars_data, f, default_flow_style=False, sort_keys=False)

print("✓ Updated config/global.yaml with pull secret")
PYEOF
EOSSH

    success "Pull secret embedded in config/global.yaml"

else
    error "Pull secret not found in any common location"
    info "  Searched:"
    info "    - ${DEV_SCRIPTS_PATH}/pull_secret.json"
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
ssh $SSH_OPTS "$LZ_SSH" bash <<'EOSSH'
if [ ! -f ~/.ssh/id_rsa.pub ]; then
    echo "  Generating SSH key pair..."
    ssh-keygen -t rsa -b 4096 -f ~/.ssh/id_rsa -N "" -q
else
    echo "  SSH key already exists"
fi
EOSSH
success "SSH key ready"

# Step 8: Display configuration summary
info "Step 8: Configuration summary..."
echo ""
info "Enclave Lab Installation Summary:"
info "  Enclave Lab Directory: $LZ_ENCLAVE_DIR"
info "  Configuration: $LZ_ENCLAVE_DIR/config/global.yaml"
info "  Certificates: $LZ_ENCLAVE_DIR/config/certificates.yaml"
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
info "  3. Edit config/global.yaml and config/certificates.yaml as needed (pull secret, SSL certs, etc.)"
info "  4. Run Enclave Lab: cd $LZ_ENCLAVE_DIR && ansible-playbook playbooks/main.yaml"
echo ""
info "To verify installation:"
info "  make verify-enclave-installation"
echo ""
