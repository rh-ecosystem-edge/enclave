# Enclave Lab Deployment Guide

This guide provides comprehensive documentation for deploying the Enclave Lab environment using Ansible. The deployment automates the installation of an OpenShift Container Platform (OCP) cluster with various operators and post-installation configurations.

## Table of Contents

1. [Overview](#overview)
2. [What Gets Deployed](#what-gets-deployed)
3. [Prerequisites](#prerequisites)
4. [Configuration](#configuration)
5. [Deployment Workflow](#deployment-workflow)
6. [Configuration Examples](#configuration-examples)
7. [Discovering New Nodes](#discovering-new-nodes)
8. [Troubleshooting](#troubleshooting)

## Overview

The Enclave Lab deployment automates the following:

- **Mirror Registry Setup**: Deploys a local Quay registry for air-gapped or disconnected environments
- **OpenShift Installation**: Deploys an OCP cluster using the Agent-Based Installer (ABI)
- **Hardware Configuration**: Configures bare metal servers using BareMetalOperator and Ironic
- **Operator Installation**: Installs and configures multiple Red Hat operators
- **Post-Install Configuration**: Applies SSL certificates and other cluster configurations
- **Application Deployment**: Deploys custom partner applications

## What Gets Deployed

### 1. Mirror Registry (Quay)

A local container registry is deployed using `mirror-registry` to serve as an internal mirror for:
- OpenShift release images
- Operator catalog images
- Additional container images

**Location**: Deployed as a Podman container on the deployment host

### 2. OpenShift Container Platform Cluster

A full OCP cluster is deployed using the Agent-Based Installer with:
- **Control Plane**: 3 control plane nodes (configurable)
- **Compute Nodes**: 0 compute nodes by default (configurable)
- **Network Configuration**: Custom networking with VIPs for API and Ingress
- **Installation Method**: Agent-Based Installer (ABI) using ISO boot

### 3. Operators

The following Red Hat operators are automatically installed and configured:

| Operator | Namespace | Purpose |
|----------|-----------|---------|
| **LVMS Operator** | `openshift-storage` | Provides local volume management and storage |
| **Quay Operator** | `quay-enterprise` | Container registry management |
| **Advanced Cluster Management** | `open-cluster-management` | Multi-cluster management |
| **OpenShift GitOps** | `openshift-operators` | GitOps workflow management (ArgoCD) |
| **OpenShift Pipelines** | `openshift-operators` | CI/CD pipeline automation |
| **NetObserv Operator** | `openshift-operators` | Network observability |
| **Red Hat OADP** | `openshift-oadp` | Backup and restore operations |
| **OpenShift Cert Manager** | `cert-manager-operator` | Certificate management |

### 4. Post-Install Configuration

- **SSL Certificates**: Custom TLS certificates for API server and Ingress
- **Registry Configuration**: Image registry and pull secret configuration
- **Custom Applications**: Partner specific applications

## Prerequisites

### Hardware Requirements

- **Deployment Host**: RHEL 10 system with:
  - Internet access (for initial downloads)
  - Sufficient disk space for container images and ISO files
  - Root or sudo access
  - Podman installed

- **Bare Metal Servers**:
  - Minimum 3 servers for control plane
  - Redfish-compatible BMC (for hardware configuration)
  - Network connectivity to deployment host
  - Boot from ISO capability

### Software Requirements

The following dependencies are automatically installed by the setup scripts (`setup_env.sh` and `setup_ansible.sh`) when you run `bootstrap.sh`:

- **System Packages** (installed via `setup_env.sh`):
  - `python3-pip`: Python package manager
  - `ansible-core`: Ansible automation tool
  - `podman`: Container runtime for mirror registry
  - `tar`: Archive utility
  - `nmstate`: Network state management
  - `httpd`: HTTP server for serving ISO files
  - `curl`: HTTP client
  - `dnsmasq`: DNS and DHCP server
  - `openssl`: SSL/TLS toolkit
  - `bind-utils`: DNS validation tools (dig, nslookup, etc.) - required for DNS validation

- **Ansible Collections** (installed via `setup_ansible.sh`):
  - `containers.podman`: Podman container management
  - `kubernetes.core`: Kubernetes resource management
  - `community.crypto`: Cryptographic operations

- **Python Packages** (installed via `setup_ansible.sh`):
  - `kubernetes==33.1.0`: Kubernetes Python client

- **SSH Keys**: Generated automatically if not present

**Note**: The `validations.sh` script (run during bootstrap) performs DNS validation using `dig` from `bind-utils`. It validates:
- `api.{{ clusterName }}.{{ baseDomain }}` resolves to the configured `apiVIP`
- `apps.{{ clusterName }}.{{ baseDomain }}` resolves to the configured `ingressVIP`
- `*.apps.{{ clusterName }}.{{ baseDomain }}` (wildcard) resolves to the configured `ingressVIP`
- `mirror.{{ baseDomain }}` resolves to an IP address that exists on the deployment host (Landing Zone)

### Network Requirements

- **Management Network**: For Redfish API access to BMCs
- **Provisioning Network**: For ISO boot and cluster installation
- **VIPs**: Virtual IPs for API and Ingress (must be in the same subnet)

## Configuration

Configuration is split across multiple files for better organization:

### Configuration Structure

**`config/global.yaml`** — main configuration file:
1. **Base Configuration**: Working directory, cluster name, domain
2. **Network Configuration**: VIPs, network ranges, DNS, gateway
3. **Registry Configuration**: Quay settings, backend storage
4. **Hardware Configuration**: Redfish credentials, host definitions

**`config/certificates.yaml`** — SSL certificate configuration:
- API server certificate and private key
- Ingress (wildcard) certificate and private key

**Default configuration files** (in `defaults/` directory):
- `defaults/operators.yaml` - General cluster operators
- `defaults/platforms.yaml` - Available OpenShift versions
- `defaults/storage_operators.yaml` - Storage operators (ODF, LVMS)
- `defaults/model_operators.yaml` - AI/ML model operators
- `defaults/vmaas_operators.yaml` - VMaaS (KubeVirt) operators
- `defaults/control_binaries.yaml` - Binary URLs and checksums (oc, helm, etc.)
- `defaults/content_images.yaml` - RHCOS images and ISOs
- `defaults/catalogs.yaml` - Operator catalog source name mappings
- `defaults/mirror_registry.yaml` - Quay hostname and CA path defaults
- `defaults/quay_operator.yaml` - Quay feature flags and backend storage defaults
- `defaults/lvms_operator.yaml` - LVMS device selector defaults

All configuration files in the `defaults/` directory are automatically loaded by the phase playbooks at runtime.

## Deployment Workflow

The deployment follows this sequence (defined in `playbooks/main-disconnected.yaml`):

```
1. Download Content (RHCOS images)
   ↓
2. Download Control Binaries (oc, helm, etc.)
   ↓
3. Mirror Registry Setup
   ↓
4. OCP ABI Configuration (ISO generation)
   ↓
5. Hardware Configuration (Redfish boot)
   ↓
6. Wait for Deployment (cluster installation)
   ↓
7. Operator Installation
   ↓
8. Post-Install Configuration
```

### Step-by-Step Process

1. **Bootstrap** (`bootstrap.sh`):
   - Validates configuration
   - Sets up environment
   - Downloads dependencies
   - Builds local cache

2. **Download Content** (`download-content` tag):
   - Downloads RHCOS live rootfs images
   - Downloads RHCOS live ISOs
   - Files are stored in `/var/www/html/`

3. **Download Control Binaries** (`download-control-binaries` tag):
   - Creates necessary directories (`bin/`, `dist/`, `config/`, `logs/`)
   - Downloads and extracts OpenShift CLI (`oc`)
   - Downloads Helm CLI
   - Downloads and extracts mirror-registry
   - Downloads and extracts oc-mirror

4. **Mirror Registry** (`mirror-registry` tag):
   - Deploys Quay registry container
   - Configures pull secrets
   - Mirrors OpenShift and operator images

5. **OCP ABI Configuration** (`configure-abi` tag):
   - Generates SSH keys
   - Extracts `openshift-install` binary
   - Creates `install-config.yaml` and `agent-config.yaml`
   - Generates installation ISO
   - Serves ISO via HTTP

6. **Hardware Configuration** (`hardware` tag):
   - Ejects existing virtual media
   - Mounts ISO via Redfish
   - Configures UEFI boot
   - Reboots servers

7. **Wait for Deployment** (`wait-deployment` tag):
   - Waits for bootstrap completion
   - Waits for installation completion
   - Disables default operator catalogs

8. **Operator Installation** (`operators` tag):
   - Creates namespaces
   - Creates OperatorGroups
   - Creates Subscriptions
   - Waits for CSV installation
   - Applies operator-specific configurations

9. **Post-Install Configuration** (`post-install-config` tag):
   - Applies SSL certificates to API server
   - Applies SSL certificates to Ingress
   - Configures registry settings

10. **Model Configuration** (`model-config` tag):
    - Applies ACM Policy including required resources to deploy a model using RHOAI 3.x.

## Configuration Examples

### Content sync

Sync content in existing environment by running `bash sync.sh`.

This script will perform:

1. **Mirror Registry** (`mirror-registry` tag):
   - Deploys Quay registry container (if not deployed)
   - Configures pull secrets
   - Mirrors OpenShift and operator images (quay.io -> lz)

2. **Quay Disconnected** (`quay-disconnected` tag):
   - Mirrors OpenShift and operator images (lz -> quay-enterprise)

3. **ACM ClusterImageSets** (`acm-cis` tag):
   - Reconciles ClusterImageSets based on mirrored OpenShift versions.

### Basic Cluster Configuration

```yaml
# Base configuration
workingDir: "/home/enclave"
baseDomain: enclave-test.nodns.in
clusterName: mgmt

# Network configuration
apiVIP: 192.168.2.201
ingressVIP: 192.168.2.202
machineNetwork: 192.168.2.0/24
defaultDNS: 192.168.2.10
defaultGateway: 192.168.2.10
defaultPrefix: 24
```

### Hardware Configuration

```yaml
# Web server for ISO serving
lzBmcIP: 100.64.1.10  # IP address of deployment host on provisioning network

# Agent hosts (control plane nodes)
agent_hosts:
  - name: mgmt-ctl01
    macAddress: 0c:c4:7a:62:fe:ec
    ipAddress: 192.168.2.24
    redfish: 100.64.1.24  # BMC IP address
    rootDisk: "/dev/disk/by-path/pci-0000:0011.4-ata-1.0"
  - name: mgmt-ctl02
    macAddress: 0c:c4:7a:39:f5:18
    ipAddress: 192.168.2.25
    redfish: 100.64.1.25
    rootDisk: "/dev/disk/by-path/pci-0000:0011.4-ata-1.0"
  - name: mgmt-ctl03
    macAddress: 0c:c4:7a:39:ec:0c
    ipAddress: 192.168.2.26
    redfish: 100.64.1.26
    rootDisk: "/dev/disk/by-path/pci-0000:0011.4-ata-1.0"

# Rendezvous IP (first control plane node)
rendezvousIP: 192.168.2.24
```

### Advanced Network Configuration (Optional)

For complex network setups like bonding, VLANs, or multiple interfaces, use `mapInterfaces` and `networkConfig` instead of the simple `macAddress`/`ipAddress` approach:

```yaml
agent_hosts:
  # Host with bonding configuration
  - name: mgmt-ctl01
    redfish: 100.64.1.24
    rootDisk: "/dev/disk/by-path/pci-0000:0011.4-ata-1.0"
    mapInterfaces:
      - name: eno1
        macAddress: "0c:c4:7a:62:fe:ec"
      - name: eno2
        macAddress: "0c:c4:7a:62:fe:ed"
    networkConfig:
      interfaces:
        - name: bond0
          type: bond
          state: up
          ipv4:
            enabled: true
            address:
              - ip: 192.168.2.24
                prefix-length: 24
          link-aggregation:
            mode: 802.3ad
            options:
              miimon: 100
            port:
              - eno1
              - eno2
        - name: eno1
          type: ethernet
          state: up
          mac-address: "0c:c4:7a:62:fe:ec"
        - name: eno2
          type: ethernet
          state: up
          mac-address: "0c:c4:7a:62:fe:ed"
      routes:
        config:
          - next-hop-address: 192.168.2.10
            next-hop-interface: bond0
            destination: 0.0.0.0/0
      dns-resolver:
        config:
          server:
            - 192.168.2.10

  # Host with simple configuration (can be mixed)
  - name: mgmt-ctl02
    macAddress: 0c:c4:7a:39:f5:18
    ipAddress: 192.168.2.25
    redfish: 100.64.1.25
    rootDisk: "/dev/disk/by-path/pci-0000:0011.4-ata-1.0"
```

**Notes**:
- When using `networkConfig`, the `macAddress` and `ipAddress` fields are not required
- `mapInterfaces` maps interface names to MAC addresses for the agent installer
- `networkConfig` follows the [nmstate](https://nmstate.io/) format
- You can mix hosts with simple and advanced configurations in the same list

### Registry Configuration

```yaml
# Quay registry settings
quayUser: quayadmin
quayPassword: YourSecurePassword
# quayHostname is auto-derived as "mirror.{{ baseDomain }}" — override only if needed

# Quay backend storage (using Ceph/RadosGW)
quayBackend: RadosGWStorage
quayBackendRGWConfiguration:
  access_key: YOUR_ACCESS_KEY
  secret_key: YOUR_SECRET_KEY
  bucket_name: quay-bucket-name
  hostname: ocs-storagecluster-cephobjectstore-openshift-storage.apps.store.enclave-test.nodns.in
  # is_secure, port, and storage_path have defaults in defaults/quay_operator.yaml
  # Uncomment only to override
  # minimum_chunk_size_mb: YOUR_MIN_CHUNK_SIZE_MB  # Default: 100
  # maximum_chunk_size_mb: YOUR_MAX_CHUNK_SIZE_MB  # Default: 500

# Pull secret (combines public and internal registry secrets) - can be downloaded from https://console.redhat.com/openshift/downloads
pullSecret: {"auths":{"cloud.openshift.com":{"auth":"...","email":"..."},"quay.io":{"auth":"...","email":"..."}}}
```

### Operator Configuration

Operators are configured in `defaults/operators.yaml`:

```yaml
operators:
  # Advanced Cluster Management
  - name: advanced-cluster-management
    channel: release-2.15
    namespace: open-cluster-management
    source: cs-redhat-operator-index-v4-19

  # OpenShift GitOps (ArgoCD)
  - name: openshift-gitops-operator
    channel: latest
    namespace: openshift-operators
    source: cs-redhat-operator-index-v4-19

  # OpenShift Pipelines (Tekton)
  - name: openshift-pipelines-operator-rh
    channel: latest
    namespace: openshift-operators
    source: cs-redhat-operator-index-v4-19

  # Network Observability
  - name: netobserv-operator
    channel: stable
    namespace: openshift-operators
    source: cs-redhat-operator-index-v4-19

  # Backup and Restore
  - name: redhat-oadp-operator
    channel: stable
    namespace: openshift-oadp
    source: cs-redhat-operator-index-v4-19

  # Certificate Manager
  - name: openshift-cert-manager-operator
    channel: stable-v1
    namespace: cert-manager-operator
    source: cs-redhat-operator-index-v4-19
  [...]
```

### SSL Certificate Configuration

Place these values in `config/certificates.yaml`:

```yaml
# API Server Certificate
sslAPICertificateKey: |
  -----BEGIN EC PRIVATE KEY-----
  ...
  -----END EC PRIVATE KEY-----

sslAPICertificateFullChain: |
  -----BEGIN CERTIFICATE-----
  ... (certificate chain)
  -----END CERTIFICATE-----

# Ingress Certificate (for *.apps domain)
sslIngressCertificateKey: |
  -----BEGIN EC PRIVATE KEY-----
  ...
  -----END EC PRIVATE KEY-----

sslIngressCertificateFullChain: |
  -----BEGIN CERTIFICATE-----
  ... (certificate chain)
  -----END CERTIFICATE-----
```

### Content Configuration

Content is configured in separate files under `defaults/`:

**`defaults/control_binaries.yaml`** - Control binaries (oc, helm, mirror-registry, oc-mirror):
```yaml
control_binaries:
  openshift_client:
    url: "https://mirror.openshift.com/pub/openshift-v4/clients/ocp/4.19.17/openshift-client-linux.tar.gz"
    checksum: "sha256:..."
  helm:
    url: "https://developers.redhat.com/content-gateway/file/pub/openshift-v4/clients/helm/3.17.1/helm-linux-amd64"
    checksum: "sha256:..."
  mirror_registry:
    url: "https://developers.redhat.com/content-gateway/file/pub/openshift-v4/clients/mirror-registry/1.3.11/mirror-registry.tar.gz"
    checksum: "sha256:..."
  oc_mirror:
    url: "https://mirror.openshift.com/pub/openshift-v4/x86_64/clients/ocp/4.19.17/oc-mirror.tar.gz"
    checksum: "sha256:..."
```

**`defaults/content_images.yaml`** - Content images (RHCOS ISO and rootfs):
```yaml
content_images:
  imgs:
    - url: "https://mirror.openshift.com/pub/openshift-v4/dependencies/rhcos/4.19/4.19.10/rhcos-4.19.10-x86_64-live-rootfs.x86_64.img"
      checksum: "sha256:..."
  isos:
    - url: "https://mirror.openshift.com/pub/openshift-v4/dependencies/rhcos/4.19/4.19.10/rhcos-4.19.10-x86_64-live-iso.x86_64.iso"
      checksum: "sha256:..."
```

## Detailed Configuration Reference

### Network Configuration

| Variable | Description | Example |
|----------|-------------|---------|
| `baseDomain` | Base domain for the cluster | `enclave-test.nodns.in` |
| `clusterName` | Short name of the cluster | `mgmt` |
| `apiVIP` | Virtual IP for API server | `192.168.2.201` |
| `ingressVIP` | Virtual IP for Ingress | `192.168.2.202` |
| `machineNetwork` | Network CIDR for cluster nodes | `192.168.2.0/24` |
| `defaultDNS` | DNS server IP | `192.168.2.10` |
| `defaultGateway` | Default gateway IP | `192.168.2.10` |
| `defaultPrefix` | Network prefix length | `24` |
| `rendezvousIP` | IP of first control plane node | `192.168.2.24` |

### Hardware Configuration

| Variable | Description | Example |
|----------|-------------|---------|
| `redfishUser` | Redfish API username | `admin` |
| `redfishPassword` | Redfish API password | `YourPassword` |
| `lzBmcIP` | IP address serving ISO files | `100.64.1.10` |
| `agent_hosts` | List of control plane nodes | See example above |

Each `agent_hosts` entry requires:
- `name`: Hostname for the node
- `macAddress`: MAC address for network identification (not required if using `networkConfig`)
- `ipAddress`: Static IP address for the node (not required if using `networkConfig`)
- `redfish`: BMC IP address for Redfish API
- `rootDisk`: Physical disk path for root filesystem (e.g., `/dev/disk/by-path/pci-0000:0011.4-ata-1.0`). **Important**: Use physical connection paths from `/dev/disk/by-path/` instead of `/dev/sda` as device names can change between reboots.

Optional fields for advanced network configuration:
- `mapInterfaces`: List of interface name to MAC address mappings
- `networkConfig`: Full nmstate network configuration (bonding, VLANs, etc.)

### Registry Configuration

| Variable | Description | Example |
|----------|-------------|---------|
| `quayUser` | Quay admin username | `quayadmin` |
| `quayPassword` | Quay admin password | `SecurePassword` |
| `quayHostname` | Quay registry hostname (auto-derived as `mirror.{{ baseDomain }}`) | `mirror.enclave-test.nodns.in` |
| `quayBackend` | Storage backend type | `RadosGWStorage` |
| `quayBackendRGWConfiguration` | Backend-specific configuration | See example above |

### Operator Configuration

Each operator in the `operators` list requires:
- `name`: Operator name (must match catalog package name)
- `channel`: Update channel (e.g., `stable-4.19`, `latest`)
- `namespace`: Target namespace for the operator
- `source`: Catalog source name (from oc-mirror)
- `config`: (Optional) Operator-specific configuration

### Additional Functionality Configuration

| Variable | Description | Example |
|----------|-------------|---------|
| `enable_openshift_ai` | Enable OpenShift AI features | `true` |
| `enable_nvidia_gpu` | Enable Nvidia GPU functionality | `true` |

### SSL Certificates

Certificates are stored in `config/certificates.yaml` and must be provided in PEM format:
- Private keys: `-----BEGIN EC PRIVATE KEY-----` or `-----BEGIN RSA PRIVATE KEY-----`
- Certificate chains: Full chain including intermediate certificates

## Running the Deployment

### Initial Setup

1. **Get the deployment files**:

   **Option A: Using Git Repository (Development)**

   If you're developing or need the latest code from the repository:
   ```bash
   git clone <repository-url>
   cd enclave
   ```

   **Option B: Using Quay Container Image (Production)**

   If you're using the published container image from Quay:
   ```bash
   podman login quay.io
   ID=$(podman create quay.io/edge-infrastructure/enclave:latest)
   mkdir -p enclave
   podman cp $ID:/enclave/. ./enclave/
   podman rm $ID &>/dev/null
   cd enclave
   ```

2. **Configure variables**:
   ```bash
   cp config/global.example.yaml config/global.yaml
   cp config/certificates.example.yaml config/certificates.yaml
   cp config/cloud_infra.example.yaml config/cloud_infra.yaml
   vim config/global.yaml       # Edit cluster, network, hardware and registry settings
   vim config/certificates.yaml # Add SSL certificates
   vim config/cloud_infra.yaml  # Add discovery hosts (leave discovery_hosts: [] if none)
   ```

3. **Run bootstrap**:
   ```bash
   bash bootstrap.sh
   ```

### Session Management

The installation process can take a considerable amount of time (potentially several hours depending on your environment, network speed, and storage performance). To prevent issues with session timeouts or disconnections, it is recommended to use a terminal multiplexer like `tmux` or `screen`.

**Using tmux** (recommended):

1. **Install tmux** (if not already installed):
   ```bash
   sudo dnf install tmux
   ```

2. **Start a new tmux session**:
   ```bash
   tmux new -s enclave-deployment
   ```

3. **Run your deployment commands** within the tmux session:
   ```bash
   bash bootstrap.sh
   # or any other deployment commands
   ```

4. **Detach from the session** (keeps it running in background):
   - Press `Ctrl+b`, then press `d`

5. **Reattach to the session** (after reconnecting to the server):
   ```bash
   tmux attach -t enclave-deployment
   ```

This ensures that the deployment continues running even if your SSH connection drops or you need to disconnect from the deployment host.

### Running Individual Phases

You can run individual deployment phases using the modular playbooks:

```bash
# Phase 1: Download binaries and content (oc, RHCOS images, etc.)
ansible-playbook playbooks/01-prepare.yaml -e workingDir=/home/cloud-user

# Phase 2: Setup mirror registry and mirror images (disconnected only)
ansible-playbook playbooks/02-mirror.yaml -e workingDir=/home/cloud-user

# Phase 3: Deploy OpenShift cluster (generate ISO, boot servers, wait for installation)
ansible-playbook playbooks/03-deploy.yaml -e workingDir=/home/cloud-user

# Phase 4: Post-install configuration (cluster config, secrets, certificates)
ansible-playbook playbooks/04-post-install.yaml -e workingDir=/home/cloud-user

# Phase 5: Install and configure operators (LVMS, ODF, Quay, etc.)
ansible-playbook playbooks/05-operators.yaml -e workingDir=/home/cloud-user

# Phase 6: Day-2 operations (Clair, ACM policies, model config)
ansible-playbook playbooks/06-day2.yaml -e workingDir=/home/cloud-user

# Phase 7: Configure hardware discovery (optional)
ansible-playbook playbooks/07-configure-discovery.yaml -e workingDir=/home/cloud-user

# Full disconnected deployment (all phases)
ansible-playbook playbooks/main.yaml -e workingDir=/home/cloud-user
```

## Troubleshooting

For diagnostic log collection, see the [Log Collection Tool](../lz-gather-logs/README.md).

### Common Issues

1. **Redfish API Connection Failures**:
   - Verify BMC IP addresses are correct
   - Check network connectivity to BMCs
   - Verify Redfish credentials
   - Try setting `redfish_legacy: true` for older BMCs

2. **ISO Boot Failures**:
   - Verify `lzBmcIP` is accessible from BMC network
   - Check HTTP server is running on deployment host
   - Verify ISO file exists at `/var/www/html/assisted/agent.x86_64.iso`

3. **Cluster Installation Failures**:
   - Check cluster logs: `{{ workingDir }}/ocp-cluster/.openshift_install.log`
   - Verify network configuration matches actual network
   - Check VIPs are not in use by other systems
   - Verify DNS resolution works
   - **Monitor installation progress**: During the management cluster installation phase, you can monitor the installation progress by running:
     ```bash
     cd ~/ocp-cluster && openshift-install agent wait-for install-complete --log-level debug
     ```
   - **View overall deployment logs**: For the overall deployment log, check the `logs/` directory and run:
     ```bash
     tail -f logs/$(ls -t logs/ | head -1)
     ```
   - **Re-running the deployment**: If you need to re-run the deployment for some reason, you'll need to remove the lock file if it already exists:
     ```bash
     rm ~/.lck-rh-lz
     ```

4. **Operator Installation Failures**:
   - Check operator catalog is available: `oc get catalogsource -n openshift-marketplace`
   - Verify operator subscriptions: `oc get subscription -A`
   - Check CSV status: `oc get csv -A`

5. **Mirror Registry Issues**:
   - Check Quay container is running: `podman ps`
   - Verify pull secrets are correctly configured
   - Check oc-mirror logs: `{{ workingDir }}/logs/oc-mirror.progress.log`

### Log Files

- **Bootstrap logs**: `logs/<timestamp>`
- **OC Mirror logs**: `{{ workingDir }}/logs/oc-mirror.progress.log`
- **OpenShift Install logs**: `{{ workingDir }}/ocp-cluster/.openshift_install.log`

### Verification Steps

1. **Check cluster status**:
   ```bash
   export KUBECONFIG={{ workingDir }}/ocp-cluster/auth/kubeconfig
   oc get nodes
   oc get clusteroperators
   ```

2. **Verify operators**:
   ```bash
   oc get csv -A
   oc get subscription -A
   ```

3. **Check registry**:
   ```bash
   podman ps | grep quay
   curl -k https://{{ quayHostname }}:8443
   ```

## Discovering New Nodes

After the initial cluster deployment, you can discover and add new bare metal nodes to the cluster.

### Recommended Approach: Red Hat Advanced Cluster Management (ACM)

**Red Hat ACM is the recommended way to manage host discovery and bare metal infrastructure.** ACM provides a comprehensive interface for managing the complete lifecycle of bare metal hosts, including:

- Adding new hosts to the cluster
- Removing hosts from the cluster
- Modifying host configurations
- Monitoring host status and health
- Managing host scaling operations

For detailed information on managing bare metal hosts with ACM, refer to the official documentation:
- [Managing bare metal hosts using Red Hat ACM](https://docs.redhat.com/en/documentation/red_hat_advanced_cluster_management_for_kubernetes/2.15/html/clusters/cluster_mce_overview#cim-intro)

### Alternative: Enclave Configuration (One-Time Convenience)

As a convenience for initial setup, you can configure hosts in the Enclave configuration (`config/cloud_infra.yaml`) and use the discovery playbook to boot them. **However, this is intended as a one-time operation.**

**Important:** If you need to perform any of the following operations, you must use Red Hat ACM instead of the Enclave configuration:
- Add more nodes after initial deployment
- Remove nodes from the cluster
- Change node configurations
- Scale up or down the cluster
- Perform day-2 operations on bare metal hosts

The Enclave configuration method is provided only for initial convenience and is not suitable for ongoing infrastructure management.

### Prerequisites for Enclave Configuration Method

- The management cluster must be fully deployed and operational
- You must have access to the cluster's kubeconfig file
- The new nodes must have Redfish-compatible BMCs
- Network connectivity must be configured for the new nodes

### Configuration

If you have not created `config/cloud_infra.yaml` yet, copy the example file first:

```bash
cp config/cloud_infra.example.yaml config/cloud_infra.yaml
```

Then add or edit the `discovery_hosts` section with the details of the nodes you want to discover:

```yaml
# Discovery hosts for cloud infrastructure (CaaS)
# These are worker nodes that will be discovered and added to the cluster
discovery_hosts:
  - name: node01
    macAddress: 0c:c4:7a:d3:bc:30
    ipAddress: 192.168.2.21
    redfish: 100.64.1.21  # BMC IP address
    rootDisk: "/dev/disk/by-path/pci-0000:0011.4-ata-1.0"
    redfishUser: admin
    redfishPassword: YourSecurePassword
  - name: node02
    macAddress: 0c:c4:7a:65:d0:84
    ipAddress: 192.168.2.22
    redfish: 100.64.1.22
    rootDisk: "/dev/disk/by-path/pci-0000:0011.4-ata-1.0"
    redfishUser: admin
    redfishPassword: YourSecurePassword
  # Add more nodes as needed
```

### Configuration Fields

Each node in `discovery_hosts` requires:

| Field | Description | Example |
|-------|-------------|---------|
| `name` | Hostname for the node | `node01` |
| `macAddress` | MAC address of the primary network interface | `0c:c4:7a:d3:bc:30` |
| `ipAddress` | Static IP address for the node | `192.168.2.21` |
| `redfish` | BMC IP address for Redfish API access | `100.64.1.21` |
| `rootDisk` | Physical disk path for root filesystem (use `/dev/disk/by-path/` paths) | `/dev/disk/by-path/pci-0000:0011.4-ata-1.0` |
| `redfishUser` | Redfish username | `admin` |
| `redfishPassword` | Redfish password | `Password` |

### Running Discovery (One-Time Setup Only)

> **⚠️ Warning:** This method is intended for initial setup only. For any subsequent host management operations, use Red Hat ACM instead.

1. **Edit the configuration** in `config/cloud_infra.yaml`:
   ```bash
   vim config/cloud_infra.yaml
   # Add or update the discovery_hosts section
   ```

2. **Run the discovery playbook**:
   ```bash
   ansible-playbook -e @config/global.yaml -e @config/certificates.yaml -e @config/cloud_infra.yaml playbooks/07-configure-discovery.yaml
   ```

   Or if you're on the Landing Zone and Enclave is installed:
   ```bash
   cd /home/cloud-user/enclave
   ansible-playbook -e @config/global.yaml -e @config/certificates.yaml -e @config/cloud_infra.yaml playbooks/07-configure-discovery.yaml
   ```

### What the Discovery Process Does

The discovery process performs the following steps:

1. **Checks existing agents**: Queries the cluster for already discovered agents to avoid duplicates
2. **Creates NMStateConfig**: Creates network configuration for each new node
3. **Creates InfraEnv resource**: The InfraEnv generates the discovery ISO image
4. **Deploys Metal3 infrastructure**: Ensures the metal3-stack pod is running to handle host provisioning
5. **Creates BMC credentials**: Creates secrets with BMC credentials for each host
6. **Creates BareMetalHost resources**: Creates Metal3 BareMetalHost resources with:
   - BMC connection details
   - bootMACAddress field set to the node's MAC address
   - Reference to the InfraEnv for the discovery ISO
7. **Monitors for Host discovery**:
   - Each host boots from the discovery ISO and registers as an Agent

### Discovery ISO and Metal3 Provisioning

The discovery ISO is automatically generated by the Assisted Installer service running in the cluster through the InfraEnv resource. With the Metal3 integration:
- The InfraEnv generates a minimal discovery ISO
- Metal3 BareMetalHost resources reference the InfraEnv for the ISO location
- The Metal3 operator handles mounting the ISO to each host via Redfish virtual media
- Hosts automatically boot from the ISO and register as Agents

### Verifying Discovery

After running the discovery script, you can verify that nodes are being discovered:

```bash
export KUBECONFIG={{ workingDir }}/ocp-cluster/auth/kubeconfig

# Check agents in the infraenv namespace
oc get agents -n infraenv

# Check BareMetalHost resources
oc get baremetalhosts -n infraenv
```

### Notes

- The discovery process automatically skips nodes that are already discovered (based on BMC address)
- If the node is pending to be restarted (after cluster destroy), it will be discovered again
- Nodes will appear as "Agents" in the `infraenv` namespace
- BareMetalHost resources are created for each node with the MAC address in the bootMACAddress field
- Agents are automatically approved by the assisted-service controllers when their inventory MAC addresses match the bootMACAddress in the corresponding BareMetalHost resource
- Each node boots from the discovery ISO and registers with the Assisted Installer service
- After discovery and approval, nodes can be used for cluster expansion or creation
- **Important**: Always use physical disk paths from `/dev/disk/by-path/` for `rootDisk`. Device names like `/dev/sda` can change between reboots, but physical paths remain stable. To find the physical path, if you have the server booted, you can use: `ls -l /dev/disk/by-path/ | grep <disk>` or `lsblk -o NAME,PATH` on the target server.

## Additional Resources

- **Topology**: See `Topo.png` for network topology
- **Architecture**: See `ArchMap.png` for deployment architecture
- **Hardware Setup**: See `Topology.pdf` for expected hardware configuration

## Notes

- The deployment is **destructive** - running bootstrap.sh will destroy and recreate the entire environment
- Some steps reuse local caches (downloaded binaries, images) for faster re-runs
- The deployment host must have internet access for initial downloads
- After mirror registry setup, the cluster operates in a disconnected/air-gapped mode
