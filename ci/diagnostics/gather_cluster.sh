#!/bin/bash
# Cluster resources collection: nodes, operators, pods, agents, assisted-service logs, etc.
# Intended to be sourced from gather.sh (uses COLLECTION_DIR, GLOBAL_VARS_FILE, getValue, log_*, ERRORS, WARNINGS).
# Sets KUBECONFIG_FOUND for use in manifest generation.
#
# When run directly, supports:
#   --must-gather[=full|operators]  Run must-gather (full = default + operators, operators = plugin images only)
#   --help                           Show usage

# Optional log helpers when not sourced (e.g. when run with --must-gather)
if ! declare -f log_info &>/dev/null; then
    log_info() { echo "[$(date '+%H:%M:%S')] $1"; }
fi
if ! declare -f log_warning &>/dev/null; then
    log_warning() { echo "[$(date '+%H:%M:%S')] WARNING: $1" >&2; }
fi

# Build must-gather command into array MUST_GATHER_CMD.
# Usage: build_must_gather_cmd <mode>
#   mode: full = default OpenShift must-gather + operator plugin images; operators = only operator plugin images
build_must_gather_cmd() {
    local mode="${1:-full}"
    local csvJson
    csvJson=$(oc get clusterserviceversions.operators.coreos.com -A -o json 2>/dev/null) || return 1

    MUST_GATHER_CMD=(oc adm must-gather)
    if [[ "$mode" == "full" ]]; then
        MUST_GATHER_CMD+=(--image-stream=openshift/must-gather)
    fi

    # Operator must-gather images from CSVs (relatedImages with "must.gather" in name)
    while IFS= read -r image; do
        [[ -n "$image" ]] && MUST_GATHER_CMD+=("$image")
    done < <(echo "$csvJson" | jq -r '.items[] | select(.status.phase == "Succeeded") | select(.spec.relatedImages != null) | .spec.relatedImages[] | select(.image | test("must.gather"; "i")) | "--image=" + .image' | sort -u)

    # Cluster Logging operator image
    while IFS= read -r image; do
        [[ -n "$image" ]] && MUST_GATHER_CMD+=("$image")
    done < <(echo "$csvJson" | jq -r '.items[] | select(.status.phase == "Succeeded") | select(.metadata.name | contains("cluster-logging")) | select(.spec.install.spec.deployments[]?.name == "cluster-logging-operator") | .spec.install.spec.deployments[].spec.template.spec.containers[].image | "--image=" + .' | sort -u)
}

# Run must-gather. If COLLECTION_DIR is set, writes to COLLECTION_DIR/cluster-logs/must-gather.
# Usage: run_must_gather <mode>
run_must_gather() {
    local mode="${1:-full}"
    local dest_dir=""
    local log_file="must-gather-console.log"

    build_must_gather_cmd "$mode" || return 1

    if [[ -n "${COLLECTION_DIR:-}" ]]; then
        mkdir -p "${COLLECTION_DIR}/cluster-logs/must-gather"
        dest_dir="${COLLECTION_DIR}/cluster-logs/must-gather"
        log_file="${dest_dir}/must-gather-console.log"
    fi

    # Insert --dest-dir before the final "--" and script
    if [[ -n "$dest_dir" ]]; then
        local run_cmd=("${MUST_GATHER_CMD[@]:0:${#MUST_GATHER_CMD[@]}-2}" --dest-dir="$dest_dir" "${MUST_GATHER_CMD[@]: -2}")
        log_info " Running must-gather (mode=$mode) -> ${dest_dir}"
        "${run_cmd[@]}" 2>&1 | tee -a "$log_file" || true
    else
        log_info " Running must-gather (mode=$mode)..."
        "${MUST_GATHER_CMD[@]}" 2>&1 | tee -a "$log_file" || true
    fi
}

print_help() {
    cat <<EOF
Usage: $(basename "$0") [OPTIONS]

Cluster resources collection for Landing Zone log collection. When sourced from
gather.sh, collects cluster resources and optionally runs must-gather. When run
directly, only must-gather options are used.

Options:
  --must-gather[=MODE]    Run must-gather after cluster collection (when sourced)
                           or run only must-gather (when run directly).
                           MODE can be:
                             full      Default OpenShift must-gather + operator
                                       must-gather plugins (default).
                             operators Only operator must-gather plugin images
                                       (no default must-gather).
  --help                  Show this help and exit.

Examples:
  $(basename "$0") --must-gather              # Run full must-gather (default + operators)
  $(basename "$0") --must-gather=operators    # Run only operator plugin must-gather
  $(basename "$0") --help
EOF
}

