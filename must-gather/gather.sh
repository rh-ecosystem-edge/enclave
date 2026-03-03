#!/bin/bash
# Landing Zone Log Collection Tool
# Purpose: Collect all Landing Zone logs in must-gather style format

set -eo pipefail

# Configuration
SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" &>/dev/null && pwd)"
ENCLAVE_DIR="$(cd -- "${SCRIPT_DIR}/.." &>/dev/null && pwd)"

# Parse options and optional global vars file
GATHER_CLUSTER_MUST_GATHER=""
while [[ $# -gt 0 ]]; do
    case "$1" in
        --help|-h)
            cat <<EOF
Usage: $(basename "$0") [OPTIONS] [GLOBAL_VARS_FILE]

Landing Zone log collection. Collects LZ and cluster logs into timestamped archives.

Options:
  --must-gather[=MODE]    Also run must-gather during cluster collection.
                          MODE: full (default + operators) or operators (plugins only).
  --help, -h               Show this help and exit.

GLOBAL_VARS_FILE          Optional path to global.yaml (default: ../config/global.yaml).
EOF
            exit 0
            ;;
        --must-gather=*)
            GATHER_CLUSTER_MUST_GATHER="${1#--must-gather=}"
            shift
            ;;
        --must-gather)
            GATHER_CLUSTER_MUST_GATHER="full"
            shift
            ;;
        -*)
            echo "Unknown option: $1" >&2
            exit 1
            ;;
        *)
            break
            ;;
    esac
done
GLOBAL_VARS_FILE="${1:-${ENCLAVE_DIR}/config/global.yaml}"

if [[ -n "$GATHER_CLUSTER_MUST_GATHER" && "$GATHER_CLUSTER_MUST_GATHER" != "full" && "$GATHER_CLUSTER_MUST_GATHER" != "operators" ]]; then
    echo "Invalid --must-gather mode: $GATHER_CLUSTER_MUST_GATHER (use 'full' or 'operators')" >&2
    exit 1
fi
export GATHER_CLUSTER_MUST_GATHER

# Read workingDir from global vars file
getValue() {
    python3 -c 'import sys, yaml, json; print(json.dumps(yaml.safe_load(sys.stdin)))' < "${GLOBAL_VARS_FILE}" \
        | jq -r "$1"
}

workingDir=$(getValue .workingDir)

TIMESTAMP=$(date +%Y%m%d-%H%M%S)
# Include GitHub Actions run ID in names when running in CI
CI_SUFFIX="${GITHUB_RUN_ID:+-${GITHUB_RUN_ID}}"
COLLECTION_DIR="${SCRIPT_DIR}/lz-logs-${TIMESTAMP}${CI_SUFFIX}"
OUTPUT_ARCHIVE="${SCRIPT_DIR}/lz-logs-${TIMESTAMP}${CI_SUFFIX}.tar.gz"
CLUSTER_OUTPUT_ARCHIVE="${SCRIPT_DIR}/cluster-logs-${TIMESTAMP}${CI_SUFFIX}.tar.gz"

# Initialize error and warning tracking
declare -a ERRORS
declare -a WARNINGS

# Helper functions
log_info() {
    echo "[$(date '+%H:%M:%S')] $1"
}

log_error() {
    echo "[$(date '+%H:%M:%S')] ERROR: $1" >&2
    ERRORS+=("$1")
}

log_warning() {
    echo "[$(date '+%H:%M:%S')] WARNING: $1"
    WARNINGS+=("$1")
}

# Print header
echo ""
echo "======================================"
echo "Landing Zone Log Collection"
echo "======================================"
echo "Timestamp: $(date -Iseconds)"
echo "Output: ${OUTPUT_ARCHIVE}"
[[ -n "${GITHUB_RUN_ID}" ]] && echo "GitHub Run ID: ${GITHUB_RUN_ID}"
[[ -n "$GATHER_CLUSTER_MUST_GATHER" ]] && echo "Must-gather: ${GATHER_CLUSTER_MUST_GATHER}"
echo ""

GATHER_START_TIME=$(date +%s)

# Create must-gather directory structure
mkdir -p "${COLLECTION_DIR}"

# Create version file
git rev-parse HEAD 2>/dev/null > "${COLLECTION_DIR}/version" || echo "unknown" > "${COLLECTION_DIR}/version"
echo "lz-gather-logs/$(cat ${COLLECTION_DIR}/version)" | tee "${COLLECTION_DIR}/lz-logs-version.txt" > /dev/null

# Run Landing Zone collection (ansible, openshift-install, oc-mirror, registry, discovery ISO, host, config)
source "${SCRIPT_DIR}/gather_lz.sh"

