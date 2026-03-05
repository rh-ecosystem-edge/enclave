#!/bin/bash
# Verify Enclave Lab installation on Landing Zone VM
#
# This script verifies that:
# - Enclave Lab repository is copied to Landing Zone
# - Configuration files config/global.yaml and config/certificates.yaml exist and are valid
# - Required dependencies are installed
# - Directory structure is correct

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

fail() {
    echo -e "${RED}✗${NC} $1"
    VERIFICATION_FAILED=1
}

# Track overall verification status
VERIFICATION_FAILED=0

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

info "========================================="
info "Enclave Lab Installation Verification"
info "========================================="
info ""
info "Landing Zone: $CLUSTER_IP"
info "Enclave Directory: $LZ_ENCLAVE_DIR"
info ""

# Test 1: SSH connectivity
info "Test 1: Checking SSH connectivity to Landing Zone..."
if ssh $SSH_OPTS "$LZ_SSH" "echo 'SSH test successful'" &>/dev/null; then
    success "SSH connection successful"
else
    fail "Cannot connect to Landing Zone at $CLUSTER_IP"
    exit 1
fi

# Test 2: Enclave Lab directory exists
info "Test 2: Checking Enclave Lab directory..."
if ssh $SSH_OPTS "$LZ_SSH" "test -d $LZ_ENCLAVE_DIR"; then
    success "Enclave Lab directory exists: $LZ_ENCLAVE_DIR"

    # Check if playbooks/main.yaml exists
    if ssh $SSH_OPTS "$LZ_SSH" "test -f $LZ_ENCLAVE_DIR/playbooks/main.yaml"; then
        success "  playbooks/main.yaml playbook found"
    else
        fail "  playbooks/main.yaml playbook not found"
    fi
else
    fail "Enclave Lab directory not found: $LZ_ENCLAVE_DIR"
    info "Run 'make install-enclave' to install Enclave Lab"
    exit 1
fi

# Test 3: Configuration files config/global.yaml and config/certificates.yaml exist
info "Test 3: Checking vars configuration..."
GLOBAL_YAML="$LZ_ENCLAVE_DIR/config/global.yaml"
CERTS_YAML="$LZ_ENCLAVE_DIR/config/certificates.yaml"

if ssh $SSH_OPTS "$LZ_SSH" "test -f $GLOBAL_YAML"; then
    success "config/global.yaml configuration file exists"

    # Validate YAML syntax
    if ssh $SSH_OPTS "$LZ_SSH" "python3 -c 'import yaml; yaml.safe_load(open(\"$GLOBAL_YAML\"))'" 2>/dev/null; then
        success "  config/global.yaml has valid YAML syntax"
    else
        warning "  config/global.yaml may have syntax errors"
    fi

    # Check key parameters
    CLUSTER_NAME_VAR=$(ssh $SSH_OPTS "$LZ_SSH" "grep '^clusterName:' $GLOBAL_YAML | awk '{print \$2}'" 2>/dev/null || echo "")
    if [ -n "$CLUSTER_NAME_VAR" ]; then
        success "  Cluster name configured: $CLUSTER_NAME_VAR"
    else
        warning "  Cluster name not found in config/global.yaml"
    fi

    BASE_DOMAIN=$(ssh $SSH_OPTS "$LZ_SSH" "grep '^baseDomain:' $GLOBAL_YAML | awk '{print \$2}'" 2>/dev/null || echo "")
    if [ -n "$BASE_DOMAIN" ]; then
        success "  Base domain configured: $BASE_DOMAIN"
    else
        warning "  Base domain not found in config/global.yaml"
    fi

    WORKER_COUNT=$(ssh $SSH_OPTS "$LZ_SSH" "grep -c '^  - name:' $GLOBAL_YAML 2>/dev/null || true")
    if [ "$WORKER_COUNT" -gt 0 ]; then
        success "  Agent hosts configured: $WORKER_COUNT nodes"
    else
        warning "  No agent hosts found in config/global.yaml"
    fi
else
    fail "config/global.yaml not found"
    info "Run 'make install-enclave' to generate configuration"
fi

if ssh $SSH_OPTS "$LZ_SSH" "test -f $CERTS_YAML"; then
    success "config/certificates.yaml configuration file exists"

    # Validate YAML syntax
    if ssh $SSH_OPTS "$LZ_SSH" "python3 -c 'import yaml; yaml.safe_load(open(\"$CERTS_YAML\"))'" 2>/dev/null; then
        success "  config/certificates.yaml has valid YAML syntax"
    else
        warning "  config/certificates.yaml may have syntax errors"
    fi
