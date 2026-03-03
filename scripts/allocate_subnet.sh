#!/usr/bin/env bash
# Atomic subnet allocation for parallel CI execution
# Uses file locking to ensure no two jobs get the same subnet

set -euo pipefail

# Colors for output
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
RED='\033[0;31m'
NC='\033[0m'

info() {
    echo -e "${GREEN}INFO:${NC} $1" >&2
}

warning() {
    echo -e "${YELLOW}WARNING:${NC} $1" >&2
}

error() {
    echo -e "${RED}ERROR:${NC} $1" >&2
}

# Configuration
WORKING_DIR="${WORKING_DIR:-/opt/dev-scripts}"
ALLOCATION_FILE="${WORKING_DIR}/subnet-allocations.json"
LOCK_FILE="${WORKING_DIR}/subnet-allocations.lock"

# Subnet range: 2-254 (avoiding 0, 1, and 255)
MIN_SUBNET=2
MAX_SUBNET=254

# Stale allocation timeout (6 hours in seconds)
STALE_TIMEOUT=21600

# Get cluster name
CLUSTER_NAME="${ENCLAVE_CLUSTER_NAME:-}"
if [ -z "$CLUSTER_NAME" ]; then
    error "ENCLAVE_CLUSTER_NAME not set"
    exit 1
fi

# Get action (allocate or release)
ACTION="${1:-allocate}"

# Initialize allocation file if it doesn't exist
initialize_allocations() {
    if [ ! -f "$ALLOCATION_FILE" ]; then
        echo '{}' > "$ALLOCATION_FILE"
    fi
}

# Clean up stale allocations (old entries from crashed jobs)
cleanup_stale_allocations() {
    local now
    local temp_file
    now=$(date +%s)
    temp_file=$(mktemp)

    jq --arg now "$now" --arg timeout "$STALE_TIMEOUT" '
        to_entries | map(
            select(
                # Keep if not stale (within timeout)
                (.value.timestamp | tonumber) > (($now | tonumber) - ($timeout | tonumber))
            )
        ) | from_entries
    ' "$ALLOCATION_FILE" > "$temp_file"

    mv "$temp_file" "$ALLOCATION_FILE"
}

# Find first available subnet ID
find_available_subnet() {
    local allocated_subnets
    allocated_subnets=$(jq -r '.[] | .subnet_id' "$ALLOCATION_FILE" 2>/dev/null || echo "")

    for subnet_id in $(seq $MIN_SUBNET $MAX_SUBNET); do
        if ! echo "$allocated_subnets" | grep -q "^${subnet_id}$"; then
            echo "$subnet_id"
            return 0
        fi
    done

    error "No available subnets (all ${MIN_SUBNET}-${MAX_SUBNET} are allocated)"
    return 1
}

# Allocate subnet for cluster
allocate_subnet() {
    info "Allocating subnet for cluster: $CLUSTER_NAME"

    # Acquire exclusive lock
    exec 200>"$LOCK_FILE"
    flock -x 200 || {
        error "Failed to acquire lock"
        exit 1
    }

    # Initialize if needed
    initialize_allocations

    # Clean up stale allocations
    cleanup_stale_allocations

    # Check if cluster already has an allocation
    EXISTING_SUBNET=$(jq -r --arg cluster "$CLUSTER_NAME" '.[$cluster] | .subnet_id // empty' "$ALLOCATION_FILE")

    if [ -n "$EXISTING_SUBNET" ]; then
        info "Cluster already has subnet allocated: $EXISTING_SUBNET"
        echo "$EXISTING_SUBNET"
        flock -u 200
        return 0
    fi

    # Find available subnet
    SUBNET_ID=$(find_available_subnet)
    if [ -z "$SUBNET_ID" ]; then
        flock -u 200
        error "No available subnets"
        exit 1
    fi

    # Allocate subnet
    TIMESTAMP=$(date +%s)
    PID=$$

    jq --arg cluster "$CLUSTER_NAME" \
       --arg subnet "$SUBNET_ID" \
       --arg timestamp "$TIMESTAMP" \
       --arg pid "$PID" \
       '.[$cluster] = {subnet_id: ($subnet | tonumber), timestamp: ($timestamp | tonumber), pid: ($pid | tonumber)}' \
       "$ALLOCATION_FILE" > "${ALLOCATION_FILE}.tmp"

    mv "${ALLOCATION_FILE}.tmp" "$ALLOCATION_FILE"

    info "Allocated subnet $SUBNET_ID for cluster $CLUSTER_NAME"
    echo "$SUBNET_ID"

    # Release lock
    flock -u 200
}

# Release subnet allocation
release_subnet() {
    info "Releasing subnet for cluster: $CLUSTER_NAME"

    # Acquire exclusive lock
    exec 200>"$LOCK_FILE"
    flock -x 200 || {
        error "Failed to acquire lock"
        exit 1
    }

    # Check if allocation file exists
    if [ ! -f "$ALLOCATION_FILE" ]; then
        warning "No allocation file found"
        flock -u 200
        return 0
    fi

    # Remove allocation
    jq --arg cluster "$CLUSTER_NAME" 'del(.[$cluster])' "$ALLOCATION_FILE" > "${ALLOCATION_FILE}.tmp"
    mv "${ALLOCATION_FILE}.tmp" "$ALLOCATION_FILE"

    info "Released subnet allocation for cluster $CLUSTER_NAME"

    # Release lock
    flock -u 200
}

# Main execution
case "$ACTION" in
    allocate)
        allocate_subnet
        ;;
    release)
        release_subnet
        ;;
    *)
        error "Invalid action: $ACTION (use 'allocate' or 'release')"
        exit 1
        ;;
esac
