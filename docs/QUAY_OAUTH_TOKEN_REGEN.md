# Regenerating the Quay OAuth token

Fresh installs pick up the scopes from `defaults/quay_operator.yaml` automatically. Existing clusters still hold the token created at install time, so a scope change in that file does not propagate until the token is reissued. The script below reissues it in place against the live Quay route.

> [!WARNING]
> Quay does not revoke the previous token when a new one is issued. The old token keeps working with its old (narrower) scopes until it expires or is deleted manually under `quayadmin` -> Settings -> Tokens. Patching the Secret only swaps which token the cluster uses.

## Prerequisites

- `oc`, `curl`, and `jq` on `PATH`
- `oc` context pointing at the target cluster

## Reissue

```bash
NS=quay-enterprise

# .quayUser / .quayPassword from config
QUAY_USER=$(oc get secret quay-credentials -n redhat-lz-admin -o jsonpath='{.data.username}' | base64 -d)
QUAY_PASS=$(oc get secret quay-credentials -n redhat-lz-admin -o jsonpath='{.data.password}' | base64 -d)
# .clusterName + .baseDomain from config
QUAY_HOST="https://$(oc get route registry-quay -n "$NS" -o jsonpath='{.spec.host}')"

# .quayOAuthApp.scopes from defaults/quay_operator.yaml
SCOPES="repo:read repo:write repo:admin repo:create user:read user:admin org:admin"

CLIENT_ID=$(oc get secret quay-oauth-credentials -n "$NS" -o jsonpath='{.data.client-id}'    | base64 -d)
REDIRECT=$( oc get secret quay-oauth-credentials -n "$NS" -o jsonpath='{.data.redirect-uri}' | base64 -d)

TOKEN=$(curl -sk -u "$QUAY_USER:$QUAY_PASS" -D - -o /dev/null -X POST "$QUAY_HOST/oauth/authorizeapp" \
  --data-urlencode "response_type=token" \
  --data-urlencode "client_id=$CLIENT_ID" \
  --data-urlencode "redirect_uri=$REDIRECT" \
  --data-urlencode "scope=$SCOPES" \
  | tr -d '\r' | sed -nE 's|.*#access_token=([^&]+).*|\1|p')

[ -n "$TOKEN" ] || { echo "no access_token in response, aborting"; return 2>/dev/null || exit 1; }

oc patch secret quay-oauth-credentials -n "$NS" --type=merge \
  -p "$(jq -n --arg t "$TOKEN" --arg s "$SCOPES" '{stringData:{"access-token":$t,scopes:$s}}')"
```

## Verify

```bash
oc get secret quay-oauth-credentials -n "$NS" -o jsonpath='{.data.scopes}' | base64 -d; echo
```

The output should match `$SCOPES` exactly. For a live check, hit the Quay API with the new token:

```bash
TOKEN=$(oc get secret quay-oauth-credentials -n "$NS" -o jsonpath='{.data.access-token}' | base64 -d)
curl -sk -H "Authorization: Bearer $TOKEN" "$QUAY_HOST/api/v1/user/" | jq '.username'
```
