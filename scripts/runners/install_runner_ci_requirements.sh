#!/bin/bash
# Install CI Prerequisites for GitHub Actions Runner
# This script installs all required packages and tools for running Enclave Lab CI workflows

set -euo pipefail

# Colors
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

info() {
    echo -e "${BLUE}INFO:${NC} $1"
}

success() {
    echo -e "${GREEN}✅${NC} $1"
}

error() {
    echo -e "${RED}❌${NC} $1"
}

warning() {
    echo -e "${YELLOW}⚠️${NC} $1"
}

# Check if running as github-runner user or root
if [ "$USER" != "github-runner" ] && [ "$USER" != "root" ]; then
    error "This script should be run as github-runner user or root"
    exit 1
fi

info "=========================================="
info "Enclave Lab CI Prerequisites Installation"
info "=========================================="
echo ""

# Step 0: Check system resources
info "Step 0: Checking system resources"
AVAILABLE_DISK=$(df -BG /opt 2>/dev/null | awk 'NR==2 {print $4}' | sed 's/G//' || echo "0")
TOTAL_RAM=$(free -g | awk 'NR==2 {print $2}')
CPU_CORES=$(nproc)

if [ "$AVAILABLE_DISK" -lt 200 ]; then
    warning "Low disk space: ${AVAILABLE_DISK}GB available in /opt (200GB+ recommended)"
else
    success "Disk space: ${AVAILABLE_DISK}GB available in /opt"
fi

if [ "$TOTAL_RAM" -lt 32 ]; then
    warning "Low RAM: ${TOTAL_RAM}GB available (32GB+ recommended for full CI workflows)"
else
    success "RAM: ${TOTAL_RAM}GB available"
fi

if [ "$CPU_CORES" -lt 8 ]; then
    warning "Low CPU cores: ${CPU_CORES} cores (8+ recommended)"
else
    success "CPU cores: ${CPU_CORES} available"
fi
echo ""

# Detect OS
if [ -f /etc/os-release ]; then
    # shellcheck source=/dev/null
    . /etc/os-release
    OS_ID=$ID
else
    error "Cannot detect OS. /etc/os-release not found"
    exit 1
fi

info "Detected OS: $NAME $VERSION_ID"
echo ""

# Check if CentOS/RHEL/Fedora
if [[ ! "$OS_ID" =~ ^(centos|rhel|fedora|almalinux|rocky)$ ]]; then
    error "This script only supports CentOS, RHEL, Fedora, AlmaLinux, and Rocky Linux"
    exit 1
fi

info "Step 1: Updating system packages"
sudo dnf update -y || true
success "System packages updated"
echo ""

info "Step 2: Installing core system tools"
sudo dnf install -y \
    git \
    curl \
    wget \
    vim \
    make \
    tar \
    unzip \
    bash-completion
success "Core system tools installed"
echo ""

info "Step 3: Installing EPEL repository (for additional packages)"
sudo dnf install -y epel-release || {
    warning "EPEL repository not available, will install validation tools via pip"
}
success "EPEL repository configured"
echo ""

info "Step 4: Installing validation tools"
# Try to install shellcheck from repos first
if sudo dnf install -y shellcheck 2>/dev/null; then
    success "shellcheck installed from repository"
else
    warning "shellcheck not available in repos, installing via binary..."
    # Install shellcheck from GitHub releases
    SHELLCHECK_VERSION="v0.10.0"
    wget -qO- "https://github.com/koalaman/shellcheck/releases/download/${SHELLCHECK_VERSION}/shellcheck-${SHELLCHECK_VERSION}.linux.x86_64.tar.xz" | tar -xJv
    sudo cp "shellcheck-${SHELLCHECK_VERSION}/shellcheck" /usr/local/bin/
    rm -rf "shellcheck-${SHELLCHECK_VERSION}"
    success "shellcheck installed from binary"
fi
success "Validation tools installed (shellcheck)"
echo ""

info "Step 5: Installing Python and pip"
sudo dnf install -y python3
sudo dnf install -y python3-pip
sudo dnf install -y sysstat

# Verify pip is now available
if command -v pip3 &>/dev/null; then
    success "Python and pip installed ($(python3 --version), $(pip3 --version))"

    # Install yamllint via pip (now that pip3 is verified)
    sudo pip3 install yamllint
    success "yamllint installed via pip"
else
    error "Failed to install pip"
    exit 1
fi

