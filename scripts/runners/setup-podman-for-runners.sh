#!/bin/bash

# Setup Podman for GitHub Actions Runners
# Configures Podman with Docker socket compatibility for GitHub Actions

set -euo pipefail

RESETS="\e[0m"
BOLD="\e[1m"
GREEN="\e[32m"
RED="\e[31m"
YELLOW="\e[33m"

echo -e "${BOLD}Setting up Podman for GitHub Actions Runners${RESETS}"
echo ""

# 1. Check if Podman is installed
if ! command -v podman &> /dev/null; then
    echo -e "${RED}Podman is not installed!${RESETS}"
    echo "Install Podman first:"
    echo "  sudo dnf install -y podman podman-docker"
    exit 1
fi

echo -e "${GREEN}✓ Podman is installed: $(podman --version)${RESETS}"

# 2. Install podman-docker if not present (provides Docker CLI compatibility)
if ! rpm -q podman-docker &> /dev/null; then
    echo -e "${YELLOW}Installing podman-docker for Docker CLI compatibility...${RESETS}"
    sudo dnf install -y podman-docker
    echo -e "${GREEN}✓ podman-docker installed${RESETS}"
else
    echo -e "${GREEN}✓ podman-docker is installed${RESETS}"
fi

# 3. Enable and start podman socket (system-wide for all users)
echo -e "${YELLOW}Enabling Podman socket...${RESETS}"
sudo systemctl enable --now podman.socket

if sudo systemctl is-active --quiet podman.socket; then
    echo -e "${GREEN}✓ Podman socket is running${RESETS}"
else
    echo -e "${RED}✗ Failed to start podman socket${RESETS}"
    sudo systemctl status podman.socket
    exit 1
fi

# 4. Create Docker socket symlink (GitHub Actions expects this)
echo -e "${YELLOW}Creating Docker socket compatibility...${RESETS}"
if [ ! -e /var/run/docker.sock ]; then
    sudo ln -sf /run/podman/podman.sock /var/run/docker.sock
    echo -e "${GREEN}✓ Docker socket symlink created${RESETS}"
else
    echo -e "${GREEN}✓ Docker socket already exists${RESETS}"
fi

# 5. Test docker command
echo -e "${YELLOW}Testing docker command...${RESETS}"
if docker --version 2>/dev/null | grep -q podman; then
    echo -e "${GREEN}✓ Docker command works (using Podman): $(docker --version)${RESETS}"
else
    echo -e "${YELLOW}⚠ Docker command may not be using Podman${RESETS}"
fi

# 6. Pull ubuntu:22.04 image
echo -e "${YELLOW}Pulling ubuntu:22.04 image...${RESETS}"
if sudo podman pull ubuntu:22.04; then
    echo -e "${GREEN}✓ Ubuntu image downloaded${RESETS}"
else
    echo -e "${RED}✗ Failed to pull Ubuntu image${RESETS}"
    exit 1
fi

# 7. Configure permissions for runner user
echo -e "${YELLOW}Setting up permissions for $(whoami)...${RESETS}"

# Ensure runner user can access the socket
SOCKET_DIR="/run/podman"
if [ -d "$SOCKET_DIR" ]; then
    sudo chmod 777 "$SOCKET_DIR"
    if [ -S "$SOCKET_DIR/podman.sock" ]; then
        sudo chmod 666 "$SOCKET_DIR/podman.sock"
    fi
    echo -e "${GREEN}✓ Socket permissions configured${RESETS}"
fi

# 8. Test as current user
echo -e "${YELLOW}Testing Podman access...${RESETS}"
if docker ps &> /dev/null; then
    echo -e "${GREEN}✓ Current user can access Podman${RESETS}"
else
    echo -e "${YELLOW}⚠ May need to restart runner services${RESETS}"
fi

# 9. Restart runner services to pick up new configuration
echo -e "${YELLOW}Restarting runner services...${RESETS}"
sudo systemctl restart 'actions.runner.rh-ecosystem-edge-enclave.pr-validation-*.service'
echo -e "${GREEN}✓ Runner services restarted${RESETS}"

echo ""
echo -e "${GREEN}${BOLD}========================================${RESETS}"
echo -e "${GREEN}${BOLD}Podman setup completed!${RESETS}"
echo -e "${GREEN}${BOLD}========================================${RESETS}"
echo ""
echo "Configuration:"
echo "- Podman socket: sudo systemctl status podman.socket"
echo "- Docker socket: /var/run/docker.sock -> /run/podman/podman.sock"
echo "- Docker CLI: provided by podman-docker package"
echo ""
echo "Verify setup:"
echo "  docker --version"
echo "  docker ps"
echo "  docker run --rm ubuntu:22.04 echo 'Podman works!'"
echo ""
echo "Runner services have been restarted and should now work with Podman."
