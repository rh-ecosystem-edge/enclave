#!/bin/bash
# Execute a command with exclusive libvirt daemon lock
#
# This prevents multiple runners on the same machine from simultaneously
# modifying global libvirt daemon state (enabling sockets, restarting services)
# which causes race conditions and failures.
#
# Usage:
#   ./with_libvirt_lock.sh <command> [args...]
#
# Example:
#   ./with_libvirt_lock.sh make -C /path/to/dev-scripts infra_only

set -euo pipefail

# Lock file location (shared across all runners on this machine)
LOCK_FILE="${LIBVIRT_LOCK_FILE:-/var/lock/libvirt-runner.lock}"
LOCK_TIMEOUT="${LIBVIRT_LOCK_TIMEOUT:-600}"  # 10 minutes
LOCK_FD=200

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

info() {
    echo -e "${BLUE}[LOCK]${NC} $1" >&2
}

success() {
    echo -e "${GREEN}[LOCK]${NC} $1" >&2
}

error() {
    echo -e "${RED}[LOCK]${NC} $1" >&2
}

warning() {
    echo -e "${YELLOW}[LOCK]${NC} $1" >&2
}

# Check if command was provided
if [ $# -eq 0 ]; then
    error "No command provided"
    echo "Usage: $0 <command> [args...]" >&2
    exit 1
fi

# Create lock file directory if it doesn't exist
LOCK_DIR=$(dirname "$LOCK_FILE")
if [ ! -d "$LOCK_DIR" ]; then
    sudo mkdir -p "$LOCK_DIR"
    sudo chmod 1777 "$LOCK_DIR"  # Sticky bit, world-writable like /tmp
fi

# Ensure lock file exists and is writable
if [ ! -f "$LOCK_FILE" ]; then
    sudo touch "$LOCK_FILE"
    sudo chmod 666 "$LOCK_FILE"
fi

info "Acquiring libvirt daemon lock: $LOCK_FILE"
info "Timeout: ${LOCK_TIMEOUT}s"

# Function to acquire lock with timeout
acquire_lock() {
    local elapsed=0
    local pid=""

    # Open lock file on FD 200
    eval "exec $LOCK_FD<>$LOCK_FILE"

    while true; do
        # Try to acquire exclusive lock (non-blocking)
        if flock -n $LOCK_FD; then
            # Lock acquired
            echo $$ >&$LOCK_FD
            success "Lock acquired (PID: $$)"
            return 0
        fi

        # Check who holds the lock
        pid=$(cat "$LOCK_FILE" 2>/dev/null || echo "unknown")

        if [ "$elapsed" -eq 0 ]; then
            warning "Lock held by PID $pid, waiting..."
        fi

        # Check timeout
        if [ "$elapsed" -ge "$LOCK_TIMEOUT" ]; then
            error "Timeout waiting for lock after ${LOCK_TIMEOUT}s"
            error "Lock held by PID: $pid"

            # Check if the process is still alive
            if [ "$pid" != "unknown" ] && ! kill -0 "$pid" 2>/dev/null; then
                warning "Lock holder (PID $pid) is dead, breaking stale lock"
                echo $$ >&$LOCK_FD
                success "Lock acquired after breaking stale lock"
                return 0
            fi

            return 1
        fi

        # Wait and retry
        sleep 5
        elapsed=$((elapsed + 5))

        # Progress indicator every 30 seconds
        if [ $((elapsed % 30)) -eq 0 ]; then
            info "Still waiting for lock... (${elapsed}s / ${LOCK_TIMEOUT}s)"
        fi
    done
}

# Function to release lock
release_lock() {
    if [ -n "${LOCK_FD:-}" ]; then
        flock -u $LOCK_FD 2>/dev/null || true
        eval "exec $LOCK_FD>&-" 2>/dev/null || true
        success "Lock released"
    fi
}

# Set up trap to release lock on exit (normal, error, interrupt, terminate)
# This ensures lock is ALWAYS released, even if command fails
trap release_lock EXIT INT TERM ERR

# Acquire the lock
if ! acquire_lock; then
    error "Failed to acquire lock"
    exit 1
fi

info "Executing: $*"
echo ""

# Execute the command with all arguments
EXIT_CODE=0
"$@" || EXIT_CODE=$?

echo ""
if [ $EXIT_CODE -eq 0 ]; then
    success "Command completed successfully"
else
    error "Command failed with exit code $EXIT_CODE"
fi

# Lock will be released by trap on exit
exit $EXIT_CODE
