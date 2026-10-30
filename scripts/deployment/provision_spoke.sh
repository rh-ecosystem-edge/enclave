#!/bin/bash
# Provision an SNO spoke cluster from a discovered agent (late binding).
#
# Phase 07 discovery boots the spoke VM and registers it as an unbound Agent in
# the `infraenv` namespace (see generate_enclave_vars.sh, which adds the spoke to
# discovery_hosts). This script creates the spoke ClusterDeployment +
# AgentClusterInstall, binds/approves the discovered agent, waits for the SNO
# install, and extracts the spoke kubeconfig. CI-only; consumes cluster-env.sh.
#
# Usage: ./provision_spoke.sh

set -euo pipefail

# Detect Enclave repository root
SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" &>/dev/null && pwd)"
ENCLAVE_DIR="$(cd -- "${SCRIPT_DIR}/../.." &>/dev/null && pwd)"

# Source shared utilities
source "${ENCLAVE_DIR}/scripts/lib/output.sh"
source "${ENCLAVE_DIR}/scripts/lib/validation.sh"
source "${ENCLAVE_DIR}/scripts/lib/config.sh"
source "${ENCLAVE_DIR}/scripts/lib/network.sh"
source "${ENCLAVE_DIR}/scripts/lib/ssh.sh"

# Source cluster environment (single source of truth from vm_infra.py)
load_cluster_env

# Nothing to do when no spoke was created. vm_infra.py emits only 0 or 1; validate
# the exact contract (fail closed on anything else rather than a numeric-test error).
SPOKE_COUNT="${ENCLAVE_SPOKE_COUNT:-0}"
case "$SPOKE_COUNT" in
    0) info "ENCLAVE_SPOKE_COUNT=0; no spoke to provision, skipping"; exit 0 ;;
    1) ;;
    *) error "ENCLAVE_SPOKE_COUNT must be 0 or 1, got: '${SPOKE_COUNT}'"; exit 1 ;;
esac

# Spoke facts emitted by vm_infra.py.
SPOKE_CLUSTER_NAME="${ENCLAVE_SPOKE_CLUSTER_NAME:?ENCLAVE_SPOKE_CLUSTER_NAME not set}"
BASE_DOMAIN="${ENCLAVE_SPOKE_BASE_DOMAIN:-${ENCLAVE_BASE_DOMAIN:?base domain not set}}"
SPOKE_MAC="${ENCLAVE_SPOKE_0_CLUSTER_MAC:?ENCLAVE_SPOKE_0_CLUSTER_MAC not set}"
MACHINE_NETWORK="${ENCLAVE_CLUSTER_NETWORK:?ENCLAVE_CLUSTER_NETWORK not set}"

# Landing Zone IP (static DHCP lease, from cluster-env.sh)
CLUSTER_IP="${ENCLAVE_LZ_CLUSTER_IP:-}"
if [ -z "$CLUSTER_IP" ]; then
    error "Could not determine Landing Zone IP address"
    exit 1
fi

setup_ssh_config "$CLUSTER_IP"

info "Provisioning spoke cluster: $SPOKE_CLUSTER_NAME"
info "  Base domain:     $BASE_DOMAIN"
info "  Agent MAC:       $SPOKE_MAC"
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
REMOTE_ENV="$(printf 'SPOKE=%q BASE_DOMAIN=%q SPOKE_MAC=%q MACHINE_NET=%q LZ_WORKDIR=%q' \
    "$SPOKE_CLUSTER_NAME" "$BASE_DOMAIN" "$SPOKE_MAC" "$MACHINE_NETWORK" "$LZ_WORKDIR")"

LOG_FILE="provision_spoke_${SPOKE_CLUSTER_NAME}.log"
info "Running spoke provisioning on the Landing Zone (logging to $LOG_FILE)..."

set +e
# shellcheck disable=SC2086  # SSH_OPTS needs word splitting
ssh $SSH_OPTS "$LZ_SSH" "${REMOTE_ENV} bash -s" <<'REMOTE' 2>&1 | tee "$LOG_FILE"
set -euo pipefail
export PATH="${LZ_WORKDIR}/bin:${PATH}"
export KUBECONFIG="${LZ_WORKDIR}/ocp-cluster/auth/kubeconfig"
NS=infraenv

# Tolerant oc for the poll loops: a transient hub API error (blip/restart during
# the spoke install) must not abort the script under set -e. Returns empty instead.
ocq() { oc --request-timeout=30s "$@" 2>/dev/null || true; }

