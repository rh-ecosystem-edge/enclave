# OSAC Day-2 Deployment Guide

This guide describes how to deploy the Open Sovereign AI Cloud (OSAC) stack as day-2 addon plugins on an existing Enclave cluster.

## Table of Contents

1. [Overview](#overview)
2. [Prerequisites](#prerequisites)
3. [Configuration](#configuration)
4. [Deployment](#deployment)
5. [Verification](#verification)
6. [BYO Database](#byo-database)
7. [Profiles](#profiles)
8. [Post-Install Steps](#post-install-steps)
9. [Troubleshooting](#troubleshooting)

## Overview

OSAC is deployed as a set of Enclave plugins that are installed sequentially. Each plugin handles its own operator installation, resource creation, and validation.

| Plugin | Order | Purpose |
|--------|-------|---------|
| `trust-manager` | 100 | cert-manager ClusterIssuer and CA bundle sync |
| `rhbk` | 101 | Red Hat Build of Keycloak (identity provider) |
| `authorino` | 102 | gRPC authorization operator |
| `aap` | 103 | AAP operator installation (provides CRDs for the OSAC chart) |
| `cnv` | 104 | OpenShift Virtualization (optional, for VMaaS) |
| `osac` | 200 | OSAC fulfillment service, operator, chart-managed AAP instance, bootstrap |

The `aap` plugin installs the AAP operator and waits for it to be available (no configuration required). The `osac` plugin then deploys the OSAC Helm chart, which creates its own AAP instance (`osac-aap`) in the `osac` namespace.

### What Gets Deployed

After a successful deployment, the `osac` namespace contains:

- **Fulfillment service**: grpc-server, rest-gateway, controller, ingress-proxy
- **OSAC operator**: manages tenant, networking, compute, and cluster order controllers
- **AAP instance** (`osac-aap`): gateway, controller, EDA, Redis, PostgreSQL (AAP internal)
- **PostgreSQL**: fulfillment database with mTLS (unless BYO database)
- **Authorino instance**: gRPC authorization for the fulfillment API
- **Bootstrap job**: configures AAP with execution environments and project templates

## Prerequisites

Before starting, ensure:

- An Enclave cluster is fully deployed (Phases 1-7 complete)
- SSH access to the Landing Zone
- An AAP license `manifest.zip` file (obtain from [Red Hat Subscription Allocations](https://access.redhat.com/management/subscription_allocations))
- For VMaaS deployments: nodes with bare-metal or nested-virt capability

## Configuration

On the Landing Zone, create the OSAC plugin configuration file:

```bash
cd ~/enclave
cp config/plugins/osac.example.yaml config/plugins/osac.yaml
```

> **Note:** The `aap` plugin requires no configuration -- it only installs the AAP operator.

Edit `config/plugins/osac.yaml`:

```yaml
# Required: path to AAP license file on the Landing Zone
osacAapLicenseFile: "/home/<user>/aap-license.zip"

# Optional: deployment profile (default: development)
# osacProfile: development   # Options: development, vmaas, caas

# Optional: bring your own database
# osacBYODatabase: true
# osacDatabaseUrl: "postgres://user@host:5432/dbname?sslmode=require"
```

### OSAC Configuration Reference

| Field | Type | Required | Default | Description |
|-------|------|----------|---------|-------------|
| `osacAapLicenseFile` | string | Yes | — | Path to AAP license manifest.zip on the Landing Zone |
| `osacProfile` | string | No | `development` | Deployment profile: `development`, `vmaas`, or `caas` |
| `osacBYODatabase` | boolean | No | `false` | Use an external database instead of the built-in dev postgres |
| `osacDatabaseUrl` | string | No | — | Connection URL for external database (requires `osacBYODatabase: true`) |

## Deployment

Deploy each plugin sequentially. Later plugins depend on resources created by earlier ones.

```bash
cd ~/enclave

# 1. Trust Manager — cert-manager ClusterIssuer and CA bundle sync
make deploy-plugin PLUGIN=trust-manager

# 2. Red Hat Build of Keycloak — identity provider
make deploy-plugin PLUGIN=rhbk

# 3. Authorino — gRPC authorization operator
make deploy-plugin PLUGIN=authorino

# 4. AAP — installs operator only, no config required (provides CRDs for OSAC)
make deploy-plugin PLUGIN=aap

# 5. (VMaaS only) OpenShift Virtualization — required for VM workloads
# make deploy-plugin PLUGIN=cnv

# 6. Deploy the OSAC plugin
make deploy-plugin PLUGIN=osac
```

The `make deploy-plugin PLUGIN=osac` command runs the full plugin lifecycle:

1. **pre-validate** — checks Keycloak ready, AAP CRDs registered, license file exists
2. **mirror** — mirrors images (disconnected environments only)
3. **post-operators** — creates osac namespace, Keycloak realm/client, PostgreSQL, secrets
4. **helm deploy** — installs the OSAC Helm chart
5. **deploy** — waits for AAP, creates access token, patches the OSAC operator
6. **post-validate** — verifies all components are healthy

> **Note:** AAP takes 30+ minutes to become fully available. The deploy step waits up to 45 minutes for the AAP gateway.

### Experience Bundle

The `osac` experience defines the complete plugin stack:

```bash
# View the experience definition
cat experiences/osac/experience.yaml
```

Each plugin must still be deployed individually via `make deploy-plugin`.

## Verification

After deployment, verify all components:

```bash
# Check all pods in the osac namespace
oc get pods -n osac

# Expected pods (all Running/Completed):
# - fulfillment-grpc-server-*
# - fulfillment-rest-gateway-*
# - fulfillment-controller-*
# - fulfillment-ingress-proxy-*
# - osac-operator-*
# - osac-aap-* (multiple pods: web, task, eda, redis, etc.)
# - postgres-* (unless BYO database)
# - authorino-* (Authorino instance)
# - osac-aap-bootstrap-* (Completed)

# Check Helm release
helm status osac -n osac

# Check AAP instance
oc get ansibleautomationplatform osac-aap -n osac

# Check fulfillment deployments
oc get deployments -n osac
```

## BYO Database

For production environments with an external PostgreSQL database:

1. Set `osacBYODatabase: true` in `config/plugins/osac.yaml`

2. **Option A** — provide the connection URL and let the plugin create the secret:
   ```yaml
   osacBYODatabase: true
   osacDatabaseUrl: "postgres://user@host:5432/dbname?sslmode=require"
   ```

3. **Option B** — pre-create the secret manually:
   ```bash
   oc create namespace osac
   oc create secret generic fulfillment-db -n osac \
     --from-literal=url="postgres://user@host:5432/dbname?sslmode=require"
   ```

4. If the external database requires mTLS, also create the client cert secret:
   ```bash
   oc create secret tls postgres-client-cert-service -n osac \
     --cert=client.crt --key=client.key
   # Add CA cert
   oc patch secret postgres-client-cert-service -n osac \
     --type merge -p '{"data":{"ca.crt":"'$(base64 -w0 ca.crt)'"}}'
   ```

5. Deploy OSAC normally:
   ```bash
   make deploy-plugin PLUGIN=osac
   ```

When BYO database is enabled:
- The built-in PostgreSQL Deployment, PVC, ConfigMap, and certificates are skipped
- The `postgres-client-cert-service` secret entry is omitted from the Helm values `service.database.connection` list
- Post-validate skips the PostgreSQL health check

## Profiles

The `osacProfile` config value selects which OSAC operator controllers are enabled:

| Profile | Controllers | Extra Prerequisites |
|---------|------------|---------------------|
| `development` (default) | clusterOrder, computeInstance, tenant, networking | CNV + MCE |
| `vmaas` | computeInstance, tenant, networking | CNV |
| `caas` | clusterOrder, tenant, networking | MCE |

- **MCE** (Multicluster Engine) is part of Enclave core infrastructure (ACM-based platform) and is always available.
- **CNV** must be deployed separately before OSAC for `vmaas` and `development` profiles: `make deploy-plugin PLUGIN=cnv`

## Post-Install Steps

After deployment, hub registration and tenant creation require manual steps:

```bash
# These steps will be documented in a follow-up and may be automated in future versions
# 1. Register hub via osac CLI
# 2. Create initial tenant
```

## Troubleshooting

| Issue | Check | Fix |
|-------|-------|-----|
| pre-validate fails on ClusterIssuer | `oc get clusterissuer default-ca` | Deploy trust-manager plugin first |
| pre-validate fails on Keycloak | `oc get keycloak -n keycloak` | Deploy rhbk plugin first |
| pre-validate fails on AAP CRD | `oc get crd ansibleautomationplatforms.aap.ansible.com` | Deploy aap plugin first |
| pre-validate fails on HyperConverged CRD | `oc get crd hyperconvergeds.hco.kubevirt.io` | Deploy cnv plugin first (required for vmaas/development profile) |
| AAP gateway not becoming available | `oc get pods -n osac -l app.kubernetes.io/managed-by=automationgateway` | AAP takes 30+ minutes; check operator logs |
| Helm chart install fails | `helm status osac -n osac` | Check post-operators secrets exist: `oc get secrets -n osac` |
| PostgreSQL not starting | `oc logs deployment/postgres -n osac` | Check PVC bound (`oc get pvc -n osac`), cert-manager certs issued (`oc get certificates -n osac`) |
| Fulfillment pods CrashLoopBackOff | `oc logs deployment/fulfillment-grpc-server -n osac` | Check `fulfillment-db` secret URL, PostgreSQL reachable, client cert valid |
| Keycloak realm creation fails | Check Keycloak pod logs in `keycloak` namespace | Verify Keycloak route resolves; check `/etc/hosts` in connected mode |
| Bootstrap job fails | `oc logs job -n osac -l app=osac-aap-bootstrap` | Check `config-as-code-ig` and `config-as-code-manifest-ig` secrets |
