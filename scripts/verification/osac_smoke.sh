#!/bin/bash
# OSAC CLI post-install smoke test (runs on the Landing Zone).
#
# Complements plugins/osac/tasks/post-validate.yaml (which checks pod/replica
# health via the Kubernetes API) with a true end-to-end check: it exercises the
# fulfillment API through its OpenShift route using the osac CLI, proving that
# the route, TLS, Keycloak-backed authz and the gRPC service all work together.
#
# Sections (each printed under a "===== N. Title =====" banner in the log):
#   1. Debug info — dump cluster and OSAC namespace state up front.
#   2. Deployment health checks — gate on core deployments and the AAP gateway.
#   3. Setup & login — resolve the route, extract the osac CLI from the deployed
#      image, read the controller credentials, and log in.
#   4. API read checks — confirm identity and perform read calls end-to-end.
#   5. Tenant provisioning — create a Tenant CR, wait for the operator to mark it
#      Ready, then tear it down (creates/deletes cluster resources; for the
#      ephemeral e2e cluster).
#
# Must run on the Landing Zone, where oc is on PATH and KUBECONFIG points at the
# managed cluster. No external network access is required: the CLI comes from the
# already-deployed image. It does require a fulfillment-service image that bakes
# in the osac CLI (see the upstream "bake the osac CLI into the fulfillment-service
# image" change); older images that predate it will fail the extraction step.
#
# Every command is traced (set -x) so the exact steps appear in the log; the
# client secret is excluded from the trace.
#
# Environment variables:
#   OSAC_NAMESPACE       Namespace of the OSAC deployment (default: osac)
#   OSAC_CHART_VERSION   Deployed OSAC chart version, reported for traceability
#                        (optional; the CLI is taken from the deployed image)
#   KEYCLOAK_NAMESPACE   Namespace of the Keycloak deployment (default: keycloak)
#   OSAC_KEYCLOAK_REALM  OSAC Keycloak realm name (default: osac)
#   OSAC_CONTROLLER_IDENTITY  Expected controller identity reported by whoami
#                        (default: service-account-osac-controller)

set -euo pipefail

# Detect Enclave repository root
SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" &>/dev/null && pwd)"
ENCLAVE_DIR="$(cd -- "${SCRIPT_DIR}/../.." &>/dev/null && pwd)"

# Source shared logging utilities
source "${ENCLAVE_DIR}/scripts/lib/output.sh"

NS="${OSAC_NAMESPACE:-osac}"
# Deployment whose image carries the version-matched osac CLI, and the path the
# CLI is baked to inside that image.
SRC_DEPLOYMENT="fulfillment-grpc-server"
IMAGE_CLI_PATH="/usr/local/bin/osac"

# Isolate CLI config/cache/binary in a temp dir so the smoke test leaves no
# state behind (secrets go to OSAC_CONFIG, never the user keyring).
WORK="$(mktemp -d)"
# Track a /etc/hosts entry added by this run so cleanup can remove exactly it
# (leaving any pre-existing entry untouched).
HOSTS_ENTRY=""
# Track resources created by the tenant provisioning test so cleanup removes
# them even if the run aborts partway (empty until that step creates them).
TENANT_NAME=""
TENANT_SC=""
cleanup() {
    rm -rf "${WORK}"
    if [ -n "${HOSTS_ENTRY}" ]; then
        # Remove exactly the line this run appended, escaping regex metachars so
        # the pattern matches the literal entry (edit in place to keep perms).
        _hosts_re="$(printf '%s' "${HOSTS_ENTRY}" | sed 's/[][\\.^$*/]/\\&/g')"
        sudo sed -i "/^${_hosts_re}$/d" /etc/hosts 2>/dev/null || true
    fi
    if [ -n "${TENANT_NAME}" ]; then
        oc delete tenant "${TENANT_NAME}" -n "${NS}" --ignore-not-found --wait=false >/dev/null 2>&1 || true
        oc delete namespace "${TENANT_NAME}" --ignore-not-found --wait=false >/dev/null 2>&1 || true
    fi
    if [ -n "${TENANT_SC}" ]; then
        oc label storageclass "${TENANT_SC}" osac.openshift.io/tenant- >/dev/null 2>&1 || true
    fi
}
trap cleanup EXIT

