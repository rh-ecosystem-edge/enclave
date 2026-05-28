#!/usr/bin/env bash
# Set up the dev-scripts directory for Enclave Lab
#
# Clones dev-scripts into DEV_SCRIPTS_PATH (idempotent) and writes
# the OpenShift pull secret to the location dev-scripts expects.
#
# Environment Variables:
#   DEV_SCRIPTS_PATH   - destination directory (required, exported by Makefile.ci)
#   DEV_SCRIPTS_REPO   - repository URL (required, exported by Makefile.ci)
#   DEV_SCRIPTS_BRANCH - branch / tag / SHA to check out (required, exported by Makefile.ci)
#   PULL_SECRET        - OpenShift pull secret JSON content (required when pull_secret.json absent or to refresh)
#   WORKING_DIR        - parent boundary for the path check (required, exported by Makefile.ci)

set -euo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" &>/dev/null && pwd)"
ENCLAVE_DIR="$(cd -- "${SCRIPT_DIR}/../.." &>/dev/null && pwd)"

source "${ENCLAVE_DIR}/scripts/lib/output.sh"
source "${ENCLAVE_DIR}/scripts/lib/validation.sh"

require_env_var "DEV_SCRIPTS_PATH"
require_env_var "DEV_SCRIPTS_REPO"
require_env_var "DEV_SCRIPTS_BRANCH"
require_env_var "WORKING_DIR"

require_path_within "${DEV_SCRIPTS_PATH}" "${WORKING_DIR}"

if [ ! -d "${DEV_SCRIPTS_PATH}/.git" ]; then
    rm -rf "${DEV_SCRIPTS_PATH}"
    info "Cloning dev-scripts (${DEV_SCRIPTS_BRANCH}) into ${DEV_SCRIPTS_PATH}..."
    if [ "${DEV_SCRIPTS_BRANCH}" = "master" ]; then
        git clone --depth=1 "${DEV_SCRIPTS_REPO}" "${DEV_SCRIPTS_PATH}"
    else
        git clone "${DEV_SCRIPTS_REPO}" "${DEV_SCRIPTS_PATH}"
        git -C "${DEV_SCRIPTS_PATH}" checkout "${DEV_SCRIPTS_BRANCH}" || {
            rm -rf "${DEV_SCRIPTS_PATH}"
            error "Failed to checkout '${DEV_SCRIPTS_BRANCH}'; removed incomplete clone"
            exit 1
        }
    fi
    success "dev-scripts cloned"
else
    info "dev-scripts already present at ${DEV_SCRIPTS_PATH}"
fi

pull_secret_file="${DEV_SCRIPTS_PATH}/pull_secret.json"
if [ ! -f "${pull_secret_file}" ] || [ -n "${PULL_SECRET:-}" ]; then
    require_env_var "PULL_SECRET"
    info "Writing pull secret to ${pull_secret_file}..."
    umask 077
    printf '%s' "${PULL_SECRET}" > "${pull_secret_file}"
    chmod 600 "${pull_secret_file}"
    success "Pull secret written"
fi
