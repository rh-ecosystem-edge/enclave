# Local CI Testing Guide

This guide explains how to run the complete CI workflow locally using Makefile targets. All CI workflows in GitHub Actions use these same targets, ensuring consistency between local testing and automated CI.

## Table of Contents

- [Prerequisites](#prerequisites)
- [Full CI Flow](#full-ci-flow)
- [Individual Components](#individual-components)
- [Environment Variables](#environment-variables)
- [Verification Commands](#verification-commands)
- [Troubleshooting](#troubleshooting)
- [Best Practices](#best-practices)

## Prerequisites

### Required Software

- **libvirt/KVM**: Virtualization
  - `sudo systemctl enable --now libvirtd`
- **Podman**: Container runtime
- **Git, Make, jq**: Standard tools

### Required System Resources

- **RAM**: 64GB+ recommended (16GB for host + 48GB for cluster VMs)
- **Disk**: 200GB+ free space
- **CPU**: 8+ cores recommended

### Environment Variables

#### Required
```bash
export BASE_WORKING_DIR=/opt/clusters                                 # Base directory for cluster data
export PULL_SECRET=$(cat /path/to/pull-secret.json)                   # Red Hat pull secret
export LZ_CLOUD_IMAGE_URL=file:///path/to/rhel-10.2-x86_64-kvm.qcow2  # RHEL 10 installation image
export LZ_RHSM_ORG="YOUR_RHSM_ORG_ID"
export LZ_RHSM_ACTIVATION_KEY="YOUR_RHSM_ACTIVATION_KEY"
```

#### Optional
```bash
export ENCLAVE_CLUSTER_NAME=my-cluster         # Custom cluster name (auto-generated if not set)
export ENCLAVE_DEPLOYMENT_MODE=connected       # "connected" or "disconnected" (default)
```

## Full CI Flow

### Connected Mode

Fastest option - skips mirror registry setup:

```bash
make -f Makefile.ci ci-flow-connected
```
### Disconnected Mode

Full air-gapped deployment with local mirror registry:

```bash
make -f Makefile.ci ci-flow-disconnected
```

## Individual Components

You can run each phase of the CI flow independently for faster iteration:

### 0. Set common env variables

```bash
export BASE_WORKING_DIR=/opt/clusters    # the place to store VMs files, etc... make it big :)
export ENCLAVE_CLUSTER_NAME=eci-local01  # In this example we pin a unique name manually
export PULL_SECRET=$(cat /path/to/pull-secret.json)
export LZ_CLOUD_IMAGE_URL=file:///path/to/rhel-10.2-x86_64-kvm.qcow2
export LZ_RHSM_ACTIVATION_KEY=<rhsm activation key>
export LZ_RHSM_ORG="YOUR_RHSM_ORG_ID"
export LZ_RHSM_ACTIVATION_KEY="YOUR_RHSM_ACTIVATION_KEY"
```

### 1. Preflight Checks

Validate environment before starting:

```bash
./scripts/setup/preflight_checks.sh \
  --check-pull-secret \
  --check-system-resources \
  --check-libvirt
```

### 2. Create Infrastructure

```bash
make -f Makefile.ci environment
```

**What `make -f Makefile.ci environment` does:**
- Creates 3 master VMs + 1 Landing Zone VM
- Sets up BMC and cluster networks
- Starts BMC emulator (sushy-tools)
- Writes under `$BASE_WORKING_DIR/$CLUSTER_NAME/cluster-env.sh` (the single source of truth)

### 3. Provision Landing Zone

Install RHEL 10 and configure Landing Zone VM:

```bash
make -f Makefile.ci provision-landing-zone
```

**What it does:**
- Creates cloud-init configuration
- Provisions VM with OS
- Configures BMC network (100.64.X.2)
- Configures cluster network (192.168.X.Y)
- Verifies connectivity to BMC emulator

### 4. Install Enclave Lab

Install Enclave Lab software on Landing Zone:

```bash
# Connected mode
ENCLAVE_DEPLOYMENT_MODE=connected make -f Makefile.ci install-enclave

# Disconnected mode
ENCLAVE_DEPLOYMENT_MODE=disconnected make -f Makefile.ci install-enclave
```

**What it does:**
- Clones Enclave Lab repository
- Generates configuration files
- Installs required packages
- Sets up Ansible collections
- Configures pull secret
- Starts httpd service

### 5. Deploy Cluster

Deploy OpenShift cluster:

```bash
make -f Makefile.ci deploy-cluster
```

**Individual phases:**
```bash
make -f Makefile.ci deploy-cluster-prepare          # Download binaries
make -f Makefile.ci deploy-cluster-mirror           # Mirror registry (disconnected only)
make -f Makefile.ci deploy-cluster-install          # Deploy cluster
make -f Makefile.ci deploy-cluster-post-install     # Post-install config
make -f Makefile.ci deploy-cluster-operators        # Install operators
make -f Makefile.ci deploy-cluster-day2             # Day-2 operations
make -f Makefile.ci deploy-cluster-discovery        # Hardware discovery
```

### 6. Verify Cluster

Verify cluster deployment and health:

```bash
make -f Makefile.ci verify-cluster
```

**Checks:**
- Landing Zone IP is reachable
- Kubeconfig exists
- Nodes are Ready
- Cluster operators are healthy (not degraded)
- Cluster version

**Output:** Both terminal (with colors) and GitHub Actions summary format

### 7. Cleanup

Remove all infrastructure:

```bash
make -f Makefile.ci clean
```

**Verify cleanup:**
```bash
make -f Makefile.ci verify-cleanup
```

**Checks for leftover:**
- VMs
- Networks
- Storage pools
- Environment files

## Verification Commands

### Check Infrastructure

```bash
# VMs
virsh list --all | grep ${ENCLAVE_CLUSTER_NAME}

# Networks
virsh net-list | grep ${ENCLAVE_CLUSTER_NAME}

# Storage pools
virsh pool-list --all | grep ${ENCLAVE_CLUSTER_NAME}

# BMC emulator
sudo podman ps | grep sushy-tools
curl -k https://100.64.X.1:8000/redfish/v1/Systems
```

### Check Landing Zone

```bash
# VM status
virsh list | grep landingzone

# Get IP address
./scripts/utils/get_landing_zone_ip.sh

# SSH connectivity
ssh cloud-user@$(./scripts/utils/get_landing_zone_ip.sh) hostname

# BMC network connectivity
ssh cloud-user@$(./scripts/utils/get_landing_zone_ip.sh) \
  "curl -k https://100.64.X.1:8000/redfish/v1/Systems"
```

### Check Enclave Installation

```bash
LZ_IP=$(./scripts/utils/get_landing_zone_ip.sh)

# Directory structure
ssh cloud-user@$LZ_IP "ls -la /home/cloud-user/enclave"

# Configuration files
ssh cloud-user@$LZ_IP "cat /home/cloud-user/enclave/config/global.yaml"

# Ansible collections
ssh cloud-user@$LZ_IP "ansible-galaxy collection list"

# Web server
ssh cloud-user@$LZ_IP "systemctl status httpd"
```

### Check Cluster

```bash
LZ_IP=$(./scripts/utils/get_landing_zone_ip.sh)
SSH_OPTS="-o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null"

# Kubeconfig exists
ssh $SSH_OPTS cloud-user@$LZ_IP \
  "test -f /home/cloud-user/sessions/1/ocp-cluster/auth/kubeconfig && echo 'OK'"

# Nodes
ssh $SSH_OPTS cloud-user@$LZ_IP \
  "export KUBECONFIG=/home/cloud-user/sessions/1/ocp-cluster/auth/kubeconfig && oc get nodes"

# Cluster operators
ssh $SSH_OPTS cloud-user@$LZ_IP \
  "export KUBECONFIG=/home/cloud-user/sessions/1/ocp-cluster/auth/kubeconfig && oc get co"

# Cluster version
ssh $SSH_OPTS cloud-user@$LZ_IP \
  "export KUBECONFIG=/home/cloud-user/sessions/1/ocp-cluster/auth/kubeconfig && oc get clusterversion"
```

## Troubleshooting

### Cluster Name Already Exists

**Error**: `Cluster name eci-XXXXXXXX already exists`

**Solution**: Either clean up or use a different name:
```bash
# Option 1: Clean up old cluster
make -f Makefile.ci clean

# Option 2: Use custom name
export ENCLAVE_CLUSTER_NAME=my-new-cluster-$(date +%s)
make -f Makefile.ci ci-flow-connected
```

### SSH Connection Refused

**Error**: `ssh: connect to host X.X.X.X port 22: Connection refused`

**Debug**:
```bash
LZ_IP=$(./scripts/utils/get_landing_zone_ip.sh)

# Ping test
ping -c 3 $LZ_IP

# Check if SSH port is open
nc -zv $LZ_IP 22

# Check VM console
virsh console ${ENCLAVE_CLUSTER_NAME}_landingzone_0
```

### Cluster Deployment Hangs

**Debug**:
```bash
LZ_IP=$(./scripts/utils/get_landing_zone_ip.sh)

# Check deployment logs
ssh cloud-user@$LZ_IP "tail -f /home/cloud-user/enclave/deployment.log"

# Check Ansible process
ssh cloud-user@$LZ_IP "ps aux | grep ansible"

# Check for errors
ssh cloud-user@$LZ_IP "journalctl -xef"
```

### Degraded Cluster Operators

**Symptom**: `verify-cluster` reports degraded operators

**Debug**:
```bash
LZ_IP=$(./scripts/utils/get_landing_zone_ip.sh)
SSH_OPTS="-o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null"

# Get degraded operators
ssh $SSH_OPTS cloud-user@$LZ_IP \
  "export KUBECONFIG=/home/cloud-user/sessions/1/ocp-cluster/auth/kubeconfig && \
   oc get co -o json | jq -r '.items[] | select(.status.conditions[] | \
   select(.type==\"Degraded\" and .status==\"True\")) | .metadata.name'"

# Describe specific operator
ssh $SSH_OPTS cloud-user@$LZ_IP \
  "export KUBECONFIG=/home/cloud-user/sessions/1/ocp-cluster/auth/kubeconfig && \
   oc describe co <operator-name>"
```

### Cleanup Fails

**Error**: Resources remain after `make -f Makefile.ci clean`

**Force cleanup**:
```bash
# Verify what remains
make -f Makefile.ci verify-cleanup

# Manual VM cleanup
virsh list --all | grep ${ENCLAVE_CLUSTER_NAME} | awk '{print $2}' | \
  xargs -I {} virsh destroy {}
virsh list --all | grep ${ENCLAVE_CLUSTER_NAME} | awk '{print $2}' | \
  xargs -I {} virsh undefine {} --nvram

# Manual network cleanup
virsh net-list --all | grep ${ENCLAVE_CLUSTER_NAME} | awk '{print $1}' | \
  xargs -I {} virsh net-destroy {}
virsh net-list --all | grep ${ENCLAVE_CLUSTER_NAME} | awk '{print $1}' | \
  xargs -I {} virsh net-undefine {}

# Manual pool cleanup
virsh pool-list --all | grep ${ENCLAVE_CLUSTER_NAME} | awk '{print $1}' | \
  xargs -I {} virsh pool-destroy {}
virsh pool-list --all | grep ${ENCLAVE_CLUSTER_NAME} | awk '{print $1}' | \
  xargs -I {} virsh pool-undefine {}
```