export OSAC_CONFIG="${WORK}/config"
export OSAC_CACHE="${WORK}/cache"
mkdir -p "${OSAC_CONFIG}" "${OSAC_CACHE}"

OSAC_BIN="${WORK}/osac"

# Run a read-only `osac get <object>` call, failing on any error. `osac get`
# exits 0 even for an unknown object type (printing "There is no object named
# ..."), so also fail on that marker; an empty-but-valid object is fine.
read_call() {
    local object="$1" out
    info "Performing read call (get ${object})"
    if ! out="$("${OSAC_BIN}" get "${object}" 2>&1)"; then
        error "osac get ${object} failed:"
        printf '%s\n' "${out}" >&2
        return 1
    fi
    printf '%s\n' "${out}"
    if printf '%s\n' "${out}" | grep -qi 'there is no object named'; then
        error "osac get ${object}: unknown object type (CLI returned no such object)"
        return 1
    fi
}

# Core deployments that must be fully rolled out for the API to work. Other
# deployments may be optional or scaled to zero, so they are not gated.
CORE_DEPLOYMENTS="fulfillment-grpc-server fulfillment-rest-gateway fulfillment-controller fulfillment-ingress-proxy osac-operator"

# Fail if any core deployment does not have available == desired replicas.
assert_deployments_available() {
    local dep desired avail rc=0
    for dep in ${CORE_DEPLOYMENTS}; do
        desired="$(oc -n "${NS}" get deploy "${dep}" -o jsonpath='{.spec.replicas}' 2>/dev/null)"
        if [ -z "${desired}" ]; then
            error "core deployment ${dep} not found in namespace ${NS}"
            rc=1
            continue
        fi
        avail="$(oc -n "${NS}" get deploy "${dep}" -o jsonpath='{.status.availableReplicas}' 2>/dev/null)"
        if [ "${avail:-0}" != "${desired}" ]; then
            error "core deployment ${dep} not available: ${avail:-0}/${desired} replicas ready"
            rc=1
        else
            info "deployment ${dep} available (${avail}/${desired})"
        fi
    done
    return "${rc}"
}

# Verify AAP is accessible per the osac-installer "Accessing AAP" docs: resolve
# its route, read the admin password, and make an authenticated request as admin.
# Tracing is disabled around the password so it never reaches an xtrace.
check_aap_access() {
    local aap_name aap_route ip pw code xtrace=""
    aap_name="$(oc -n "${NS}" get ansibleautomationplatform \
        -o jsonpath='{.items[0].metadata.name}' 2>/dev/null)"
    if [ -z "${aap_name}" ]; then
        error "no AnsibleAutomationPlatform resource found in namespace ${NS}"
        return 1
    fi
    aap_route="$(oc -n "${NS}" get route "${aap_name}" \
        -o jsonpath='{.spec.host}' 2>/dev/null)"
    ip="$(awk '/[[:space:]].*\.apps\./ {print $1; exit}' /etc/hosts)"
    if [ -z "${aap_route}" ] || [ -z "${ip}" ]; then
        error "AAP route or ingress IP not found (route=${aap_route:-<none>})"
        return 1
    fi
    case $- in *x*) xtrace=1; set +x ;; esac
    pw="$(oc -n "${NS}" get secret osac-aap-admin-password \
        -o jsonpath='{.data.password}' 2>/dev/null | base64 -d)"
    if [ -z "${pw}" ]; then
        [ -n "${xtrace}" ] && set -x
        error "could not read osac-aap-admin-password secret in namespace ${NS}"
        return 1
    fi
    code="$(curl -sk -u "admin:${pw}" --resolve "${aap_route}:443:${ip}" \
        -o /dev/null -w '%{http_code}' \
        "https://${aap_route}/api/gateway/v1/me/" 2>/dev/null)"
    [ -n "${xtrace}" ] && set -x
    if [ "${code}" != "200" ]; then
        error "AAP admin access failed: GET /api/gateway/v1/me/ returned HTTP ${code:-<none>} (${aap_route})"
        return 1
    fi
    info "AAP accessible as admin (${aap_route})"
    return 0
}

