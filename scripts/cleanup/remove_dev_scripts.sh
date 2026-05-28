#!/usr/bin/env bash
# Remove the dev-scripts clone directory
#
# Validates that DEV_SCRIPTS_PATH is within WORKING_DIR before deletion
# to prevent accidental removal of arbitrary paths.
#
# Environment Variables:
#   DEV_SCRIPTS_PATH - directory to remove (required, exported by Makefile.ci)
#   WORKING_DIR      - parent boundary for the path check (required, exported by Makefile.ci)

set -euo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" &>/dev/null && pwd)"
ENCLAVE_DIR="$(cd -- "${SCRIPT_DIR}/../.." &>/dev/null && pwd)"

source "${ENCLAVE_DIR}/scripts/lib/output.sh"
source "${ENCLAVE_DIR}/scripts/lib/validation.sh"

require_env_var "DEV_SCRIPTS_PATH"
require_env_var "WORKING_DIR"

if [ ! -d "${DEV_SCRIPTS_PATH}" ]; then
    info "dev-scripts directory not found, nothing to remove: ${DEV_SCRIPTS_PATH}"
    exit 0
fi

require_path_within "${DEV_SCRIPTS_PATH}" "${WORKING_DIR}"

info "Removing dev-scripts clone: ${DEV_SCRIPTS_PATH}"
rm -rf "${DEV_SCRIPTS_PATH}"
success "dev-scripts clone removed"
