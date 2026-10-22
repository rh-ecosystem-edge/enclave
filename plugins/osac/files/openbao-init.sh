#!/usr/bin/env sh
set -eu

# Bundled OpenBao bootstrap for OSAC (ported from osac-installer's osac-infra
# bundled OpenBao). This is a dev/test secret store:
# the unseal key and initial root token are persisted next to the data on the
# PVC so the instance recovers after a restart. All dynamic values come from
# the container environment so this script stays static (no templating):
#   OSAC_VAULT_NS        OpenBao logical namespace (e.g. osac). NOT named VAULT_NAMESPACE/BAO_NAMESPACE:
#                        the bao CLI auto-applies those to every command, sending the
#                        root-only init/unseal/status into the namespace -> "operation
#                        unavailable in namespaces".
#   KEYCLOAK_ISSUER_URL  external Keycloak realm issuer URL (must equal the iss claim)
#   KEYCLOAK_AUDIENCE    expected token audience (becomes bound_audiences on the role)
#   KEYCLOAK_CLIENT_ID   controller client id; bound via the role's azp claim so only
#                        that confidential client can assume the lifecycle role
umask 077
bootstrap_file=/vault/data/bootstrap.json
temporary_bootstrap_file="${bootstrap_file}.tmp"

# /tmp is a pod-scoped emptyDir, so a readiness marker from a previous container
# run survives a container-only restart. Clear it before doing any work so the
# sidecar is only Ready once THIS run has re-completed the bootstrap.
rm -f /tmp/bootstrap-ready

get_status() {
  bao status -format=json 2>/dev/null || true
}

status_value() {
  printf '%s\n' "$status_json" | awk -F: -v field="\"$1\"" '
    index($1, field) {
      gsub(/[[:space:],"]/, "", $2)
      print $2
      exit
    }'
}

echo "Waiting for the OpenBao API..."
status_json=""
until [ -n "$status_json" ]; do
  status_json="$(get_status)"
  [ -n "$status_json" ] || sleep 1
done

if [ ! -s "$bootstrap_file" ] && [ -s "$temporary_bootstrap_file" ]; then
  mv "$temporary_bootstrap_file" "$bootstrap_file"
fi

initialized="$(status_value initialized)"
if [ "$initialized" = false ]; then
  echo "Initializing OpenBao..."
  bao operator init -key-shares=1 -key-threshold=1 -format=json > "$temporary_bootstrap_file"
  mv "$temporary_bootstrap_file" "$bootstrap_file"
  status_json="$(get_status)"
elif [ "$initialized" != true ]; then
  echo "ERROR: Could not determine whether OpenBao is initialized." >&2
  exit 1
fi

if [ ! -s "$bootstrap_file" ]; then
  echo "ERROR: OpenBao is initialized but its bootstrap file is missing." >&2
  exit 1
fi

unseal_key="$(awk -F '"' '
  /"unseal_keys_b64"[[:space:]]*:/ { in_keys=1; next }
  in_keys && NF >= 3 { print $2; exit }
' "$bootstrap_file")"
root_token="$(awk -F '"' '/"root_token"[[:space:]]*:/ { print $4; exit }' "$bootstrap_file")"
if [ -z "$unseal_key" ] || [ -z "$root_token" ]; then
  echo "ERROR: Could not read OpenBao bootstrap material." >&2
  exit 1
fi

sealed="$(status_value sealed)"
if [ "$sealed" = true ]; then
  echo "Unsealing OpenBao..."
  bao operator unseal "$unseal_key" >/dev/null
  status_json="$(get_status)"
fi
if [ "$(status_value sealed)" != false ]; then
  echo "ERROR: OpenBao remains sealed after unsealing." >&2
  exit 1
fi

export BAO_TOKEN="$root_token"
echo "OpenBao is ready."

echo "Creating parent namespace '${OSAC_VAULT_NS}'..."
# Check-first rather than masking every error as "already exists": a real
# permission/transport failure must stop the script with its original error.
if bao namespace lookup "${OSAC_VAULT_NS}" >/dev/null 2>&1; then
  echo "Namespace '${OSAC_VAULT_NS}' already exists."
else
  bao namespace create "${OSAC_VAULT_NS}"
fi

echo "Enabling JWT auth in ${OSAC_VAULT_NS} namespace..."
if bao auth list -namespace="${OSAC_VAULT_NS}" -format=json | grep -q '"jwt/"'; then
  echo "JWT auth already enabled."
else
  bao auth enable -namespace="${OSAC_VAULT_NS}" -path=jwt jwt
fi

echo "Writing lifecycle policy..."
bao policy write -namespace="${OSAC_VAULT_NS}" lifecycle - <<'POLICY'
path "sys/namespaces/*" {
  capabilities = ["create", "read", "update", "delete", "list", "sudo"]
}
path "sys/namespaces" {
  capabilities = ["list"]
}
path "+/sys/mounts/*" {
  capabilities = ["create", "read", "update", "delete", "sudo"]
}
path "+/sys/mounts" {
  capabilities = ["read", "list"]
}
path "+/sys/policies/*" {
  capabilities = ["create", "read", "update", "delete", "list", "sudo"]
}
path "+/sys/auth/*" {
  capabilities = ["create", "read", "update", "delete", "list", "sudo"]
}
path "+/auth/*" {
  capabilities = ["create", "read", "update", "delete", "list", "sudo"]
}
POLICY

echo "Configuring JWT auth with Keycloak OIDC discovery..."
attempts=0
max_attempts=30
until [ $attempts -ge $max_attempts ]; do
  if bao write -namespace="${OSAC_VAULT_NS}" auth/jwt/config \
    oidc_discovery_url="${KEYCLOAK_ISSUER_URL}" \
    oidc_discovery_ca_pem=@/vault/ca/bundle.pem \
    default_role="lifecycle"; then
    break
  fi
  attempts=$((attempts + 1))
  echo "OIDC discovery not reachable yet (attempt $attempts/$max_attempts), retrying in 10s..."
  sleep 10
done
if [ "$attempts" -ge "$max_attempts" ]; then
  echo "ERROR: Failed to configure OIDC discovery after $max_attempts attempts." >&2
  exit 1
fi

echo "Creating lifecycle role..."
# bound_claims is a map, so the role must be written as JSON on stdin (it cannot
# be expressed as a key=value CLI argument). Restrict the role to the controller
# client via the azp claim, on top of the audience bound_audiences check.
bao write -namespace="${OSAC_VAULT_NS}" auth/jwt/role/lifecycle - <<EOF
{
  "role_type": "jwt",
  "bound_audiences": ["${KEYCLOAK_AUDIENCE}"],
  "bound_claims": {"azp": "${KEYCLOAK_CLIENT_ID}"},
  "user_claim": "sub",
  "policies": ["lifecycle"],
  "token_ttl": "1h"
}
EOF

echo "OpenBao bootstrap complete."
# Readiness marker for the sidecar's readiness probe (cleared at script start).
touch /tmp/bootstrap-ready
while :; do
  sleep 5
  status_json="$(get_status)"
  if [ "$(status_value sealed)" = true ]; then
    echo "OpenBao is sealed; unsealing it..."
    bao operator unseal "$unseal_key" >/dev/null
  fi
done
