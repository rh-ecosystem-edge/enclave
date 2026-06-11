# OSAC Plugin

The OSAC (Open Source as a Capability) plugin deploys the OSAC platform on an Enclave-managed OpenShift cluster. It installs the OSAC operator, fulfillment service, and AAP bootstrap job via a single OCI Helm chart, after provisioning all required infrastructure: Keycloak identity provider, PostgreSQL database, CA certificates, and AAP integration.

## Plugin Summary

| Field | Value |
|-------|-------|
| Type | `addon` |
| Order | `200` |
| Namespace | `osac` |
| Helm chart | `oci://ghcr.io/osac-project/charts/osac` v0.0.1 |
| Operators | None (delegated to prerequisite plugins) |

## Prerequisites

OSAC does not install any operators itself (`installOperators: false`). It depends on several plugins that must run first, enforced by their lower `order` values:

| Dependency | Plugin | Order | What it provides |
|------------|--------|-------|------------------|
| trust-manager | `trust-manager` | 100 | CA Bundle CRD that syncs cluster CA certificates to labeled namespaces |
| Keycloak (RHBK) | `rhbk` | 101 | Keycloak operator and instance in `keycloak` namespace |
| Authorino | `authorino` | 102 | Authorization service (optional, used by OSAC APIs) |
| AAP | `aap` | 103 | AAP operator + `AnsibleAutomationPlatform` CR + license secret in `ansible-aap` namespace |
| cert-manager | core | N/A | Installed from `defaults/operators.yaml` before addon plugins |
| Storage | `lvms` or `odf` | 10 | StorageClass for PostgreSQL and AAP PVCs |

### Required Variables

| Variable | Source | Description |
|----------|--------|-------------|
| `aap_license_file` | User-provided | Path to AAP license file (zip) on the Landing Zone |

### Required Files

| File | Description |
|------|-------------|
| `config/plugins/aap.yaml` | Required by the `aap` plugin (can be empty `---`) |

## Deployment Flow

The plugin uses Enclave's standard lifecycle. Here is what runs at each step:

```
1. Load plugin.yaml + defaults
2. Validate requirements (aap_license_file)
3. pre-validate.yaml         Check StorageClass exists, AAP license file present
4. Mirror additionalImages   (disconnected mode only)
5. post-operators.yaml       Create all Helm chart prerequisites (see below)
6. Helm install              Pull OCI chart, render values template, install
7. deploy.yaml               Create OSAC config secret and RoleBinding
8. post-validate.yaml        Verify AAP controller, EDA, OSAC operator running
```

### post-operators.yaml — Helm Chart Prerequisites

The OSAC Helm chart expects several Kubernetes resources to exist before it is installed. The `post-operators.yaml` hook creates all of them:

| # | Task Group | Resources Created |
|---|-----------|-------------------|
| 1 | Namespace + CA bundle | Labels `osac` namespace with `enclave/ca-bundle: "true"`, waits for trust-manager to sync the `ca-bundle` ConfigMap |
| 2 | RBAC | ServiceAccount, ClusterRole, ClusterRoleBinding for OSAC in AAP namespace |
| 3 | Keycloak route | Discovers Keycloak route hostname, adds to `/etc/hosts` for LZ DNS resolution |
| 4 | Keycloak auth | Extracts admin credentials from `keycloak-initial-admin` secret, obtains admin access token |
| 5 | Keycloak realm + client | Creates `osac` realm and `osac-controller` confidential client via Admin REST API |
| 6 | Keycloak credentials secret | Creates `fulfillment-controller-credentials` Secret (client-id + client-secret) |
| 7 | PostgreSQL credentials | Generates or reuses database password, creates `fulfillment-database-credentials` Secret |
| 8 | PostgreSQL deployment | Deploys Service, NetworkPolicy, StatefulSet with PVC, waits for readiness |
| 9 | Database connection secret | Creates `fulfillment-db` Secret with `postgres://` connection URL |
| 10 | AAP integration | Waits for AAP controller route, extracts admin password, creates API token via `awx-manage` |
| 11 | AAP secrets | Creates kubeconfig secret, `config-as-code-ig` and `config-as-code-manifest-ig` secrets |

All tasks are idempotent and safe to re-run.

## Kubernetes Resources Created

### In `osac` namespace