# Dump spoke install diagnostics: AgentClusterInstall conditions + state, agent
# progress/conditions, and recent namespace events.
dump_spoke_diag() {
    echo "----- AgentClusterInstall/${SPOKE} conditions -----"
    ocq -n "$NS" get agentclusterinstall "$SPOKE" \
        -o jsonpath='{range .status.conditions[*]}{.type}={.status} ({.reason}) {.message}{"\n"}{end}'
    echo "----- AgentClusterInstall/${SPOKE} state -----"
    ocq -n "$NS" get agentclusterinstall "$SPOKE" \
        -o jsonpath='state={.status.debugInfo.state}{"\n"}stateInfo={.status.debugInfo.stateInfo}{"\n"}'
    if [ -n "${AGENT:-}" ]; then
        echo "----- Agent ${AGENT} -----"
        ocq -n "$NS" get agent "$AGENT" \
            -o jsonpath='approved={.spec.approved} role={.status.role} stage={.status.progress.currentStage}{"\n"}{range .status.conditions[*]}  {.type}={.status} ({.reason}) {.message}{"\n"}{end}'
    fi
    echo "----- recent events (${NS}) -----"
    ocq -n "$NS" get events --sort-by=.lastTimestamp | tail -25
}

echo "==> Resolving a mirror-backed ClusterImageSet"
# Disconnected spoke installs must use a ClusterImageSet whose releaseImage is the
# hub mirror, not the upstream quay.io pullspec (local-cluster-image-set) that
# assisted-service cannot reach air-gapped. enclave reconciles these as
# openshift-v<ver>-disconnected (operators/advanced-cluster-management/acm_cis.yaml).
CIS=$(oc get clusterimageset -o json | jq -r \
    '[.items[] | select(.metadata.name | endswith("-disconnected"))][0].metadata.name // empty')
if [ -z "$CIS" ]; then
    # Fall back to any CIS whose releaseImage is not the upstream quay.io pullspec.
    CIS=$(oc get clusterimageset -o json | jq -r \
        '[.items[] | select((.spec.releaseImage // "") | startswith("quay.io/") | not)][0].metadata.name // empty')
fi
if [ -z "$CIS" ]; then
    echo "ERROR: no mirror-backed ClusterImageSet found on the hub"
    oc get clusterimageset -o custom-columns=NAME:.metadata.name,RELEASE:.spec.releaseImage || true
    exit 1
fi
echo "    ClusterImageSet: $CIS -> $(oc get clusterimageset "$CIS" -o jsonpath='{.spec.releaseImage}')"

SSHKEY=$(cat "${LZ_WORKDIR}/.ssh/id_rsa.pub")

echo "==> Applying ClusterDeployment + AgentClusterInstall for ${SPOKE}"
oc apply -f - <<YAML
apiVersion: hive.openshift.io/v1
kind: ClusterDeployment
metadata:
  name: ${SPOKE}
  namespace: ${NS}
spec:
  baseDomain: ${BASE_DOMAIN}
  clusterName: ${SPOKE}
  platform:
    agentBareMetal:
      agentSelector:
        matchLabels:
          enclave.openshift.io/spoke: "${SPOKE}"
  clusterInstallRef:
    group: extensions.hive.openshift.io
    kind: AgentClusterInstall
    name: ${SPOKE}
    version: v1beta1
  pullSecretRef:
    name: pullsecret
---
apiVersion: extensions.hive.openshift.io/v1beta1
kind: AgentClusterInstall
metadata:
  name: ${SPOKE}
  namespace: ${NS}
spec:
  clusterDeploymentRef:
    name: ${SPOKE}
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

echo "==> Waiting for the discovered agent with MAC ${SPOKE_MAC}"
AGENT=""
for _ in $(seq 1 60); do
    AGENT=$(ocq -n "$NS" get agents -o json | jq -r --arg m "$SPOKE_MAC" \
        '.items[] | select(any(.status.inventory.interfaces[]?; (.macAddress // "" | ascii_downcase) == ($m | ascii_downcase))) | .metadata.name' 2>/dev/null | head -1 || true)
    if [ -n "$AGENT" ]; then break; fi
    sleep 15
done
if [ -z "$AGENT" ]; then
    echo "ERROR: spoke agent with MAC ${SPOKE_MAC} not discovered after ~15m"
    echo "----- BareMetalHosts (${NS}) -----"
    ocq -n "$NS" get bmh -o wide
    ocq -n "$NS" get bmh -o jsonpath='{range .items[*]}{.metadata.name}: state={.status.provisioning.state} error={.status.errorMessage}{"\n"}{end}'
    echo "----- InfraEnv (${NS}) -----"
    ocq -n "$NS" get infraenv -o wide
    echo "----- Agents (${NS}) with MACs -----"
    ocq -n "$NS" get agents -o wide
    ocq -n "$NS" get agents -o jsonpath='{range .items[*]}{.metadata.name}: {range .status.inventory.interfaces[*]}{.macAddress}{" "}{end}{"\n"}{end}'
    echo "----- recent events (${NS}) -----"
    ocq -n "$NS" get events --sort-by=.lastTimestamp | tail -30
    exit 1
