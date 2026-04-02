# ODF External Ceph Architecture for CI

This document describes the containerized Ceph cluster used to provide external storage for ODF (OpenShift Data Foundation) E2E testing in CI.

## Overview

ODF in external mode connects to a pre-existing Ceph cluster rather than deploying its own. For CI, we run a single-node Ceph cluster directly on the CI runner machine using cephadm. All Ceph daemons run as podman containers on the host.

```
CI Runner Machine (labels: self-hosted, enclave-large, odf)
+------------------------------------------------------------------+
|                                                                  |
|  Ceph Cluster (cephadm, podman containers, --net host)           |
|  +------------------------------------------------------------+  |
|  |  MON  (port 3300/6789)  - cluster state and consensus      |  |
|  |  MGR  (port 8443)       - dashboard and metrics            |  |
|  |  OSD x3                 - storage daemons on loopback LVs  |  |
|  |  RGW  (port 8080)       - S3-compatible object gateway     |  |
|  +------------------------------------------------------------+  |
|                                                                  |
|  Storage Backing                                                 |
|  +------------------------------------------------------------+  |
|  |  /disk1/ceph-loops/osd-0.img -> /dev/loop0 -> ceph-vg0/lv |  |
|  |  /disk1/ceph-loops/osd-1.img -> /dev/loop1 -> ceph-vg1/lv |  |
|  |  /disk1/ceph-loops/osd-2.img -> /dev/loop2 -> ceph-vg2/lv |  |
|  +------------------------------------------------------------+  |
|                                                                  |
|  libvirt VMs (OpenShift cluster)                                 |
|  +------------------------------------------------------------+  |
|  |  Landing Zone VM  (192.168.X.2)                            |  |
|  |  Master nodes     (192.168.X.20+)                          |  |
|  |    -> reach Ceph via gateway 192.168.X.1 -> host IP        |  |
|  +------------------------------------------------------------+  |
|                                                                  |
|  sushy-tools container (existing, --net host)                    |
+------------------------------------------------------------------+
```

## Storage Architecture

### Loopback OSDs with LVM

Cephadm filters out raw loop devices, so each OSD uses an LVM stack:

```
Sparse file (50GB)          Loop device          LVM
osd-0.img  ──────────>  /dev/loop0  ──────>  ceph-vg0/ceph-lv0  ──> OSD.0
osd-1.img  ──────────>  /dev/loop1  ──────>  ceph-vg1/ceph-lv1  ──> OSD.1
osd-2.img  ──────────>  /dev/loop2  ──────>  ceph-vg2/ceph-lv2  ──> OSD.2
```

- Files are **sparse** (`truncate -s 50G`) -- only consume disk as data is written
- A systemd service (`ceph-loopback.service`) re-attaches loops and activates VGs on boot
- Single-node replication: `osd_pool_default_size=1` (no redundancy, CI-only)

### Ceph Services

| Service | Port | Purpose |
|---------|------|---------|
| MON | 3300, 6789 | Cluster state, consensus |
| MGR | 8443 | Dashboard, metrics |
| OSDs | 6800-7300 | Data storage daemons |
| RGW | 8080 | S3-compatible object gateway (Quay backend) |

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

Ceph containers run with `--net host` (cephadm default), binding to the host's physical IP.

The libvirt VMs reach Ceph through standard routing:

```
VM (192.168.X.20) -> gateway (192.168.X.1 = host) -> Ceph (host IP:port)
```

No cross-machine routing or special network configuration is needed.

### Firewall

Ceph ports are restricted to internal networks only. External traffic is rejected:

```
Allow:  192.168.0.0/16  (libvirt VM networks)
Allow:  100.64.0.0/16   (BMC/provisioning networks)
Allow:  127.0.0.0/8     (localhost)
Reject: everything else
```

This is implemented via firewalld rich rules. Source-matched allow rules take priority over the catch-all reject rules.

## How ODF Config Flows to the Cluster

```
GitHub Secret: ODF_EXTERNAL_CONFIG
        |
        v
e2e-deployment.yml (env var)
        |
        v
deploy_phase.sh reads $ODF_EXTERNAL_CONFIG
        |
        v
Writes to EXTRA_VARS_CONTENT -> SSH'd to Landing Zone as phase_vars.yaml
        |
        v
Ansible loads: -e @phase_vars.yaml
        |
        v
plugins/odf/tasks/deploy.yaml reads odfExternalConfig variable
        |
        v
Creates Secret: rook-ceph-external-cluster-details (base64-encoded JSON)
Creates StorageCluster CR with externalStorage.enable: true
```