else
    fail "config/certificates.yaml not found"
    info "Run 'make install-enclave' to generate configuration"
fi

# Test 4: Required tools installed
info "Test 4: Checking required tools on Landing Zone..."
REQUIRED_TOOLS=("git" "ansible" "python3" "curl" "jq" "podman" "httpd" "nmstatectl")
for tool in "${REQUIRED_TOOLS[@]}"; do
    if ssh $SSH_OPTS "$LZ_SSH" "command -v $tool &>/dev/null"; then
        success "$tool is installed"
    else
        fail "$tool is NOT installed"
    fi
done

# Check Python libraries
if ssh $SSH_OPTS "$LZ_SSH" "python3 -c 'import jsonschema' 2>/dev/null"; then
    success "python3-jsonschema is installed"
else
    fail "python3-jsonschema is NOT installed"
fi

# Test 5: Web server setup
info "Test 5: Checking web server setup..."
if ssh $SSH_OPTS "$LZ_SSH" "sudo systemctl is-active httpd &>/dev/null"; then
    success "httpd service is running"
else
    fail "httpd service is NOT running"
fi

if ssh $SSH_OPTS "$LZ_SSH" "test -d /var/www/html"; then
    success "/var/www/html directory exists"
    # Check permissions
    PERMS=$(ssh $SSH_OPTS "$LZ_SSH" "stat -c '%U:%G %a' /var/www/html" 2>/dev/null || echo "")
    if [ -n "$PERMS" ]; then
        info "  Permissions: $PERMS"
    fi
else
    fail "/var/www/html directory does NOT exist"
fi

# Test 6: Ansible collections
info "Test 6: Checking Ansible collections..."
COLLECTIONS_MISSING=0
if ssh $SSH_OPTS "$LZ_SSH" "ansible-galaxy collection list 2>/dev/null | grep -q containers.podman"; then
    success "containers.podman collection installed"
else
    fail "containers.podman collection NOT installed"
    COLLECTIONS_MISSING=1
fi

if ssh $SSH_OPTS "$LZ_SSH" "ansible-galaxy collection list 2>/dev/null | grep -q kubernetes.core"; then
    success "kubernetes.core collection installed"
else
    fail "kubernetes.core collection NOT installed"
    COLLECTIONS_MISSING=1
fi

if ssh $SSH_OPTS "$LZ_SSH" "ansible-galaxy collection list 2>/dev/null | grep -q community.crypto"; then
    success "community.crypto collection installed"
else
    fail "community.crypto collection NOT installed"
    COLLECTIONS_MISSING=1
fi

if ssh $SSH_OPTS "$LZ_SSH" "ansible-galaxy collection list 2>/dev/null | grep -q ansible.utils"; then
    success "ansible.utils collection installed"
else
    fail "ansible.utils collection NOT installed"
    COLLECTIONS_MISSING=1
fi

if [ $COLLECTIONS_MISSING -eq 1 ]; then
    info "  Run 'make install-enclave' to install missing collections"
fi

# Test 7: SSH key
info "Test 7: Checking SSH key on Landing Zone..."
if ssh $SSH_OPTS "$LZ_SSH" "test -f ~/.ssh/id_rsa.pub"; then
    success "SSH public key exists"
    SSH_KEY=$(ssh $SSH_OPTS "$LZ_SSH" "cat ~/.ssh/id_rsa.pub" 2>/dev/null | head -c 50)
    info "  Key: ${SSH_KEY}..."
else
    warning "SSH key not found (will be generated during Enclave Lab run)"
fi

# Test 8: Pull secret
info "Test 8: Checking pull secret..."
if ssh $SSH_OPTS "$LZ_SSH" "test -f ~/config/pull-secret.json"; then
    success "Pull secret exists at ~/config/pull-secret.json"
else
    warning "Pull secret not found at ~/config/pull-secret.json"
    info "  You will need to provide a valid OpenShift pull secret before running Enclave Lab"
fi

# Test 9: Directory structure
info "Test 9: Checking directory structure..."
EXPECTED_DIRS=("defaults" "templates" "operators" "files")
for dir in "${EXPECTED_DIRS[@]}"; do
    if ssh $SSH_OPTS "$LZ_SSH" "test -d $LZ_ENCLAVE_DIR/$dir"; then
        success "  $dir/ directory exists"
    else
        fail "  $dir/ directory not found"
    fi
done

