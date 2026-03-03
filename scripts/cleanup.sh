#!/usr/bin/env bash
# Cleanup script for Enclave Lab infrastructure
# Ensures dev-scripts cleanup works by creating config if needed

set -euo pipefail

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m' # No Color

# Helper functions
info() {
    echo -e "${GREEN}INFO:${NC} $1"
}

success() {
    echo -e "${GREEN}✓${NC} $1"
}

warning() {
    echo -e "${YELLOW}WARNING:${NC} $1"
}

error() {
    echo -e "${RED}ERROR:${NC} $1" >&2
}

# Get cluster name and paths from environment
CLUSTER_NAME="${ENCLAVE_CLUSTER_NAME:-enclave-test}"
CONFIG_NAME="config_${CLUSTER_NAME}.sh"

info "=========================================="
info "Cleaning up infrastructure for: ${CLUSTER_NAME}"
info "=========================================="

# Check if DEV_SCRIPTS_PATH is set
if [ -z "${DEV_SCRIPTS_PATH:-}" ]; then
    error "DEV_SCRIPTS_PATH not set"
    error "Cannot run cleanup without knowing dev-scripts location"
    exit 1
fi

if [ ! -d "${DEV_SCRIPTS_PATH}" ]; then
    error "DEV_SCRIPTS_PATH directory does not exist: ${DEV_SCRIPTS_PATH}"
    exit 1
fi

CONFIG_FILE="${DEV_SCRIPTS_PATH}/${CONFIG_NAME}"

# Ensure config file exists for cleanup
if [ ! -f "$CONFIG_FILE" ]; then
    warning "Config file not found: $CONFIG_FILE"
    info "Creating minimal config for cleanup..."

    # Create minimal config file just for cleanup to work
    cat > "$CONFIG_FILE" <<EOF
#!/bin/bash
# Minimal config for cleanup
export CLUSTER_NAME="${CLUSTER_NAME}"
export NUM_MASTERS=${ENCLAVE_NUM_MASTERS:-3}
export NUM_WORKERS=0
export NUM_EXTRA_WORKERS=${ENCLAVE_NUM_LANDINGZONE:-1}
export PROVISIONING_NETWORK=${ENCLAVE_BMC_NETWORK:-100.64.1.0/24}
export EXTERNAL_SUBNET_V4=${ENCLAVE_CLUSTER_NETWORK:-192.168.2.0/24}
export WORKING_DIR=${WORKING_DIR:-/opt/dev-scripts}
EOF
    chmod +x "$CONFIG_FILE"
    info "Created minimal config at: $CONFIG_FILE"
fi

# Clean up libvirt networks first (more reliable than dev-scripts cleanup)
info "Cleaning up libvirt networks for cluster: ${CLUSTER_NAME}..."
for net in $(sudo virsh net-list --all --name 2>/dev/null | grep "^${CLUSTER_NAME}-" || true); do
    info "  Found network: $net"

    # Disable autostart first
    if sudo virsh net-info "$net" 2>/dev/null | grep -q "Autostart:.*yes"; then
        info "    Disabling autostart for: $net"
        sudo virsh net-autostart --disable "$net" 2>/dev/null || warning "    Failed to disable autostart for $net"
    fi

    # Destroy if active
    if sudo virsh net-info "$net" 2>/dev/null | grep -q "Active:.*yes"; then
        info "    Destroying active network: $net"
        sudo virsh net-destroy "$net" 2>/dev/null || warning "    Failed to destroy $net"
    fi

    # Undefine
    info "    Undefining network: $net"
    sudo virsh net-undefine "$net" 2>/dev/null || warning "    Failed to undefine $net"
done

# Clean up orphaned bridge interfaces
info "Cleaning up bridge interfaces for cluster: ${CLUSTER_NAME}..."
for bridge in $(ip link show type bridge 2>/dev/null | grep -oE "${CLUSTER_NAME}-[bc]" || true); do
    info "  Removing bridge: $bridge"
    sudo ip link set "$bridge" down 2>/dev/null || true
    sudo ip link delete "$bridge" 2>/dev/null || true
done

# Run dev-scripts cleanup (with lock to prevent conflicts with parallel runners)
info "Running dev-scripts cleanup..."
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

if "${SCRIPT_DIR}/with_libvirt_lock.sh" sh -c "cd ${DEV_SCRIPTS_PATH} && CONFIG=${CONFIG_NAME} make clean"; then
    success "dev-scripts cleanup completed successfully"
else
    warning "dev-scripts cleanup reported failure, but continuing..."
fi

# Clean up environment files
if [ -n "${WORKING_DIR:-}" ]; then
    # Remove cluster-specific environment file
    ENV_FILE="${WORKING_DIR}/environment-${CLUSTER_NAME}.json"
    if [ -f "$ENV_FILE" ]; then
        info "Removing environment file: $ENV_FILE"
        rm -f "$ENV_FILE"
    fi

    # Always remove environment.json symlink/file
    if [ -e "${WORKING_DIR}/environment.json" ]; then
        if [ -L "${WORKING_DIR}/environment.json" ]; then
            info "Removing environment.json symlink"
        else
            info "Removing environment.json file"
        fi
        rm -f "${WORKING_DIR}/environment.json"
    fi

    # Remove cluster-specific private-mirror file (leftover from oc-mirror)
    PRIVATE_MIRROR_FILE="${WORKING_DIR}/private-mirror-${CLUSTER_NAME}.json"
    if [ -f "$PRIVATE_MIRROR_FILE" ]; then
        info "Removing private-mirror file: $PRIVATE_MIRROR_FILE"
        rm -f "$PRIVATE_MIRROR_FILE"
    fi
