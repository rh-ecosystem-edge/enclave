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
export WORKING_DIR=${WORKING_DIR}
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
# Support both old naming (-b, -c) and new naming (-p, -e) during transition
info "Cleaning up bridge interfaces for cluster: ${CLUSTER_NAME}..."
for bridge in $(ip link show type bridge 2>/dev/null | grep -oE "${CLUSTER_NAME}-[bcpe]" || true); do
    info "  Removing bridge: $bridge"

    # Check if NetworkManager is managing this bridge
    if nmcli con show 2>/dev/null | grep -q "$bridge"; then
        info "    Removing NetworkManager connection for: $bridge"
        sudo nmcli con delete "$bridge" 2>/dev/null || true
    fi

    sudo ip link set "$bridge" down 2>/dev/null || true
    sudo ip link delete "$bridge" 2>/dev/null || true
done

# Also check for any orphaned bridges from failed cleanups
# These bridges have no VMs attached (NO-CARRIER) but still exist
info "Checking for orphaned cluster bridges from previous runs..."
ORPHANED_BRIDGES=$(ip link show type bridge 2>/dev/null | grep -E "eci-[a-f0-9]+-[pe]" | grep -oE "eci-[a-f0-9]+-[pe]" || true)
for bridge in $ORPHANED_BRIDGES; do
    # Skip if it's the current cluster (already handled above)
    if [[ "$bridge" =~ ^${CLUSTER_NAME}- ]]; then
        continue
    fi

    # Check if bridge has NO-CARRIER (no VMs attached) - safe to remove
    if ip link show "$bridge" 2>/dev/null | grep -q "NO-CARRIER"; then
        warning "  Found orphaned bridge from previous cluster: $bridge (removing)"

        # Remove from NetworkManager if managed
        if nmcli con show 2>/dev/null | grep -q "$bridge"; then
            sudo nmcli con delete "$bridge" 2>/dev/null || true
        fi

        sudo ip link set "$bridge" down 2>/dev/null || true
        sudo ip link delete "$bridge" 2>/dev/null || true
    else
        warning "  Found bridge from other cluster with active VMs: $bridge (skipping)"
    fi
done

# Run dev-scripts cleanup (with lock to prevent conflicts with parallel runners)
info "Running dev-scripts cleanup..."
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

if "${SCRIPT_DIR}/../utils/with_libvirt_lock.sh" sh -c "cd ${DEV_SCRIPTS_PATH} && CONFIG=${CONFIG_NAME} make clean"; then
    success "dev-scripts cleanup completed successfully"
else
    warning "dev-scripts cleanup reported failure, but continuing..."
fi

# Clean up cluster-specific working directory and files
# Support both new structure (BASE_WORKING_DIR/clusters/CLUSTER_NAME) and old structure (WORKING_DIR)
if [ -n "${WORKING_DIR:-}" ]; then
    # Determine the actual cluster directory
    # If WORKING_DIR ends with /clusters/${CLUSTER_NAME}, use it directly
    # Otherwise, check if BASE_WORKING_DIR is set and use that structure
    if [[ "${WORKING_DIR}" == *"/clusters/${CLUSTER_NAME}" ]]; then
        CLUSTER_DIR="${WORKING_DIR}"
    elif [ -n "${BASE_WORKING_DIR:-}" ]; then
        CLUSTER_DIR="${BASE_WORKING_DIR}/clusters/${CLUSTER_NAME}"
    else
        # Old structure - files in WORKING_DIR directly
        CLUSTER_DIR=""
    fi

    # If we have a cluster-specific directory, remove it entirely
    if [ -n "$CLUSTER_DIR" ] && [ -d "$CLUSTER_DIR" ]; then
        info "Removing cluster-specific working directory: $CLUSTER_DIR"
        sudo rm -rf "$CLUSTER_DIR" || warning "Failed to remove cluster directory"
    else
        # Old structure - clean up individual files
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

    # Also check HOME directory for private-mirror files (oc-mirror creates these in PWD)
    if [ -n "${HOME:-}" ]; then
        PRIVATE_MIRROR_HOME="${HOME}/private-mirror-${CLUSTER_NAME}.json"
        if [ -f "$PRIVATE_MIRROR_HOME" ]; then
            info "Removing private-mirror file from HOME: $PRIVATE_MIRROR_HOME"
            rm -f "$PRIVATE_MIRROR_HOME"
        fi

        # Also remove orphaned private-mirror files from previous clusters
        # These accumulate in HOME directory from parallel CI runs
        info "Checking for orphaned private-mirror files in HOME..."
        ORPHANED_MIRRORS=$(find "${HOME}" -maxdepth 1 -name "private-mirror-eci-*.json" 2>/dev/null || true)
        if [ -n "$ORPHANED_MIRRORS" ]; then
            ORPHAN_COUNT=$(echo "$ORPHANED_MIRRORS" | wc -l)
            info "  Found $ORPHAN_COUNT orphaned private-mirror files, removing..."
            echo "$ORPHANED_MIRRORS" | while IFS= read -r mirror_file; do
                if [ -f "$mirror_file" ]; then
                    rm -f "$mirror_file"
                fi
            done
            success "Removed orphaned private-mirror files"
        fi
    fi
