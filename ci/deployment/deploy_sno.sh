#!/bin/bash
# Deploy an SNO managed cluster on the additional SNO VM and import it into ACM.
#
# Discovery registers the SNO VM as an unbound Agent in the `infraenv` namespace
# (generate_enclave_vars.sh adds it to discovery_hosts). This script creates the
# ClusterDeployment + AgentClusterInstall and the ManagedCluster +
# KlusterletAddonConfig in a namespace named after the SNO cluster (so ACM
# auto-imports it via Hive), binds/approves the Agent across namespaces, waits for
# the install and the ACM import, and extracts the SNO kubeconfig on the Landing
# Zone. CI-only; consumes cluster-env.sh.
#
# Usage: ./deploy_sno.sh

set -euo pipefail

# Detect Enclave repository root
SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" &>/dev/null && pwd)"
ENCLAVE_DIR="$(cd -- "${SCRIPT_DIR}/../.." &>/dev/null && pwd)"

# Source shared utilities
source "${ENCLAVE_DIR}/ci/lib/output.sh"
source "${ENCLAVE_DIR}/ci/lib/validation.sh"
source "${ENCLAVE_DIR}/ci/lib/config.sh"
source "${ENCLAVE_DIR}/ci/lib/network.sh"
source "${ENCLAVE_DIR}/ci/lib/ssh.sh"

# Source cluster environment (single source of truth from vm_infra.py)
load_cluster_env

case "${ENCLAVE_SNO_VM_ENABLED:-false}" in
    false) info "ENCLAVE_SNO_VM_ENABLED=false; no SNO to deploy, skipping"; exit 0 ;;
    true) ;;
    *) error "ENCLAVE_SNO_VM_ENABLED must be true or false, got: '${ENCLAVE_SNO_VM_ENABLED}'"; exit 1 ;;
esac

# SNO VM facts emitted by vm_infra.py.
SNO_CLUSTER_NAME="${ENCLAVE_SNO_VM_CLUSTER_NAME:?ENCLAVE_SNO_VM_CLUSTER_NAME not set}"
BASE_DOMAIN="${ENCLAVE_BASE_DOMAIN:?ENCLAVE_BASE_DOMAIN not set}"
SNO_MAC="${ENCLAVE_SNO_VM_CLUSTER_MAC:?ENCLAVE_SNO_VM_CLUSTER_MAC not set}"
MACHINE_NETWORK="${ENCLAVE_CLUSTER_NETWORK:?ENCLAVE_CLUSTER_NETWORK not set}"

# Landing Zone IP (static DHCP lease, from cluster-env.sh)
CLUSTER_IP="${ENCLAVE_LZ_CLUSTER_IP:-}"
if [ -z "$CLUSTER_IP" ]; then
    error "Could not determine Landing Zone IP address"
    exit 1
fi

setup_ssh_config "$CLUSTER_IP"

info "Deploying SNO managed cluster: $SNO_CLUSTER_NAME"
info "  Base domain:     $BASE_DOMAIN"
info "  Agent MAC:       $SNO_MAC"
info "  Machine network: $MACHINE_NETWORK"
info "  Landing Zone:    $CLUSTER_IP"

if ! ssh_test_connection; then
    error "Cannot connect to Landing Zone at $CLUSTER_IP"
    exit 1
fi

# On-LZ working dir holding the hub kubeconfig and SSH key (matches deploy_plugin.sh).
LZ_WORKDIR="/home/${LZ_USER}/sessions/1"

# Values passed to the remote script, safely quoted. The remote heredoc is quoted
# (literal), so it references these via its own environment rather than local
# expansion.
REMOTE_ENV="$(printf 'SNO=%q BASE_DOMAIN=%q SNO_MAC=%q MACHINE_NET=%q LZ_WORKDIR=%q' \
    "$SNO_CLUSTER_NAME" "$BASE_DOMAIN" "$SNO_MAC" "$MACHINE_NETWORK" "$LZ_WORKDIR")"

LOG_FILE="deploy_sno_${SNO_CLUSTER_NAME}.log"
info "Running the SNO deployment on the Landing Zone (logging to $LOG_FILE)..."