fi

# Remove config file after successful cleanup
if [ -f "$CONFIG_FILE" ]; then
    info "Removing config file: $CONFIG_FILE"
    rm -f "$CONFIG_FILE"
fi

# Clean up cluster directory
if [ -n "${WORKING_DIR:-}" ]; then
    CLUSTER_DIR="${WORKING_DIR}/${CLUSTER_NAME}"
    if [ -d "$CLUSTER_DIR" ]; then
        info "Removing cluster directory: $CLUSTER_DIR"
        rm -rf "$CLUSTER_DIR" || warning "Failed to remove cluster directory"
    fi
fi

# Clean up volume files from pool
if [ -n "${WORKING_DIR:-}" ]; then
    POOL_DIR="${WORKING_DIR}/pool"
    if [ -d "$POOL_DIR" ]; then
        info "Cleaning up volume files for cluster: ${CLUSTER_NAME}"

        # Find and remove all volume files for this cluster
        VOLUME_FILES=$(find "$POOL_DIR" -maxdepth 1 -type f \( -name "${CLUSTER_NAME}_*.img" -o -name "${CLUSTER_NAME}_*.qcow2" \) 2>/dev/null || true)

        if [ -n "$VOLUME_FILES" ]; then
            VOLUME_COUNT=$(echo "$VOLUME_FILES" | wc -l)
            info "  Found $VOLUME_COUNT volume files to remove"

            while IFS= read -r vol_file; do
                [ -z "$vol_file" ] && continue
                info "    Removing: $(basename "$vol_file")"
                sudo rm -f "$vol_file" || warning "    Failed to remove $vol_file"
            done <<< "$VOLUME_FILES"

            # Refresh the pool to update libvirt's volume list
            info "  Refreshing libvirt storage pool..."
            sudo virsh pool-refresh default &>/dev/null || true
        else
            info "  No volume files found for cleanup"
        fi
    fi
fi

# Release allocated subnet
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
if [ -f "${SCRIPT_DIR}/allocate_subnet.sh" ]; then
    info "Releasing allocated subnet for cluster: ${CLUSTER_NAME}"

    # Export variables for subprocess
    export ENCLAVE_CLUSTER_NAME="${CLUSTER_NAME}"
    export WORKING_DIR="${WORKING_DIR:-/opt/dev-scripts}"

    "${SCRIPT_DIR}/allocate_subnet.sh" release || warning "Failed to release subnet (may not have been allocated)"
fi

success "=========================================="
success "Cleanup complete for cluster: ${CLUSTER_NAME}"
success "=========================================="

# Verify cleanup
info ""
info "Verifying cleanup..."

# Check for leftover VMs
LEFTOVER_VMS=$(sudo virsh list --all 2>/dev/null | grep -E "${CLUSTER_NAME}" || true)
if [ -n "$LEFTOVER_VMS" ]; then
    warning "Found leftover VMs for cluster ${CLUSTER_NAME}:"
    echo "$LEFTOVER_VMS"
else
    success "No leftover VMs found"
fi

# Check for leftover networks
LEFTOVER_NETS=$(sudo virsh net-list --all 2>/dev/null | grep -E "${CLUSTER_NAME}" || true)
if [ -n "$LEFTOVER_NETS" ]; then
    warning "Found leftover networks for cluster ${CLUSTER_NAME}:"
    echo "$LEFTOVER_NETS"
else
    success "No leftover networks found"
fi

# Check for leftover environment files
if [ -n "${WORKING_DIR:-}" ]; then
    LEFTOVER_ENVS=$(ls -1 "${WORKING_DIR}"/environment*.json 2>/dev/null || true)
    if [ -n "$LEFTOVER_ENVS" ]; then
        warning "Found leftover environment files:"
        echo "$LEFTOVER_ENVS"
    else
        success "No leftover environment files found"
    fi
fi

# Check for leftover cluster directory
if [ -n "${WORKING_DIR:-}" ]; then
    CLUSTER_DIR="${WORKING_DIR}/${CLUSTER_NAME}"
    if [ -d "$CLUSTER_DIR" ]; then
        warning "Found leftover cluster directory: $CLUSTER_DIR"
    else
        success "No leftover cluster directory found"
    fi
fi

# Check for leftover volume files
if [ -n "${WORKING_DIR:-}" ]; then
    POOL_DIR="${WORKING_DIR}/pool"
    if [ -d "$POOL_DIR" ]; then
        LEFTOVER_VOLS=$(find "$POOL_DIR" -maxdepth 1 -type f \( -name "${CLUSTER_NAME}_*.img" -o -name "${CLUSTER_NAME}_*.qcow2" \) 2>/dev/null || true)
        if [ -n "$LEFTOVER_VOLS" ]; then
            warning "Found leftover volume files for cluster ${CLUSTER_NAME}:"
            echo "$LEFTOVER_VOLS" | while read -r vol; do
                [ -n "$vol" ] && echo "  $(basename "$vol")"
            done
        else
            success "No leftover volume files found"
        fi
    fi
fi