fi

# Remove config file after successful cleanup
if [ -f "$CONFIG_FILE" ]; then
    info "Removing config file: $CONFIG_FILE"
    rm -f "$CONFIG_FILE"
fi

# Stop and remove cluster-specific sushy-tools container
SUSHY_CONTAINER="sushy-tools-${CLUSTER_NAME}"
if sudo podman ps -a --format '{{.Names}}' | grep -q "^${SUSHY_CONTAINER}$"; then
    info "Stopping and removing sushy-tools container for cluster: ${CLUSTER_NAME}"
    sudo podman stop "$SUSHY_CONTAINER" 2>/dev/null || warning "Failed to stop $SUSHY_CONTAINER"
    sudo podman rm "$SUSHY_CONTAINER" 2>/dev/null || warning "Failed to remove $SUSHY_CONTAINER"
else
    info "No sushy-tools container found for cluster ${CLUSTER_NAME}"
fi

# Remove firewall rule for cluster-specific sushy-tools port
if sudo firewall-cmd --state >/dev/null 2>&1; then
    # Calculate the BMC port for this cluster
    if [ -n "${PROVISIONING_NETWORK:-}" ]; then
        SUBNET_ID=$(echo "$PROVISIONING_NETWORK" | awk -F. '{print $3}')
        BMC_PORT="$((8000 + SUBNET_ID))"

        info "Removing firewall port ${BMC_PORT}/tcp for cluster ${CLUSTER_NAME}"
        # Remove from all zones (we don't know which zone it was added to)
        for zone in $(sudo firewall-cmd --get-active-zones | grep -v "^\s" | grep -v "^$"); do
            sudo firewall-cmd --zone="$zone" --remove-port="${BMC_PORT}/tcp" 2>/dev/null || true
            sudo firewall-cmd --zone="$zone" --remove-port="${BMC_PORT}/tcp" --permanent 2>/dev/null || true
        done
    fi
fi

# Clean up cluster-specific landing-zone directory (in shared BASE_WORKING_DIR)
BASE_DIR="${BASE_WORKING_DIR:-${WORKING_DIR}}"
if [ -n "${BASE_DIR}" ]; then
    LZ_DIR="${BASE_DIR}/landing-zone/${CLUSTER_NAME}"
    if [ -d "$LZ_DIR" ]; then
        info "Removing landing-zone directory: $LZ_DIR"
        sudo rm -rf "$LZ_DIR" || warning "Failed to remove landing-zone directory"
    fi
fi

