# Existing Management Cluster Mode

## Overview

Enclave supports deploying to a pre-existing management cluster, automatically skipping cluster provisioning phases and focusing on the mirroring flow and operator installation.

This mode is ideal when:
- You already have a management cluster deployed
- You want to focus on Enclave's mirroring flow (quay.io → mirror-registry → quay-enterprise)
- You need to re-run operator installation or configuration without reprovisioning

## How It Works

The deployment **automatically detects** an existing management cluster by checking for:
```
workingDir/ocp-cluster/auth/kubeconfig
```

If this file exists, the deployment:
1. ✅ Runs Phase 1 (Prepare) - downloads binaries
2. ✅ Runs Phase 2 (Mirror) - sets up mirror-registry and mirrors images
3. ✅ Validates existing cluster - connectivity and version checks
4. ⏭️  Skips Phase 3 (Deploy) - cluster already exists
5. ⏭️  Skips Phase 4 (Post-Install) - cluster already configured
6. ✅ Runs Phase 5 (Operators) - installs operators
7. ✅ Runs Phase 6 (Day2) - day-2 operations
8. ✅ Runs Phase 7 (Discovery) - hardware discovery (optional)

## Setup

### 1. Prepare Your Kubeconfig

Place your existing cluster's kubeconfig at the expected location:

```bash
mkdir -p /home/enclave/ocp-cluster/auth
cp /path/to/your/kubeconfig /home/enclave/ocp-cluster/auth/kubeconfig

# Verify connectivity
export KUBECONFIG=/home/enclave/ocp-cluster/auth/kubeconfig
oc whoami
oc get clusterversion
```

### 2. Configure Your Deployment

Update `config/global.yaml` to match your existing cluster:

```yaml
workingDir: /home/enclave
clusterName: mgmt  # Must match existing cluster
baseDomain: example.com  # Must match existing cluster

# Network settings (for reference, not used for provisioning)
apiVIP: 192.168.2.201
ingressVIP: 192.168.2.202
machineNetwork: 192.168.2.0/24

# Agent hosts (still required for documentation)
agent_hosts:
  - hostname: node1
    # ... (not provisioned, but kept for reference)
```

### 3. Run Deployment

```bash
# Full deployment with existing cluster
ansible-playbook playbooks/main.yaml -e workingDir=/home/enclave

# Or run specific phases
ansible-playbook playbooks/02-mirror.yaml -e workingDir=/home/enclave
ansible-playbook playbooks/05-operators.yaml -e workingDir=/home/enclave
```

## Validation

The `validate-existing-cluster.yaml` playbook automatically runs when an existing cluster is detected. It checks:

1. **Kubeconfig accessibility** - can read and parse the kubeconfig
2. **Cluster connectivity** - `oc whoami` succeeds
3. **Version match** - cluster version matches `openshift_version_default` in configuration

If validation fails:
- Update your `config/global.yaml` to match the cluster version, OR
- Use a different cluster that matches the expected version

## Example Workflow

```bash
# 1. You already have a management cluster
oc get nodes
# NAME    STATUS   ROLES           AGE   VERSION
# node1   Ready    control-plane   5d    v1.30.0
# node2   Ready    control-plane   5d    v1.30.0
# node3   Ready    control-plane   5d    v1.30.0

# 2. Place kubeconfig
mkdir -p /home/enclave/ocp-cluster/auth
cp ~/.kube/config /home/enclave/ocp-cluster/auth/kubeconfig

# 3. Run deployment - automatically detects existing cluster
ansible-playbook playbooks/main.yaml -e workingDir=/home/enclave

# Output:
# TASK [Display deployment mode]
# ok: [localhost] => {
#     "msg": "Management cluster exists - skipping provisioning, focusing on mirroring flow"
# }
#
# PLAY [Validate existing management cluster]
# ...
# TASK [Validation successful]
# ok: [localhost] => {
#     "msg": "✓ Management cluster validation passed - proceeding with mirroring and operator installation"
# }
```

## Switching Modes

### From New Cluster → Existing Cluster

After initial deployment, the kubeconfig exists. Future runs automatically use existing cluster mode.

### From Existing Cluster → New Cluster

Remove the kubeconfig to force fresh provisioning:

```bash
rm -rf /home/enclave/ocp-cluster/auth/kubeconfig
# Next deployment will provision a new cluster
```

## Configuration Reference

The `agent_hosts` configuration is **still required** even in existing cluster mode:
- Kept for documentation and reference
- Not used for provisioning
- Useful for understanding cluster topology

## Troubleshooting

### "Management cluster version mismatch" Error

**Problem**: Your cluster version doesn't match the configuration.

**Solution**:
```yaml
# Update config/global.yaml
openshift_version_default: "4.17.8"  # Match your cluster
```

Or check available versions:
```bash
oc get clusterversion -o yaml
```

### Kubeconfig Permission Issues

**Problem**: Cannot read kubeconfig file.

**Solution**:
```bash
chmod 600 /home/enclave/ocp-cluster/auth/kubeconfig
```

### Want to Force Fresh Provisioning

**Problem**: Kubeconfig exists but you want to deploy fresh.

**Solution**:
```bash
# Backup existing kubeconfig
mv /home/enclave/ocp-cluster/auth/kubeconfig{,.backup}

# Run deployment - will provision new cluster
ansible-playbook playbooks/main.yaml -e workingDir=/home/enclave
```
