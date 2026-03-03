#!/bin/bash

# GitHub Actions Runner Setup Script
# Sets up multiple self-hosted runners for parallel PR validation
#
# USAGE:
#   1. Get a registration token from GitHub:
#      - Go to: https://github.com/rh-ecosystem-edge/enclave/settings/actions/runners/new
#      - Copy the token from the configuration command shown
#   2. Run this script:
#      ./action-runners-setup.sh <TOKEN> [NUM_RUNNERS]
#
# EXAMPLE:
#   ./action-runners-setup.sh AABBCCDDEE112233445566 6

# --- Configuration ---
RESETS="\e[0m"
BOLD="\e[1m"
GREEN="\e[32m"
RED="\e[31m"
YELLOW="\e[33m"

# Parse arguments
TOKEN="${1}"
NUM_RUNNERS="${2:-5}"
URL="https://github.com/rh-ecosystem-edge/enclave"
RUNNER_VERSION="2.311.0"
BASE_DIR="$HOME/action-runners"

# Get hostname prefix (short hostname without domain)
HOST_PREFIX=$(hostname -s)

# Validate required parameters
if [ -z "$TOKEN" ]; then
    echo -e "${RED}${BOLD}ERROR: GitHub registration token is required!${RESETS}"
    echo ""
    echo "To get a token:"
    echo "  1. Go to: https://github.com/rh-ecosystem-edge/enclave/settings/actions/runners/new"
    echo "  2. Copy the token from the configuration command shown on that page"
    echo "  3. Run: $0 <TOKEN> [NUM_RUNNERS]"
    echo ""
    echo "Example:"
    echo "  $0 AABBCCDDEE112233445566 6"
    exit 1
fi

echo -e "${BOLD}GitHub Actions Runner Setup${RESETS}"
echo -e "${GREEN}Repository: $URL${RESETS}"
echo -e "${GREEN}Hostname prefix: $HOST_PREFIX${RESETS}"
echo -e "${GREEN}Runners to create: $NUM_RUNNERS${RESETS}"
echo -e "${GREEN}Base directory: $BASE_DIR${RESETS}"
echo ""

# 1. Ensure Base Directory exists
mkdir -p "$BASE_DIR"
cd "$BASE_DIR" || { echo -e "${RED}Failed to access $BASE_DIR${RESETS}"; exit 1; }

# 2. Download Runner Tarball
TARBALL="actions-runner-linux-x64-${RUNNER_VERSION}.tar.gz"

# Remove existing tarball if it exists (to avoid corruption issues)
if [ -f "$TARBALL" ]; then
    echo -e "${YELLOW}Removing existing tarball to ensure fresh download...${RESETS}"
    rm -f "$TARBALL"
fi

echo -e "${GREEN}Downloading runner v${RUNNER_VERSION}...${RESETS}"
if curl -o "$TARBALL" -L "https://github.com/actions/runner/releases/download/v${RUNNER_VERSION}/${TARBALL}"; then
    echo -e "${GREEN}✓ Download successful!${RESETS}"

    # Verify tarball integrity
    if tar -tzf "$TARBALL" > /dev/null 2>&1; then
        echo -e "${GREEN}✓ Tarball verified${RESETS}"
    else
        echo -e "${RED}✗ Tarball is corrupted!${RESETS}"
        rm -f "$TARBALL"
        exit 1
    fi
else
    echo -e "${RED}✗ Failed to download runner tarball${RESETS}"
    exit 1
fi

echo ""

# 3. Loop to create N runners
for i in $(seq 1 "$NUM_RUNNERS"); do
    RUNNER_NAME="${HOST_PREFIX}-runner-$(printf "%02d" "$i")"
    RUNNER_DIR="$BASE_DIR/runner-$i"

    echo -e "${BOLD}--- Setting up $RUNNER_NAME ($(($i))/$NUM_RUNNERS) ---${RESETS}"

    # Remove existing directory if present
    if [ -d "$RUNNER_DIR" ]; then
        echo -e "${YELLOW}Removing existing runner directory...${RESETS}"
        rm -rf "$RUNNER_DIR"
    fi

    # Create directory and extract
    mkdir -p "$RUNNER_DIR"
    echo -e "${GREEN}Extracting runner files...${RESETS}"
    if tar xzf "$BASE_DIR/$TARBALL" -C "$RUNNER_DIR"; then
        echo -e "${GREEN}✓ Extraction successful${RESETS}"
    else
        echo -e "${RED}✗ Failed to extract runner${RESETS}"
        exit 1
    fi

    # Change to runner directory
    cd "$RUNNER_DIR" || { echo -e "${RED}Failed to cd to $RUNNER_DIR${RESETS}"; exit 1; }

    # Configure the runner
    echo -e "${GREEN}Configuring runner...${RESETS}"
    if ./config.sh --url "$URL" \
                --token "$TOKEN" \
                --name "$RUNNER_NAME" \
                --labels "self-hosted,pr-validation" \
                --unattended \
                --replace; then
        echo -e "${GREEN}✓ Configuration successful${RESETS}"
    else
        echo -e "${RED}✗ Configuration failed${RESETS}"
        echo -e "${YELLOW}Note: Tokens expire quickly. Get a new token and try again.${RESETS}"
        exit 1
    fi

    # Install and Start Systemd Service
    echo -e "${GREEN}Installing systemd service for $RUNNER_NAME...${RESETS}"
    if sudo ./svc.sh install "$(whoami)"; then
        echo -e "${GREEN}✓ Service installed${RESETS}"
    else
        echo -e "${RED}✗ Service installation failed${RESETS}"
        exit 1
    fi

    if sudo ./svc.sh start; then
        echo -e "${GREEN}✓ Service started${RESETS}"
    else
        echo -e "${RED}✗ Service start failed${RESETS}"
        exit 1
    fi

    # Return to base directory for next iteration
    cd "$BASE_DIR" || { echo -e "${RED}Failed to cd back to $BASE_DIR${RESETS}"; exit 1; }

    echo -e "${GREEN}✓ $RUNNER_NAME ready!${RESETS}"
    echo ""
done

echo -e "\n${GREEN}${BOLD}========================================${RESETS}"
echo -e "${GREEN}${BOLD}Successfully set up $NUM_RUNNERS runners!${RESETS}"
echo -e "${GREEN}${BOLD}========================================${RESETS}"
echo ""
echo "Runners are now registered and running as systemd services."
echo "You can check their status with:"
echo "  sudo systemctl status actions.runner.rh-ecosystem-edge-enclave.pr-validation-*.service"
echo ""
echo "To view runner logs:"
echo "  sudo journalctl -u actions.runner.rh-ecosystem-edge-enclave.pr-validation-01.service -f"