# Clean up volume files for this cluster
# Volume files are in cluster-specific pool: /opt/dev-scripts/clusters/eci-XXXXXXXX/pool/
# Determine the pool directory based on cluster structure
if [ -n "${WORKING_DIR:-}" ]; then
    # Use cluster-specific pool path for new structure
    if [[ "${WORKING_DIR}" == *"/clusters/${CLUSTER_NAME}" ]]; then
        POOL_DIR="${WORKING_DIR}/pool"
    elif [ -n "${BASE_WORKING_DIR:-}" ]; then
        POOL_DIR="${BASE_WORKING_DIR}/clusters/${CLUSTER_NAME}/pool"
    else
        # Fallback to old shared pool structure
        POOL_DIR="${WORKING_DIR}/pool"
    fi

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

            # Refresh the storage pools to update libvirt's volume list
            # The pool might be cluster-specific or shared (oooq_pool)
            info "  Refreshing libvirt storage pools..."
            sudo virsh pool-refresh "${CLUSTER_NAME}" &>/dev/null || true
            sudo virsh pool-refresh "${CLUSTER_NAME}-lz" &>/dev/null || true
            sudo virsh pool-refresh "${CLUSTER_NAME}_pool" &>/dev/null || true
            sudo virsh pool-refresh "oooq_pool" &>/dev/null || true
        else
            info "  No volume files found for cleanup"
        fi
    fi
fi

# Clean up cluster-specific libvirt storage pools
# Find all pools that point to our cluster directory, regardless of name
# Dev-scripts may create pools with various names (oooq_pool, ${CLUSTER_NAME}, etc.)
# and various paths (pool/, landing-zone/, etc.)
CLUSTER_POOL_PATHS=()
if [ -n "${WORKING_DIR:-}" ]; then
    if [[ "${WORKING_DIR}" == *"/clusters/${CLUSTER_NAME}" ]]; then
        CLUSTER_POOL_PATHS+=("${WORKING_DIR}/pool")
        CLUSTER_POOL_PATHS+=("${WORKING_DIR}/landing-zone/${CLUSTER_NAME}")
    elif [ -n "${BASE_WORKING_DIR:-}" ]; then
        CLUSTER_POOL_PATHS+=("${BASE_WORKING_DIR}/clusters/${CLUSTER_NAME}/pool")
        CLUSTER_POOL_PATHS+=("${BASE_WORKING_DIR}/clusters/${CLUSTER_NAME}/landing-zone/${CLUSTER_NAME}")
    fi
fi

# Build list of pools to clean up
POOLS_TO_CLEAN=()

# Add standard pool names we might have created
# Dev-scripts may append -1, -lz, or _pool suffixes
for POOL_NAME in "${CLUSTER_NAME}" "${CLUSTER_NAME}-1" "${CLUSTER_NAME}-lz" "${CLUSTER_NAME}_pool"; do
    if sudo virsh pool-uuid "$POOL_NAME" > /dev/null 2>&1; then
        POOLS_TO_CLEAN+=("$POOL_NAME")
    fi
done