# Best-effort debug: print the osac Keycloak realm user count (a realm with zero
# users points at broken user provisioning, e.g. OSAC-2504). Uses curl --resolve
# so it needs no /etc/hosts change. Tracing is disabled around the admin secret
# and token so neither reaches an xtrace, then restored to the caller's setting.
dump_realm_user_count() {
    local kc_ns kc_route kc_ip kc_user kc_pass token count xtrace=""
    kc_ns="${KEYCLOAK_NAMESPACE:-keycloak}"
    kc_route="$(oc -n "${kc_ns}" get route -o jsonpath='{.items[0].spec.host}' 2>/dev/null)"
    kc_ip="$(awk '/[[:space:]].*\.apps\./ {print $1; exit}' /etc/hosts)"
    if [ -z "${kc_route}" ] || [ -z "${kc_ip}" ]; then
        info "keycloak route/ingress IP not found; skipping realm user count"
        return 0
    fi
    case $- in *x*) xtrace=1; set +x ;; esac
    kc_user="$(oc -n "${kc_ns}" get secret keycloak-initial-admin \
        -o jsonpath='{.data.username}' 2>/dev/null | base64 -d)"
    kc_pass="$(oc -n "${kc_ns}" get secret keycloak-initial-admin \
        -o jsonpath='{.data.password}' 2>/dev/null | base64 -d)"
    token="$(curl -sk --resolve "${kc_route}:443:${kc_ip}" \
        "https://${kc_route}/realms/master/protocol/openid-connect/token" \
        -d grant_type=password -d client_id=admin-cli \
        -d "username=${kc_user}" --data-urlencode "password=${kc_pass}" \
        | sed -n 's/.*"access_token":"\([^"]*\)".*/\1/p')"
    if [ -n "${token}" ]; then
        count="$(curl -sk --resolve "${kc_route}:443:${kc_ip}" \
            -H "Authorization: Bearer ${token}" \
            "https://${kc_route}/admin/realms/${OSAC_KEYCLOAK_REALM:-osac}/users/count" 2>/dev/null)"
        info "osac realm user count: ${count:-<unavailable>}"
    else
        info "could not obtain keycloak admin token; skipping realm user count"
    fi
    [ -n "${xtrace}" ] && set -x
    return 0
}

# Exercise tenant provisioning through the operator: create a namespace and a
# Tenant CR (with a StorageClass labeled for it), wait for the operator to mark
# it Ready, then tear everything down. Creates and deletes cluster-scoped
# resources, so it is meant for the ephemeral e2e cluster. Cleanup runs on exit
# via the trap as well, so a mid-run failure never leaves resources behind.
run_tenant_test() {
    TENANT_NAME="osac-smoke-$$"
    TENANT_SC="$(oc get storageclass -o jsonpath='{.items[?(@.metadata.annotations.storageclass\.kubernetes\.io/is-default-class=="true")].metadata.name}' 2>/dev/null)"
    [ -z "${TENANT_SC}" ] && TENANT_SC="$(oc get storageclass -o jsonpath='{.items[0].metadata.name}' 2>/dev/null)"
    if [ -z "${TENANT_SC}" ]; then
        error "no StorageClass found; cannot run tenant provisioning test"
        return 1
    fi
    info "Creating namespace ${TENANT_NAME} and labeling StorageClass ${TENANT_SC}"
    oc create namespace "${TENANT_NAME}"
    oc label storageclass "${TENANT_SC}" "osac.openshift.io/tenant=${TENANT_NAME}" --overwrite
    info "Applying Tenant CR ${TENANT_NAME}"
    oc apply -f - <<YAML
apiVersion: osac.openshift.io/v1alpha1
kind: Tenant
metadata:
  name: ${TENANT_NAME}
  namespace: ${NS}
spec: {}
YAML
    info "Waiting for tenant ${TENANT_NAME} to become Ready"
    if ! oc wait --for=jsonpath='{.status.phase}'=Ready \
            "tenant/${TENANT_NAME}" -n "${NS}" --timeout=60s; then
        error "tenant ${TENANT_NAME} did not reach Ready"
        oc get tenant "${TENANT_NAME}" -n "${NS}" -o yaml 2>&1 | tail -n 30 || true
        return 1
    fi
    info "Tenant ${TENANT_NAME} is Ready; tearing it down"
    oc delete tenant "${TENANT_NAME}" -n "${NS}" --ignore-not-found --wait=false
    oc delete namespace "${TENANT_NAME}" --ignore-not-found --wait=false
    oc label storageclass "${TENANT_SC}" osac.openshift.io/tenant- 2>/dev/null || true
    TENANT_NAME=""
    TENANT_SC=""
    return 0
}

