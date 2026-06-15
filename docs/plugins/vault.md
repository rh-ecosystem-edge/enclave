# Vault Plugin

This plugin deploys HashiCorp Vault and the Vault Secrets Operator (VSO) on OpenShift clusters managed by Red Hat Sovereign Enclave.

## Overview

The Vault plugin provides:
- **Vault Secrets Operator** - Certified Red Hat operator for syncing secrets from Vault to Kubernetes
- **Vault Server** - Optional HA Vault server deployment using Raft storage
- **Integration** - Seamless integration with OpenShift clusters

## Components

### Vault Secrets Operator (VSO)

The [Vault Secrets Operator](https://www.redhat.com/en/blog/vault-secrets-operator-now-certified-on-red-hat-openshift) is a certified Red Hat operator that:
- Syncs secrets from Vault to native Kubernetes Secret objects
- Supports multiple Vault backends (KV v1/v2, PKI, Transit, etc.)
- Enables GitOps-friendly secret management
- Decouples application teams from direct Vault access

### Vault Server (Optional)

When enabled, deploys a production-ready Vault cluster with:
- High Availability using Raft consensus (3+ replicas)
- Persistent storage for Vault data and audit logs
- Service mesh integration ready
- Auto-registration with Kubernetes service discovery

## Configuration

### Default Configuration

See `plugins/vault/defaults.yaml` for all available configuration options. Key defaults:

```yaml
vaultDefaults:
  server:
    enabled: true
    ha:
      enabled: true
      replicas: 3
    dataStorage:
      size: 10Gi
```

### Cluster Selection

The plugin can be selectively enabled on specific clusters using ACM cluster labels. Edit `plugins/vault/plugin.yaml`:

```yaml
clusterSelector:
  matchLabels:
    secrets.vault.io/enabled: "true"
```

### Custom Configuration

Create a `plugins/vault/config.yaml` file to override defaults:

```yaml
vaultDefaults:
  server:
    ha:
      replicas: 5
    dataStorage:
      size: 50Gi
      storageClass: fast-ssd
```

## Deployment

The plugin deploys in the following order:

1. **Pre-validation** (`tasks/pre-validate.yaml`)
   - Validates configuration
   - Checks storage class availability
   - Verifies HA replica count

2. **Operator Installation** (automatic via OLM)
   - Installs vault-secrets-operator from certified catalog
   - Creates operator namespace and RBAC

3. **Vault Server Deployment** (`tasks/deploy.yaml`)
   - Creates vault namespace
   - Deploys Vault StatefulSet with Raft storage
   - Configures services and RBAC
   - Sets up ConfigMaps

4. **Post-validation** (`tasks/post-validate.yaml`)
   - Waits for operator readiness
   - Verifies Vault pods are running
   - Displays initialization instructions

## Post-Deployment Steps

### Initialize Vault

After deployment, Vault must be initialized and unsealed:

```bash
# Initialize Vault (run once)
oc exec -n vault vault-0 -- vault operator init

# Save the output! It contains:
# - 5 unseal keys (you need 3 to unseal)
# - Initial root token

# Unseal each Vault pod (requires 3 different keys)
oc exec -n vault vault-0 -- vault operator unseal <key1>
oc exec -n vault vault-0 -- vault operator unseal <key2>
oc exec -n vault vault-0 -- vault operator unseal <key3>

# Repeat for vault-1 and vault-2 if using HA
```

### Configure Kubernetes Auth

Enable Kubernetes authentication for VSO:

```bash
# Login to Vault
oc exec -n vault vault-0 -- vault login <root-token>

# Enable Kubernetes auth
oc exec -n vault vault-0 -- vault auth enable kubernetes

# Configure Kubernetes auth
oc exec -n vault vault-0 -- vault write auth/kubernetes/config \
  kubernetes_host="https://kubernetes.default.svc:443"

# Create a policy
oc exec -n vault vault-0 -- vault policy write app-policy - <<EOF
path "secret/data/*" {
  capabilities = ["read"]
}
EOF

# Create a role
oc exec -n vault vault-0 -- vault write auth/kubernetes/role/app \
  bound_service_account_names=app \
  bound_service_account_namespaces=default \
  policies=app-policy \
  ttl=24h
```

### Using VSO

Create a `VaultConnection` and `VaultStaticSecret` to sync secrets:

```yaml
apiVersion: secrets.hashicorp.com/v1beta1
kind: VaultConnection
metadata:
  name: vault-connection
  namespace: default
spec:
  address: http://vault.vault.svc.cluster.local:8200
  skipTLSVerify: true

---
apiVersion: secrets.hashicorp.com/v1beta1
kind: VaultStaticSecret
metadata:
  name: my-secret
  namespace: default
spec:
  vaultAuthRef: vault-auth
  mount: secret
  path: myapp/config
  destination:
    name: my-k8s-secret
    create: true
  refreshAfter: 30s
```

## Architecture

```
┌─────────────────────────────────────────┐
│         OpenShift Cluster               │
│                                         │
│  ┌───────────────────────────────────┐ │
│  │  vault namespace                  │ │
│  │  ┌─────────────────────────────┐  │ │
│  │  │  VSO Controller Manager     │  │ │
│  │  └─────────────────────────────┘  │ │
│  └───────────────────────────────────┘ │
│              │                          │
│              │ watches/syncs            │
│              ▼                          │
│  ┌───────────────────────────────────┐ │
│  │  vault namespace                  │ │
│  │  ┌──────────┬──────────┬────────┐ │ │
│  │  │ vault-0  │ vault-1  │vault-2 │ │ │
│  │  │  (Raft)  │  (Raft)  │ (Raft) │ │ │
│  │  └──────────┴──────────┴────────┘ │ │
│  │          Vault HA Cluster         │ │
│  └───────────────────────────────────┘ │
│              │                          │
│              │ provides secrets         │
│              ▼                          │
│  ┌───────────────────────────────────┐ │
│  │  Application Namespaces           │ │
│  │  (Kubernetes Secrets synced)      │ │
│  └───────────────────────────────────┘ │
└─────────────────────────────────────────┘
```

## Requirements

- OpenShift 4.12 or later
- Persistent storage (for Vault data and audit logs)
- Sufficient resources for HA deployment (3+ replicas)

## References

- [Vault Secrets Operator on OpenShift](https://developer.hashicorp.com/vault/docs/deploy/kubernetes/vso/openshift)
- [VSO Certification Announcement](https://www.redhat.com/en/blog/vault-secrets-operator-now-certified-on-red-hat-openshift)
- [Vault on OpenShift Guide](https://developer.hashicorp.com/vault/docs/deploy/kubernetes/helm/openshift)
- [Red Hat Ecosystem Catalog - VSO](https://catalog.redhat.com/en/software/containers/hashicorp/vault-secrets-operator-bundle/64ddcd189d40d16b88133fd8)

## Troubleshooting

### Vault Pods Not Starting

Check storage class availability:
```bash
oc get sc
oc describe pvc -n vault
```

### VSO Not Syncing Secrets

Check operator logs:
```bash
oc logs -n vault deployment/vault-secrets-operator-controller-manager
```

### Vault Sealed After Restart

Vault seals on restart. Re-run unseal commands:
```bash
oc exec -n vault vault-0 -- vault operator unseal <key>
```

Consider configuring auto-unseal using Transit or cloud KMS for production.