# When run directly: parse args and run must-gather if requested
if [[ "${BASH_SOURCE[0]}" == "${0}" ]]; then
    MUST_GATHER_MODE=""
    while [[ $# -gt 0 ]]; do
        case "$1" in
            --help|-h)
                print_help
                exit 0
                ;;
            --must-gather=*)
                MUST_GATHER_MODE="${1#--must-gather=}"
                shift
                ;;
            --must-gather)
                MUST_GATHER_MODE="full"
                shift
                ;;
            *)
                echo "Unknown option: $1" >&2
                print_help >&2
                exit 1
                ;;
        esac
    done

    if [[ -n "$MUST_GATHER_MODE" ]]; then
        if [[ "$MUST_GATHER_MODE" != "full" && "$MUST_GATHER_MODE" != "operators" ]]; then
            echo "Invalid --must-gather mode: $MUST_GATHER_MODE (use 'full' or 'operators')" >&2
            exit 1
        fi
        run_must_gather "$MUST_GATHER_MODE"
        exit $?
    fi
    echo "No action specified. Use --must-gather or --help." >&2
    exit 1
fi

# Gather cluster resources (if kubeconfig available)
log_info " Gathering cluster resources..."
mkdir -p "${COLLECTION_DIR}/cluster-resources"
mkdir -p "${COLLECTION_DIR}/cluster-logs"

# Check cluster access
KUBECONFIG_FOUND=false

# Get expected cluster API from global vars file
CLUSTER_NAME=$(getValue .clusterName)
BASE_DOMAIN=$(getValue .baseDomain)
EXPECTED_API="https://api.${CLUSTER_NAME}.${BASE_DOMAIN}:6443"

# Check if we have cluster access
if oc whoami &>/dev/null; then
    # Verify we're connected to the correct cluster
    CURRENT_API=$(oc whoami --show-server 2>/dev/null)

    if [ "$CURRENT_API" = "$EXPECTED_API" ]; then
        log_info "  Cluster access verified: $CURRENT_API"
        KUBECONFIG_FOUND=true
    else
        log_warning "Connected to wrong cluster"
        log_warning "  Expected: $EXPECTED_API"
        log_warning "  Current:  $CURRENT_API"
        log_warning "Skipping cluster resource collection"
        KUBECONFIG_FOUND=false
    fi
else
    log_warning "No cluster access - cannot collect cluster resources"
    log_warning "Expected cluster: $EXPECTED_API"
    KUBECONFIG_FOUND=false
fi

if [ "$KUBECONFIG_FOUND" = true ]; then
    # Nodes
    oc get nodes -o wide > "${COLLECTION_DIR}/cluster-resources/nodes.txt" 2>&1 || true

    # Cluster operators
    oc get clusteroperators > "${COLLECTION_DIR}/cluster-resources/clusteroperators.txt" 2>&1 || true

    # CSVs (installed operators)
    oc get csv -A > "${COLLECTION_DIR}/cluster-resources/csv.txt" 2>&1 || true

    # Pods not running
    oc get pods -A | grep -v Running | grep -v Completed > "${COLLECTION_DIR}/cluster-resources/pods-not-running.txt" 2>&1 || \
        echo "All pods running" > "${COLLECTION_DIR}/cluster-resources/pods-not-running.txt"

    # Agents (if infraenv exists)
    oc get agents -n infraenv -o yaml > "${COLLECTION_DIR}/cluster-resources/agents.yaml" 2>&1 || true

    # InfraEnv
    oc get infraenv -n infraenv -o yaml > "${COLLECTION_DIR}/cluster-resources/infraenv.yaml" 2>&1 || true

    # NMStateConfig
    oc get nmstateconfig -n infraenv -o yaml > "${COLLECTION_DIR}/cluster-resources/nmstateconfig.yaml" 2>&1 || true

    # BareMetalHosts
    oc get baremetalhosts -A -o yaml > "${COLLECTION_DIR}/cluster-resources/baremetalhosts.yaml" 2>&1 || true

    # Assisted service logs
    oc logs -n multicluster-engine deployment/assisted-service --tail=1000 > "${COLLECTION_DIR}/cluster-logs/assisted-service.log" 2>&1 || true


    # Run must-gather if requested via environment
    if [[ -n "${GATHER_CLUSTER_MUST_GATHER:-}" ]]; then
        if [[ "$GATHER_CLUSTER_MUST_GATHER" == "full" || "$GATHER_CLUSTER_MUST_GATHER" == "operators" ]]; then
            log_info "Gathering must-gather logs (mode=$GATHER_CLUSTER_MUST_GATHER)..."
            run_must_gather "$GATHER_CLUSTER_MUST_GATHER" || log_warning "must-gather failed"
        fi
    fi

    log_info "  OK Collected cluster resources"
else
    log_warning "No kubeconfig found, skipping cluster resources"
    echo "NO_KUBECONFIG" > "${COLLECTION_DIR}/cluster-resources/.not_found"
fi