set +e
# shellcheck disable=SC2086  # SSH_OPTS needs word splitting
ssh $SSH_OPTS "$LZ_SSH" "${REMOTE_ENV} bash -s" <<'REMOTE' 2>&1 | tee "$LOG_FILE"
set -euo pipefail
export PATH="${LZ_WORKDIR}/bin:${PATH}"
export KUBECONFIG="${LZ_WORKDIR}/ocp-cluster/auth/kubeconfig"
# Discovery registers the Agent in infraenv; the cluster resources live in a namespace
# named after the SNO, which ACM's import controller requires for Hive auto-import.
AGENT_NS=infraenv
NS="$SNO"

# Tolerant oc for the poll loops: a transient hub API error (blip/restart during
# the install) must not abort the script under set -e. Returns empty instead.
ocq() { oc --request-timeout=30s "$@" 2>/dev/null || true; }

# Dump install diagnostics: AgentClusterInstall conditions + state, Agent progress,
# conditions and failing validations, ManagedCluster conditions, and recent events.
dump_sno_diag() {
    echo "----- AgentClusterInstall/${SNO} conditions -----"
    ocq -n "$NS" get agentclusterinstall "$SNO" \
        -o jsonpath='{range .status.conditions[*]}{.type}={.status} ({.reason}) {.message}{"\n"}{end}'
    echo "----- AgentClusterInstall/${SNO} state -----"
    ocq -n "$NS" get agentclusterinstall "$SNO" \
        -o jsonpath='state={.status.debugInfo.state}{"\n"}stateInfo={.status.debugInfo.stateInfo}{"\n"}'
    if [ -n "${AGENT:-}" ]; then
        echo "----- Agent ${AGENT} -----"
        ocq -n "$AGENT_NS" get agent "$AGENT" \
            -o jsonpath='approved={.spec.approved} role={.status.role} stage={.status.progress.currentStage}{"\n"}{range .status.conditions[*]}  {.type}={.status} ({.reason}) {.message}{"\n"}{end}'
        echo "----- Agent ${AGENT} validations not passing -----"
        ocq -n "$AGENT_NS" get agent "$AGENT" -o json | jq -r \
            '.status.validationsInfo // {} | to_entries[] | .value[] | select(.status != "success") | "  \(.id): \(.status) - \(.message)"' 2>/dev/null || true
    fi
    echo "----- ManagedCluster/${SNO} conditions -----"
    ocq get managedcluster "$SNO" \
        -o jsonpath='{range .status.conditions[*]}{.type}={.status} ({.reason}) {.message}{"\n"}{end}'
    for ns in "$NS" "$AGENT_NS"; do
        echo "----- recent events (${ns}) -----"
        ocq -n "$ns" get events --sort-by=.lastTimestamp | tail -25
    done
}

echo "==> Resolving the hub version's mirror-backed ClusterImageSet"
# Install the SNO at the hub's own OpenShift version, from the hub mirror. enclave
# reconciles one openshift-v<ver>-disconnected per configured version
# (operators/advanced-cluster-management/acm_cis.yaml); the upstream quay.io one
# (local-cluster-image-set) is unreachable air-gapped.
HUB_VERSION=$(oc get clusterversion version -o jsonpath='{.status.desired.version}')
CIS="openshift-v${HUB_VERSION}-disconnected"
if [ -z "$HUB_VERSION" ] || ! oc get clusterimageset "$CIS" >/dev/null 2>&1; then
    echo "ERROR: ClusterImageSet ${CIS} for the hub version '${HUB_VERSION}' not found"
    oc get clusterimageset -o custom-columns=NAME:.metadata.name,RELEASE:.spec.releaseImage || true
    exit 1
fi
RELEASE_IMAGE=$(oc get clusterimageset "$CIS" -o jsonpath='{.spec.releaseImage}')
echo "    ClusterImageSet: $CIS -> $RELEASE_IMAGE"