# Run cluster resources collection (nodes, operators, pods, agents, etc.)
source "${SCRIPT_DIR}/gather_cluster.sh"

# 9. Generate manifest.json
log_info " Generating manifest..."

cat > "${COLLECTION_DIR}/manifest.json" <<EOF
{
  "version": "1.0.0",
  "tool": "lz-gather-logs",
  "collection_date": "$(date -Iseconds)",
  "hostname": "$(hostname)",
  "user": "$(whoami)",
  "git_sha": "$(cat ${COLLECTION_DIR}/version)",
  "sections_collected": {
    "ansible_logs": $([ -d "${COLLECTION_DIR}/ansible-logs" ] && [ ! -f "${COLLECTION_DIR}/ansible-logs/.not_found" ] && echo "true" || echo "false"),
    "openshift_install": $([ -f "${COLLECTION_DIR}/openshift-install/.openshift_install.log" ] && echo "true" || echo "false"),
    "oc_mirror": $([ -d "${COLLECTION_DIR}/oc-mirror" ] && [ ! -f "${COLLECTION_DIR}/oc-mirror/.not_found" ] && echo "true" || echo "false"),
    "registry": $([ -f "${COLLECTION_DIR}/registry/quay-app.log" ] && echo "true" || echo "false"),
    "cluster_resources": $([ "${KUBECONFIG_FOUND:-false}" = true ] && echo "true" || echo "false"),
    "host_info": true
  },
  "errors": $(printf '%s\n' "${ERRORS[@]}" | jq -R . | jq -s . 2>/dev/null || echo '[]'),
  "warnings": $(printf '%s\n' "${WARNINGS[@]}" | jq -R . | jq -s . 2>/dev/null || echo '[]')
}
EOF

log_info "  OK Generated manifest"

# Create archives
log_info ""
log_info "Creating archives..."
# Main LZ archive (exclude cluster data)
tar -czf "${OUTPUT_ARCHIVE}" -C "${SCRIPT_DIR}" --exclude='cluster-resources' --exclude='cluster-logs' "$(basename ${COLLECTION_DIR})/"
ARCHIVE_SIZE=$(du -h "${OUTPUT_ARCHIVE}" | cut -f1)
# Separate cluster archive only when cluster access was available (KUBECONFIG_FOUND)
if [[ "${KUBECONFIG_FOUND:-false}" = true ]]; then
    tar -czf "${CLUSTER_OUTPUT_ARCHIVE}" -C "${COLLECTION_DIR}" cluster-resources cluster-logs
    CLUSTER_ARCHIVE_SIZE=$(du -h "${CLUSTER_OUTPUT_ARCHIVE}" | cut -f1)
fi

# Cleanup temporary directory
rm -rf "${COLLECTION_DIR}"

# Print summary
GATHER_END_TIME=$(date +%s)
GATHER_ELAPSED=$((GATHER_END_TIME - GATHER_START_TIME))
GATHER_ELAPSED_MIN=$((GATHER_ELAPSED / 60))
GATHER_ELAPSED_SEC=$((GATHER_ELAPSED % 60))

echo ""
echo "======================================"
echo "Collection Complete!"
echo "======================================"
[[ -n "${GATHER_CLUSTER_MUST_GATHER:-}" ]] && echo "Time elapsed: ${GATHER_ELAPSED_MIN}m ${GATHER_ELAPSED_SEC}s"
echo "Archive: ${OUTPUT_ARCHIVE}"
echo "Size: ${ARCHIVE_SIZE}"
if [[ -f "${CLUSTER_OUTPUT_ARCHIVE}" ]]; then
    echo "Cluster archive: ${CLUSTER_OUTPUT_ARCHIVE}"
    echo "Cluster archive size: ${CLUSTER_ARCHIVE_SIZE}"
fi
echo ""
echo "Errors: ${#ERRORS[@]}"
echo "Warnings: ${#WARNINGS[@]}"

if [ ${#ERRORS[@]} -gt 0 ]; then
    echo ""
    echo "Errors encountered:"
    for error in "${ERRORS[@]}"; do
        echo "  - $error"
    done
fi

if [ ${#WARNINGS[@]} -gt 0 ]; then
    echo ""
    echo "Warnings:"
    for warning in "${WARNINGS[@]}"; do
        echo "  - $warning"
    done
fi

echo ""
echo "To extract LZ:    tar -xzf $(basename ${OUTPUT_ARCHIVE})"
[[ -f "${CLUSTER_OUTPUT_ARCHIVE}" ]] && echo "To extract cluster: tar -xzf $(basename ${CLUSTER_OUTPUT_ARCHIVE})"
echo "To view manifest: tar -xzf $(basename ${OUTPUT_ARCHIVE}) --to-stdout '*/manifest.json' | jq ."
echo ""
