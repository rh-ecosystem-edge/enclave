# ODF External Ceph Architecture for CI

This document describes the containerized Ceph cluster used to provide external storage for ODF (OpenShift Data Foundation) E2E testing in CI.

## Overview

ODF in external mode connects to a pre-existing Ceph cluster rather than deploying its own. For CI, we run a single-node Ceph cluster on the Landing Zone VM using cephadm. All Ceph daemons run as podman containers on the LZ, which shares the same libvirt network as the OpenShift nodes.

```text
CI Runner Machine (runs: [self-hosted, enclave-large])
├── libvirt VMs
│   ├── Landing Zone VM (192.168.X.2)
│   │   ├── Ceph cluster (cephadm, podman containers)
│   │   │   ├── MON  – port 3300/6789
│   │   │   ├── MGR  – prometheus metrics port 9283
│   │   │   ├── OSDs – 3x loopback files (/var/lib/ceph-loops/osd-{0,1,2}.img)
│   │   │   └── RGW  – S3-compatible, port 7480
│   │   ├── Enclave Lab (Ansible playbooks)
│   │   └── ~/ceph-config/  (generated config files)
│   │
│   └── Master nodes (192.168.X.10+)
│       └── reach Ceph directly via cluster network (192.168.X.2)
│
└── sushy-tools container (existing)
```

**Key advantage**: All VMs share the same libvirt cluster network. No firewall rules needed. OpenShift pods reach Ceph at `192.168.X.2:9283/7480` directly -- no SDN routing issues.

## Storage Architecture

### Loopback OSDs with LVM

Cephadm filters out raw loop devices, so each OSD uses an LVM stack:

```text
Sparse file (20GB)          Loop device          LVM
osd-0.img  ──────────>  /dev/loop0  ──────>  ceph-vg0/ceph-lv0  ──> OSD.0
osd-1.img  ──────────>  /dev/loop1  ──────>  ceph-vg1/ceph-lv1  ──> OSD.1
osd-2.img  ──────────>  /dev/loop2  ──────>  ceph-vg2/ceph-lv2  ──> OSD.2
```

- Files are **sparse** (`truncate -s 20G`) -- only consume disk as data is written
- LZ is ephemeral (destroyed after each CI run), so no systemd loopback service is needed
- Single-node replication: `osd_pool_default_size=1` (no redundancy, CI-only)

### Ceph Services

| Service | Port | Purpose |
|---------|------|---------|
| MON | 3300, 6789 | Cluster state, consensus |
| MGR | 9283 | Prometheus metrics (required by ODF StorageCluster) |
| OSDs | 6800-7300 | Data storage daemons |
| RGW | 7480 | S3-compatible object gateway (Quay backend) |

### Pools

| Pool | Purpose |
|------|---------|
| `ceph-rbd` | RBD block storage for ODF StorageCluster |
| Default pools | Internal Ceph pools (`.mgr`, `.rgw.root`, etc.) |

### S3 / RGW

- User: `quay-ci` (system user with full access)
- Bucket: `quay-storage` (pre-created for Quay mirror registry)
- Used as Quay's `RadosGWStorage` backend instead of `LocalStorage`

## Networking

Ceph runs on the Landing Zone VM, which is on the same libvirt cluster network as the OpenShift master nodes. All communication is direct:

```text
Master node (192.168.X.10) -> Landing Zone (192.168.X.2:9283/7480) -- same L2 network
```

No firewall configuration is needed. No gateway routing. No SDN workarounds.

## How ODF Config Flows to the Cluster

```text
setup_ceph.sh runs on LZ (via SSH from CI runner)
  ↓ writes files to ~/ceph-config/
  ├── odf_external_config.json
  └── quay_backend_rgw_config.yaml
  ↓
deploy_phase.sh reads files from LZ via SSH (scripts/lib/odf.sh)
  ↓ injects into EXTRA_VARS_CONTENT
  ↓
phase_vars.yaml on LZ
  ↓
Ansible loads: -e @phase_vars.yaml
  ↓
plugins/odf/tasks/deploy.yaml reads odfExternalConfig variable
  ↓
Creates Secret: rook-ceph-external-cluster-details (base64-encoded JSON)
Creates StorageCluster CR with externalStorage.enable: true
```

No GitHub secrets needed. Config is generated and consumed within the same CI run.

The `ODF_EXTERNAL_CONFIG` and `QUAY_BACKEND_RGW_CONFIG` environment variables are still supported for backward compatibility -- env vars take precedence over LZ config files.