echo "==> Checking the hub is ready for an install"
# A hub still rolling out a MachineConfig, or with the mirror registry or
# assisted-service down, cannot start the install (assisted-service cannot read the
# release image). Check once and fail with diagnostics rather than wait.
REGISTRY="${RELEASE_IMAGE%%/*}"
MCP_BUSY=$(ocq get mcp -o json | jq -r '[.items[] | .metadata.name as $p | .status.conditions[]?
    | select((.type == "Updating" or .type == "Degraded") and .status == "True")
    | "\($p):\(.type)"] | if length == 0 then "none" else join(",") end' 2>/dev/null || true)
MCP_BUSY="${MCP_BUSY:-unknown}"
REG_CODE=$(curl -sk -o /dev/null -m 10 -w '%{http_code}' "https://${REGISTRY}/v2/" || true)
AS_AVAIL=$(ocq -n multicluster-engine get deployment assisted-service -o jsonpath='{.status.availableReplicas}')
echo "    mcp-busy=${MCP_BUSY} registry=${REG_CODE} assisted-service=${AS_AVAIL:-0}"
if [ "$MCP_BUSY" != "none" ] || { [ "$REG_CODE" != 200 ] && [ "$REG_CODE" != 401 ]; } \
    || [ "${AS_AVAIL:-0}" -lt 1 ]; then
    echo "ERROR: hub not ready for the SNO install"
    oc get mcp || true
    oc get nodes || true
    oc -n quay-enterprise get pods -o wide || true
    oc -n multicluster-engine get deployment assisted-service || true
    exit 1
fi

SSHKEY=$(cat "${LZ_WORKDIR}/.ssh/id_rsa.pub")

echo "==> Creating namespace ${NS} with the install pull secret"
oc create namespace "$NS" --dry-run=client -o yaml | oc apply -f -
# The ClusterDeployment's pullSecretRef is namespace-local; reuse discovery's secret.
oc -n "$AGENT_NS" get secret pullsecret -o json \
    | jq --arg ns "$NS" '{apiVersion, kind, type, data, metadata: {name: .metadata.name, namespace: $ns}}' \
    | oc apply -f -

echo "==> Applying ManagedCluster, KlusterletAddonConfig, ClusterDeployment + AgentClusterInstall for ${SNO}"
oc apply -f - <<YAML
apiVersion: cluster.open-cluster-management.io/v1
kind: ManagedCluster
metadata:
  name: ${SNO}
  labels:
    cloud: auto-detect
    vendor: auto-detect
spec:
  hubAcceptsClient: true
---
apiVersion: agent.open-cluster-management.io/v1
kind: KlusterletAddonConfig
metadata:
  name: ${SNO}
  namespace: ${NS}
spec:
  applicationManager:
    enabled: true
  certPolicyController:
    enabled: true
  policyController:
    enabled: true
  searchCollector:
    enabled: true
---
apiVersion: hive.openshift.io/v1
kind: ClusterDeployment
metadata:
  name: ${SNO}
  namespace: ${NS}
spec:
  baseDomain: ${BASE_DOMAIN}
  clusterName: ${SNO}
  platform:
    agentBareMetal:
      agentSelector:
        matchLabels:
          enclave.openshift.io/sno: "${SNO}"
  clusterInstallRef:
    group: extensions.hive.openshift.io
    kind: AgentClusterInstall
    name: ${SNO}
    version: v1beta1
  pullSecretRef:
    name: pullsecret
---
apiVersion: extensions.hive.openshift.io/v1beta1
kind: AgentClusterInstall
metadata:
  name: ${SNO}
  namespace: ${NS}
spec:
  clusterDeploymentRef:
    name: ${SNO}
  imageSetRef:
    name: ${CIS}
  provisionRequirements:
    controlPlaneAgents: 1
  networking:
    clusterNetwork:
    - cidr: 10.128.0.0/14
      hostPrefix: 23
    serviceNetwork:
    - 172.30.0.0/16
    machineNetwork:
    - cidr: ${MACHINE_NET}
  sshPublicKey: "${SSHKEY}"
YAML

