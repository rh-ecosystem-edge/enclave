# Configuration Reference

This document provides detailed explanations and examples for all configuration variables.

## Configuration File Structure

Configuration is split across multiple files for better organization and maintainability:

| File | Description |
|------|-------------|
| `config/global.yaml` | Main configuration file with cluster, network, hardware, registry, and pull secret settings |
| `config/certificates.yaml` | SSL certificates for the API server and Ingress |
| `config/cloud_infra.yaml` | Cloud infrastructure configuration, including discovery hosts for bare metal node discovery |
| `defaults/operators.yaml` | General cluster operators configuration |
| `defaults/platforms.yaml` | Available OpenShift versions |
| `defaults/deployment.yaml` | Deployment defaults (storage plugin, disconnected mode, etc.) |
| `defaults/control_binaries.yaml` | URLs and checksums for required binaries (oc, helm, etc.) |
| `defaults/content_images.yaml` | RHCOS images and ISOs configuration |
| `defaults/catalogs.yaml` | Operator catalog source name mappings |
| `defaults/mirror_registry.yaml` | Quay hostname and CA path defaults |
| `defaults/k8s.yaml` | Kubernetes retry settings for k8s module calls |
| `defaults/quay_operator.yaml` | Quay feature flags and backend storage defaults |
| `plugins/<name>/plugin.yaml` | Plugin configuration (operators, defaults, registries) |

Copy the example files to get started:
```bash
cp config/global.example.yaml config/global.yaml
cp config/certificates.example.yaml config/certificates.yaml
cp config/cloud_infra.example.yaml config/cloud_infra.yaml
```

All configuration files in the `defaults/` directory are automatically loaded by the phase playbooks at runtime via `playbooks/common/load-vars.yaml`.

## Table of Contents