| Resource | Kind | Purpose |
|----------|------|---------|
| `ca-bundle` | ConfigMap | Cluster CA certificate (synced by trust-manager) |
| `fulfillment-controller-credentials` | Secret | Keycloak client credentials for the fulfillment service |
| `fulfillment-db` | Secret | PostgreSQL connection URL for the fulfillment service |
| `fulfillment-database-credentials` | Secret | PostgreSQL username and password |
| `fulfillment-database` | StatefulSet | PostgreSQL instance for the fulfillment service |
| `fulfillment-database` | Service | PostgreSQL service endpoint |
| `allow-fulfillment-to-db` | NetworkPolicy | Restricts PostgreSQL ingress to port 5432 |
| `config-as-code-ig` | Secret | AAP controller hostname and credentials |
| `config-as-code-manifest-ig` | Secret | AAP license manifest |
| `osac-config` | Secret | OSAC operator configuration (created in deploy.yaml) |

### In `ansible-aap` namespace

| Resource | Kind | Purpose |
|----------|------|---------|
| OSAC ServiceAccount | ServiceAccount | Used by OSAC to interact with AAP |
| `aap-kubeconfig` | Secret | Cluster kubeconfig for AAP |

### In `keycloak` namespace (via Admin API)

| Resource | Kind | Purpose |
|----------|------|---------|
| `osac` | Keycloak Realm | Identity realm for OSAC services |
| `osac-controller` | Keycloak Client | Confidential client with service account for the fulfillment service |

## Configuration Defaults

All defaults live under `osacDefaults` in `plugin.yaml`:

```yaml
osacDefaults:
  # AAP
  aapName: "aap"
  aapNamespace: "ansible-aap"
  aapTokenDescription: "OSAC Operator Token"
  aapTokenOverwrite: false
  aapApiUser: "admin"
  aapApiValidateCerts: false

  # OSAC
  osacNamespace: "osac"

  # Keycloak
  keycloakNamespace: "keycloak"
  keycloakName: "keycloak"
  keycloakRealmName: "osac"
  keycloakClientId: "osac-controller"

  # PostgreSQL (fulfillment service database)
  fulfillmentDbName: "fulfillment-database"
  fulfillmentDbImage: "quay.io/sclorg/postgresql-15-c9s:latest"
  fulfillmentDbSize: "5Gi"
  fulfillmentDbUsername: "service"
  fulfillmentDbDatabase: "service"

  # Container images
  images:
    operator: "ghcr.io/osac-project/osac-operator"
    operatorTag: "latest"
    fulfillmentService: "ghcr.io/osac-project/fulfillment-service:latest"
    envoy: "docker.io/envoyproxy/envoy:v1.33.0"
    aapBootstrap: "ghcr.io/osac-project/osac-aap:latest"
    cli: "quay.io/openshift/origin-cli:4.20.0"
```

## Files

```
plugins/osac/
  plugin.yaml                    Plugin descriptor and defaults
  tasks/
    pre-validate.yaml            Check StorageClass and AAP license
    post-operators.yaml          Create all Helm prerequisites
    deploy.yaml                  Post-Helm config secret and RBAC
    post-validate.yaml           Verify AAP + OSAC components running
  templates/
    values.yaml.j2               Helm values template
  files/
    aap-osac-sa.yaml.j2          ServiceAccount template
    aap-osac-clusterrole.yaml.j2 ClusterRole template
    aap-osac-rolebinding.yaml.j2 ClusterRoleBinding template
    osac-config.yaml.j2          OSAC config secret template
    osac-rolebinding.yaml.j2     OSAC namespace RoleBinding template
```

## Design Decisions

**Keycloak Admin API vs KeycloakRealmImport CRD.** The plugin uses the Keycloak Admin REST API to create the realm and client rather than a `KeycloakRealmImport` CR. This is simpler for a minimal setup (one realm, one client) and avoids managing CRD lifecycle and status polling.

**PostgreSQL pattern reuse.** The fulfillment service PostgreSQL deployment follows the same pattern as the `rhbk` plugin's database: idempotent credential generation, hardened security context (`runAsNonRoot`, `readOnlyRootFilesystem`, drop ALL capabilities), and PVC-backed storage.

**Password auth for PostgreSQL.** The database connection uses password authentication rather than mTLS, matching the `rhbk` plugin pattern and reducing the failure surface. mTLS can be added later if needed.

**CA bundle via namespace label.** Rather than manually creating a CA bundle ConfigMap, the namespace is labeled with `enclave/ca-bundle: "true"`. This triggers trust-manager's existing Bundle CR to automatically sync the cluster CA certificate into the namespace.

**DNS resolution for dynamic routes.** Libvirt dnsmasq only resolves a fixed set of app hostnames. Both the Keycloak and AAP route hostnames are added to `/etc/hosts` using the `ingressVIP` so the Landing Zone can reach them.