# Test 10: DNS resolution for mirror registry
info "Test 10: Checking DNS resolution for mirror registry..."
BASE_DOMAIN=$(ssh $SSH_OPTS "$LZ_SSH" "grep '^baseDomain:' $LZ_ENCLAVE_DIR/config/global.yaml | awk '{print \$2}'" 2>/dev/null || echo "")
if [ -n "$BASE_DOMAIN" ]; then
    MIRROR_HOSTNAME="mirror.${BASE_DOMAIN}"
    if ssh $SSH_OPTS "$LZ_SSH" "getent hosts ${MIRROR_HOSTNAME} >/dev/null 2>&1"; then
        MIRROR_IP=$(ssh $SSH_OPTS "$LZ_SSH" "getent hosts ${MIRROR_HOSTNAME} | awk '{print \$1}'" 2>/dev/null || echo "")
        success "${MIRROR_HOSTNAME} resolves to ${MIRROR_IP}"
    else
        fail "${MIRROR_HOSTNAME} does NOT resolve"
        info "  Run 'make install-enclave' to configure DNS resolution"
    fi
else
    warning "Cannot determine baseDomain from config/global.yaml"
fi

# Test 11: Check network connectivity from Landing Zone to BMC
info "Test 11: Testing network connectivity from Landing Zone..."
BMC_NETWORK="${PROVISIONING_NETWORK}"
BMC_GATEWAY=$(echo "$BMC_NETWORK" | sed 's|/.*||' | awk -F. '{print $1"."$2"."$3".1"}')
SUSHY_ENDPOINT="http://${BMC_GATEWAY}:8000/redfish/v1/Systems"

if ssh $SSH_OPTS "$LZ_SSH" "curl -s -o /dev/null -w '%{http_code}' --connect-timeout 5 $SUSHY_ENDPOINT" 2>/dev/null | grep -q "200"; then
    success "Can reach BMC emulation (sushy-tools) at $BMC_GATEWAY:8000"
else
    warning "Cannot reach BMC emulation from Landing Zone"
    info "  This may prevent Enclave Lab from managing cluster VMs"
fi

# Test 12: Disk space
info "Test 12: Checking available disk space..."
DISK_AVAIL=$(ssh $SSH_OPTS "$LZ_SSH" "df -h / | tail -1 | awk '{print \$4}'" 2>/dev/null)
DISK_AVAIL_GB=$(ssh $SSH_OPTS "$LZ_SSH" "df -BG / | tail -1 | awk '{print \$4}' | sed 's/G//'" 2>/dev/null)

if [ "$DISK_AVAIL_GB" -ge 400 ]; then
    success "Available disk space: $DISK_AVAIL (sufficient for image mirroring)"
elif [ "$DISK_AVAIL_GB" -ge 200 ]; then
    warning "Available disk space: $DISK_AVAIL (should be sufficient but may be tight)"
    info "  Recommended: 400GB+ free space for mirroring OpenShift images and operators"
else
    fail "Available disk space: $DISK_AVAIL (insufficient for full deployment)"
    info "  Required: 400GB+ free space (Quay storage ~130GB + catalogs ~7GB + ISOs ~3GB + buffer)"
fi

echo ""
info "========================================="
info "Verification Summary"
info "========================================="
echo ""
success "Enclave Lab is installed on Landing Zone!"
echo ""
info "Installation Details:"
info "  Location: $LZ_SSH:$LZ_ENCLAVE_DIR"
info "  Configuration vars: $LZ_ENCLAVE_DIR/config/global.yaml"
info "  Certificates vars: $LZ_ENCLAVE_DIR/config/certificates.yaml"
info "  SSH Access: ssh $LZ_SSH"
echo ""
info "Before running Enclave Lab:"
info "  1. Review configuration vars: ssh $LZ_SSH 'cat $LZ_ENCLAVE_DIR/config/global.yaml'"
info "  2. Update pull secret: ssh $LZ_SSH 'vi ~/config/pull-secret.json'"
info "  3. Adjust any other settings in config/global.yaml and config/certificates.yaml as needed"
echo ""
info "To run Enclave Lab:"
info "  ssh $LZ_SSH"
info "  cd $LZ_ENCLAVE_DIR"
info "  ansible-playbook -e@config/global.yaml playbooks/main.yaml"
echo ""

# Exit with failure if any tests failed
if [ $VERIFICATION_FAILED -eq 1 ]; then
    echo ""
    error "========================================="
    error "Verification FAILED - Issues Detected"
    error "========================================="
    error "Please review the failures above and run 'make install-enclave' to fix missing dependencies"
    exit 1
fi