1. [Base Configuration](#base-configuration)
2. [Network Configuration](#network-configuration)
3. [Hardware Configuration](#hardware-configuration)
4. [Discovery Hosts Configuration](#discovery-hosts-configuration)
5. [Registry Configuration](#registry-configuration)
6. [Storage Configuration](#storage-configuration)
7. [SSL Certificate Configuration](#ssl-certificate-configuration)
8. [Operator Configuration](#operator-configuration)
9. [Content Configuration](#content-configuration)
10. [Complete Example](#complete-example)

## Base Configuration

### `workingDir`

**Description**: Root directory where all deployment files, binaries, and cluster data will be stored.

**Type**: String (absolute path)

**Example**:
```yaml
workingDir: "/home/enclave"
```

**Notes**:
- Must be an absolute path
- Requires sufficient disk space (recommended: 100GB+)
- Used for:
  - Downloaded binaries (`{{ workingDir }}/bin/`)
  - Cluster configuration (`{{ workingDir }}/ocp-cluster/`)
  - Registry data (`{{ workingDir }}/data/`)
  - Pull secrets (`{{ workingDir }}/config/pull-secret.json`)

## Network Configuration

### Cluster Network Settings

#### `baseDomain`

**Description**: Base domain name for the OpenShift cluster. All cluster services will use subdomains of this domain.

**Type**: String

**Example**:
```yaml
baseDomain: enclave-test.nodns.in
```

**Resulting URLs**:
- API: `api.mgmt.enclave-test.nodns.in`
- Ingress: `*.apps.mgmt.enclave-test.nodns.in`
- Quay: `mirror.enclave-test.nodns.in`

#### `clusterName`

**Description**: Short name for the cluster. Combined with `baseDomain` to form the full cluster domain.

**Type**: String

**Example**:
```yaml
clusterName: mgmt
```

**Resulting domain**: `mgmt.enclave-test.nodns.in`

### Virtual IPs (VIPs)

#### `apiVIP`

**Description**: Virtual IP address for the Kubernetes API server. This IP must be:
- In the same subnet as `machineNetwork`
- Not in use by any other system
- Routable from your network

**Type**: String (IP address)

**Example**:
```yaml
apiVIP: 192.168.2.201
```

**Notes**:
- Used for `api.{{ clusterName }}.{{ baseDomain }}`
- Must be available before cluster installation


#### `ingressVIP`

**Description**: Virtual IP address for the Ingress router. This IP must be:
- In the same subnet as `machineNetwork`
- Not in use by any other system
- Routable from your network

**Type**: String (IP address)

**Example**:
```yaml
ingressVIP: 192.168.2.202
```

**Notes**:
- Used for `*.apps.{{ clusterName }}.{{ baseDomain }}`
- Must be available before cluster installation

#### `machineNetwork`

**Description**: Network CIDR for the cluster nodes. All nodes must have IP addresses in this range.

**Type**: String (CIDR notation)

**Example**:
```yaml
machineNetwork: 192.168.2.0/24
```

**Notes**:
- Must match your actual network configuration
- VIPs must be in this range
- All `agent_hosts` IPs must be in this range

### Network Infrastructure

#### `defaultDNS`

**Description**: DNS server IP address for cluster nodes.

**Type**: String (IP address)

**Example**:
```yaml
defaultDNS: 192.168.2.10
```

**Notes**:
- Used for node DNS resolution
- Should be a reliable DNS server
- Can be the same as `defaultGateway` if your gateway provides DNS

#### `defaultGateway`

**Description**: Default gateway IP address for cluster nodes.

**Type**: String (IP address)

**Example**:
```yaml
defaultGateway: 192.168.2.10
```

**Notes**:
- Must be in the same subnet as `machineNetwork`
- Used for routing external traffic

#### `defaultPrefix`

**Description**: Network prefix length (subnet mask) for the cluster network.

**Type**: Integer

**Example**:
```yaml
defaultPrefix: 24
```

**Common values**:
- `24` = `/24` = `255.255.255.0` (254 hosts)
- `23` = `/23` = `255.255.254.0` (510 hosts)
- `22` = `/22` = `255.255.252.0` (1022 hosts)

#### `rendezvousIP`

**Description**: IP address of the first control plane node. This node coordinates the installation process.

**Type**: String (IP address)

**Example**:
```yaml
rendezvousIP: 192.168.2.24
```

**Notes**:
- Must match the `ipAddress` of the first entry in `agent_hosts`
- This node must be accessible from other nodes during installation

#### `lzBmcIP`

**Description**: IP address of the HTTP server that serves the installation ISO. This should be the IP of the deployment host on the provisioning network.

**Type**: String (IP address)

**Example**:
```yaml
lzBmcIP: 100.64.1.10
```

**Notes**:
- Must be accessible from the BMC network (for Redfish virtual media)
- ISO will be served at `http://{{ lzBmcIP }}/assisted/agent.x86_64.iso`
- Ensure HTTP server (Apache/Nginx) is running on this host

#### `defaultNtpServers`

**Description**: Optional list of additional NTP server addresses for cluster nodes. When not set, the cluster uses its default NTP sources.

**Type**: List of strings (optional)

**Example**:
```yaml
defaultNtpServers:
  - 192.168.2.10
  - 192.168.2.11
```

**Notes**:
- Only needed when cluster nodes cannot reach the default public NTP pool
- Useful in air-gapped or firewalled environments

## Hardware Configuration

### Agent Hosts Configuration

#### `agent_hosts`

**Description**: List of control plane nodes with their network and hardware configuration.

**Type**: List of dictionaries

**Example**:
```yaml
agent_hosts:
  - name: mgmt-ctl01
    macAddress: 0c:c4:7a:62:fe:ec
    ipAddress: 192.168.2.24
    redfish: 100.64.1.24
    rootDisk: "/dev/disk/by-path/pci-0000:0011.4-ata-1.0"
    redfishUser: admin
    redfishPassword: YourSecurePassword
  - name: mgmt-ctl02
    macAddress: 0c:c4:7a:39:f5:18
    ipAddress: 192.168.2.25
    redfish: 100.64.1.25
    rootDisk: "/dev/disk/by-path/pci-0000:0011.4-ata-1.0"
    redfishUser: admin
    redfishPassword: YourSecurePassword
  - name: mgmt-ctl03
    macAddress: 0c:c4:7a:39:ec:0c
    ipAddress: 192.168.2.26
    redfish: 100.64.1.26
    rootDisk: "/dev/disk/by-path/pci-0000:0011.4-ata-1.0"
    # optional fields
    redfishUser: admin
    redfishPassword: YourSecurePassword
```

**Required fields for each host**:

| Field | Description | Example |
|-------|-------------|---------|
| `name` | Hostname for the node | `mgmt-ctl01` |
| `macAddress` | MAC address of the primary network interface | `0c:c4:7a:62:fe:ec` |
| `ipAddress` | Static IP address for the node (must be in `machineNetwork`) | `192.168.2.24` |
| `redfish` | BMC IP address for Redfish API access | `100.64.1.24` |
| `rootDisk` | Physical disk path for root filesystem (use `/dev/disk/by-path/` paths) | `/dev/disk/by-path/pci-0000:0011.4-ata-1.0` |
| `redfishUser` | Override Username for Redfish API authentication on BMCs | `admin-override` |
| `redfishPassword` | Override Password for Redfish API authentication on BMCs | `YourSecurePassword-override` |

**Optional fields for each host**:

| Field | Description | Example |
|-------|-------------|---------|
| `mapInterfaces` | List of interface-to-MAC mappings for advanced network configuration | See example below |
| `networkConfig` | Full nmstate network configuration in YAML format | See example below |

### Advanced Network Configuration

For complex network setups (bonding, VLANs, multiple interfaces, etc.), you can use `mapInterfaces` and `networkConfig` instead of the simple `macAddress`/`ipAddress` approach.

When `networkConfig` is defined for a host, the template uses the custom configuration instead of generating the default single-interface setup.

#### `mapInterfaces`

**Description**: List of interface name to MAC address mappings. This tells the installer which physical interface corresponds to which MAC address.

**Type**: List of dictionaries

**Example**:
```yaml
mapInterfaces:
  - name: eno1
    macAddress: "0c:c4:7a:62:fe:ec"
  - name: eno2
    macAddress: "0c:c4:7a:62:fe:ed"
```

#### `networkConfig`

**Description**: Full nmstate network configuration in YAML format. This allows you to define complex network setups including bonds, VLANs, bridges, and multiple interfaces.

**Type**: Dictionary (nmstate format)

**Example with bonding**:
```yaml
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
```

#### Complete Example with Advanced Network Configuration

```yaml
agent_hosts:
  # Host with simple network configuration (default)
  - name: mgmt-ctl01
    macAddress: 0c:c4:7a:62:fe:ec
    ipAddress: 192.168.2.24
    redfish: 100.64.1.24
    rootDisk: "/dev/disk/by-path/pci-0000:0011.4-ata-1.0"

  # Host with advanced network configuration (bonding)
  - name: mgmt-ctl02
    redfish: 100.64.1.25
    rootDisk: "/dev/disk/by-path/pci-0000:0011.4-ata-1.0"
    mapInterfaces:
      - name: eno1
        macAddress: "0c:c4:7a:39:f5:18"
      - name: eno2
        macAddress: "0c:c4:7a:39:f5:19"
    networkConfig:
      interfaces:
        - name: bond0
          type: bond
          state: up
          ipv4:
            enabled: true
            address:
              - ip: 192.168.2.25
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
          mac-address: "0c:c4:7a:39:f5:18"
        - name: eno2
          type: ethernet
          state: up
          mac-address: "0c:c4:7a:39:f5:19"
      routes:
        config:
          - next-hop-address: 192.168.2.10
            next-hop-interface: bond0
            destination: 0.0.0.0/0
      dns-resolver:
        config:
          server:
            - 192.168.2.10

  # Host with VLAN configuration
  - name: mgmt-ctl03
    redfish: 100.64.1.26
    rootDisk: "/dev/disk/by-path/pci-0000:0011.4-ata-1.0"
    mapInterfaces:
      - name: eno1
        macAddress: "0c:c4:7a:39:ec:0c"
    networkConfig:
      interfaces:
        - name: eno1.100
          type: vlan
          state: up
          vlan:
            base-iface: eno1
            id: 100
          ipv4:
            enabled: true
            address:
              - ip: 192.168.2.26
                prefix-length: 24
        - name: eno1
          type: ethernet
          state: up
          mac-address: "0c:c4:7a:39:ec:0c"
      routes:
        config:
          - next-hop-address: 192.168.2.10
            next-hop-interface: eno1.100
            destination: 0.0.0.0/0
      dns-resolver:
        config:
          server:
            - 192.168.2.10
```

**Notes on Advanced Network Configuration**:
- When using `networkConfig`, the `macAddress` and `ipAddress` fields are not required (they're part of the networkConfig)
- The `mapInterfaces` field maps interface names to MAC addresses for the agent installer
- The `networkConfig` follows the [nmstate](https://nmstate.io/) format
- You can mix hosts with simple and advanced configurations in the same `agent_hosts` list
- Common use cases: bonding, VLANs, bridges, multiple NICs, static routes

**Notes**:
- Number of entries must match `controlPlane_replicas`
- First entry's `ipAddress` should match `rendezvousIP`
- MAC addresses must be unique
- IP addresses must be unique and in `machineNetwork` range
- `rootDisk` must use a physical disk path from `/dev/disk/by-path/` (not `/dev/sda` or similar). Device names can change between reboots, but physical paths remain stable.
- `rootDisk` should be the primary disk (not a partition)

**Finding disk paths**:
```bash
# If you have the server booted, list all physical disk paths
ls -l /dev/disk/by-path/

# Or use lsblk to see disk paths
lsblk -o NAME,PATH

# Find the path for a specific device (e.g., /dev/sda)
ls -l /dev/disk/by-path/ | grep sda
```

**Finding MAC addresses**:
```bash
# On the server
ip link show

# Or via Redfish
curl -k -u user:pass https://<redfish-ip>/redfish/v1/Systems/1/EthernetInterfaces
```

## Discovery Hosts Configuration

> **⚠️ Important: Use Red Hat ACM for Production Host Management**
>
> **Red Hat Advanced Cluster Management (ACM) is the recommended approach for managing bare metal host discovery and lifecycle operations.** The discovery hosts configuration in this file is provided as a convenience for initial one-time setup only.
>
> For ongoing operations such as adding nodes, removing nodes, changing configurations, or scaling the cluster, use Red Hat ACM instead. See the [Managing bare metal hosts documentation](https://docs.redhat.com/en/documentation/red_hat_advanced_cluster_management_for_kubernetes/2.12/html/clusters/cluster_mce_overview#managing-bare-metal-hosts-console) for details.

The discovery hosts configuration is defined in `config/cloud_infra.yaml` for discovering new nodes after the initial cluster deployment. This configuration uses the same network settings (defaultDNS, defaultGateway, defaultPrefix, lzBmcIP) as the main cluster deployment.

### Discovery Hosts Settings

#### `discovery_hosts`

**Description**: List of nodes to discover and add to the cluster. These nodes will be discovered via the Assisted Installer service.

**Type**: List of dictionaries

**Example**:
```yaml
discovery_hosts:
  - name: node01
    macAddress: 0c:c4:7a:d3:bc:30
    ipAddress: 192.168.2.21
    redfish: 100.64.1.21
    rootDisk: "/dev/disk/by-path/pci-0000:0011.4-ata-1.0"
    redfishUser: admin
    redfishPassword: YourSecurePassword
  - name: node02
    macAddress: 0c:c4:7a:65:d0:84
    ipAddress: 192.168.2.22
    redfish: 100.64.1.22
    rootDisk: "/dev/disk/by-path/pci-0000:0011.4-ata-1.0"
    # Optional: override default Redfish credentials
    redfishUser: admin
    redfishPassword: YourSecurePassword
```

**Required fields for each host**:

| Field | Description | Example |
|-------|-------------|---------|
| `name` | Hostname for the node | `node01` |
| `macAddress` | MAC address of the primary network interface | `0c:c4:7a:d3:bc:30` |
| `ipAddress` | Static IP address for the node | `192.168.2.21` |
| `redfish` | BMC IP address for Redfish API access | `100.64.1.21` |
| `rootDisk` | Physical disk path for root filesystem (use `/dev/disk/by-path/` paths) | `/dev/disk/by-path/pci-0000:0011.4-ata-1.0` |
| `redfishUser` | Username for Redfish API authentication | `admin` |
| `redfishPassword` | Password for Redfish API authentication | `YourSecurePassword` |

**Notes**:
- MAC addresses must be unique
- IP addresses must be unique and in the same network as the cluster
- `rootDisk` must use a physical disk path from `/dev/disk/by-path/` (not `/dev/sda` or similar). Device names can change between reboots, but physical paths remain stable.
- `rootDisk` should be the primary disk (not a partition)
- Nodes are automatically skipped if already discovered (based on BMC address)
- If a node is pending restart after cluster destroy, it will be discovered again
- The discovery process creates NMStateConfig and BareMetalHost resources to trigger discovery

**Network Configuration**:

The discovery hosts use the same network configuration variables as the main cluster:
- `defaultDNS`: DNS server for the nodes
- `defaultGateway`: Default gateway for the nodes
- `defaultPrefix`: Network prefix length (subnet mask)

These are used to configure the network interface on each discovered node.

**Discovery Process**:

1. Ensures the namespace, infraenv, and pull secret have been created in the management cluster
2. Checks for already discovered agents to avoid duplicates
3. Creates NMStateConfig, BareMetalHost, and BMC credential secret for new hosts
4. Waits for BareMetalHost to report "provisioned" state
5. Waits for agents to register

## Registry Configuration

### Quay Registry Settings

#### `quayUser`

**Description**: Administrator username for the Quay registry.

**Type**: String

**Example**:
```yaml
quayUser: quayadmin
```

**Notes**:
- Created during mirror-registry installation
- Used for registry authentication
- Should be different from system users

#### `quayPassword`

**Description**: Administrator password for the Quay registry.

**Type**: String

**Example**:
```yaml
quayPassword: YourSecurePassword
```

**Note**: This is a placeholder - use a strong, unique password in your actual configuration.

**Security Note**: Consider using Ansible Vault to encrypt this value.

#### `quayHostname`

**Description**: Hostname for the Quay registry. Auto-derived from `baseDomain` in `defaults/mirror_registry.yaml` — only set this in `config/global.yaml` if a non-standard hostname is needed.

**Type**: String

**Default** (`defaults/mirror_registry.yaml`):
```yaml
quayHostname: "mirror.{{ baseDomain }}"
```

**Notes**:
- Must resolve to the deployment host
- Used in pull secrets
- Should match DNS configuration

#### `quayCAPath`

**Description**: Path to the Quay registry CA certificate file. Auto-derived from `workingDir` in `defaults/mirror_registry.yaml` — only set this in `config/global.yaml` if the CA file is stored at a non-standard path.

**Type**: String (file path)

**Default** (`defaults/mirror_registry.yaml`):
```yaml
quayCAPath: "{{ workingDir }}/data/quay-rootCA/rootCA.pem"
```

**Notes**:
- Automatically generated by mirror-registry
- Used to trust self-signed certificates
- Included in `install-config.yaml` as `additionalTrustBundle`

#### `quayFeatureProxyStorage`

**Description**: Enables or disables the Quay proxy storage feature. Auto-set in `defaults/quay_operator.yaml` — only override in `config/global.yaml` when a non-default value is needed.

**Type**: Boolean

**Default** (`defaults/quay_operator.yaml`):
```yaml
quayFeatureProxyStorage: true
```

#### `quayFeatureQuotaManagement`

**Description**: Enables or disables Quay's quota management feature. Auto-set in `defaults/quay_operator.yaml` — only override in `config/global.yaml` when a non-default value is needed.

**Type**: Boolean

**Default** (`defaults/quay_operator.yaml`):
```yaml
quayFeatureQuotaManagement: true
```

#### `quayMaximumLayerSize`

**Description**: Maximum layer size for the Quay registry. Auto-set in `defaults/quay_operator.yaml` — only override in `config/global.yaml` when a non-default value is needed.

**Type**: String

**Default** (`defaults/quay_operator.yaml`):
```yaml
quayMaximumLayerSize: "100G"
```

### Quay Backend Storage

#### `quayBackend`

**Description**: Storage backend type for Quay registry.

**Type**: String

**Example**:
```yaml
quayBackend: RadosGWStorage
```

**Valid values**:
- `RadosGWStorage`: Ceph Rados Gateway (S3-compatible)
- `LocalStorage`: Local filesystem (default, not recommended for production)

#### `quayBackendRGWConfiguration`

**Description**: Configuration for the RadosGW (Ceph S3-compatible) storage backend. Required when `quayBackend` is `RadosGWStorage`.

**Type**: Dictionary

**Example**:
```yaml
quayBackendRGWConfiguration:
  access_key: YOUR_S3_ACCESS_KEY_HERE
  secret_key: YOUR_S3_SECRET_KEY_HERE
  bucket_name: quay-bucket-name
  hostname: ocs-storagecluster-cephobjectstore-openshift-storage.apps.store.enclave-test.nodns.in
  # Uncomment only to override
  # minimum_chunk_size_mb: YOUR_MIN_CHUNK_SIZE_MB  # Default: 100
  # maximum_chunk_size_mb: YOUR_MAX_CHUNK_SIZE_MB  # Default: 500
```

**Note**: Replace `YOUR_S3_ACCESS_KEY_HERE` and `YOUR_S3_SECRET_KEY_HERE` with your actual S3/RadosGW credentials.

**Fields**:

| Field | Description | Default | Example |
|-------|-------------|---------|---------|
| `access_key` | S3 access key | *(required)* | `YOUR_S3_ACCESS_KEY` |
| `secret_key` | S3 secret key | *(required)* | `YOUR_S3_SECRET_KEY` |
| `bucket_name` | S3 bucket name | *(required)* | `quay-bucket-name` |
| `hostname` | RadosGW endpoint hostname | *(required)* | `ocs-storagecluster-cephobjectstore-openshift-storage.apps.store.enclave-test.nodns.in` |
| `is_secure` | Use HTTPS | `true` | `false` |
| `port` | Port number | `443` | `8080` |
| `storage_path` | Path prefix in bucket | `/datastorage/registry` | `/custom/path` |
| `minimum_chunk_size_mb` | Minimum multipart upload chunk size (MB) | `100` | `50` |
| `maximum_chunk_size_mb` | Maximum multipart upload chunk size (MB) | `500` | `800` |

**Notes**:
- Access and secret keys are created when setting up Ceph/RadosGW
- Bucket must exist before Quay configuration
- Hostname should be the OCP route for RadosGW service
- `is_secure`, `port`, and `storage_path` have defaults in `defaults/quay_operator.yaml` and only need to be set here when overriding those defaults
- `minimum_chunk_size_mb` and `maximum_chunk_size_mb` default to `100` and `500` respectively; override in `quayBackendRGWConfiguration` if needed
- `server_side_assembly` is always enabled as it is included in the defaults alongside `maximum_chunk_size_mb`

### Pull Secrets

#### `pullSecret`

**Description**: JSON-formatted pull secret containing authentication for container registries.

**Type**: String (JSON)

**Example**:
```yaml
pullSecret: |
  {
    "auths": {
      "cloud.openshift.com": {
        "auth": "base64-encoded-auth",
        "email": "user@example.com"
      },
      "quay.io": {
        "auth": "base64-encoded-auth",
        "email": "user@example.com"
      },
      "registry.redhat.io": {
        "auth": "base64-encoded-auth",
        "email": "user@example.com"
      }
    }
  }
```

**Notes**:
- Get from https://console.redhat.com/openshift/install/pull-secret
- Must include credentials for:
  - `cloud.openshift.com`
  - `quay.io`
  - `registry.redhat.io`
  - `registry.connect.redhat.com`
- Internal registry credentials are automatically merged

#### `pullSecretPath`

**Description**: Path to pull secret JSON file. Defaults to `{{ workingDir }}/config/pull-secret.json`. Override in `config/global.yaml` if your pull secret is stored elsewhere.

**Type**: String (file path)

**Default**: `{{ workingDir }}/config/pull-secret.json`

**Example**:
```yaml
pullSecretPath: "{{ workingDir }}/config/pull-secret.json"
```

## Storage Configuration

Storage is configured via the plugin system. The `storage_plugin` variable selects which storage plugin to deploy, and each plugin provides its own operator definitions, defaults, and registry mirrors in `plugins/<name>/plugin.yaml`.

#### `storage_plugin`

**Description**: Selects which storage plugin to deploy for block storage (used by Quay and the Assisted Installer).

**Type**: String

**Default**: `lvms`

**Valid values**:
- `lvms`: Local Volume Manager Storage (plugin at `plugins/lvms/`)
- `odf`: OpenShift Data Foundation in external mode (plugin at `plugins/odf/`)

**Example**:
```yaml
storage_plugin: lvms
```

**Notes**:
- The selected plugin is automatically added to `enabled_plugins`

#### `enabled_plugins`

**Description**: List of plugins to deploy during the pipeline. Defaults to only the selected storage plugin.

**Type**: List of strings (optional)

**Default**: `["{{ storage_plugin }}"]`

**Example**:
```yaml
enabled_plugins:
  - lvms
  - openshift-ai
  - nvidia-gpu
```

**Notes**:
- Override this to deploy additional plugins alongside the storage plugin
- Each entry must match a directory name under `plugins/`
- Available plugins: `lvms`, `odf`, `openshift-ai`, `nvidia-gpu`, `example`

#### LVMS Configuration

#### `lvmsConfig`

**Description**: Optional device selector for the LVMS plugin. When omitted, LVMS auto-detects and uses all available disks on each node (LVMS default behaviour). Set this variable to restrict which physical disks LVMS manages.

**Type**: Dictionary (optional)

**Example**:
```yaml
lvmsConfig:
  deviceSelector:
    optionalPaths:
      - /dev/disk/by-path/YOUR_DISK_PATH_1
```

**Notes**:
- Use paths from `/dev/disk/by-path/` for stable device identification across reboots
- `forceWipeDevicesAndDestroyAllData` defaults to `true` (set in the LVMS plugin defaults)
- Only needed when you want to restrict which disks LVMS uses; omit to let LVMS manage all disks automatically

#### ODF Configuration

#### `odfExternalConfig`

**Description**: External Ceph cluster configuration required when `storage_plugin: odf`. Contains the JSON output from the `ceph-external-cluster-details-exporter.py` script.

**Type**: String (JSON, required when `storage_plugin: odf`)

**Example**:
```yaml
odfExternalConfig:
  '[{"name": "external-cluster-user-command",
     "kind": "ConfigMap", "data": ..}]'
```

**Notes**:
- Only required when `storage_plugin` is set to `odf`
- If `storage_plugin: odf` and this variable is missing, the ODF plugin fails at load-time validation with a clear error message

#### `odfDefaults`

**Description**: ODF plugin defaults. The plugin sets `defaultStorageClass: true` automatically. Override in `config/global.yaml` only if you need to change the default.

**Type**: Dictionary (optional)

**Default** (from `plugins/odf/plugin.yaml`):
```yaml
odfDefaults:
  defaultStorageClass: true
```

**Example** (to override):
```yaml
odfDefaults:
  defaultStorageClass: false
```

**Notes**:
- Controls whether the ODF block pool is set as the default StorageClass on the cluster

### Datacenter Cache Configuration

#### `dc_cache_address`

**Description**: Address (host:port) for the Datacenter Cache registry.

**Type**: String

**Example**:
```yaml
dc_cache_address: "registry.cdn.nodns.in:443"
```

#### `dc_cache_user`

**Description**: Administrator username for the Datacenter Cache registry.

**Type**: String

**Example**:
```yaml
dc_cache_user: registry-admin
```

#### `dc_cache_password`

**Description**: Administrator password for the Datacenter Cache registry.

**Type**: String

**Example**:
```yaml
dc_cache_password: YourSecurePassword
```

**Note**: This is a placeholder - use a strong, unique password in your actual configuration.

**Security Note**: Consider using Ansible Vault to encrypt this value.

### Operator Catalog Configuration

#### `certified_operator_catalog`

**Description**: Address of the _certified-operator_ index.

**Type**: String

**Example**:
```yaml
certified_operator_catalog: "registry.redhat.io/redhat/certified-operator-index"
```

#### `certified_operator_catalog_version`

**Description**: Version of the _certified-operator_ index.

**Type**: String

**Example**:
```yaml
certified_operator_catalog_version: "v4.20"
```

#### `rh_operator_catalog`

**Description**: Address of the _redhat-operator_ index.

**Type**: String

**Example**:
```yaml
rh_operator_catalog: "registry.redhat.io/redhat/redhat-operator-index"

```

#### `rh_operator_catalog_version`

**Description**: Version of the _redhat-operator_ index.

**Type**: String

**Example**:
```yaml
rh_operator_catalog_version: "v4.20"
```

## SSL Certificate Configuration

SSL certificates are stored in `config/certificates.yaml`, separated from the main configuration in `config/global.yaml`.

### API Server Certificate

#### `sslAPICertificateKey`

**Description**: Private key for the API server TLS certificate.

**Type**: String (PEM format)

**Example**:
```yaml
sslAPICertificateKey: |
  -----BEGIN EC PRIVATE KEY-----
  ... (your private key content here) ...
  -----END EC PRIVATE KEY-----
```

**Note**: This is a placeholder - replace with your actual private key. Never commit real private keys to version control.

**Notes**:
- Must match the certificate in `sslAPICertificateFullChain`
- Can be EC (Elliptic Curve) or RSA key
- Keep secure - consider using Ansible Vault

#### `sslAPICertificateFullChain`

**Description**: Full certificate chain for the API server, including intermediate certificates.

**Type**: String (PEM format)

**Example**:
```yaml
sslAPICertificateFullChain: |
  -----BEGIN CERTIFICATE-----
  ... (server certificate)
  -----END CERTIFICATE-----
  -----BEGIN CERTIFICATE-----
  ... (intermediate certificate)
  -----END CERTIFICATE-----
```

**Certificate requirements**:
- Subject Alternative Name (SAN) must include: `api.{{ clusterName }}.{{ baseDomain }}`
- Must be valid (not expired)
- Should include full chain (server + intermediate + root)

### Ingress Certificate

#### `sslIngressCertificateKey`

**Description**: Private key for the Ingress router TLS certificate.

**Type**: String (PEM format)

**Example**:
```yaml
sslIngressCertificateKey: |
  -----BEGIN EC PRIVATE KEY-----
  ... (your private key content here) ...
  -----END EC PRIVATE KEY-----
```

**Note**: This is a placeholder - replace with your actual private key. Never commit real private keys to version control.

#### `sslIngressCertificateFullChain`

**Description**: Full certificate chain for the Ingress router.

**Type**: String (PEM format)

**Example**:
```yaml
sslIngressCertificateFullChain: |
  -----BEGIN CERTIFICATE-----
  ... (server certificate)
  -----END CERTIFICATE-----
  -----BEGIN CERTIFICATE-----
  ... (intermediate certificate)
  -----END CERTIFICATE-----
```

**Certificate requirements**:
- Subject Alternative Name (SAN) must include:
  - `*.apps.{{ clusterName }}.{{ baseDomain }}`
  - `apps.{{ clusterName }}.{{ baseDomain }}`
- Wildcard certificate recommended
- Must be valid (not expired)

**Obtaining certificates**:
- Use Let's Encrypt (certbot)
- Use internal CA
- Use commercial certificate authority

#### `sslCACertificate`

**Description**: Root CA certificate.

**Type**: String (PEM format)

**Example**:
```yaml
sslCACertificate: |
  -----BEGIN CERTIFICATE-----
  ... (server certificate)
  -----END CERTIFICATE-----
```

## Operator Configuration

Operator configuration is stored in:
- `defaults/operators.yaml` - General cluster operators
- `plugins/<name>/plugin.yaml` - Storage and other plugin operators (selected via `storage_plugin` / `enabled_plugins`)

### Operator List Structure

#### `operators`

**Description**: List of operators to install and configure.

**Location**: `defaults/operators.yaml`

**Type**: List of dictionaries

**Common structure**:
```yaml
operators:
  - name: operator-name
    version: 1.0.0
    channel: channel-name
    init_version: 1.0.0
    namespace: target-namespace
    source: catalog-source-name
```

### Operator Fields

| Field | Description | Required |
|-------|-------------|----------|
| `name` | Operator package name as it appears in the catalog | Yes |
| `version` | Operator version | Yes |
| `channel` | Update channel for the operator | Yes |
| `init_version` | Initial operator version | Yes |
| `namespace` | Target namespace for installation. OperatorGroup is auto-created if not `openshift-operators` | No |
| `source` | Catalog source name (from oc-mirror). Must match a source created by oc-mirror | No |
| `global` | Set to `true` to configure operator to watch the entire cluster | No |
| `csvNames` | ClusterServiceVersion names for mirroring | No |
| `csvMirror` | Set to `true` to mirror packages listed in `csvNames` | No |

Core operators are defined in `defaults/operators.yaml`. Plugin operators are defined in each plugin's `plugin.yaml` under the `operators` field. See `schemas/plugin.yaml` for the full operator schema.

## Content Configuration

Content configuration is stored in the `defaults/` directory:
- `defaults/control_binaries.yaml` - Binary downloads (oc, helm, mirror-registry, oc-mirror)
- `defaults/content_images.yaml` - RHCOS ISO images

### Control Binaries

#### `control_binaries`

**Description**: URLs and checksums for required binaries.

**Location**: `defaults/control_binaries.yaml`

**Type**: Dictionary

**Example**:
```yaml
control_binaries:
  openshift_client:
    url: "https://mirror.openshift.com/pub/openshift-v4/clients/ocp/4.19.17/openshift-client-linux.tar.gz"
    checksum: "sha256:3226d9e1bc33f95eac456750c6f56c0b3b313e89e46e679a93cf24f434f153ed"
  helm:
    url: "https://developers.redhat.com/content-gateway/file/pub/openshift-v4/clients/helm/3.17.1/helm-linux-amd64"
    checksum: "sha256:ef6c04f6a748d0f1d624a94a56dc0db83f8d70a65e3dbb19e94107126efcc5fd"
  mirror_registry:
    url: "https://developers.redhat.com/content-gateway/file/pub/openshift-v4/clients/mirror-registry/1.3.11/mirror-registry.tar.gz"
    checksum: "sha256:b2fbf8b13d794cdebb8baee48afbfa78d13c4d76a1c60fc99d52c3c9abd31cf1"
  oc_mirror:
    url: "https://mirror.openshift.com/pub/openshift-v4/x86_64/clients/ocp/4.19.17/oc-mirror.tar.gz"
    checksum: "sha256:83a9e2a99a0600e50d0ee49a10758609a1d6e4acb0d00745098745e71516a2e9"
```

**Available binaries**:
- `openshift_client`: OpenShift CLI (`oc`)
- `helm`: Helm CLI
- `mirror_registry`: Mirror registry installer
- `oc_mirror`: oc-mirror tool for image mirroring

**Notes**:
- Checksums are verified after download
- URLs should point to official Red Hat sources
- Update version numbers as needed

### Content Images

#### `content_images`

**Description**: URLs and checksums for RHCOS images.

**Location**: `defaults/content_images.yaml`

**Type**: Dictionary

**Example**:
```yaml
content_images:
  isos:
    - url: "https://mirror.openshift.com/pub/openshift-v4/dependencies/rhcos/4.19/4.19.10/rhcos-4.19.10-x86_64-live-iso.x86_64.iso"
      checksum: "sha256:7a47d0c7a9bf5edb143d52809e793af2d74731567b95d91c6225171a1c49b5ab"
```

**Fields**:
- `isos`: List of ISO images (used for cluster deployment)

**Notes**:
- Version should match OCP version
- Checksums are verified after download
- Multiple entries allowed (for different architectures)

## Complete Example

Configuration is split across two files. Here are complete examples for each.

### `config/global.yaml`

```yaml
# Base Configuration
workingDir: "/home/enclave"

# Cluster Configuration
baseDomain: enclave-test.nodns.in
clusterName: mgmt

# Network Configuration
apiVIP: 192.168.2.201
ingressVIP: 192.168.2.202
machineNetwork: 192.168.2.0/24
defaultDNS: 192.168.2.10
defaultGateway: 192.168.2.10
defaultPrefix: 24
rendezvousIP: 192.168.2.24
lzBmcIP: 100.64.1.10

# OpenShift Deployment Configuration (optional — uncomment only to override defaults)
# disconnected: false  # Default: true (set to false for connected deployments)
# diskEncryption: true  # Default: false (set to true to enable TPM v2 encryption)
# ocMirrorLogLevel: debug  # Default: info
# defaultNtpServers:  # No additional servers by default
#  - YOUR_NTP_SERVER_1
#  - YOUR_NTP_SERVER_2

# Pull Secret and SSH Public Key
pullSecret: '{"auths":{"cloud.openshift.com":{...},"quay.io":{...}}}'
# pullSecretPath: "{{ workingDir }}/config/pull-secret.json"  # Default
sshPubPath: "{{ workingDir }}/.ssh/id_rsa.pub"

# Storage Plugin
storage_plugin: lvms

# To use ODF instead:
# storage_plugin: odf
# odfExternalConfig:
#   '[{"name": "external-cluster-user-command",
#      "kind": "ConfigMap", "data": ..}]'

# To deploy additional plugins (addon plugins):
# enabled_plugins:
#   - lvms
#   - openshift-ai
#   - nvidia-gpu

# Agent Hosts (control plane nodes)
agent_hosts:
  - name: mgmt-ctl01
    macAddress: 0c:c4:7a:62:fe:ec
    ipAddress: 192.168.2.24
    redfish: 100.64.1.24
    rootDisk: "/dev/disk/by-path/pci-0000:0011.4-ata-1.0"
    redfishUser: admin
    redfishPassword: YourSecurePassword
  - name: mgmt-ctl02
    macAddress: 0c:c4:7a:39:f5:18
    ipAddress: 192.168.2.25
    redfish: 100.64.1.25
    rootDisk: "/dev/disk/by-path/pci-0000:0011.4-ata-1.0"
    redfishUser: admin
    redfishPassword: YourSecurePassword
  - name: mgmt-ctl03
    macAddress: 0c:c4:7a:39:ec:0c
    ipAddress: 192.168.2.26
    redfish: 100.64.1.26
    rootDisk: "/dev/disk/by-path/pci-0000:0011.4-ata-1.0"
    redfishUser: admin
    redfishPassword: YourSecurePassword

# Registry Configuration
quayUser: quayadmin
quayPassword: YourSecurePassword
quayBackend: RadosGWStorage
quayBackendRGWConfiguration:
  access_key: YOUR_S3_ACCESS_KEY_HERE
  secret_key: YOUR_S3_SECRET_KEY_HERE
  bucket_name: quay-bucket-name
  hostname: ocs-storagecluster-cephobjectstore-openshift-storage.apps.store.enclave-test.nodns.in

```

### `config/certificates.yaml`

```yaml
# API Certificate (for api.<clusterName>.<baseDomain>)
sslAPICertificateKey: |
  -----BEGIN PRIVATE KEY-----
  ...
  -----END PRIVATE KEY-----

sslAPICertificateFullChain: |
  -----BEGIN CERTIFICATE-----
  ... (server certificate)
  -----END CERTIFICATE-----
  -----BEGIN CERTIFICATE-----
  ... (intermediate CA, if applicable)
  -----END CERTIFICATE-----

# Ingress Certificate (for *.apps.<clusterName>.<baseDomain>)
sslIngressCertificateKey: |
  -----BEGIN PRIVATE KEY-----
  ...
  -----END PRIVATE KEY-----

sslIngressCertificateFullChain: |
  -----BEGIN CERTIFICATE-----
  ... (server certificate)
  -----END CERTIFICATE-----
  -----BEGIN CERTIFICATE-----
  ... (intermediate CA, if applicable)
  -----END CERTIFICATE-----

# Root CA Certificate
sslCACertificate: |
  -----BEGIN CERTIFICATE-----
  ...
  -----END CERTIFICATE-----
```

## Security Best Practices

1. **Restrict file permissions**:
   ```bash
   chmod 600 config/global.yaml config/certificates.yaml config/cloud_infra.yaml
   ```

2. **Use strong passwords** for:
   - Redfish credentials
   - Quay admin password
   - Registry access keys

3. **Rotate certificates** before expiration

4. **Keep pull secrets secure** - never commit to version control

## Validation

Before running the deployment, validate your configuration:

1. **Network connectivity**:
   ```bash
   ping $apiVIP
   ping $ingressVIP
   for host in ${agent_hosts[@]}; do ping $host.redfish; done
   ```

2. **DNS resolution**:
   ```bash
   nslookup api.$clusterName.$baseDomain
   nslookup $quayHostname
   ```

3. **Redfish API access**:
   ```bash
   curl -k -u $redfishUser:$redfishPassword \
     https://$redfish_ip/redfish/v1/Systems/1
   ```

4. **File paths**:
   ```bash
   test -d $workingDir || mkdir -p $workingDir
   test -f pull-secret.json || echo "Pull secret missing"
   ```