# Trace every command from here on (setup boilerplate above is skipped).
set -x

# General debug info. Dump cluster/namespace state up front so the CI log always
# has deployment context, even if a later step fails. Best-effort: each command
# may fail without aborting the smoke test (set -e is active).
info "===== 1. Debug info (namespace ${NS}) ====="
info "--- oc version ---"
oc version 2>&1 || true
info "--- nodes ---"
oc get nodes 2>&1 || true
info "--- pods ---"
oc -n "${NS}" get pods -o wide 2>&1 || true
info "--- deploy / statefulset / svc / route / certificate ---"
oc -n "${NS}" get deploy,statefulset,svc,route,certificate 2>&1 || true
info "--- keycloak namespace (${KEYCLOAK_NAMESPACE:-keycloak}) ---"
oc -n "${KEYCLOAK_NAMESPACE:-keycloak}" get keycloak,pods,route 2>&1 || true
info "--- osac realm user count ---"
dump_realm_user_count
info "--- recent events (last 30) ---"
oc -n "${NS}" get events --sort-by=.lastTimestamp 2>&1 | tail -n 30 || true
info "--- not-ready pods: describe & logs ---"
# Pods whose ready count != desired and are not Completed (ImagePullBackOff,
# CrashLoopBackOff, Pending, Error, ...). Empty output means all pods are ready.
_not_ready="$(oc -n "${NS}" get pods --no-headers 2>/dev/null \
    | awk '{split($2, a, "/"); if (a[1] != a[2] && $3 != "Completed") print $1}')"
if [ -z "${_not_ready}" ]; then
    info "(all pods ready)"
else
    for _pod in ${_not_ready}; do
        info "--- describe pod/${_pod} (last 40 lines) ---"
        oc -n "${NS}" describe pod "${_pod}" 2>&1 | tail -n 40 || true
        info "--- logs pod/${_pod} (current, last 50) ---"
        oc -n "${NS}" logs "${_pod}" --all-containers --tail=50 2>&1 || true
        info "--- logs pod/${_pod} (previous, last 50) ---"
        oc -n "${NS}" logs "${_pod}" --all-containers --previous --tail=50 2>&1 || true
    done
fi

info "===== 2. Deployment health checks ====="
# Gate on core deployments being fully rolled out before doing anything else, so
# a partial deployment fails here with a clear message rather than as a login error.
info "Checking core deployment availability"
if ! assert_deployments_available; then
    error "One or more core OSAC deployments are not available; aborting smoke test."
    exit 1
fi

# Verify AAP (an OSAC fulfillment dependency) is accessible as admin.
info "Checking AAP access"
if ! check_aap_access; then
    error "AAP is not accessible; aborting smoke test."
    exit 1
fi

info "===== 3. Setup & login ====="
# Resolve the fulfillment-api route.
info "Reading fulfillment-api route from namespace ${NS}"
ROUTE="$(oc -n "${NS}" get route fulfillment-api -o jsonpath='{.spec.host}')"
if [ -z "${ROUTE}" ]; then
    error "Could not resolve fulfillment-api route host in namespace ${NS}"
    exit 1
fi

if ! getent hosts "${ROUTE}" >/dev/null 2>&1; then
    # dnsmasq does not resolve dynamically created routes; point the route at the
    # ingress VIP, reusing the IP the plugin already registered for its other
    # app routes (keycloak/aap) in /etc/hosts.
    INGRESS_IP="$(awk '/[[:space:]].*\.apps\./ {print $1; exit}' /etc/hosts)"
    if [ -z "${INGRESS_IP}" ]; then
        error "${ROUTE} does not resolve and no ingress VIP found in /etc/hosts"
        exit 1
    fi
    info "Adding /etc/hosts entry: ${INGRESS_IP} ${ROUTE}"
    HOSTS_ENTRY="${INGRESS_IP} ${ROUTE}"
    echo "${HOSTS_ENTRY}" | sudo tee -a /etc/hosts >/dev/null
