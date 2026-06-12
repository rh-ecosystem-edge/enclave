# OSAC Plugin Setup Guide

This guide walks you through the steps required to deploy the OSAC (Open Sovereign AI Cloud) plugin on an Enclave-managed OpenShift cluster.

## Before You Begin

You need:

- A deployed Enclave cluster (Phase 1-4 complete)
- An **AAP license file** (`manifest.zip`) — download from [Red Hat Subscription Allocations](https://access.redhat.com/management/subscription_allocations)
- Storage plugin configured (`lvms`, `odf`, or `vast-csi`)

## Step 1 — Enable the OSAC Plugin and Dependencies

OSAC supports two service profiles that determine which plugins you need:

- **VMaaS** (VM as a Service) — provisions virtual machines via OpenShift Virtualization
- **CaaS** (Clusters as a Service) — provisions OpenShift clusters via Multicluster Engine

You can enable one or both profiles.

Edit `config/global.yaml` and add the required plugins to `enabled_plugins`:

### CaaS-only (no VM provisioning)

```yaml
enabled_plugins:
  - trust-manager    # CA bundle distribution (order 100)
  - rhbk             # Keycloak identity provider (order 101)
  - authorino        # Authorization service (order 102)
  - aap              # Ansible Automation Platform (order 103)
  - osac             # OSAC platform (order 200)
```

### VMaaS or VMaaS + CaaS

```yaml
enabled_plugins:
  - trust-manager    # CA bundle distribution (order 100)
  - rhbk             # Keycloak identity provider (order 101)
  - authorino        # Authorization service (order 102)
  - aap              # Ansible Automation Platform (order 103)
  - cnv              # OpenShift Virtualization (order 104) — required for VMaaS
  - osac             # OSAC platform (order 200)
```

The `cnv` plugin installs the KubeVirt hyperconverged operator, which provides the VirtualMachine CRDs that the OSAC operator needs for VM provisioning.

Enclave deploys plugins in the order shown above. All listed plugins are required for the chosen profile.

> **Note:** If you are using the VAST CSI storage backend, the `osac` experience in `experiences/osac/experience.yaml` bundles `vast-csi` and `authorino`. You still need to add the remaining plugins manually.

## Step 2 — Configure the AAP Plugin

The AAP plugin requires a license file. Copy the example config and set the path:

```bash
cp config/plugins/aap.example.yaml config/plugins/aap.yaml
```

Edit `config/plugins/aap.yaml`:

```yaml
---
aapLicenseFile: "/home/<user>/aap-license.zip"
```

Set this to the absolute path of the `manifest.zip` file **on the Landing Zone**.

Alternatively, if deploying via `deploy_plugin.sh`, you can pass the license file as an environment variable:

```bash
export AAP_LICENSE_FILE="/local/path/to/manifest.zip"
```

The script copies it to the Landing Zone automatically.

## Step 3 — (Optional) Customize Plugin Defaults

OSAC ships with sensible defaults. You only need this step if you want to override them.

### RHBK (Keycloak)

```bash
cp config/plugins/rhbk.example.yaml config/plugins/rhbk.yaml
```

Available overrides:

| Variable | Default | Description |
|----------|---------|-------------|
| `rhbk_instances` | `1` | Number of Keycloak replicas |
| `rhbk_deploy_database` | `true` | Deploy PostgreSQL alongside Keycloak (set `false` for external DB) |
| `rhbk_db_size` | `5Gi` | PVC size for Keycloak's PostgreSQL |

### VAST CSI (if using VAST storage)

```bash
cp config/plugins/vast-csi.example.yaml config/plugins/vast-csi.yaml
```

See `config/plugins/vast-csi.example.yaml` for required fields (endpoint, credentials, VIP pool).

### OSAC Defaults

OSAC does not require a config file. All defaults are in `plugins/osac/plugin.yaml` under `osacDefaults`. To override any of these, create `config/plugins/osac.yaml`:

```yaml
---
# Example: increase the fulfillment database PVC size
osacDefaults:
  fulfillmentDbSize: "20Gi"
```

Available defaults:

| Variable | Default | Description |
|----------|---------|-------------|
| `osacDefaults.osacNamespace` | `osac` | Namespace for OSAC components |
| `osacDefaults.keycloakNamespace` | `keycloak` | Namespace where Keycloak runs |
| `osacDefaults.keycloakName` | `keycloak` | Keycloak instance name |
| `osacDefaults.keycloakRealmName` | `osac` | Keycloak realm created for OSAC |
| `osacDefaults.keycloakClientId` | `osac-controller` | Keycloak client created for the fulfillment service |
| `osacDefaults.fulfillmentDbName` | `fulfillment-database` | PostgreSQL StatefulSet name |
| `osacDefaults.fulfillmentDbImage` | `quay.io/sclorg/postgresql-15-c9s:latest` | PostgreSQL container image |
| `osacDefaults.fulfillmentDbSize` | `5Gi` | PVC size for the fulfillment database |
| `osacDefaults.fulfillmentDbUsername` | `service` | Database username |
| `osacDefaults.fulfillmentDbDatabase` | `service` | Database name |
| `osacDefaults.aapName` | `aap` | AAP instance name (must match the `aap` plugin) |
| `osacDefaults.aapNamespace` | `ansible-aap` | AAP namespace (must match the `aap` plugin) |
| `osacDefaults.aapApiUser` | `admin` | AAP admin username |
| `osacDefaults.aapApiValidateCerts` | `false` | Validate AAP TLS certificates |
| `osacDefaults.aapProjectGitUri` | `https://github.com/osac-project/osac-aap` | Git repo for AAP config-as-code project |
| `osacDefaults.aapProjectGitBranch` | `main` | Git branch for AAP config-as-code project |

## Step 4 — Deploy

Run the standard Enclave Phase 5 deployment:

```bash
ansible-playbook playbooks/05-operators.yaml
```

Or deploy plugins individually (useful for testing):

```bash
./scripts/deployment/deploy_plugin.sh trust-manager
./scripts/deployment/deploy_plugin.sh rhbk
./scripts/deployment/deploy_plugin.sh authorino
./scripts/deployment/deploy_plugin.sh aap
./scripts/deployment/deploy_plugin.sh cnv    # only if using VMaaS
./scripts/deployment/deploy_plugin.sh osac
```

## What Gets Created

During deployment, the OSAC plugin automatically provisions all required infrastructure:

| Component | Namespace | How |
|-----------|-----------|-----|
| `ca-bundle` ConfigMap | `osac` | Namespace labeled for trust-manager sync |
| Keycloak `osac` realm | `keycloak` | Created via Keycloak Admin REST API |
| Keycloak `osac-controller` client | `keycloak` | Confidential client with service account |
| `fulfillment-controller-credentials` Secret | `osac` | Keycloak client-id and client-secret |
| PostgreSQL StatefulSet | `osac` | Fulfillment service database |
| `fulfillment-db` Secret | `osac` | PostgreSQL connection URL |
| AAP personal access token | `ansible-aap` | Created via `awx-manage` |
| Config-as-code secrets | `osac` | AAP credentials for bootstrap |
| OSAC Helm chart | `osac` | Operator, fulfillment service, AAP bootstrap |

No manual secret or resource creation is needed. All tasks are idempotent and safe to re-run.

## Verifying the Deployment

After deployment, check that OSAC components are running:

```bash
# OSAC operator and fulfillment service
oc get pods -n osac

# Keycloak realm (should show 'osac')
oc get keycloaks -n keycloak

# AAP instance
oc get ansible-automation-platform -n ansible-aap

# PostgreSQL for fulfillment service
oc get statefulset fulfillment-database -n osac

# OpenShift Virtualization (VMaaS only)
oc get hyperconverged -n openshift-cnv
```

## Troubleshooting

### AAP license file not found

```
TASK [Validate requirements] ***
fatal: aap_license_file is required but not set
```

Ensure `config/plugins/aap.yaml` exists with a valid `aapLicenseFile` path, or set `AAP_LICENSE_FILE` before running the deploy script.

### CA bundle ConfigMap not appearing

```
TASK [Wait for ca-bundle ConfigMap] ***
FAILED - RETRYING
```

Verify the `trust-manager` plugin deployed successfully and the Bundle CR exists:

```bash
oc get bundles.trust.cert-manager.io ca-bundle
oc get configmap ca-bundle -n osac
```

### Keycloak admin token fails

```
TASK [Get Keycloak admin access token] ***
FAILED - RETRYING
```

Verify Keycloak is ready and the route is accessible from the Landing Zone:

```bash
oc get keycloak keycloak -n keycloak -o jsonpath='{.status.conditions}'
curl -sk https://$(oc get route keycloak-ingress -n keycloak -o jsonpath='{.spec.host}')/realms/master
```

### PostgreSQL pod stuck in Pending

Check that a StorageClass is available and can provision the PVC:

```bash
oc get pvc -n osac
oc get storageclass
```
