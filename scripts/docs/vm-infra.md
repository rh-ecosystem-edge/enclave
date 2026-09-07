# VM Infrastructure

`scripts/infrastructure/vm_infra.py` owns the libvirt networks and KVM VMs used
by Enclave CI e2e runs, replacing the previous dependency on
[dev-scripts](https://github.com/openshift-metal3/dev-scripts) and
[metal3-dev-env](https://github.com/metal3-io/metal3-dev-env).

## Network topology

All subnets share the same third octet N (e.g. N=5 → BMC `100.64.5.0/24`,
cluster `192.168.5.0/24`, LZ uplink `172.16.5.0/24`).

`vm_infra.py` selects N itself at create time, using **libvirt as the source of
truth**: under a host-wide lock (`/run/lock/enclave-subnet.lock`) it enumerates
the subnets already in use by any defined libvirt network (active or inactive)
and by any host interface, then picks the lowest free N in `[2, 254]`. Holding
the lock across select→create means concurrent CI runs on the same hypervisor
can never race onto the same subnet, and a re-run of the same cluster reuses its
existing subnet (read back from the `{cluster}-p` network). This replaces the old
`allocate_subnet.sh` JSON allocation ledger, which could drift from reality and
hand out a subnet a leftover network still used.

N can be pinned manually with `ENCLAVE_SUBNET_ID` (or the third octet of
`ENCLAVE_BMC_NETWORK`); when neither is set, N is auto-selected.

### Connected mode — 2 bridges

```text
{CLUSTER}-p  isolated  100.64.N.0/24   BMC / provisioning
  host:  100.64.N.1  (libvirt assigns synchronously — no polling needed)
  LZ:    100.64.N.2  (static, configured via nmcli after first boot)

{CLUSTER}-e  NAT       192.168.N.0/24  Cluster — all VMs have internet
  host:  192.168.N.1
  DHCP with static leases: LZ → .2, master-0 → .11, master-1 → .12, …
```

### Disconnected mode — 3 bridges

```text
{CLUSTER}-p  isolated  100.64.N.0/24   BMC / provisioning (same as connected)

{CLUSTER}-e  isolated  192.168.N.0/24  Cluster — NO internet for any VM
  host:  192.168.N.1
  DHCP with static leases (same assignments as connected)

{CLUSTER}-u  NAT       172.16.N.0/24   LZ uplink — Landing Zone third NIC only
  host:  172.16.N.1
  DHCP: LZ uplink MAC → 172.16.N.2
```

The isolated cluster bridge in disconnected mode closes a gap in the old
dev-scripts setup where masters had unintended internet access via NAT.

## VM layout

### NIC assignments

| VM | NIC 1 — BMC | NIC 2 — cluster | NIC 3 — uplink |
|---|---|---|---|
| `{cluster}_landingzone_0` (connected) | `{cluster}-p` | `{cluster}-e` | — |
| `{cluster}_landingzone_0` (disconnected) | `{cluster}-p` | `{cluster}-e` | `{cluster}-u` |
| `{cluster}_master_N` (both modes) | `{cluster}-p` (L2 only) | `{cluster}-e` | — |

Masters have no IP on the BMC bridge — they are identified by MAC address for
Redfish (`SUSHY_EMULATOR_LIBVIRT_MAC_AS_ID=True`).

VM names follow the dev-scripts convention (`{cluster}_landingzone_0`,
`{cluster}_master_{i}`) so no downstream scripts need to change.

### Boot order

Master VMs (`{cluster}_master_N`) are defined with `<os><boot dev='cdrom'/><boot dev='hd'/></os>`:

1. SATA CD-ROM (first) — sushy-tools mounts the ABI ISO here via Redfish VirtualMedia
2. virtio primary disk (second) — boots RHCOS after installation

sushy-tools changes the boot device by modifying `<os><boot>` elements via the
libvirt API. Per-device `<boot order='N'/>` attributes on disk elements must NOT
be used — libvirt rejects mixing the two styles and sushy-tools' Redfish boot
order change fails with a 500 error.

The Landing Zone VM is **not** started by vm_infra.py. `provision_landing_zone.sh`
tears down the placeholder LZ domain created by vm_infra.py and recreates it with
`virt-install` using a RHEL cloud image and a cloud-init ISO. In disconnected mode
`provision_landing_zone.sh` attaches the uplink NIC (`{cluster}-u`) and reads the
uplink MAC from `macs.json` so the static DHCP lease applies.

## Parallelism and isolation

All resource names (bridges, IP ranges, sushy-tools port, storage pool, VM names)
embed the cluster name, so multiple CI runs on the same host never conflict.

The libvirt API is safe for concurrent operations on distinct resources; the old
`with_libvirt_lock.sh` wrapper is no longer needed.

The one shared resource is the subnet third octet N. `create` serializes subnet
selection and network creation across all concurrent runs on the host with an
exclusive `flock` on `/run/lock/enclave-subnet.lock` (see *Network topology*).

## State files

`create` writes two files to `$WORKING_DIR`:

### `cluster-env.sh`

Sourced by `scripts/lib/config.sh` in all downstream scripts:

```bash
export ENCLAVE_CLUSTER_NAME="..."
export ENCLAVE_BMC_BRIDGE="{cluster}-p"
export ENCLAVE_CLUSTER_BRIDGE="{cluster}-e"
export ENCLAVE_BMC_NETWORK="100.64.N.0/24"
export ENCLAVE_CLUSTER_NETWORK="192.168.N.0/24"
export ENCLAVE_LZ_NETWORK="172.16.N.0/24"   # empty string in connected mode
export ENCLAVE_DEPLOYMENT_MODE="connected|disconnected"
```

`scripts/lib/config.sh` also exports compat aliases (`CLUSTER_NAME`,
`PROVISIONING_NETWORK`, `EXTERNAL_SUBNET_V4`, etc.) so scripts that used
dev-scripts variable names continue to work unchanged.

### `macs.json`

MAC address map keyed by VM name. Persisted so that `destroy` + `create` reuses
the same MACs, preserving static DHCP leases and any Ironic node configuration
that references them.

## Python tool

### Environment variables

| Variable | Required | Default | Description |
|---|---|---|---|
| `ENCLAVE_CLUSTER_NAME` | yes | — | Unique cluster identifier (e.g. `eci-abc123`) |
| `WORKING_DIR` | yes | — | Per-cluster working directory |
| `ENCLAVE_SUBNET_ID` | no | auto | Pin the shared third octet N (2–254); auto-selected from libvirt when unset |
| `ENCLAVE_BMC_NETWORK` | no | derived | Alternative way to pin N via its third octet (e.g. `100.64.5.0/24`); ignored if `ENCLAVE_SUBNET_ID` is set |
| `ENCLAVE_DEPLOYMENT_MODE` | no | `disconnected` | `connected` or `disconnected` |
| `ENCLAVE_NUM_MASTERS` | no | `3` | Number of master VMs |
| `STORAGE_PLUGIN` | no | `lvms` | `lvms` or `odf` — affects VM sizing defaults |
| `MASTER_MEMORY` | no | 32768 / 49152 (odf) | Master RAM in MiB |
| `MASTER_VCPU` | no | 12 / 16 (odf) | Master vCPU count |
| `MASTER_DISK` | no | 120 | Master primary disk in GiB |
| `MASTER_EXTRA_DISK` | derived | 1200 (disconnected+lvms) / 60 | Master extra disk in GiB; not directly overridable — derived from `STORAGE_PLUGIN` and `ENCLAVE_DEPLOYMENT_MODE` |
| `LANDINGZONE_MEMORY` | no | 8192 | Landing Zone RAM in MiB |
| `LANDINGZONE_DISK` | no | 60 / 500 (odf) | Landing Zone disk in GiB |
| `LANDINGZONE_VCPU` | no | 4 | Landing Zone vCPU count |

### XML templates

Network and domain XML definitions live in `scripts/infrastructure/templates/`
as Jinja2 templates (Jinja2 3.1+ is present as a system RPM on RHEL 9):

| Template | Used for |
|---|---|
| `network-bmc.xml.j2` | BMC isolated network (no DHCP) |
| `network-cluster.xml.j2` | Cluster network (isolated or NAT) and LZ uplink |
| `domain.xml.j2` | VM domain definition (q35, UEFI, virtio) |

### Usage

```bash
# Create infrastructure (requires root for libvirt and bridge.conf access)
sudo python3 scripts/infrastructure/vm_infra.py create

# Destroy infrastructure
sudo python3 scripts/infrastructure/vm_infra.py destroy

# Verbose output
sudo python3 scripts/infrastructure/vm_infra.py create -v

# Dry-run: print the subnet create would select (creating nothing)
sudo python3 scripts/infrastructure/vm_infra.py create --print-subnet
```

`--print-subnet` runs the same locked selection logic as `create` and prints
`subnet_id=`, `bmc_network=`, `cluster_network=` and `lz_network=` without
defining any networks or VMs — useful for verifying allocation on a host.

Both commands are idempotent: `create` skips resources that already exist;
`destroy` skips resources already gone.

In CI, the `environment` Makefile target calls `create`, and `clean-infra` calls
`destroy` (via `cleanup.sh`).
