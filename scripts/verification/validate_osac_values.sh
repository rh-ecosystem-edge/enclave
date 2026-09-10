#!/bin/bash
# Validate the OSAC values overlay against the pinned OSAC Helm chart schema.
#
# For every profile fixture under plugins/osac/test-fixtures/values/profile-*.yaml
# this renders plugins/osac/templates/values.yaml.j2 (via Ansible, so the same
# Jinja filters and variable layering as at deploy time) and runs
# `helm template` against the pinned OSAC chart. `helm template` validates the
# rendered overlay against the chart's values.schema.json, so a chart bump that
# renames/retypes a key the overlay sets is caught at PR time — before E2E.
#
# The pinned chart version comes from plugins/osac/defaults.yaml
# (osacChartVersion) unless OSAC_CHART_VERSION is set. The chart reference comes
# from plugins/osac/plugin.yaml. Runtime-only stubs come from that descriptor's
# extractPlaceholders (see playbooks/validation/validate-osac-values.yaml).
#
# Requires: helm, ansible-playbook, python3 (PyYAML). Network egress to the
# chart registry (ghcr.io) is required to pull the chart.

set -euo pipefail

# Detect Enclave repository root
SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" &>/dev/null && pwd)"
ENCLAVE_DIR="$(cd -- "${SCRIPT_DIR}/../.." &>/dev/null && pwd)"

# Source shared utilities (info/error/success/warning)
# shellcheck source=scripts/lib/output.sh
source "${ENCLAVE_DIR}/scripts/lib/output.sh"

cd "${ENCLAVE_DIR}"

PLUGIN_YAML="plugins/osac/plugin.yaml"
DEFAULTS_YAML="plugins/osac/defaults.yaml"
PLAYBOOK="playbooks/validation/validate-osac-values.yaml"
FIXTURE_DIR="plugins/osac/test-fixtures/values"

# --- Preconditions -----------------------------------------------------------

for tool in helm ansible-playbook python3; do
    if ! command -v "${tool}" >/dev/null 2>&1; then
        error "Required tool not found on PATH: ${tool}"
        exit 1
    fi
done

# --- Resolve chart reference and version -------------------------------------

read_yaml_path() {
    # Usage: read_yaml_path <file> <python-expression-on-'d'>
    python3 -c "import yaml,sys; d=yaml.safe_load(open('$1')); print($2)"
}

CHART_REF="$(read_yaml_path "${PLUGIN_YAML}" "d['helm'][0]['chart']")"
CHART_VERSION="${OSAC_CHART_VERSION:-}"
if [ -z "${CHART_VERSION}" ]; then
    CHART_VERSION="$(read_yaml_path "${DEFAULTS_YAML}" "d['osacChartVersion']")"
fi

if [ -z "${CHART_REF}" ] || [ -z "${CHART_VERSION}" ]; then
    error "Could not resolve chart reference/version (ref='${CHART_REF}', version='${CHART_VERSION}')"
    exit 1
fi

info "Chart:   ${CHART_REF}"
info "Version: ${CHART_VERSION}"

# --- Pull the chart once -----------------------------------------------------

WORK_DIR="$(mktemp -d)"
cleanup() { rm -rf "${WORK_DIR}"; }
trap cleanup EXIT

info "Pulling chart to ${WORK_DIR} ..."
if ! helm pull "${CHART_REF}" --version "${CHART_VERSION}" --destination "${WORK_DIR}" --untar; then
    error "Failed to pull chart ${CHART_REF} version ${CHART_VERSION}"
    exit 1
fi

# `helm pull --untar` extracts to <dest>/<chart-name>; derive it from the ref.
CHART_NAME="${CHART_REF##*/}"
CHART_DIR="${WORK_DIR}/${CHART_NAME}"
if [ ! -f "${CHART_DIR}/Chart.yaml" ]; then
    error "Pulled chart not found at ${CHART_DIR}"
    exit 1
fi

# --- Validate each profile ---------------------------------------------------

shopt -s nullglob
PROFILES=("${FIXTURE_DIR}"/profile-*.yaml)
shopt -u nullglob

if [ "${#PROFILES[@]}" -eq 0 ]; then
    error "No profile fixtures found under ${FIXTURE_DIR}/profile-*.yaml"
    exit 1
fi

failed=0
for profile in "${PROFILES[@]}"; do
    name="$(basename "${profile}" .yaml)"
    rendered="${WORK_DIR}/values-${name}.yaml"

    info "── Profile: ${name}"

    # Render the overlay in a fresh Ansible run (no cross-profile var leakage).
    if ! ansible-playbook "${PLAYBOOK}" \
        -e "@${profile}" \
        -e osac_values_out="${rendered}"; then
        error "Render failed for profile '${name}'"
        failed=1
        continue
    fi

    # helm template runs values.schema.json validation as a side effect.
    if helm template osac "${CHART_DIR}" --namespace osac -f "${rendered}" >/dev/null; then
        success "Profile '${name}' validated against chart schema"
    else
        error "helm template / schema validation failed for profile '${name}'"
        failed=1
    fi
done

if [ "${failed}" -ne 0 ]; then
    error "OSAC values overlay validation failed"
    exit 1
fi

success "OSAC values overlay validated against chart ${CHART_VERSION} for all ${#PROFILES[@]} profiles"
