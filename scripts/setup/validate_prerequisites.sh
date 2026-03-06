#!/bin/bash
# Validate prerequisites for environment creation
#
# This script checks that all required dependencies are installed
# and configured before creating the test infrastructure.

set -euo pipefail

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m' # No Color

# Configuration
DEV_SCRIPTS_PATH="${DEV_SCRIPTS_PATH:-}"
MIN_RAM_GB=48
MIN_DISK_GB=200

# Track validation status
VALIDATION_FAILED=0

# Helper functions
error() {
    echo -e "${RED}✗ $1${NC}"
    VALIDATION_FAILED=1
}

success() {
    echo -e "${GREEN}✓ $1${NC}"
}

warning() {
    echo -e "${YELLOW}⚠ $1${NC}"
}

info() {
    echo "  $1"
}

# Check if command exists
check_command() {
    local cmd=$1
    local package=${2:-$1}

    if command -v "$cmd" &> /dev/null; then
        success "$cmd is installed"
        return 0
    else
        error "$cmd is not installed (install: $package)"
        return 1
    fi
}

# Check if systemd service is running
check_service() {
    local service=$1

    if systemctl is-active --quiet "$service"; then
        success "$service is running"
        return 0
    else
        error "$service is not running (run: sudo systemctl start $service)"
        return 1
    fi
}

echo "Validating prerequisites for environment creation..."
echo ""

# 0. Check if DEV_SCRIPTS_PATH is set
if [ -z "$DEV_SCRIPTS_PATH" ]; then
    error "DEV_SCRIPTS_PATH environment variable is not set"
    echo ""
    echo "Please set DEV_SCRIPTS_PATH to your dev-scripts installation:"
    echo "  export DEV_SCRIPTS_PATH=/path/to/dev-scripts"
    echo ""
    echo "Example:"
    echo "  export DEV_SCRIPTS_PATH=/path/to/dev-scripts"
    echo "  ./scripts/validate_prerequisites.sh"
    echo ""
    exit 1
fi

# 1. Check dev-scripts installation
echo "1. Checking dev-scripts installation..."
if [ -d "$DEV_SCRIPTS_PATH" ]; then
    success "dev-scripts found at $DEV_SCRIPTS_PATH"

    # Check for infra_only target in Makefile
    if grep -q "^infra_only:" "$DEV_SCRIPTS_PATH/Makefile" 2>/dev/null; then
        success "dev-scripts has infra_only target"
    else
        error "dev-scripts Makefile missing 'infra_only' target"
        info "Please ensure you have dev-scripts with infra_only support"
        info "Clone or update dev-scripts:"
        info "  git clone https://github.com/openshift-metal3/dev-scripts.git $DEV_SCRIPTS_PATH"
    fi
else
    error "dev-scripts not found at $DEV_SCRIPTS_PATH"
    info "Clone dev-scripts:"
    info "  git clone https://github.com/openshift-metal3/dev-scripts.git $DEV_SCRIPTS_PATH"
fi
echo ""

# 2. Check required commands
echo "2. Checking required commands..."
check_command "virsh" "libvirt-client"
check_command "virt-install" "virt-install"
check_command "ansible" "ansible"
check_command "jq" "jq"
check_command "git" "git"
check_command "make" "make"
echo ""

# 3. Check libvirt service
echo "3. Checking libvirt services..."
# Test if virsh commands work (more reliable than checking specific services)
if virsh version &> /dev/null; then
    success "libvirt is functional (virsh commands work)"

    # Additional info: show which service model is in use
    if systemctl list-unit-files | grep -q "virtqemud.socket"; then
        info "Using modular libvirt (virtqemud.socket)"
    elif systemctl is-active --quiet libvirtd 2>/dev/null; then
        info "Using monolithic libvirt (libvirtd.service)"
    fi
