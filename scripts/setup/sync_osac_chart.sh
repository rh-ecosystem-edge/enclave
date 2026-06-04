#!/bin/bash
# Sync OSAC Helm sub-charts from the osac-installer repository.
#
# Clones osac-project/osac-installer (with submodules), copies each sub-chart
# into plugins/osac/charts/osac/charts/, and runs helm dependency build.
#
# Usage:
#   scripts/setup/sync_osac_chart.sh [--ref REF]
#
# Options:
#   --ref REF   Git ref (branch, tag, or commit) to check out. Default: main

set -euo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" &>/dev/null && pwd)"
ENCLAVE_DIR="$(cd -- "${SCRIPT_DIR}/../.." &>/dev/null && pwd)"

source "${ENCLAVE_DIR}/scripts/lib/output.sh"

INSTALLER_REPO="https://github.com/osac-project/osac-installer.git"
PLUGIN_CHART_DIR="${ENCLAVE_DIR}/plugins/osac/charts/osac"
REF="main"

while [[ $# -gt 0 ]]; do
    case "$1" in
        --ref)
            REF="$2"
            shift 2
            ;;
        *)
            error "Unknown option: $1"
            echo "Usage: $0 [--ref REF]" >&2
            exit 1
            ;;
    esac
done

# Sub-chart mapping: <destination dir> <submodule path>/<chart path>
declare -A CHART_MAP=(
    ["operator-crds"]="base/osac-operator/charts/operator-crds"
    ["operator"]="base/osac-operator/charts/operator"
    ["service"]="base/osac-fulfillment-service/charts/service"
    ["aap"]="base/osac-aap/charts/aap"
)

# Validate plugin chart directory
if [[ ! -f "${PLUGIN_CHART_DIR}/Chart.yaml" ]]; then
    error "OSAC umbrella Chart.yaml not found at ${PLUGIN_CHART_DIR}/Chart.yaml"
    exit 1
fi

# Clone installer repo into a temp directory
TMPDIR=$(mktemp -d)
trap 'rm -rf "${TMPDIR}"' EXIT

info "Cloning osac-installer (ref: ${REF}) with submodules..."
git clone --depth 1 --branch "${REF}" --recurse-submodules --shallow-submodules \
    "${INSTALLER_REPO}" "${TMPDIR}/osac-installer"

# Copy each sub-chart
DEST_CHARTS="${PLUGIN_CHART_DIR}/charts"
mkdir -p "${DEST_CHARTS}"

for chart_name in "${!CHART_MAP[@]}"; do
    src="${TMPDIR}/osac-installer/${CHART_MAP[$chart_name]}"
    dest="${DEST_CHARTS}/${chart_name}"

    if [[ ! -d "${src}" ]]; then
        error "Sub-chart not found: ${src}"
        exit 1
    fi

    rm -rf "${dest}"
    cp -r "${src}" "${dest}"
    info "Copied ${chart_name} from ${CHART_MAP[$chart_name]}"
done

# Record the synced ref for traceability
COMMIT=$(git -C "${TMPDIR}/osac-installer" rev-parse HEAD)
echo "${REF} (${COMMIT})" > "${PLUGIN_CHART_DIR}/charts/.synced-ref"
info "Synced from commit: ${COMMIT}"

# Run helm dependency build if helm is available
if command -v helm &>/dev/null; then
    info "Running helm dependency build..."
    helm dependency build "${PLUGIN_CHART_DIR}"
    success "Helm dependency build completed"
elif [[ -x "${ENCLAVE_DIR}/bin/helm" ]]; then
    info "Running helm dependency build..."
    "${ENCLAVE_DIR}/bin/helm" dependency build "${PLUGIN_CHART_DIR}"
    success "Helm dependency build completed"
else
    warning "helm not found — skipping dependency build. Run 'helm dependency build ${PLUGIN_CHART_DIR}' manually."
fi

success "OSAC chart sync complete. Sub-charts at: ${DEST_CHARTS}/"