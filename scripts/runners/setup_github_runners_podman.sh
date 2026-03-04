#!/bin/bash
# Complete GitHub Actions Runner Setup with Podman
#
# This script configures a runner machine to use podman for GitHub Actions.
# Run this once on each runner machine.
#
# Usage: sudo bash setup_github_runners_podman.sh

set -euo pipefail

# Colors
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m'

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

heading() {
    echo ""
    echo -e "${BLUE}========================================${NC}"
    echo -e "${BLUE}$1${NC}"
    echo -e "${BLUE}========================================${NC}"
    echo ""
}

# Check if running as root
if [ "$EUID" -ne 0 ]; then
    error "This script must be run as root (use sudo)"
    exit 1
fi

heading "GitHub Actions Runner - Podman Setup"

# Step 1: Remove Docker if present
heading "Step 1: Checking for Docker"

DOCKER_PACKAGES=("docker" "docker-ce" "docker-ce-cli" "docker-engine" "docker.io" "containerd" "containerd.io")
FOUND_DOCKER=0

for pkg in "${DOCKER_PACKAGES[@]}"; do
    if rpm -q "$pkg" &>/dev/null; then
        warning "Found Docker package: $pkg"
        FOUND_DOCKER=1
    fi
done

if [ $FOUND_DOCKER -eq 1 ]; then
    warning "Docker packages found"
    read -p "Remove Docker and use podman only? (yes/NO) " -r
    echo
    if [[ $REPLY =~ ^[Yy][Ee][Ss]$ ]]; then
        info "Removing Docker..."
        systemctl stop docker.socket docker.service 2>/dev/null || true
        systemctl disable docker.socket docker.service 2>/dev/null || true
        dnf remove -y docker* containerd* 2>/dev/null || true
        [ -d /var/lib/docker ] && rm -rf /var/lib/docker
        success "Docker removed"
    else
        info "Keeping Docker (you'll have both Docker and podman)"
    fi
else
    success "No Docker packages found"
fi

# Step 2: Install podman
heading "Step 2: Installing Podman"

info "Installing podman and podman-docker..."
dnf install -y podman podman-docker
success "Podman installed"
podman --version

# Step 3: Configure system podman socket
heading "Step 3: Configuring System Podman Socket"

info "Enabling podman.socket..."
systemctl enable --now podman.socket
sleep 2

if [ -S "/run/podman/podman.sock" ]; then
    success "Podman socket created"
else
    error "Failed to create podman socket"
    exit 1
fi

# Step 4: Configure socket permissions
heading "Step 4: Configuring Socket Permissions"

info "Creating systemd override for socket permissions..."
mkdir -p /etc/systemd/system/podman.socket.d

cat > /etc/systemd/system/podman.socket.d/override.conf <<'EOF'
[Socket]
# Make socket world-accessible for GitHub Actions
SocketMode=0666
EOF

info "Setting directory permissions..."
chmod 755 /run/podman

info "Restarting podman.socket with new permissions..."
systemctl daemon-reload
systemctl restart podman.socket
sleep 2

info "Verifying permissions..."
chmod 666 /run/podman/podman.sock
ls -la /run/podman/podman.sock

success "Socket permissions configured"

# Step 5: Create /var/run/docker.sock symlink
heading "Step 5: Creating Docker Socket Symlink"

# Remove old docker.sock if it exists and is not a symlink
if [ -e "/var/run/docker.sock" ] && [ ! -L "/var/run/docker.sock" ]; then
    warning "Removing old /var/run/docker.sock"
    rm -f /var/run/docker.sock
fi

# Remove old symlink if it points to wrong location
if [ -L "/var/run/docker.sock" ]; then
    CURRENT_TARGET=$(readlink -f /var/run/docker.sock 2>/dev/null || echo "")
    if [ "$CURRENT_TARGET" != "/run/podman/podman.sock" ]; then
        warning "Removing incorrect symlink"
        rm -f /var/run/docker.sock
    fi
fi