# Try to install python packages from repos
sudo dnf install -y python3-pyyaml 2>/dev/null || sudo pip3 install pyyaml
sudo dnf install -y python3-kubernetes 2>/dev/null || warning "python3-kubernetes not in repos, will install via pip later"

echo ""

info "Step 5a: Installing Ansible"
# Try ansible-core first (newer), then ansible (older), then pip
if sudo dnf install -y ansible-core 2>/dev/null; then
    success "Ansible (ansible-core) installed from repository"
elif sudo dnf install -y ansible 2>/dev/null; then
    success "Ansible installed from repository"
else
    warning "Ansible not available in repos, installing via pip..."
    sudo pip3 install ansible-core
    success "Ansible installed via pip"
fi
echo ""

info "Step 6: Installing Python packages from requirements"
if [ -f "ansible_pip_requirements.txt" ]; then
    info "  Installing Python packages from ansible_pip_requirements.txt..."
    sudo pip3 install -r ansible_pip_requirements.txt
    success "Python packages installed from requirements file"
else
    warning "ansible_pip_requirements.txt not found, installing packages manually..."
    sudo pip3 install \
        kubernetes \
        jsonschema \
        rpds-py
    success "Python packages installed manually"
fi
echo ""

info "Step 7: Installing Ansible collections"
if [ -f "ansible_collections.txt" ]; then
    info "  Installing from ansible_collections.txt..."
    ansible-galaxy collection install -r ansible_collections.txt --force
else
    info "  Installing required collections manually..."
    ansible-galaxy collection install \
        community.crypto \
        containers.podman \
        kubernetes.core \
        community.general \
        ansible.utils \
        --force
fi
success "Ansible collections installed"
echo ""

info "Step 8: Installing ansible-lint"
sudo pip3 install ansible-lint
success "ansible-lint installed"
echo ""

info "Step 9: Installing libvirt and KVM"
sudo dnf install -y \
    libvirt \
    libvirt-client \
    libvirt-daemon \
    libvirt-daemon-kvm \
    libvirt-nss \
    qemu-kvm \
    virt-install \
    dnsmasq
success "Libvirt and KVM installed (with NSS and dnsmasq)"
echo ""

info "Step 10: Installing networking tools"
sudo dnf install -y \
    firewalld \
    iptables \
    iproute \
    iputils \
    bind-utils \
    NetworkManager \
    nmstate
success "Networking tools installed"
echo ""

info "Step 11: Installing container tools"
sudo dnf install -y \
    podman \
    podman-docker \
    buildah \
    skopeo
success "Container tools installed (podman, buildah, skopeo)"
echo ""

info "Step 12: Installing utilities"
sudo dnf install -y \
    jq \
    rsync \
    openssh-clients \
    sshpass \
    httpd \
    genisoimage
success "Utilities installed (jq, rsync, ssh, httpd, genisoimage)"
echo ""

info "Step 12a: Installing GitHub CLI"
# Install dnf config-manager plugin if not present
sudo dnf install -y 'dnf-command(config-manager)' 2>/dev/null || true

# Add GitHub CLI repository
if ! sudo dnf config-manager --add-repo https://cli.github.com/packages/rpm/gh-cli.repo 2>/dev/null; then
    # Fallback: manually create repo file
    sudo tee /etc/yum.repos.d/gh-cli.repo > /dev/null <<EOF
[gh-cli]
name=packages by GitHub CLI
baseurl=https://cli.github.com/packages/rpm
enabled=1
gpgkey=https://keyserver.ubuntu.com/pks/lookup?op=get&search=0x23F3D4EA75716059
EOF
fi

# Install GitHub CLI
sudo dnf install -y gh
success "GitHub CLI installed"
echo ""

info "Step 13: Starting and enabling services"

# Start and enable libvirtd
info "  Starting libvirtd service..."
sudo systemctl enable --now libvirtd
sudo systemctl status libvirtd --no-pager || true
success "libvirtd service started and enabled"

# Start and enable firewalld
info "  Starting firewalld service..."
sudo systemctl enable --now firewalld
sudo systemctl status firewalld --no-pager || true
success "firewalld service started and enabled"

echo ""

info "Step 14: Configuring user permissions"

# Add github-runner to libvirt and qemu groups
if id github-runner &>/dev/null; then
    info "  Adding github-runner to libvirt and qemu groups..."
    sudo usermod -aG libvirt github-runner || true
    sudo usermod -aG qemu github-runner || true
    success "github-runner added to libvirt and qemu groups"