## CI Workflow Integration

### Runner Labels

ODF runs use the runner labels `[self-hosted, enclave-large, odf]`. The `odf` label ensures ODF jobs are routed to runners with sufficient disk space for Ceph loopback OSDs.

### Ceph Setup Step

After installing Enclave Lab on the LZ, the workflow runs `make -f Makefile.ci setup-ceph` which:

1. SSHs to the Landing Zone
2. Installs lvm2 if needed
3. Runs `setup_ceph.sh` with LZ-appropriate settings (small OSDs, no systemd service, no firewall)
4. Verifies config files were created

This step is conditional on `STORAGE_PLUGIN == 'odf'` and is skipped for LVMS runs.

### Slash Commands

| Command | Description |
|---------|-------------|
| `/test e2e-disconnected-odf` | Disconnected mode E2E with ODF storage |

## Setup

### CI Setup (Automatic)

Ceph is set up automatically during each ODF CI run. No manual setup or GitHub secrets are needed.

The `setup_ceph_on_lz.sh` script handles everything:

```bash
# This is called automatically by: make -f Makefile.ci setup-ceph
# You don't need to run it manually
./scripts/infrastructure/setup_ceph_on_lz.sh
```

### Manual Setup (Standalone)

For running `setup_ceph.sh` directly on a machine (not via CI):

```bash
sudo ./scripts/infrastructure/setup_ceph.sh
```

The script is idempotent. Environment variables for customization:

| Variable | Default | Description |
|----------|---------|-------------|
| `CEPH_HOST_IP` | auto-detected | Host IP for Ceph to bind to |
| `CEPH_RELEASE` | `reef` | Ceph release to install |
| `OSD_COUNT` | `3` | Number of loopback OSDs |
| `OSD_SIZE_GB` | `50` | Size per OSD (sparse) |
| `RGW_PORT` | `7480` | RadosGW port |
| `LOOP_DIR` | `/var/lib/ceph-loops` | Directory for loopback image files |
| `SKIP_LOOPBACK_SERVICE` | `false` | Skip systemd loopback service |
| `SKIP_FIREWALL` | `false` | Skip firewall configuration |
| `CEPH_CONFIG_DIR` | unset | Write config files for CI consumption |

### Verification

```bash
# Cluster health
sudo cephadm shell -- ceph health        # expect HEALTH_OK
sudo cephadm shell -- ceph osd tree      # expect 3 OSDs up

# RGW / S3
curl http://localhost:7480/
sudo cephadm shell -- radosgw-admin bucket stats --bucket=quay-storage

# Containers
sudo podman ps --format 'table {{.Names}}\t{{.Status}}' | grep ceph
```

### Teardown

For standalone installations (CI runs clean up automatically via `make clean`):

```bash
FSID=$(sudo cephadm shell -- ceph fsid)
sudo cephadm rm-cluster --force --zap-osds --fsid $FSID
sudo losetup -D
sudo rm -rf /var/lib/ceph-loops
for i in 0 1 2; do
    sudo lvremove -f ceph-vg${i}/ceph-lv${i} 2>/dev/null
    sudo vgremove -f ceph-vg${i} 2>/dev/null
done
sudo systemctl disable ceph-loopback.service 2>/dev/null
sudo rm -f /etc/systemd/system/ceph-loopback.service
```

## Files

| File | Purpose |
|------|---------|
| `scripts/infrastructure/setup_ceph.sh` | Ceph cluster setup (runs on LZ or standalone) |
| `scripts/infrastructure/setup_ceph_on_lz.sh` | SSH wrapper to run setup_ceph.sh on Landing Zone |
| `scripts/infrastructure/ceph-attach-loops.sh` | Boot-time loop device re-attachment helper |
| `scripts/infrastructure/verify_ceph.sh` | Manual Ceph health verification |
| `scripts/lib/odf.sh` | Shared ODF config loading (env vars or LZ files) |
| `scripts/deployment/deploy_phase.sh` | Injects `odfExternalConfig` into Ansible extra vars |
| `scripts/deployment/deploy_plugin.sh` | Same ODF injection for plugin deployments |
| `scripts/deployment/deploy_cluster.sh` | Same ODF injection for full cluster deployments |
| `scripts/infrastructure/generate_enclave_vars.sh` | Overrides `storage_plugin` and `quayBackend` when ODF |
| `.github/workflows/e2e-deployment.yml` | Adds setup-ceph step (conditional on ODF) |
| `.github/workflows/slash-command.yml` | Adds `/test e2e-*-odf` commands |