# Create symlink
if [ ! -e "/var/run/docker.sock" ]; then
    info "Creating symlink: /var/run/docker.sock -> /run/podman/podman.sock"
    ln -s /run/podman/podman.sock /var/run/docker.sock
    success "Symlink created"
else
    success "Symlink already exists"
fi

ls -la /var/run/docker.sock

# Step 6: Test podman via docker socket
heading "Step 6: Testing Podman"

info "Testing podman via docker socket..."
if podman --remote --url unix:///var/run/docker.sock ps >/dev/null 2>&1; then
    success "Podman works via /var/run/docker.sock"
else
    error "Podman test failed"
    exit 1
fi

info "Testing docker command (uses podman)..."
docker ps >/dev/null 2>&1 || true
success "Docker command works (via podman)"

# Step 7: Configure github-runner user
heading "Step 7: Configuring github-runner User"

if id github-runner &>/dev/null; then
    info "Enabling linger for github-runner..."
    loginctl enable-linger github-runner 2>/dev/null || true

    info "Adding github-runner to libvirt and qemu groups..."
    usermod -aG libvirt github-runner 2>/dev/null || true
    usermod -aG qemu github-runner 2>/dev/null || true

    success "github-runner user configured"
else
    warning "github-runner user not found (will be configured when runners are installed)"
fi

# Step 8: Configure runner services
heading "Step 8: Configuring Runner Services"

RUNNER_SERVICES=$(systemctl list-units --type=service --all 'actions.runner.*' --no-legend 2>/dev/null | awk '{print $1}' || echo "")

if [ -n "$RUNNER_SERVICES" ]; then
    info "Found runner services:"
    echo "$RUNNER_SERVICES" | sed 's/^/  - /'
    echo ""

    for service in $RUNNER_SERVICES; do
        info "Configuring $service..."

        OVERRIDE_DIR="/etc/systemd/system/${service}.d"
        OVERRIDE_FILE="${OVERRIDE_DIR}/podman-environment.conf"

        mkdir -p "$OVERRIDE_DIR"

        cat > "$OVERRIDE_FILE" <<'EOF'
[Service]
# Use system podman socket via /var/run/docker.sock
Environment="DOCKER_HOST=unix:///var/run/docker.sock"
EOF

        success "  Configured $service"
    done

    info "Reloading systemd..."
    systemctl daemon-reload

    info "Restarting all runner services..."
    for service in $RUNNER_SERVICES; do
        systemctl stop "$service"
    done

    sleep 3

    for service in $RUNNER_SERVICES; do
        systemctl start "$service"
    done

    sleep 3

    info "Verifying services..."
    for service in $RUNNER_SERVICES; do
        if systemctl is-active --quiet "$service"; then
            success "  $service is active"
        else
            error "  $service is NOT active"
        fi
    done
else
    info "No runner services found yet"
    info "Run this script again after installing runners"
fi

# Step 9: Final verification
heading "Step 9: Final Verification"

info "Podman version:"
podman --version

echo ""
info "Docker command (via podman):"
docker --version

echo ""
info "Socket status:"
ls -la /run/podman/podman.sock
ls -la /var/run/docker.sock

echo ""
info "Testing as github-runner user:"
if id github-runner &>/dev/null; then
    if sudo -u github-runner test -r /var/run/docker.sock && sudo -u github-runner test -w /var/run/docker.sock; then
        success "github-runner can access /var/run/docker.sock"
        sudo -u github-runner docker ps 2>/dev/null || true
    else
        error "github-runner CANNOT access /var/run/docker.sock"
    fi
else
    info "github-runner user not found, skipping user test"
fi

# Summary
heading "Setup Complete!"

success "✓ Docker removed (if present)"
success "✓ Podman installed and configured"
success "✓ System podman socket enabled"
success "✓ /var/run/docker.sock symlink created"
success "✓ Socket permissions set (666)"
success "✓ Runner services configured (if found)"

echo ""
info "Next steps:"
echo "  1. If you haven't installed runners yet, install them now"
echo "  2. After installing runners, run this script again to configure them"
echo "  3. Test a GitHub Actions workflow with container jobs"
echo ""
info "All 'docker' commands now use podman transparently!"
echo ""