fi

ENDPOINT="https://${ROUTE}"
info "Fulfillment API endpoint: ${ENDPOINT}"

# Extract the version-matched osac CLI from the deployed image.
[ -n "${OSAC_CHART_VERSION:-}" ] && info "OSAC chart version: ${OSAC_CHART_VERSION}"
info "Locating deployed fulfillment-service image"
# Use the immutable digest a running pod actually runs
# (containerStatuses[].imageID) rather than the deployment's (possibly mutable)
# image tag, so the extracted CLI is exactly version-matched to the running
# service. Fail if no running pod exposes a repo@digest imageID.
CONTAINER="$(oc -n "${NS}" get deploy "${SRC_DEPLOYMENT}" \
    -o jsonpath='{.spec.template.spec.containers[0].name}')"
SELECTOR="$(oc -n "${NS}" get deploy "${SRC_DEPLOYMENT}" \
    -o go-template='{{range $k, $v := .spec.selector.matchLabels}}{{$k}}={{$v}},{{end}}')"
SELECTOR="${SELECTOR%,}"
if [ -z "${SELECTOR}" ]; then
    error "Could not determine pod selector for deployment ${SRC_DEPLOYMENT} in namespace ${NS}"
    exit 1
fi
IMAGE_ID="$(oc -n "${NS}" get pods -l "${SELECTOR}" \
    --field-selector=status.phase=Running \
    -o jsonpath="{.items[0].status.containerStatuses[?(@.name=='${CONTAINER}')].imageID}")"
IMAGE="${IMAGE_ID#docker-pullable://}"
case "${IMAGE}" in
    *@sha256:*) ;;  # full repo@digest ref, as expected
    *)
        error "Could not determine the image digest for container ${CONTAINER} in deployment ${SRC_DEPLOYMENT} (namespace ${NS})"
        error "No Running pod with a repo@sha256 imageID was found."
        exit 1
        ;;
esac
info "Deployed image: ${IMAGE}"

info "Extracting osac CLI (${IMAGE_CLI_PATH}) from image"
# oc image extract pulls only the requested path; --confirm allows extracting
# into the non-empty work dir. The extracted file is not executable by default.
if ! oc image extract "${IMAGE}" --path "${IMAGE_CLI_PATH}:${WORK}" --confirm; then
    error "Failed to pull/extract from ${IMAGE} (image not reachable from here?)"
    exit 1
fi
# oc image extract treats a missing path as success (it extracts nothing), so an
# empty/absent binary means the CLI is not present in this image.
if [ ! -s "${OSAC_BIN}" ]; then
    error "osac CLI not found at ${IMAGE_CLI_PATH} in ${IMAGE}"
    error "The deployed fulfillment-service image likely predates the baked-in osac CLI."
    exit 1
fi
chmod +x "${OSAC_BIN}"

# Report the exact binary version that will run (helps correlate CLI behaviour
# with a specific build in CI logs).
CLI_VERSION_OUT="$("${OSAC_BIN}" version 2>/dev/null | head -n1)"
info "osac CLI: ${CLI_VERSION_OUT:-unknown (version subcommand unavailable)}"

# Read controller OAuth credentials. client-id is not sensitive; the client
# secret is read and used with tracing disabled so it never appears in an xtrace
# (set -x would otherwise print both the secret's assignment and its expansion in
# the login command).
info "Reading controller credentials from namespace ${NS}"
CLIENT_ID="$(oc -n "${NS}" get secret fulfillment-controller-credentials \
    -o jsonpath='{.data.client-id}' | base64 -d)"

set +x
CLIENT_SECRET="$(oc -n "${NS}" get secret fulfillment-controller-credentials \
    -o jsonpath='{.data.client-secret}' | base64 -d)"
if [ -z "${CLIENT_ID}" ] || [ -z "${CLIENT_SECRET}" ]; then
    error "Missing client-id/client-secret in fulfillment-controller-credentials secret"
    exit 1