else
    warning "github-runner user not found, skipping group configuration"
fi

echo ""

info "Step 14a: Configuring rootless podman for github-runner"
if id github-runner &>/dev/null; then
    RUNNER_UID=$(id -u github-runner)
    RUNNER_HOME="/home/github-runner"

    info "  Enabling linger for github-runner user..."
    # Enable linger so user services persist after logout
    sudo loginctl enable-linger github-runner 2>/dev/null || true
    success "Linger enabled"

    info "  Enabling podman socket for github-runner user..."
    # Enable and start podman socket as github-runner user
    sudo -u github-runner XDG_RUNTIME_DIR=/run/user/${RUNNER_UID} systemctl --user enable podman.socket 2>/dev/null || true
    sudo -u github-runner XDG_RUNTIME_DIR=/run/user/${RUNNER_UID} systemctl --user start podman.socket 2>/dev/null || true

    # Wait a moment for socket to be created
    sleep 2

    PODMAN_SOCKET_PATH="/run/user/${RUNNER_UID}/podman/podman.sock"
    if [ -S "$PODMAN_SOCKET_PATH" ]; then
        success "Podman socket created at $PODMAN_SOCKET_PATH"
    else
        warning "Podman socket not found yet at $PODMAN_SOCKET_PATH"
        info "  Socket will be created on first podman command"
    fi

    # Configure environment for GitHub Actions runner
    info "  Configuring environment for GitHub Actions..."

    # Create .bash_profile if it doesn't exist
    if [ ! -f "$RUNNER_HOME/.bash_profile" ]; then
        sudo -u github-runner touch "$RUNNER_HOME/.bash_profile"
    fi

    # Add podman configuration to .bash_profile (sourced by non-interactive shells like systemd services)
    if ! grep -q "DOCKER_HOST.*podman" "$RUNNER_HOME/.bash_profile" 2>/dev/null; then
        sudo -u github-runner tee -a "$RUNNER_HOME/.bash_profile" > /dev/null <<EOF

# Podman configuration for GitHub Actions (use podman instead of docker)
export DOCKER_HOST=unix:///run/user/${RUNNER_UID}/podman/podman.sock
export XDG_RUNTIME_DIR=/run/user/${RUNNER_UID}
EOF
        success "Environment configured in .bash_profile"
    else
        info "  Environment already configured in .bash_profile"
    fi

    # Also add to .bashrc for interactive shells
    if [ -f "$RUNNER_HOME/.bashrc" ]; then
        if ! grep -q "DOCKER_HOST.*podman" "$RUNNER_HOME/.bashrc" 2>/dev/null; then
            sudo -u github-runner tee -a "$RUNNER_HOME/.bashrc" > /dev/null <<EOF

# Podman configuration for GitHub Actions (use podman instead of docker)
export DOCKER_HOST=unix:///run/user/${RUNNER_UID}/podman/podman.sock
export XDG_RUNTIME_DIR=/run/user/${RUNNER_UID}
EOF
        fi
    fi

    # Create systemd override directory for runner services
    SYSTEMD_OVERRIDE_DIR="/etc/systemd/system"
    info "  Creating systemd environment file for runner services..."

    # Create environment file that will be used by runner services
    sudo tee /etc/systemd/system/github-runner-env.conf > /dev/null <<EOF
[Service]
Environment="DOCKER_HOST=unix:///run/user/${RUNNER_UID}/podman/podman.sock"
Environment="XDG_RUNTIME_DIR=/run/user/${RUNNER_UID}"
EOF
    success "Systemd environment file created"

    info "  Note: Apply this to runner services with:"
    info "    sudo systemctl edit actions.runner.*.service"
    info "    Add: .include /etc/systemd/system/github-runner-env.conf"

else
    warning "github-runner user not found, skipping podman socket configuration"
fi

echo ""

info "Step 15: Installing dev-scripts dependencies (optional)"
# Check if dev-scripts directory exists
DEV_SCRIPTS_PATHS=(
    "/home/github-runner/go/src/github.com/openshift-metal3/dev-scripts"
    "${HOME}/go/src/github.com/openshift-metal3/dev-scripts"
)

DEV_SCRIPTS_FOUND=false
for path in "${DEV_SCRIPTS_PATHS[@]}"; do
    if [ -d "$path" ]; then
        DEV_SCRIPTS_FOUND=true
        break
    fi
