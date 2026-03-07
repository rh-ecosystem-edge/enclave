#!/bin/bash
# Shared output and logging utilities
#
# Provides color codes and logging functions for consistent terminal output
# across all scripts. Supports both local terminal and GitHub Actions environments.
#
# Usage:
#   source "${ENCLAVE_DIR}/scripts/lib/output.sh"
#   info "Starting process..."
#   success "Operation completed"
#   warning "Potential issue detected"
#   error "Operation failed"
#
# Functions:
#   info MESSAGE    - Display informational message in green (to stderr)
#   error MESSAGE   - Display error message in red (to stderr)
#   warning MESSAGE - Display warning message in yellow (to stderr)
#   success MESSAGE - Display success message with checkmark (to stderr)
#   output MESSAGE  - Display message to both terminal and GitHub Actions summary (to stdout)

# Color codes for terminal output (exported for use in sourcing scripts)
export RED='\033[0;31m'
export GREEN='\033[0;32m'
export YELLOW='\033[1;33m'
export BLUE='\033[0;34m'
export NC='\033[0m'  # No Color

# Detect GitHub Actions environment
if [ -n "${GITHUB_STEP_SUMMARY:-}" ]; then
    USE_GITHUB=true
else
    USE_GITHUB=false
fi

# Logging functions (all write to stderr to not interfere with data output)
info() {
    echo -e "${GREEN}INFO:${NC} $1" >&2
}

error() {
    echo -e "${RED}ERROR:${NC} $1" >&2
}

warning() {
    echo -e "${YELLOW}WARNING:${NC} $1" >&2
}

success() {
    echo -e "${GREEN}✓${NC} $1" >&2
}

# Output helper that works both locally and in GitHub Actions
# Writes to terminal and GitHub step summary (if in CI)
output() {
    local msg="$1"
    echo -e "$msg"
    if [ "$USE_GITHUB" = true ]; then
        # Strip ANSI color codes for GitHub summary
        echo "$msg" | sed 's/\x1b\[[0-9;]*m//g' >> "$GITHUB_STEP_SUMMARY"
    fi
}