The same flow applies to `QUAY_BACKEND_RGW_CONFIG`, which configures Quay to use RadosGW instead of local storage.

## CI Workflow Integration

### Runner Labels

When `storage-plugin=odf`, the E2E jobs require `[self-hosted, enclave-large, odf]`. This ensures the job lands on the machine with Ceph installed. Default LVMS runs use `[self-hosted, enclave-large]`.

### Pre-flight Check

Before infrastructure creation, the workflow runs `verify_ceph.sh` which checks:

1. MON port (3300) is reachable
2. RGW endpoint (8080) responds
3. `ceph health` reports OK (if cephadm is available)

### GitHub Secrets and Variables

| Type | Name | Content |
|------|------|---------|
| Variable | `CEPH_HOST_IP` | Physical IP of the Ceph runner |
| Secret | `ODF_EXTERNAL_CONFIG` | JSON from `ceph-external-cluster-details-exporter` (or manual equivalent) |
| Secret | `QUAY_BACKEND_RGW_CONFIG` | YAML: `{access_key, secret_key, bucket_name, hostname, port, is_secure}` |

### Slash Commands

| Command | Description |
|---------|-------------|
| `/test e2e-connected-odf` | Connected mode E2E with ODF storage |
| `/test e2e-disconnected-odf` | Disconnected mode E2E with ODF storage |

## Setup

### Initial Setup (One-time per Runner)

Run as root on the CI runner machine:

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
| `RGW_PORT` | `8080` | RadosGW port |
| `LOOP_DIR` | `/disk1/ceph-loops` | Directory for loopback image files |

The script prints the GitHub secrets values at the end.

### Verification

```bash
# Cluster health
sudo cephadm shell -- ceph health        # expect HEALTH_OK
sudo cephadm shell -- ceph osd tree      # expect 3 OSDs up

# RGW / S3
curl http://localhost:8080/
sudo cephadm shell -- radosgw-admin bucket stats --bucket=quay-storage

# Containers
sudo podman ps --format 'table {{.Names}}\t{{.Status}}' | grep ceph
```

### Maintenance

**Restart Ceph after reboot:**

The `ceph-loopback.service` handles loop re-attachment automatically. Ceph services are managed by cephadm and restart via systemd.

**Check OSD status:**

```bash
sudo cephadm shell -- ceph osd stat
sudo cephadm shell -- ceph osd tree
```

**Teardown (remove Ceph entirely):**

```bash
FSID=$(sudo cephadm shell -- ceph fsid)
sudo cephadm rm-cluster --force --zap-osds --fsid $FSID
sudo losetup -D
sudo rm -rf /disk1/ceph-loops
for i in 0 1 2; do
    sudo lvremove -f ceph-vg${i}/ceph-lv${i} 2>/dev/null
    sudo vgremove -f ceph-vg${i} 2>/dev/null
done
sudo systemctl disable ceph-loopback.service
sudo rm -f /etc/systemd/system/ceph-loopback.service
```

## Files

| File | Purpose |
|------|---------|
| `scripts/infrastructure/setup_ceph.sh` | One-time Ceph cluster setup on runner |
| `scripts/infrastructure/ceph-attach-loops.sh` | Boot-time loop device re-attachment helper |
| `scripts/infrastructure/verify_ceph.sh` | CI pre-flight Ceph health verification |
| `scripts/deployment/deploy_phase.sh` | Injects `odfExternalConfig` into Ansible extra vars |
| `scripts/deployment/deploy_plugin.sh` | Same ODF injection for plugin deployments |
| `scripts/deployment/deploy_cluster.sh` | Same ODF injection for full cluster deployments |
| `scripts/infrastructure/generate_enclave_vars.sh` | Overrides `storage_plugin` and `quayBackend` when ODF |
| `.github/workflows/e2e-deployment.yml` | Adds ODF secrets, Ceph health check, `odf` runner label |
| `.github/workflows/slash-command.yml` | Adds `/test e2e-*-odf` commands |