else
    error "libvirt is not functional (virsh commands fail)"
    info "Start libvirt services:"
    if systemctl list-unit-files | grep -q "virtqemud.socket"; then
        info "  sudo systemctl enable --now virtqemud.socket"
    else
        info "  sudo systemctl enable --now libvirtd"
    fi
fi
echo ""

# 4. Check user permissions
echo "4. Checking user permissions..."
if groups | grep -q libvirt; then
    success "User is in libvirt group"
else
    error "User is not in libvirt group"
    info "Add user to libvirt group:"
    info "  sudo usermod -a -G libvirt \$USER"
    info "  newgrp libvirt  # or logout and login again"
fi

# Check passwordless sudo
if sudo -n true 2>/dev/null; then
    success "Passwordless sudo is configured"
else
    warning "Passwordless sudo not configured (may require password during setup)"
    info "To enable passwordless sudo:"
    info "  echo \"\$USER  ALL=(ALL) NOPASSWD: ALL\" | sudo tee /etc/sudoers.d/\$USER"
fi
echo ""

# 5. Check system resources
echo "5. Checking system resources..."

# Check RAM
TOTAL_RAM_KB=$(grep MemTotal /proc/meminfo | awk '{print $2}')
TOTAL_RAM_GB=$((TOTAL_RAM_KB / 1024 / 1024))
if [ "$TOTAL_RAM_GB" -ge "$MIN_RAM_GB" ]; then
    success "Sufficient RAM: ${TOTAL_RAM_GB}GB (minimum: ${MIN_RAM_GB}GB)"
else
    warning "Low RAM: ${TOTAL_RAM_GB}GB (recommended: ${MIN_RAM_GB}GB+)"
    info "Environment requires ~48GB RAM (3 masters x 16GB + Landing Zone 8GB)"
    info "You may need to reduce VM specs in configuration"
fi

# Check disk space on /opt (where WORKING_DIR is)
if [ -d "/opt" ]; then
    AVAILABLE_GB=$(df -BG /opt | tail -1 | awk '{print $4}' | sed 's/G//')
    if [ "$AVAILABLE_GB" -ge "$MIN_DISK_GB" ]; then
        success "Sufficient disk space on /opt: ${AVAILABLE_GB}GB (minimum: ${MIN_DISK_GB}GB)"
    else
        warning "Low disk space on /opt: ${AVAILABLE_GB}GB (recommended: ${MIN_DISK_GB}GB+)"
        info "Environment requires ~200GB (3 masters x 120GB + Landing Zone 60GB)"
    fi
else
    warning "/opt directory does not exist (will be created)"
fi
echo ""

# 6. Check network configuration
echo "6. Checking network configuration..."
if ip link show virbr0 &> /dev/null; then
    success "Default libvirt network (virbr0) exists"
else
    info "Default libvirt network not found (will be created by dev-scripts)"
fi

# Check if firewalld is running (required by dev-scripts)
if command -v firewalld &> /dev/null; then
    if systemctl is-active --quiet firewalld; then
        success "firewalld is running"
    else
        warning "firewalld is installed but not running"
        info "dev-scripts will start it automatically"
    fi
else
    warning "firewalld is not installed"
    info "dev-scripts requires firewalld for network setup"
fi
echo ""

# Summary
echo "=========================================="
if [ $VALIDATION_FAILED -eq 0 ]; then
    echo -e "${GREEN}✅ All prerequisites validated successfully${NC}"
    echo ""
    echo "Ready to create environment!"
    exit 0
else
    echo -e "${RED}❌ Validation failed - please fix errors above${NC}"
    echo ""
    echo "Common fixes:"
    echo "  - Install missing packages: sudo dnf install -y libvirt virt-install ansible jq git"
    echo "  - Start libvirt: sudo systemctl start libvirtd"
    echo "  - Add user to libvirt group: sudo usermod -a -G libvirt \$USER && newgrp libvirt"
    echo "  - Clone dev-scripts: git clone https://github.com/openshift-metal3/dev-scripts.git $DEV_SCRIPTS_PATH"
    echo ""
    exit 1
fi