# wait_for_agent <minutes>: set AGENT to the Agent whose inventory has SNO_MAC.
wait_for_agent() {
    local deadline=$(( $(date +%s) + $1 * 60 ))
    while [ "$(date +%s)" -lt "$deadline" ]; do
        AGENT=$(ocq -n "$AGENT_NS" get agents -o json | jq -r --arg m "$SNO_MAC" \
            '.items[] | select(any(.status.inventory.interfaces[]?; (.macAddress // "" | ascii_downcase) == ($m | ascii_downcase))) | .metadata.name' 2>/dev/null | head -1 || true)
        [ -n "$AGENT" ] && return 0
        sleep 15
    done
    return 1
}

echo "==> Waiting for the discovered agent with MAC ${SNO_MAC} (up to 10m)"
AGENT=""
if ! wait_for_agent 10; then
    # Discovery can boot the ISO while the hub is still rolling out (e.g. the image
    # service drained mid-boot), leaving the host stuck without an agent. The hub passed
    # its readiness check, so reboot it into the discovery ISO once and wait again.
    echo "    No agent yet; rebooting BareMetalHost ${SNO} into the discovery ISO"
    oc -n "$AGENT_NS" annotate bmh "$SNO" reboot.metal3.io= --overwrite
    echo "==> Waiting for the discovered agent after the reboot (up to 15m)"
    wait_for_agent 15 || true
fi
if [ -z "$AGENT" ]; then
    echo "ERROR: agent with MAC ${SNO_MAC} not discovered, even after a reboot"
    echo "----- BareMetalHosts (${AGENT_NS}) -----"
    ocq -n "$AGENT_NS" get bmh -o wide
    ocq -n "$AGENT_NS" get bmh -o jsonpath='{range .items[*]}{.metadata.name}: state={.status.provisioning.state} error={.status.errorMessage}{"\n"}{end}'
    echo "----- InfraEnv (${AGENT_NS}) -----"
    ocq -n "$AGENT_NS" get infraenv -o wide
    echo "----- Agents (${AGENT_NS}) with MACs -----"
    ocq -n "$AGENT_NS" get agents -o wide
    ocq -n "$AGENT_NS" get agents -o jsonpath='{range .items[*]}{.metadata.name}: {range .status.inventory.interfaces[*]}{.macAddress}{" "}{end}{"\n"}{end}'
    echo "----- recent events (${AGENT_NS}) -----"
    ocq -n "$AGENT_NS" get events --sort-by=.lastTimestamp | tail -30
    exit 1
fi
echo "    Agent: $AGENT"

echo "==> Binding and approving the agent (${AGENT_NS} -> ClusterDeployment in ${NS})"
oc -n "$AGENT_NS" label agent "$AGENT" "enclave.openshift.io/sno=${SNO}" --overwrite
oc -n "$AGENT_NS" patch agent "$AGENT" --type merge \
    -p "{\"spec\":{\"approved\":true,\"hostname\":\"${SNO}-0\",\"role\":\"master\",\"clusterDeploymentName\":{\"name\":\"${SNO}\",\"namespace\":\"${NS}\"}}}"

echo "==> Waiting for the SNO installation to complete (up to 90m)"
DONE=""
# Wall-clock deadline, not an iteration count: when the hub API times out, each
# ocq can take up to its --request-timeout, so a fixed loop count could run far
# longer than intended. The deadline caps total wait regardless of per-poll cost.
DEADLINE=$(( $(date +%s) + 90 * 60 ))
i=0
# assisted-service retries a failed spec sync (e.g. an unreadable release image);
# 15 minutes of continuous SpecSynced=False means it will not recover on its own.
SYNC_FAIL_LIMIT=$((15 * 60))
SYNC_FAIL_SINCE=""
while [ "$(date +%s)" -lt "$DEADLINE" ]; do
    i=$((i + 1))
    SYNCED=$(ocq -n "$NS" get agentclusterinstall "$SNO" \
        -o jsonpath='{range .status.conditions[?(@.type=="SpecSynced")]}{.status}{end}')
    if [ "$SYNCED" = "False" ]; then
        SYNC_FAIL_SINCE="${SYNC_FAIL_SINCE:-$(date +%s)}"
        if [ $(( $(date +%s) - SYNC_FAIL_SINCE )) -ge "$SYNC_FAIL_LIMIT" ]; then
            echo "ERROR: AgentClusterInstall SpecSynced=False for 15m; the install cannot start"
            dump_sno_diag
            exit 1
        fi
    else
        SYNC_FAIL_SINCE=""
    fi
    # ocq tolerates transient API errors so a single blip never aborts the loop.
    COMPLETED=$(ocq -n "$NS" get agentclusterinstall "$SNO" \
        -o jsonpath='{range .status.conditions[?(@.type=="Completed")]}{.status}{end}')
    FAILED=$(ocq -n "$NS" get agentclusterinstall "$SNO" \
        -o jsonpath='{range .status.conditions[?(@.type=="Failed")]}{.status}{end}')
    STAGE=$(ocq -n "$AGENT_NS" get agent "$AGENT" -o jsonpath='{.status.progress.currentStage}')
    echo "    [$i] Completed=${COMPLETED:-?} Failed=${FAILED:-?} stage=${STAGE:-?}"
    if [ "$COMPLETED" = "True" ]; then DONE=yes; break; fi
    if [ "$FAILED" = "True" ]; then
        echo "ERROR: SNO installation failed"
        dump_sno_diag
        exit 1
    fi
    # Emit full diagnostics every ~10 polls so a stuck install is visible in the log.
    if [ "$((i % 10))" -eq 0 ]; then dump_sno_diag; fi
    sleep 30