fi
echo "    Agent: $AGENT"

echo "==> Binding and approving the agent"
oc -n "$NS" label agent "$AGENT" "enclave.openshift.io/spoke=${SPOKE}" --overwrite
oc -n "$NS" patch agent "$AGENT" --type merge \
    -p "{\"spec\":{\"approved\":true,\"hostname\":\"${SPOKE}-0\",\"role\":\"master\",\"clusterDeploymentName\":{\"name\":\"${SPOKE}\",\"namespace\":\"${NS}\"}}}"

echo "==> Waiting for the SNO installation to complete (up to 90m)"
DONE=""
# Wall-clock deadline, not an iteration count: when the hub API times out, each
# ocq can take up to its --request-timeout, so a fixed loop count could run far
# longer than intended. The deadline caps total wait regardless of per-poll cost.
DEADLINE=$(( $(date +%s) + 90 * 60 ))
i=0
while [ "$(date +%s)" -lt "$DEADLINE" ]; do
    i=$((i + 1))
    # ocq tolerates transient API errors so a single blip never aborts the loop.
    COMPLETED=$(ocq -n "$NS" get agentclusterinstall "$SPOKE" \
        -o jsonpath='{range .status.conditions[?(@.type=="Completed")]}{.status}{end}')
    FAILED=$(ocq -n "$NS" get agentclusterinstall "$SPOKE" \
        -o jsonpath='{range .status.conditions[?(@.type=="Failed")]}{.status}{end}')
    STAGE=$(ocq -n "$NS" get agent "$AGENT" -o jsonpath='{.status.progress.currentStage}')
    echo "    [$i] Completed=${COMPLETED:-?} Failed=${FAILED:-?} stage=${STAGE:-?}"
    if [ "$COMPLETED" = "True" ]; then DONE=yes; break; fi
    if [ "$FAILED" = "True" ]; then
        echo "ERROR: spoke installation failed"
        dump_spoke_diag
        exit 1
    fi
    # Emit full diagnostics every ~10 polls so a stuck install is visible in the log.
    if [ "$((i % 10))" -eq 0 ]; then dump_spoke_diag; fi
    sleep 30
done
if [ -z "$DONE" ]; then
    echo "ERROR: timed out waiting for spoke installation after 90m"
    dump_spoke_diag
    exit 1
fi

echo "==> Extracting spoke kubeconfig"
umask 077
oc -n "$NS" get secret "${SPOKE}-admin-kubeconfig" -o jsonpath='{.data.kubeconfig}' \
    | base64 -d > "${LZ_WORKDIR}/${SPOKE}-kubeconfig"
echo "    Spoke kubeconfig: ${LZ_WORKDIR}/${SPOKE}-kubeconfig"
echo "==> Spoke ${SPOKE} installed"
REMOTE
SPOKE_EXIT_CODE=${PIPESTATUS[0]}
set -e

echo ""
if [ "$SPOKE_EXIT_CODE" -ne 0 ]; then
    error "Spoke provisioning failed: $SPOKE_CLUSTER_NAME (exit code: $SPOKE_EXIT_CODE)"
    exit 1
fi

# Copy the spoke kubeconfig back to the local working dir for later steps/artifacts.
# umask 077 so the admin kubeconfig is never briefly world/group-readable on the runner.
LOCAL_KUBECONFIG="${WORKING_DIR}/${SPOKE_CLUSTER_NAME}-kubeconfig"
# shellcheck disable=SC2086  # SSH_OPTS needs word splitting
if (umask 077; scp $SSH_OPTS "${LZ_SSH}:${LZ_WORKDIR}/${SPOKE_CLUSTER_NAME}-kubeconfig" "$LOCAL_KUBECONFIG") 2>/dev/null; then
    chmod 600 "$LOCAL_KUBECONFIG"
    success "Spoke provisioned: $SPOKE_CLUSTER_NAME (kubeconfig: $LOCAL_KUBECONFIG)"
else
    success "Spoke provisioned: $SPOKE_CLUSTER_NAME (kubeconfig on LZ: ${LZ_WORKDIR}/${SPOKE_CLUSTER_NAME}-kubeconfig)"
fi
