#!/bin/bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ENCLAVE_DIR="$(cd "${SCRIPT_DIR}/../.." && pwd)"
# shellcheck source=scripts/lib/output.sh
source "${ENCLAVE_DIR}/scripts/lib/output.sh"

die() {
    error "$1"
    exit 1
}

# --- Load configuration -----------------------------------------------------
ENV_FILE="${1:-${SCRIPT_DIR}/dry-run.env}"
if [ -f "${ENV_FILE}" ]; then
    info "Loading configuration from ${ENV_FILE}"
    set -a
    # shellcheck disable=SC1090
    source "${ENV_FILE}"
    set +a
else
    info "No env file at ${ENV_FILE}; using exported environment only"
fi

# --- Fixed workflow environment ---------------------------------------------
export OPENSHIFT_CI="true"
export ENCLAVE_ENABLE_GPU_PASSTHROUGH="false"

MAKE="make -f Makefile.ci"

# --- Validate required inputs (fail fast, before any work) -------------------
: "${BASE_WORKING_DIR:?BASE_WORKING_DIR must be set (see dry-run.env.example)}"
: "${PULL_SECRET:?PULL_SECRET must be set (see dry-run.env.example)}"

# PULL_SECRET may be a path to a JSON file (recommended) or the raw JSON string.
# Sourcing dry-run.env strips the double quotes from an unquoted inline JSON
# value, so a file path is the robust way to supply it.
if [ -f "${PULL_SECRET}" ]; then
    info "Reading pull secret from file: ${PULL_SECRET}"
    PULL_SECRET="$(cat "${PULL_SECRET}")"
fi
if ! printf '%s' "${PULL_SECRET}" | jq -e '.auths."registry.redhat.io"' >/dev/null 2>&1; then
    error "PULL_SECRET is not valid JSON with registry.redhat.io credentials."
    error "Set PULL_SECRET to a file path (e.g. /path/to/pull-secret.json), or if"
    error "inline in dry-run.env wrap the JSON in single quotes: PULL_SECRET='{...}'"
    exit 1
fi
# Each auth token must be well-formed base64. A line-wrapped or YAML-folded
# secret still parses as JSON but has spaces/newlines embedded inside the long
# tokens, which only fails much later inside `oc` ("illegal base64 data").
if ! PS="${PULL_SECRET}" python3 - <<'PY'
import os, json, base64, sys
d = json.loads(os.environ["PS"])
bad = []
for reg, v in (d.get("auths") or {}).items():
    try:
        base64.b64decode(v.get("auth", ""), validate=True)
    except Exception:
        bad.append(reg)
if bad:
    sys.stderr.write("malformed base64 auth token(s): " + ", ".join(bad) + "\n")
    sys.exit(1)
PY
then
    error "PULL_SECRET has malformed base64 auth tokens — likely a line-wrapped or"
    error "YAML-folded secret with spaces/newlines inside the tokens. Re-download a"
    error "clean single-line pull secret from console.redhat.com, or strip the"
    error "whitespace: jq '.auths |= with_entries(.value.auth |= gsub(\"[[:space:]]+\";\"\"))'"
    exit 1
fi
export BASE_WORKING_DIR PULL_SECRET

if [ -z "${LZ_RHSM_ORG:-}" ] || [ -z "${LZ_RHSM_ACTIVATION_KEY:-}" ]; then
    warning "LZ_RHSM_ORG / LZ_RHSM_ACTIVATION_KEY not set — Landing Zone will not be RHSM-registered"
fi

cd "${ENCLAVE_DIR}"

# --- Always collect artifacts and tear down on exit -------------------------
cleanup() {
    local rc=$?
    if [ "$(printf '%s' "${SKIP_CLEANUP:-false}" | tr '[:upper:]' '[:lower:]')" = "true" ]; then
        warning "SKIP_CLEANUP=true — leaving environment '${ENCLAVE_CLUSTER_NAME}' in place"
        return $rc
    fi
    echo ""
    info "=== Collecting artifacts and cleaning up (exit code ${rc}) ==="
    ${MAKE} collect-artifacts-deployment || warning "Artifact collection reported errors"
    ${MAKE} clean || warning "Cleanup reported errors"
    return $rc
}
trap cleanup EXIT

# --- Cluster name -----------------------------------------------------------
# Generate one if not supplied, matching the workflow's eci-<hash> convention.
if [ -z "${ENCLAVE_CLUSTER_NAME:-}" ]; then
    ./scripts/setup/generate_cluster_name.sh --strategy hash --prefix eci
    # shellcheck disable=SC1091
    source /tmp/cluster_name.env
fi
export ENCLAVE_CLUSTER_NAME
info "Cluster name: ${ENCLAVE_CLUSTER_NAME}"

# --- Working directory ------------------------------------------------------
# setup-working-dir writes the cluster-specific path to /tmp/working_dir; every
# later target must run against that same WORKING_DIR.
${MAKE} setup-working-dir
WORKING_DIR="$(cat /tmp/working_dir)"
export WORKING_DIR
info "Working dir: ${WORKING_DIR}"

${MAKE} ci-flow-disconnected