done
if [ -z "$DONE" ]; then
    echo "ERROR: timed out waiting for the SNO installation after 90m"
    dump_sno_diag
    exit 1
fi

echo "==> Extracting the SNO kubeconfig"
umask 077
oc -n "$NS" get secret "${SNO}-admin-kubeconfig" -o jsonpath='{.data.kubeconfig}' \
    | base64 -d > "${LZ_WORKDIR}/${SNO}-kubeconfig"
echo "    SNO kubeconfig: ${LZ_WORKDIR}/${SNO}-kubeconfig"
# Baseline before the import: verify-sno-update-service expects ACM to set it later.
echo "    ClusterVersion upstream before the ACM import: '$(ocq --kubeconfig "${LZ_WORKDIR}/${SNO}-kubeconfig" \
    get clusterversion version -o jsonpath='{.spec.upstream}')'"

echo "==> Waiting for ACM to import ${SNO} (up to 20m)"
# The import controller links the ManagedCluster to the installed ClusterDeployment of
# the same name/namespace and deploys the klusterlet with Hive's admin kubeconfig.
IMPORTED=""
DEADLINE=$(( $(date +%s) + 20 * 60 ))
while [ "$(date +%s)" -lt "$DEADLINE" ]; do
    JOINED=$(ocq get managedcluster "$SNO" \
        -o jsonpath='{range .status.conditions[?(@.type=="ManagedClusterJoined")]}{.status}{end}')
    AVAILABLE=$(ocq get managedcluster "$SNO" \
        -o jsonpath='{range .status.conditions[?(@.type=="ManagedClusterConditionAvailable")]}{.status}{end}')
    echo "    Joined=${JOINED:-?} Available=${AVAILABLE:-?}"
    if [ "$JOINED" = "True" ] && [ "$AVAILABLE" = "True" ]; then IMPORTED=yes; break; fi
    sleep 30
done
if [ -z "$IMPORTED" ]; then
    echo "ERROR: ManagedCluster ${SNO} not Joined/Available after 20m"
    dump_sno_diag
    echo "----- klusterlet on the SNO -----"
    ocq --kubeconfig "${LZ_WORKDIR}/${SNO}-kubeconfig" get pods -n open-cluster-management-agent -o wide
    exit 1
fi
echo "==> SNO ${SNO} installed and imported into ACM"
REMOTE
SNO_EXIT_CODE=${PIPESTATUS[0]}
set -e

echo ""
if [ "$SNO_EXIT_CODE" -ne 0 ]; then
    error "SNO deployment failed: $SNO_CLUSTER_NAME (exit code: $SNO_EXIT_CODE)"
    exit 1
fi

# The SNO admin kubeconfig stays on the Landing Zone; later steps use it there.
success "SNO deployed and imported: $SNO_CLUSTER_NAME (kubeconfig on LZ: ${LZ_WORKDIR}/${SNO_CLUSTER_NAME}-kubeconfig)"
