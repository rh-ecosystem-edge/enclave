# Local CI Testing Guide

This guide explains how to run the complete CI workflow locally using Makefile targets. All CI workflows in GitHub Actions use these same targets, ensuring consistency between local testing and automated CI.

## Table of Contents

- [Quick Start](#quick-start)
- [Prerequisites](#prerequisites)
- [Full CI Flow](#full-ci-flow)
- [Individual Components](#individual-components)
- [Environment Variables](#environment-variables)
- [Verification Commands](#verification-commands)
- [Troubleshooting](#troubleshooting)
- [Best Practices](#best-practices)

## Quick Start

Run the complete CI workflow locally in one command:

```bash
# Set required environment variables
export BASE_WORKING_DIR=/opt/clusters

# Run full CI flow (connected mode - faster)
make -f Makefile.ci ci-flow-connected

# Or disconnected mode (full validation)
make -f Makefile.ci ci-flow-disconnected
```

The flow automatically:
- Generates a unique cluster name
- Runs preflight checks
- Creates infrastructure
- Provisions Landing Zone
- Installs Enclave Lab
- Deploys OpenShift cluster
- Verifies cluster deployment

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
export BASE_WORKING_DIR=/opt/clusters           # Base directory for cluster data
```

#### Optional
```bash
export ENCLAVE_CLUSTER_NAME=my-cluster         # Custom cluster name (auto-generated if not set)
export ENCLAVE_DEPLOYMENT_MODE=connected       # "connected" or "disconnected" (default)
export PULL_SECRET=/path/to/pull-secret.json   # Red Hat pull secret
```

## Full CI Flow

### Connected Mode (Recommended for Development)

Fastest option - skips mirror registry setup:

```bash
export BASE_WORKING_DIR=/opt/clusters

make -f Makefile.ci ci-flow-connected
```

**What happens:**
1. Preflight checks validate environment
2. Generates unique cluster name (e.g., `eci-a28be7f5`)
3. Creates cluster-specific working directory
4. Creates VMs, networks, and BMC emulation
5. Provisions Landing Zone VM with CentOS Stream 10
6. Installs Enclave Lab on Landing Zone
7. Deploys OpenShift cluster (pulls from upstream registries)
8. Verifies cluster health (nodes, operators, version)

**Duration:** ~45-60 minutes

### Disconnected Mode (Production Validation)

Full air-gapped deployment with local mirror registry:

```bash
export BASE_WORKING_DIR=/opt/clusters

make -f Makefile.ci ci-flow-disconnected
```

**Additional steps:**
- Creates local Quay registry
- Mirrors all required container images (~30-40GB)
- Uses local registry for cluster deployment

**Duration:** ~90-120 minutes (extra time for mirroring)

### Custom Cluster Name

Override automatic cluster name generation:

```bash
export ENCLAVE_CLUSTER_NAME=dev-test-cluster
export BASE_WORKING_DIR=/opt/clusters

make -f Makefile.ci ci-flow-connected
```

## Individual Components

You can run each phase of the CI flow independently for faster iteration:

### 1. Preflight Checks

Validate environment before starting:

```bash
make -f Makefile.ci preflight-checks
```

**Checks:**
- WORKING_DIR is set
- System has sufficient RAM
- Libvirt is accessible
- Disk space is available

**Options:**
```bash
# With all checks
./scripts/setup/preflight_checks.sh \
  --check-pull-secret \
  --check-system-resources \
  --check-libvirt

# Custom title
./scripts/setup/preflight_checks.sh --title "My Custom Checks"
```

### 2. Create Infrastructure

Create VMs, networks, and BMC emulation:

```bash
make -f Makefile.ci environment
```

`vm_infra.py` owns cluster identity: when `ENCLAVE_CLUSTER_NAME` is unset it generates a
unique `<prefix>-<8hex>` name (override the prefix with `ENCLAVE_CLUSTER_PREFIX`, default
`eci`) and derives `WORKING_DIR` as `${BASE_WORKING_DIR}/clusters/${ENCLAVE_CLUSTER_NAME}`.

For a multi-step local run where the name is generated, either **pin the name** so every
step shares it:

```bash
export ENCLAVE_CLUSTER_NAME=eci-local
make -f Makefile.ci environment
make -f Makefile.ci provision-landing-zone
```

or let `make -f Makefile.ci environment` record the generated identity into an env file and read the two
values back **as data** (this is what `make -f Makefile.ci ci-flow-connected` does under the hood — it
never runs `create` twice):

```bash
ident=$(mktemp)
GITHUB_ENV="$ident" make -f Makefile.ci environment
# Read the values literally (do NOT `source` the file — it is GitHub's KEY=value format,
# not shell-quoted, so a path with spaces would break sourcing).
export ENCLAVE_CLUSTER_NAME="$(sed -n 's/^ENCLAVE_CLUSTER_NAME=//p' "$ident")"
export WORKING_DIR="$(sed -n 's/^WORKING_DIR=//p' "$ident")"
echo "$ENCLAVE_CLUSTER_NAME $WORKING_DIR"
make -f Makefile.ci provision-landing-zone
```

> Do **not** run `vm_infra.py create` again to "read back" the name — a second create
> generates a new name and a whole second cluster.

**What `make -f Makefile.ci environment` does:**
- Generates the cluster name + working dir (via vm_infra.py)
- Creates 3 master VMs + 1 Landing Zone VM
- Sets up BMC and cluster networks
- Starts BMC emulator (sushy-tools)
- Writes cluster-env.sh (the single source of truth)

**Verify:**
```bash
virsh list --all                    # Check VMs created
virsh net-list                      # Check networks active
sudo podman ps | grep sushy-tools   # Check BMC emulator
```

### 3. Provision Landing Zone

Install OS and configure Landing Zone VM:

```bash
make -f Makefile.ci provision-landing-zone
```

**What it does:**
- Downloads CentOS Stream 10 cloud image
- Creates cloud-init configuration
- Provisions VM with OS
- Configures BMC network (100.64.X.2)
- Configures cluster network (192.168.X.Y)
- Verifies connectivity to BMC emulator

**Verify:**
```bash
make -f Makefile.ci verify-landing-zone

# Or manually:
virsh list | grep landingzone       # VM running
ssh cloud-user@<landing-zone-ip>    # SSH access works
```

### 4. Install Enclave Lab

Install Enclave Lab software on Landing Zone:

```bash
# Connected mode (faster)
ENCLAVE_DEPLOYMENT_MODE=connected make -f Makefile.ci install-enclave

# Disconnected mode (default)
make -f Makefile.ci install-enclave
```

**What it does:**
- Clones Enclave Lab repository
- Generates configuration files
- Installs required packages
- Sets up Ansible collections
- Configures pull secret
- Starts httpd service

**Verify:**
```bash
make -f Makefile.ci verify-enclave-installation

# Or manually:
ssh cloud-user@<landing-zone-ip> ls -la /home/cloud-user/enclave
```

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

## Environment Variables

### BASE_WORKING_DIR

**Required**: Yes (for `make -f Makefile.ci environment` / `ci-flow-*`)
**Description**: Base directory for cluster-specific data; vm_infra.py derives WORKING_DIR from it
**Default**: `/opt/clusters` (in some scripts)
**Example**: `/opt/clusters`

```bash
export BASE_WORKING_DIR=/opt/clusters
```

Each cluster gets: `${BASE_WORKING_DIR}/clusters/${ENCLAVE_CLUSTER_NAME}`

### ENCLAVE_CLUSTER_NAME

**Required**: No (auto-generated if not set)
**Description**: Unique cluster name
**Auto-generated format**: `eci-XXXXXXXX` (hash of timestamp + PID)
**Example**: `eci-a28be7f5` or `my-test-cluster`

```bash
# Auto-generate (recommended for local testing)
unset ENCLAVE_CLUSTER_NAME
make -f Makefile.ci ci-flow-connected

# Custom name
export ENCLAVE_CLUSTER_NAME=dev-cluster-1
make -f Makefile.ci ci-flow-connected
```

### ENCLAVE_DEPLOYMENT_MODE

**Required**: No
**Description**: Deployment mode
**Values**: `connected` or `disconnected`
**Default**: `disconnected`

```bash
# Connected mode (faster, uses upstream registries)
export ENCLAVE_DEPLOYMENT_MODE=connected

# Disconnected mode (mirrors to local registry)
export ENCLAVE_DEPLOYMENT_MODE=disconnected
```

### WORKING_DIR

**Required**: No
**Description**: Cluster-specific working directory
**Auto-set by**: `vm_infra.py` (via `make -f Makefile.ci environment`), recorded in `cluster-env.sh` / `$GITHUB_ENV`
**Format**: `${BASE_WORKING_DIR}/clusters/${ENCLAVE_CLUSTER_NAME}`

Usually don't set manually - let `vm_infra.py` derive it from `BASE_WORKING_DIR`.

### PULL_SECRET

**Required**: For cluster deployment
**Description**: Red Hat pull secret for image downloads
**Obtain from**: https://console.redhat.com/openshift/install/pull-secret

```bash
export PULL_SECRET=/path/to/pull-secret.json
# Or inline:
export PULL_SECRET='{"auths":{"cloud.openshift.com":...}}'
```

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

### Storage Pool Already Active

**Error**: `storage pool 'oooq_pool' is already active`

**Fix**: This should be handled automatically now. If you still see it:
```bash
# Check pool status
sudo virsh pool-info oooq_pool

# Refresh pool
sudo virsh pool-refresh oooq_pool
```

### Landing Zone IP Not Found

**Error**: `Cannot find Landing Zone IP`

**Debug**:
```bash
# Check environment file
cat ${WORKING_DIR}/environment-${ENCLAVE_CLUSTER_NAME}.json

# Check VM
sudo virsh domifaddr ${ENCLAVE_CLUSTER_NAME}_landingzone_0

# Check VM is running
virsh list | grep landingzone

# Check VM console
virsh console ${ENCLAVE_CLUSTER_NAME}_landingzone_0  # Ctrl+] to exit
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

## Best Practices

### Development Workflow

1. **Always validate before commit**:
   ```bash
   make -f Makefile.ci validate
   ```

2. **Use connected mode for development**:
   ```bash
   ENCLAVE_DEPLOYMENT_MODE=connected make -f Makefile.ci ci-flow-connected
   ```

3. **Test disconnected mode before PR**:
   ```bash
   make -f Makefile.ci ci-flow-disconnected
   ```

4. **Clean up after testing**:
   ```bash
   make -f Makefile.ci clean
   make -f Makefile.ci verify-cleanup
   ```

### Efficient Testing

**Test specific components instead of full flow:**

Pin `ENCLAVE_CLUSTER_NAME` first (e.g. `export ENCLAVE_CLUSTER_NAME=eci-local`) so each
target operates on the same cluster; otherwise `make -f Makefile.ci environment` generates a name that
the later standalone targets won't know.

```bash
# Just infrastructure
make -f Makefile.ci environment
make -f Makefile.ci verify

# Just Landing Zone
make -f Makefile.ci provision-landing-zone
make -f Makefile.ci verify-landing-zone

# Just Enclave installation
ENCLAVE_DEPLOYMENT_MODE=connected make -f Makefile.ci install-enclave
make -f Makefile.ci verify-enclave-installation
```

**Use custom cluster names for parallel testing:**

```bash
# Terminal 1
export ENCLAVE_CLUSTER_NAME=test-feature-a
make -f Makefile.ci ci-flow-connected

# Terminal 2
export ENCLAVE_CLUSTER_NAME=test-feature-b
make -f Makefile.ci ci-flow-connected
```

### Resource Management

**Monitor resource usage:**

```bash
# RAM
free -h

# Disk
df -h ${BASE_WORKING_DIR}

# VMs
virsh list --all | wc -l
```

**Clean up regularly:**

```bash
# Remove old clusters
ls ${BASE_WORKING_DIR}/clusters/

# Remove specific cluster
export ENCLAVE_CLUSTER_NAME=old-cluster
make -f Makefile.ci clean
```

### Debugging

**Enable verbose output:**

```bash
# For Makefile
make -f Makefile.ci -n ci-flow-connected     # Dry run (show commands)

# For scripts
bash -x ./scripts/infrastructure/provision_landing_zone.sh

# For Ansible (on Landing Zone)
ssh cloud-user@$LZ_IP
cd /home/cloud-user/enclave
ansible-playbook -vvv playbooks/main.yaml
```

**Collect logs for troubleshooting:**

```bash
# Step logs
make -f Makefile.ci collect-step-logs

# Full artifacts
make -f Makefile.ci collect-artifacts-full
ls artifacts/
```

### CI Consistency

**Test the same way CI does:**

```bash
# Mimic e2e-deployment.yml workflow
make -f Makefile.ci validate
export BASE_WORKING_DIR=/opt/clusters
export ENCLAVE_DEPLOYMENT_MODE=connected
# Pin the cluster name so every target below shares the same identity. (Leave it unset
# and vm_infra.py generates one — but then use `make -f Makefile.ci ci-flow-connected`, or the
# identity-capture shown in "Create Infrastructure" above, so later targets can find it.)
export ENCLAVE_CLUSTER_NAME=eci-local
make -f Makefile.ci preflight-checks
make -f Makefile.ci environment
make -f Makefile.ci provision-landing-zone
make -f Makefile.ci install-enclave
make -f Makefile.ci deploy-cluster
make -f Makefile.ci verify-cluster
make -f Makefile.ci clean
make -f Makefile.ci verify-cleanup
```

**Or use the one-liner:**

```bash
make -f Makefile.ci ci-flow-connected
```

## Advanced Usage

### Custom Scripts Location

If scripts are updated, use them directly:

```bash
# Instead of: make -f Makefile.ci verify-cluster
./scripts/verification/verify_cluster.sh

# With custom arguments
./scripts/setup/preflight_checks.sh --check-all --title "Custom Checks"
```

### Parallel Testing

Test multiple configurations simultaneously:

```bash
# Terminal 1: Connected mode
export ENCLAVE_CLUSTER_NAME=test-connected-$(date +%s)
export ENCLAVE_DEPLOYMENT_MODE=connected
make -f Makefile.ci ci-flow-connected

# Terminal 2: Disconnected mode
export ENCLAVE_CLUSTER_NAME=test-disconnected-$(date +%s)
export ENCLAVE_DEPLOYMENT_MODE=disconnected
make -f Makefile.ci ci-flow-disconnected
```

### Custom Working Directory

Use different base directories:

```bash
# Project-specific directory
export BASE_WORKING_DIR=/opt/my-project/clusters
mkdir -p $BASE_WORKING_DIR

make -f Makefile.ci ci-flow-connected
```

### Integration with Your Workflow

Add to your shell profile:

```bash
# ~/.bashrc or ~/.zshrc
export BASE_WORKING_DIR=/opt/clusters

alias enclave-test='make -f Makefile.ci ci-flow-connected'
alias enclave-clean='make -f Makefile.ci clean && make -f Makefile.ci verify-cleanup'
alias enclave-verify='make -f Makefile.ci verify-cluster'
```

## Summary

The local CI testing workflow provides:

✅ **Consistency**: Same commands locally and in CI
✅ **Speed**: Test locally before pushing
✅ **Flexibility**: Run full flow or individual components
✅ **Debugging**: Full control and visibility
✅ **Confidence**: Verify changes before PR

**Key commands to remember:**

```bash
# Full flow
make -f Makefile.ci ci-flow-connected

# Verify
make -f Makefile.ci verify-cluster

# Clean up
make -f Makefile.ci clean
```

For more information:
- **README.md**: Overview and quick start
- **docs/CI_WORKFLOWS.md**: GitHub Actions workflows
- **Makefile**: All available targets (`make -f Makefile.ci help`)