# Also find any pool pointing to our cluster-specific paths (handles oooq_pool, eci-XXXX-1, etc.)
if [ ${#CLUSTER_POOL_PATHS[@]} -gt 0 ]; then
    while IFS= read -r pool; do
        if [ -n "$pool" ]; then
            POOL_PATH_CHECK=$(sudo virsh pool-dumpxml "$pool" 2>/dev/null | grep -oP '(?<=<path>).*(?=</path>)' || echo "")
            for cluster_path in "${CLUSTER_POOL_PATHS[@]}"; do
                if [ "$POOL_PATH_CHECK" = "$cluster_path" ]; then
                    # Check if not already in the list
                    if [[ ! " ${POOLS_TO_CLEAN[*]} " =~ \ $pool\  ]]; then
                        info "Found pool '$pool' pointing to cluster path $cluster_path, will clean it up"
                        POOLS_TO_CLEAN+=("$pool")
                    fi
                    break
                fi
            done
        fi
    done < <(sudo virsh pool-list --all --name)
fi

for POOL_NAME in "${POOLS_TO_CLEAN[@]}"; do
    if sudo virsh pool-uuid "$POOL_NAME" > /dev/null 2>&1; then
        info "Removing libvirt storage pool: $POOL_NAME"

        # Disable autostart first
        if sudo virsh pool-info "$POOL_NAME" 2>/dev/null | grep -q "Autostart:.*yes"; then
            info "  Disabling autostart for: $POOL_NAME"
            sudo virsh pool-autostart --disable "$POOL_NAME" 2>/dev/null || warning "  Failed to disable autostart for $POOL_NAME"
        fi

        # Destroy if running
        # Note: virsh pool-info shows "running" or "not running" (different from pool-list "active"/"inactive")
        # Pattern matches "State: <spaces> running" but not "State: <spaces> not running"
        if sudo virsh pool-info "$POOL_NAME" 2>/dev/null | grep -qE "State:[[:space:]]+running$"; then
            info "  Destroying running pool: $POOL_NAME"
            sudo virsh pool-destroy "$POOL_NAME" 2>/dev/null || warning "  Failed to destroy $POOL_NAME"
        fi

        # Undefine
        info "  Undefining pool: $POOL_NAME"
        sudo virsh pool-undefine "$POOL_NAME" 2>/dev/null || warning "  Failed to undefine $POOL_NAME"
    fi
done

# Release allocated subnet
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
if [ -f "${SCRIPT_DIR}/../setup/allocate_subnet.sh" ]; then
    info "Releasing allocated subnet for cluster: ${CLUSTER_NAME}"

    # Export variables for subprocess
    export ENCLAVE_CLUSTER_NAME="${CLUSTER_NAME}"
    BASE_DIR="${BASE_WORKING_DIR:-${WORKING_DIR}}"
    export WORKING_DIR="${BASE_DIR}"

    "${SCRIPT_DIR}/../setup/allocate_subnet.sh" release || warning "Failed to release subnet (may not have been allocated)"
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

# Check for leftover cluster-specific working directory
BASE_DIR="${BASE_WORKING_DIR:-${WORKING_DIR}}"
if [ -n "${BASE_DIR}" ]; then
    CLUSTER_WORKING_DIR="${BASE_DIR}/clusters/${CLUSTER_NAME}"
    if [ -d "$CLUSTER_WORKING_DIR" ]; then
        warning "Found leftover cluster working directory: $CLUSTER_WORKING_DIR"
    else
        success "No leftover cluster working directory found"
    fi
fi

# Check for leftover environment files (old structure)
if [ -n "${WORKING_DIR:-}" ]; then
    LEFTOVER_ENVS=$(ls -1 "${WORKING_DIR}"/environment*-${CLUSTER_NAME}.json 2>/dev/null || true)
    if [ -n "$LEFTOVER_ENVS" ]; then
        warning "Found leftover environment files (old structure):"
        echo "$LEFTOVER_ENVS"
    else
        success "No leftover environment files found (old structure)"
    fi
fi

# Check for leftover landing-zone directory
if [ -n "${BASE_DIR}" ]; then
    LZ_DIR="${BASE_DIR}/landing-zone/${CLUSTER_NAME}"
    if [ -d "$LZ_DIR" ]; then
        warning "Found leftover landing-zone directory: $LZ_DIR"
    else
        success "No leftover landing-zone directory found"
    fi
fi

# Check for leftover volume files in cluster-specific pool directory
if [ -n "${WORKING_DIR:-}" ]; then
    # Use cluster-specific pool path
    if [[ "${WORKING_DIR}" == *"/clusters/${CLUSTER_NAME}" ]]; then
        POOL_DIR="${WORKING_DIR}/pool"
    elif [ -n "${BASE_WORKING_DIR:-}" ]; then
        POOL_DIR="${BASE_WORKING_DIR}/clusters/${CLUSTER_NAME}/pool"
    else
        POOL_DIR="${WORKING_DIR}/pool"
    fi

    if [ -d "$POOL_DIR" ]; then
        LEFTOVER_VOLS=$(find "$POOL_DIR" -maxdepth 1 -type f \( -name "${CLUSTER_NAME}_*.img" -o -name "${CLUSTER_NAME}_*.qcow2" \) 2>/dev/null || true)
        if [ -n "$LEFTOVER_VOLS" ]; then
            warning "Found leftover volume files in pool:"
            echo "$LEFTOVER_VOLS" | while read -r vol; do
                [ -n "$vol" ] && echo "    $(basename "$vol")"
            done
        else
            success "No leftover volume files found"
        fi
    else
        success "Pool directory does not exist"
    fi
fi

# Check for leftover pool definitions (by name or by path)
LEFTOVER_POOLS=""

# Check standard pool names (including -1 suffix from dev-scripts)
for POOL_NAME in "${CLUSTER_NAME}" "${CLUSTER_NAME}-1" "${CLUSTER_NAME}-lz" "${CLUSTER_NAME}_pool"; do
    if sudo virsh pool-uuid "$POOL_NAME" > /dev/null 2>&1; then
        LEFTOVER_POOLS="${LEFTOVER_POOLS}${POOL_NAME} "
    fi
done

# Check for any pool pointing to cluster paths (handles oooq_pool, eci-XXXX-1, etc.)
if [ ${#CLUSTER_POOL_PATHS[@]} -gt 0 ]; then
    while IFS= read -r pool; do
        if [ -n "$pool" ]; then
            POOL_PATH_CHECK=$(sudo virsh pool-dumpxml "$pool" 2>/dev/null | grep -oP '(?<=<path>).*(?=</path>)' || echo "")
            for cluster_path in "${CLUSTER_POOL_PATHS[@]}"; do
                if [ "$POOL_PATH_CHECK" = "$cluster_path" ]; then
                    # Add if not already in list
                    if [[ ! "$LEFTOVER_POOLS" =~ $pool ]]; then
                        LEFTOVER_POOLS="${LEFTOVER_POOLS}${pool} "
                    fi
                    break
                fi
            done
        fi
    done < <(sudo virsh pool-list --all --name)
fi

if [ -n "$LEFTOVER_POOLS" ]; then
    warning "Found leftover pool definition(s): $LEFTOVER_POOLS"
else
    success "No leftover pool definitions found"
fi

# Check for leftover private-mirror files (current cluster and any orphans)
LEFTOVER_MIRROR_FILES=""
if [ -n "${BASE_DIR}" ]; then
    CLUSTER_WORKING_DIR="${BASE_DIR}/clusters/${CLUSTER_NAME}"
    if [ -f "${CLUSTER_WORKING_DIR}/private-mirror-${CLUSTER_NAME}.json" ]; then
        LEFTOVER_MIRROR_FILES="${CLUSTER_WORKING_DIR}/private-mirror-${CLUSTER_NAME}.json "
    fi
fi
if [ -n "${WORKING_DIR:-}" ] && [ -f "${WORKING_DIR}/private-mirror-${CLUSTER_NAME}.json" ]; then
    LEFTOVER_MIRROR_FILES="${LEFTOVER_MIRROR_FILES}${WORKING_DIR}/private-mirror-${CLUSTER_NAME}.json "
fi
if [ -n "${HOME:-}" ]; then
    # Check for current cluster's file
    if [ -f "${HOME}/private-mirror-${CLUSTER_NAME}.json" ]; then
        LEFTOVER_MIRROR_FILES="${LEFTOVER_MIRROR_FILES}${HOME}/private-mirror-${CLUSTER_NAME}.json "
    fi

    # Check for any orphaned private-mirror files from other clusters
    ORPHANED_HOME_MIRRORS=$(find "${HOME}" -maxdepth 1 -name "private-mirror-eci-*.json" 2>/dev/null || true)
    if [ -n "$ORPHANED_HOME_MIRRORS" ]; then
        LEFTOVER_MIRROR_FILES="${LEFTOVER_MIRROR_FILES}${ORPHANED_HOME_MIRRORS}"
    fi
fi

if [ -n "$LEFTOVER_MIRROR_FILES" ]; then
    warning "Found leftover private-mirror files:"
    echo "$LEFTOVER_MIRROR_FILES" | tr ' ' '\n' | while IFS= read -r file; do
        if [ -n "$file" ] && [ -f "$file" ]; then
            warning "  $file"
        fi
    done
else
    success "No leftover private-mirror files found"
fi