fi

# Log in via the OAuth credentials flow. The CLI's own stdout/stderr is left
# to stream so its error message surfaces directly in the log on failure. Tracing
# is off here, so echo the login command manually with the secret redacted.
info "Logging in to ${ENDPOINT} (OAuth credentials flow, client ${CLIENT_ID})"
echo "+ osac login --insecure --flow credentials --client-id ${CLIENT_ID} --client-secret <redacted> ${ENDPOINT}"
if ! "${OSAC_BIN}" login --insecure --flow credentials \
        --client-id "${CLIENT_ID}" --client-secret "${CLIENT_SECRET}" \
        "${ENDPOINT}"; then
    error "osac login failed against ${ENDPOINT} (client ${CLIENT_ID})"
    error "Check the fulfillment-ingress-proxy/grpc-server pods, the route, and Keycloak availability."
    exit 1
fi
# Re-enable tracing now that the secret is no longer in play.
set -x
info "Login succeeded"

info "===== 4. API read checks ====="
# Confirm identity.
info "Verifying identity (whoami)"
if ! WHOAMI_OUT="$("${OSAC_BIN}" whoami 2>&1)"; then
    error "osac whoami failed:"
    printf '%s\n' "${WHOAMI_OUT}" >&2
    exit 1
fi
printf '%s\n' "${WHOAMI_OUT}"
# "Logged in as: <identity>" / "Roles: <roles>" — surface them in the summary.
IDENTITY="$(printf '%s\n' "${WHOAMI_OUT}" | sed -n 's/^Logged in as:[[:space:]]*//p')"
ROLES="$(printf '%s\n' "${WHOAMI_OUT}" | sed -n 's/^Roles:[[:space:]]*//p')"

# Assert the login mapped to the expected controller identity with a role set;
# a wrong or empty identity means the credentials/authz mapping is broken.
EXPECTED_IDENTITY="${OSAC_CONTROLLER_IDENTITY:-service-account-osac-controller}"
if [ "${IDENTITY}" != "${EXPECTED_IDENTITY}" ]; then
    error "Unexpected identity from whoami: got '${IDENTITY:-<empty>}', expected '${EXPECTED_IDENTITY}'"
    exit 1
fi
if [ -z "${ROLES}" ]; then
    error "whoami returned no roles for identity ${IDENTITY}"
    exit 1
fi

# Perform a read call that exercises authz end-to-end.
info "Performing read call (get clusters)"
if ! CLUSTERS_OUT="$("${OSAC_BIN}" get clusters 2>&1)"; then
    error "osac get clusters failed:"
    printf '%s\n' "${CLUSTERS_OUT}" >&2
    exit 1
fi
printf '%s\n' "${CLUSTERS_OUT}"
# The CLI prints a "no objects" notice on an empty inventory; otherwise it prints
# a header row plus one line per cluster. Count data rows for a helpful summary.
if printf '%s\n' "${CLUSTERS_OUT}" | grep -qi 'no objects matching'; then
    CLUSTER_COUNT=0
else
    CLUSTER_COUNT="$(printf '%s\n' "${CLUSTERS_OUT}" | grep -cvE '^[[:space:]]*$|^(NAME|ID)[[:space:]]')"
fi

# Exercise additional read paths across core object types.
for _obj in \
    clustertemplates clustercatalogitems clusterversions \
    tenants projects projectmemberships users roles rolebindings identityproviders; do
    read_call "${_obj}"
done

info "===== 5. Tenant provisioning (create/teardown) ====="
if ! run_tenant_test; then
    error "Tenant provisioning test failed."
    exit 1
fi

echo
success "OSAC CLI smoke test passed"
info "  Endpoint: ${ENDPOINT}"
[ -n "${OSAC_CHART_VERSION:-}" ] && info "  Chart:    ${OSAC_CHART_VERSION}"
info "  Image:    ${IMAGE}"
info "  CLI:      ${CLI_VERSION_OUT:-<unknown>}"
info "  Identity: ${IDENTITY:-<unknown>}"
info "  Roles:    ${ROLES:-<none>}"
info "  Clusters: ${CLUSTER_COUNT} found"
