#!/bin/bash

# GitHub Actions Runner Cleanup Script
# Removes all self-hosted runners and their systemd services
#
# USAGE:
#   1. Get a removal token from GitHub:
#      - Go to: https://github.com/rh-ecosystem-edge/enclave/settings/actions/runners
#      - Or get it programmatically via API
#   2. Run this script:
#      ./action-runners-cleanup.sh <TOKEN>
#
# EXAMPLE:
#   ./action-runners-cleanup.sh AABBCCDDEE112233445566
#
# NOTE: You can also run without a token to just stop/remove services and delete directories

# --- Configuration ---
RESETS="\e[0m"
BOLD="\e[1m"
GREEN="\e[32m"
RED="\e[31m"
YELLOW="\e[33m"

# Parse arguments
TOKEN="${1}"
BASE_DIR="$HOME/action-runners"

echo -e "${BOLD}GitHub Actions Runner Cleanup${RESETS}"
echo -e "${YELLOW}This will remove all runners and their services${RESETS}"
echo -e "${GREEN}Base directory: $BASE_DIR${RESETS}"
echo ""

if [ -z "$TOKEN" ]; then
    echo -e "${YELLOW}No token provided - will only stop services and remove directories${RESETS}"
    echo -e "${YELLOW}Runners will remain registered in GitHub (shown as offline)${RESETS}"
    echo ""
    read -p "Continue? (y/N) " -n 1 -r
    echo
    if [[ ! $REPLY =~ ^[Yy]$ ]]; then
        echo "Cancelled"
        exit 0
    fi
fi

# Check if base directory exists
if [ ! -d "$BASE_DIR" ]; then
    echo -e "${YELLOW}Base directory $BASE_DIR does not exist${RESETS}"
    echo "Nothing to clean up"
    exit 0
fi

cd "$BASE_DIR" || { echo -e "${RED}Failed to access $BASE_DIR${RESETS}"; exit 1; }

# Find all runner directories
RUNNER_DIRS=$(find . -maxdepth 1 -type d -name "runner-*" | sort)

if [ -z "$RUNNER_DIRS" ]; then
    echo -e "${YELLOW}No runner directories found${RESETS}"
    echo "Nothing to clean up"
    exit 0
fi

RUNNER_COUNT=$(echo "$RUNNER_DIRS" | wc -l)
echo -e "${GREEN}Found $RUNNER_COUNT runner(s) to remove${RESETS}"
echo ""

# Process each runner
for RUNNER_DIR in $RUNNER_DIRS; do
    RUNNER_DIR=$(basename "$RUNNER_DIR")
    RUNNER_NUM=$(echo "$RUNNER_DIR" | sed 's/runner-//')
    RUNNER_NAME="pr-validation-$(printf "%02d" "$RUNNER_NUM")"

    echo -e "${BOLD}--- Removing $RUNNER_NAME ---${RESETS}"

    cd "$BASE_DIR/$RUNNER_DIR" || {
        echo -e "${RED}✗ Failed to access $RUNNER_DIR${RESETS}"
        continue
    }

    # 1. Stop the systemd service
    SERVICE_NAME="actions.runner.rh-ecosystem-edge-enclave.${RUNNER_NAME}.service"
    echo -e "${YELLOW}Stopping systemd service...${RESETS}"
    if sudo systemctl is-active --quiet "$SERVICE_NAME"; then
        if sudo ./svc.sh stop 2>/dev/null; then
            echo -e "${GREEN}✓ Service stopped${RESETS}"
        else
            echo -e "${YELLOW}⚠ Failed to stop service (may not exist)${RESETS}"
        fi
    else
        echo -e "${GREEN}✓ Service not running${RESETS}"
    fi

    # 2. Uninstall the systemd service
    echo -e "${YELLOW}Uninstalling systemd service...${RESETS}"
    if sudo ./svc.sh uninstall 2>/dev/null; then
        echo -e "${GREEN}✓ Service uninstalled${RESETS}"
    else
        echo -e "${YELLOW}⚠ Failed to uninstall service (may not exist)${RESETS}"
    fi

    # 3. Remove runner from GitHub (if token provided)
    if [ -n "$TOKEN" ]; then
        echo -e "${YELLOW}Removing runner from GitHub...${RESETS}"
        if ./config.sh remove --token "$TOKEN" 2>/dev/null; then
            echo -e "${GREEN}✓ Runner removed from GitHub${RESETS}"
        else
            echo -e "${RED}✗ Failed to remove runner from GitHub${RESETS}"
            echo -e "${YELLOW}  Runner may appear as offline in GitHub settings${RESETS}"
        fi
    fi

    # 4. Return to base directory
    cd "$BASE_DIR" || { echo -e "${RED}Failed to cd back to $BASE_DIR${RESETS}"; exit 1; }

    # 5. Remove runner directory
    echo -e "${YELLOW}Removing runner directory...${RESETS}"
    if rm -rf "$RUNNER_DIR"; then
        echo -e "${GREEN}✓ Directory removed${RESETS}"
    else
        echo -e "${RED}✗ Failed to remove directory${RESETS}"
    fi

    echo -e "${GREEN}✓ $RUNNER_NAME cleaned up!${RESETS}"
    echo ""
done

# Clean up the tarball
TARBALL="actions-runner-linux-x64-*.tar.gz"
if ls $TARBALL 1> /dev/null 2>&1; then
    echo -e "${YELLOW}Removing runner tarball...${RESETS}"
    rm -f $TARBALL
    echo -e "${GREEN}✓ Tarball removed${RESETS}"
fi

# Optionally remove base directory if empty
if [ -z "$(ls -A "$BASE_DIR")" ]; then
    echo ""
    echo -e "${YELLOW}Base directory is empty${RESETS}"
    read -p "Remove base directory $BASE_DIR? (y/N) " -n 1 -r
    echo
    if [[ $REPLY =~ ^[Yy]$ ]]; then
        cd ~ || exit
        rmdir "$BASE_DIR"
        echo -e "${GREEN}✓ Base directory removed${RESETS}"
    fi
fi

echo ""
echo -e "${GREEN}${BOLD}========================================${RESETS}"
echo -e "${GREEN}${BOLD}Cleanup completed!${RESETS}"
echo -e "${GREEN}${BOLD}========================================${RESETS}"
echo ""

if [ -n "$TOKEN" ]; then
    echo "All runners have been removed from GitHub and local system"
else
    echo "Local runner files and services removed"
    echo "To fully unregister from GitHub, run with a token:"
    echo "  $0 <TOKEN>"
fi