done

if [ "$DEV_SCRIPTS_FOUND" = true ]; then
    info "  dev-scripts directory found, installing dependencies..."
    sudo dnf install -y \
        golang \
        libvirt-devel
    success "dev-scripts dependencies installed (golang, libvirt-devel)"
else
    info "  dev-scripts directory not found, skipping optional dependencies"
fi
echo ""

info "Step 16: Cleaning up package cache"
sudo dnf clean all
success "Package cache cleaned"
echo ""

info "Step 17: Verifying installations"
echo ""

# Function to check if command exists
check_cmd() {
    if command -v "$1" &>/dev/null; then
        success "$1: $(command -v $1)"
        return 0
    else
        error "$1: NOT FOUND"
        return 1
    fi
}

FAILED=0

# Check all required commands
check_cmd git || FAILED=1
check_cmd make || FAILED=1
check_cmd python3 || FAILED=1
check_cmd ansible || FAILED=1
check_cmd ansible-playbook || FAILED=1
check_cmd ansible-lint || FAILED=1
check_cmd ansible-galaxy || FAILED=1
check_cmd shellcheck || FAILED=1
check_cmd yamllint || FAILED=1
check_cmd jq || FAILED=1
check_cmd gh || FAILED=1
check_cmd virsh || FAILED=1
check_cmd virt-install || FAILED=1
check_cmd podman || FAILED=1
check_cmd nmstatectl || FAILED=1
check_cmd genisoimage || FAILED=1
check_cmd dnsmasq || FAILED=1

echo ""

# Check Python modules
info "Checking Python modules..."
python3 -c "import kubernetes" && success "Python: kubernetes module" || { error "Python: kubernetes module NOT FOUND"; FAILED=1; }
python3 -c "import jsonschema" && success "Python: jsonschema module" || { error "Python: jsonschema module NOT FOUND"; FAILED=1; }
python3 -c "import yaml" && success "Python: yaml module" || { error "Python: yaml module NOT FOUND"; FAILED=1; }

echo ""

# Check Ansible collections
info "Checking Ansible collections..."
ansible-galaxy collection list | grep -q "community.crypto" && success "Ansible: community.crypto" || { error "Ansible: community.crypto NOT FOUND"; FAILED=1; }
ansible-galaxy collection list | grep -q "containers.podman" && success "Ansible: containers.podman" || { error "Ansible: containers.podman NOT FOUND"; FAILED=1; }
ansible-galaxy collection list | grep -q "kubernetes.core" && success "Ansible: kubernetes.core" || { error "Ansible: kubernetes.core NOT FOUND"; FAILED=1; }

echo ""

# Check services
info "Checking services..."
sudo systemctl is-active --quiet libvirtd && success "Service: libvirtd is running" || { error "Service: libvirtd is NOT running"; FAILED=1; }
sudo systemctl is-active --quiet firewalld && success "Service: firewalld is running" || { error "Service: firewalld is NOT running"; FAILED=1; }

echo ""

if [ $FAILED -eq 0 ]; then
    echo -e "${GREEN}=========================================="
    echo -e "✅ All prerequisites installed successfully!"
    echo -e "==========================================${NC}"
    echo ""
    info "Next steps:"
    echo ""
    echo "  1. For github-runner user, configure runner services with podman environment:"
    echo "     For each runner service file, add environment variables:"
    echo ""
    echo "     Create override: sudo systemctl edit actions.runner.*.service"
    echo "     Add these lines in the [Service] section:"
    echo "       Environment=\"DOCKER_HOST=unix:///run/user/$(id -u github-runner)/podman/podman.sock\""
    echo "       Environment=\"XDG_RUNTIME_DIR=/run/user/$(id -u github-runner)\""
    echo ""
    echo "  2. Restart GitHub Actions runner services:"
    echo "     sudo systemctl daemon-reload"
    echo "     sudo systemctl restart actions.runner.*.service"
    echo ""
    echo "  3. Verify libvirt access (as github-runner user, NOT with sudo):"
    echo "     virsh list --all"
    echo ""
    echo "  4. Verify podman works (as github-runner user):"
    echo "     podman ps"
    echo "     podman run --rm hello-world"
    echo ""
    exit 0
else
    echo -e "${RED}=========================================="
    echo -e "❌ Some prerequisites failed to install"
    echo -e "==========================================${NC}"
    echo ""
    error "Please check the errors above and fix them manually"
    exit 1
fi

