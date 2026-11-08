#!/usr/bin/env bash
# Reap leaked CI clusters older than REAP_AGE_HOURS (default 12h) across all runs.

set -euo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" &>/dev/null && pwd)"
ENCLAVE_DIR="$(cd -- "${SCRIPT_DIR}/../.." &>/dev/null && pwd)"

source "${ENCLAVE_DIR}/ci/lib/common.sh"

enclave_ci="$(enclave_ci_environment_bin)"
# -E keeps REAP_AGE_HOURS and BASE_WORKING_DIR for the reap command.
sudo -E "${enclave_ci}" environment reap

