#!/usr/bin/env python3
"""
VM infrastructure management for Enclave CI.

Creates/destroys libvirt networks and KVM VMs for e2e test runs.
All configuration is read from environment variables; no pip dependencies
beyond the system-provided libvirt and jinja2 packages.

See scripts/docs/vm-infra.md for details.

This script currently needs to be run in a RHEL9 Hypervisor with Python 3.9.

Usage:
    sudo python3 vm_infra.py create
    sudo python3 vm_infra.py destroy
"""

from __future__ import annotations

import argparse
import fcntl
import json
import logging
import os
import re
import secrets
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Optional

import jinja2
import libvirt

LOG = logging.getLogger(__name__)

TEMPLATES_DIR = Path(__file__).parent / "templates"
BRIDGE_CONF = "/etc/qemu-kvm/bridge.conf"
BRIDGE_LOCK = "/run/lock/enclave-bridge-conf.lock"


# ─── Configuration ────────────────────────────────────────────────────────────


@dataclass
class VMSpec:
    """Hardware sizing for one VM class (LZ or master)."""

    memory_mb: int
    vcpu: int
    disk_gb: int
    extra_disk_gb: int


@dataclass
class Config:
    """All infrastructure configuration for one cluster, sourced from environment variables."""

    cluster_name: str
    bmc_network: str
    cluster_network: str
    deployment_mode: str
    num_masters: int
    working_dir: Path
    master: VMSpec
    lz: VMSpec
    storage_plugin: str

    @classmethod
    def from_env(cls) -> "Config":
        """Build Config from environment variables; exits with an error message on any missing required var."""

        def req(name: str) -> str:
            """Return env var value or exit with an error if unset/empty."""
            v = os.environ.get(name, "")
            if not v:
                sys.exit(f"ERROR: {name} environment variable is required")
            return v

        def optint(name: str, default: int) -> int:
            """Return env var as int, falling back to default if unset."""
            raw = os.environ.get(name, "")
            if not raw:
                return default
            try:
                return int(raw)
            except ValueError:
                sys.exit(f"ERROR: {name} must be an integer, got {raw!r}")

        def validate(name: str, value: str, pattern: str) -> str:
            """Reject values that don't match an allow-list regex; exits on mismatch."""
            if not re.fullmatch(pattern, value):
                sys.exit(f"ERROR: {name}={value!r} does not match expected pattern {pattern!r}")
            return value

        def validate_cidr(name: str, value: str) -> str:
            """Validate a CIDR like 100.64.5.0/24; empty string is allowed (optional nets)."""
            if value and not re.fullmatch(r"\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3}/\d{1,2}", value):
                sys.exit(f"ERROR: {name}={value!r} is not a valid CIDR")
            return value

        deployment_mode = os.environ.get("ENCLAVE_DEPLOYMENT_MODE", "disconnected")
        if deployment_mode not in ("connected", "disconnected"):
            sys.exit(f"ERROR: ENCLAVE_DEPLOYMENT_MODE must be 'connected' or 'disconnected', got {deployment_mode!r}")

        storage_plugin = os.environ.get("STORAGE_PLUGIN", "lvms")
        if storage_plugin not in ("lvms", "odf"):
            sys.exit(f"ERROR: STORAGE_PLUGIN must be 'lvms' or 'odf', got {storage_plugin!r}")

        if storage_plugin == "odf":
            default_master_mem = 49152
            default_master_vcpu = 16
            default_master_extra = 60
            default_lz_disk = 500
        else:
            default_master_mem = 32768
            default_master_vcpu = 12
            default_master_extra = 1200 if deployment_mode == "disconnected" else 60
            default_lz_disk = 60

        cluster_name = validate(
            "ENCLAVE_CLUSTER_NAME",
            req("ENCLAVE_CLUSTER_NAME"),
            r"[a-z][a-z0-9-]{0,62}",
        )
        working_dir_str = req("WORKING_DIR")
        if any(c in working_dir_str for c in "<>&\"'"):
            sys.exit(f"ERROR: WORKING_DIR contains XML-unsafe characters: {working_dir_str!r}")

        return cls(
            cluster_name=cluster_name,
            bmc_network=validate_cidr("ENCLAVE_BMC_NETWORK", os.environ.get("ENCLAVE_BMC_NETWORK", "")),
            cluster_network=validate_cidr("ENCLAVE_CLUSTER_NETWORK", os.environ.get("ENCLAVE_CLUSTER_NETWORK", "")),
            deployment_mode=deployment_mode,
            num_masters=optint("ENCLAVE_NUM_MASTERS", 3),
            working_dir=Path(working_dir_str),
            master=VMSpec(
                memory_mb=optint("MASTER_MEMORY", default_master_mem),
                vcpu=optint("MASTER_VCPU", default_master_vcpu),
                disk_gb=optint("MASTER_DISK", 120),
                extra_disk_gb=default_master_extra,
            ),
            lz=VMSpec(
                memory_mb=optint("LANDINGZONE_MEMORY", 8192),
                vcpu=optint("LANDINGZONE_VCPU", 4),
                disk_gb=optint("LANDINGZONE_DISK", default_lz_disk),
                extra_disk_gb=0,
            ),
            storage_plugin=storage_plugin,
        )

    @property
    def subnet_id(self) -> int:
        """Third octet of the BMC CIDR; shared across all per-cluster subnets for alignment."""
        return int(self.bmc_network.split(".")[2])

    @property
    def bmc_bridge(self) -> str:
        """{cluster}-p: isolated bridge for BMC/provisioning traffic, no internet."""
        return f"{self.cluster_name}-p"

    @property
    def cluster_bridge(self) -> str:
        """{cluster}-e: NAT (connected) or isolated (disconnected) bridge for cluster VMs."""
        return f"{self.cluster_name}-e"

    @property
    def uplink_bridge(self) -> Optional[str]:
        """{cluster}-u: NAT uplink for LZ internet access in disconnected mode; None otherwise."""
        return f"{self.cluster_name}-u" if self.deployment_mode == "disconnected" else None

    @property
    def lz_network(self) -> Optional[str]:
        """172.16.N.0/24 LZ uplink CIDR in disconnected mode; empty string written to cluster-env.sh when None."""
        n = self.subnet_id
        return f"172.16.{n}.0/24" if self.deployment_mode == "disconnected" else None

    @property
    def bmc_gateway(self) -> str:
        """Host IP assigned to the BMC isolated bridge by libvirt (100.64.N.1)."""
        return f"100.64.{self.subnet_id}.1"

    @property
    def cluster_gateway(self) -> str:
        """Host IP assigned to the cluster bridge by libvirt (192.168.N.1)."""
        return f"192.168.{self.subnet_id}.1"

    @property
    def uplink_gateway(self) -> Optional[str]:
        """Host IP assigned to the LZ uplink NAT bridge (172.16.N.1); None in connected mode."""
        return f"172.16.{self.subnet_id}.1" if self.deployment_mode == "disconnected" else None

    @property
    def pool_dir(self) -> Path:
        """Libvirt dir-type storage pool backing directory."""
        return self.working_dir / "pool"

    @property
    def macs_file(self) -> Path:
        """Persists MAC addresses so destroy+create cycles reuse the same MACs."""
        return self.working_dir / "macs.json"

    @property
    def cluster_env_file(self) -> Path:
        """Shell env file written on create and sourced by scripts/lib/config.sh."""
        return self.working_dir / "cluster-env.sh"

    @property
    def lz_vm_name(self) -> str:
        """Matches dev-scripts naming convention so downstream scripts need no changes."""
        return f"{self.cluster_name}_landingzone_0"

    def master_vm_name(self, i: int) -> str:
        """Matches dev-scripts naming convention so downstream scripts need no changes."""
        return f"{self.cluster_name}_master_{i}"

    @property
    def all_bridges(self) -> List[str]:
        """All bridge names that require allow entries in /etc/qemu-kvm/bridge.conf."""
        bridges = [self.bmc_bridge, self.cluster_bridge]
        if self.uplink_bridge:
            bridges.append(self.uplink_bridge)
        return bridges


# ─── MAC addresses ────────────────────────────────────────────────────────────


def _mac() -> str:
    """Generate a random QEMU-standard MAC address in the 52:54:00:* range."""
    b = secrets.token_bytes(3)
    return f"52:54:00:{b[0]:02x}:{b[1]:02x}:{b[2]:02x}"


def generate_macs(cfg: Config) -> Dict[str, Dict[str, str]]:
    """Generate a fresh per-NIC MAC map for all VMs in the cluster."""
    macs: Dict[str, Dict[str, str]] = {
        cfg.lz_vm_name: {"bmc": _mac(), "cluster": _mac()},
    }
    if cfg.deployment_mode == "disconnected":
        macs[cfg.lz_vm_name]["uplink"] = _mac()
    for i in range(cfg.num_masters):
        macs[cfg.master_vm_name(i)] = {"bmc": _mac(), "cluster": _mac()}
    return macs


def load_or_generate_macs(cfg: Config) -> Dict[str, Dict[str, str]]:
    """Load persisted MACs so BMC/DHCP addressing stays stable across destroy+create cycles."""
    if not cfg.macs_file.exists():
        LOG.info("Generating new MAC addresses")
        return generate_macs(cfg)

    LOG.info("Loading existing MACs from %s", cfg.macs_file)
    with open(cfg.macs_file) as f:
        macs: Dict[str, Dict[str, str]] = json.load(f)

    _MAC_RE = re.compile(r"^([0-9a-f]{2}:){5}[0-9a-f]{2}$")
    for vm, nics in macs.items():
        for nic, mac in nics.items():
            if not _MAC_RE.fullmatch(mac):
                sys.exit(f"ERROR: {cfg.macs_file}: {vm}/{nic} MAC {mac!r} is not a valid lowercase MAC address")

    # Patch in MACs for any VMs missing from the persisted map (e.g. num_masters increased).
    added = []
    if cfg.lz_vm_name not in macs:
        macs[cfg.lz_vm_name] = {"bmc": _mac(), "cluster": _mac()}
        if cfg.deployment_mode == "disconnected":
            macs[cfg.lz_vm_name]["uplink"] = _mac()
        added.append(cfg.lz_vm_name)
    elif cfg.deployment_mode == "disconnected" and "uplink" not in macs[cfg.lz_vm_name]:
        macs[cfg.lz_vm_name]["uplink"] = _mac()
        added.append(f"{cfg.lz_vm_name}(uplink)")
    for i in range(cfg.num_masters):
        name = cfg.master_vm_name(i)
        if name not in macs:
            macs[name] = {"bmc": _mac(), "cluster": _mac()}
            added.append(name)
    if added:
        LOG.info("Generated MACs for new VMs: %s", ", ".join(added))
    return macs


def save_macs(cfg: Config, macs: Dict[str, Dict[str, str]]) -> None:
    """Persist the MAC map to disk so the next create reuses the same addresses."""
    cfg.working_dir.mkdir(parents=True, exist_ok=True)
    with open(cfg.macs_file, "w") as f:
        json.dump(macs, f, indent=2)
        f.write("\n")
    LOG.info("Saved MACs to %s", cfg.macs_file)


# ─── Jinja2 rendering ─────────────────────────────────────────────────────────


def _jinja() -> jinja2.Environment:
    """Return a Jinja2 environment loading templates from the templates/ directory."""
    return jinja2.Environment(
        loader=jinja2.FileSystemLoader(str(TEMPLATES_DIR)),
        autoescape=False,
        keep_trailing_newline=True,
    )


def render(template: str, **ctx) -> str:
    """Render a Jinja2 template by filename from the templates/ directory."""
    return _jinja().get_template(template).render(**ctx)


# ─── bridge.conf management ───────────────────────────────────────────────────


def _update_bridge_conf(bridges: List[str], remove: bool = False) -> None:
    """Add or remove 'allow <bridge>' entries in bridge.conf under an exclusive file lock."""
    with open(BRIDGE_LOCK, "w") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        try:
            try:
                with open(BRIDGE_CONF) as f:
                    lines = f.readlines()
            except FileNotFoundError:
                lines = []

            if remove:
                removes = {f"allow {b}" for b in bridges}
                new_lines = [ln for ln in lines if ln.rstrip() not in removes]
            else:
                existing = {ln.rstrip() for ln in lines}
                new_lines = list(lines)
                for bridge in bridges:
                    entry = f"allow {bridge}"
                    if entry not in existing:
                        new_lines.append(f"{entry}\n")

            os.makedirs(os.path.dirname(BRIDGE_CONF), exist_ok=True)
            tmp_path = f"{BRIDGE_CONF}.tmp"
            with open(tmp_path, "w") as f:
                f.writelines(new_lines)
                f.flush()
                os.fsync(f.fileno())
            os.replace(tmp_path, BRIDGE_CONF)
            LOG.info(
                "%s bridge.conf entries: %s",
                "Removed" if remove else "Added",
                ", ".join(bridges),
            )
        finally:
            fcntl.flock(lock, fcntl.LOCK_UN)


# ─── libvirt helpers ──────────────────────────────────────────────────────────


def _connect() -> libvirt.virConnect:
    """Open a connection to the local QEMU/KVM libvirt daemon."""
    # Suppress libvirt's own stderr error messages — Python catches exceptions instead.
    libvirt.registerErrorHandler(lambda *_: None, None)
    try:
        conn = libvirt.open("qemu:///system")
    except libvirt.libvirtError as exc:
        sys.exit(f"ERROR: Failed to connect to libvirt at qemu:///system: {exc}")
    if conn is None:
        sys.exit("ERROR: Failed to connect to libvirt at qemu:///system")
    return conn


def _net_exists(conn: libvirt.virConnect, name: str) -> bool:
    """Return True if a libvirt network with this name is already defined."""
    try:
        conn.networkLookupByName(name)
        return True
    except libvirt.libvirtError:
        return False


def _dom_exists(conn: libvirt.virConnect, name: str) -> bool:
    """Return True if a libvirt domain with this name is already defined."""
    try:
        conn.lookupByName(name)
        return True
    except libvirt.libvirtError:
        return False


def _pool_exists(conn: libvirt.virConnect, name: str) -> bool:
    """Return True if a libvirt storage pool with this name is already defined."""
    try:
        conn.storagePoolLookupByName(name)
        return True
    except libvirt.libvirtError:
        return False


# ─── Create ───────────────────────────────────────────────────────────────────


def _define_network(conn: libvirt.virConnect, xml: str, name: str) -> None:
    """Define, autostart, and activate a libvirt network; skip if it already exists."""
    if _net_exists(conn, name):
        LOG.info("Network %s already exists, skipping", name)
        return
    net = conn.networkDefineXML(xml)
    net.setAutostart(1)
    net.create()
    LOG.info("Created network: %s", name)


def _create_pool(conn: libvirt.virConnect, cfg: Config) -> None:
    """Define a dir-type storage pool backed by the cluster's pool_dir; skip if already present."""
    name = cfg.cluster_name
    if _pool_exists(conn, name):
        LOG.info("Storage pool %s already exists, skipping", name)
        return
    cfg.pool_dir.mkdir(parents=True, exist_ok=True)
    xml = (
        f"<pool type='dir'>"
        f"<name>{name}</name>"
        f"<target><path>{cfg.pool_dir}</path></target>"
        f"</pool>"
    )
    pool = conn.storagePoolDefineXML(xml)
    pool.build(0)
    pool.create(0)
    pool.setAutostart(1)
    LOG.info("Created storage pool: %s → %s", name, cfg.pool_dir)


def _create_volume(conn: libvirt.virConnect, cfg: Config, vol_name: str, size_gb: int) -> None:
    """Create a thin-provisioned qcow2 volume in the cluster pool; skip if already present."""
    pool = conn.storagePoolLookupByName(cfg.cluster_name)
    try:
        pool.storageVolLookupByName(vol_name)
        LOG.info("Volume %s already exists, skipping", vol_name)
        return
    except libvirt.libvirtError:
        pass
    xml = (
        f"<volume>"
        f"<name>{vol_name}</name>"
        f"<capacity unit='G'>{size_gb}</capacity>"
        f"<allocation unit='G'>0</allocation>"
        f"<target><format type='qcow2'/></target>"
        f"</volume>"
    )
    pool.createXML(xml, 0)
    LOG.info("Created volume: %s (%d GB)", vol_name, size_gb)


def _create_sparse_file(path: Path, size_gb: int) -> None:
    """Create a sparse raw image via truncate; no disk space consumed until data is written."""
    path.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(["truncate", "-s", f"{size_gb}G", str(path)], check=True)
    LOG.info("Created sparse extra disk: %s (%d GB)", path, size_gb)


def _define_domain(conn: libvirt.virConnect, xml: str, name: str) -> None:
    """Define a libvirt domain (VM) without starting it; skip if already defined."""
    if _dom_exists(conn, name):
        LOG.info("Domain %s already exists, skipping", name)
        return
    conn.defineXML(xml)
    LOG.info("Defined domain: %s", name)


def _write_cluster_env(cfg: Config) -> None:
    """Write ENCLAVE_* exports to cluster-env.sh so scripts/lib/config.sh can source them."""
    cfg.working_dir.mkdir(parents=True, exist_ok=True)
    lines = [
        f'export ENCLAVE_CLUSTER_NAME="{cfg.cluster_name}"',
        f'export ENCLAVE_BMC_BRIDGE="{cfg.bmc_bridge}"',
        f'export ENCLAVE_CLUSTER_BRIDGE="{cfg.cluster_bridge}"',
        f'export ENCLAVE_BMC_NETWORK="{cfg.bmc_network}"',
        f'export ENCLAVE_CLUSTER_NETWORK="{cfg.cluster_network}"',
        f'export ENCLAVE_LZ_NETWORK="{cfg.lz_network or ""}"',
        f'export ENCLAVE_DEPLOYMENT_MODE="{cfg.deployment_mode}"',
    ]
    with open(cfg.cluster_env_file, "w") as f:
        f.write("\n".join(lines) + "\n")
    LOG.info("Wrote cluster-env.sh: %s", cfg.cluster_env_file)


def create(cfg: Config) -> None:
    """Create all networks, storage, and VM definitions for the cluster; idempotent on re-run."""
    if not cfg.bmc_network:
        sys.exit("ERROR: ENCLAVE_BMC_NETWORK environment variable is required")
    if not cfg.cluster_network:
        sys.exit("ERROR: ENCLAVE_CLUSTER_NETWORK environment variable is required")
    LOG.info("=== Creating infrastructure for cluster: %s ===", cfg.cluster_name)

    macs = load_or_generate_macs(cfg)
    save_macs(cfg, macs)

    conn = _connect()
    n = cfg.subnet_id

    # BMC network: isolated, host IP, no DHCP (masters are L2 only; LZ gets static IP via nmcli)
    _define_network(
        conn,
        render("network-bmc.xml.j2", name=cfg.bmc_bridge, address=cfg.bmc_gateway),
        cfg.bmc_bridge,
    )

    # Cluster network: isolated (disconnected) or NAT (connected), with DHCP and static leases
    cluster_hosts = [
        {"mac": macs[cfg.lz_vm_name]["cluster"], "name": "lz", "ip": f"192.168.{n}.2"},
    ] + [
        {
            "mac": macs[cfg.master_vm_name(i)]["cluster"],
            "name": f"master-{i}",
            "ip": f"192.168.{n}.{11 + i}",
        }
        for i in range(cfg.num_masters)
    ]
    cluster_net_kwargs: dict = dict(
        name=cfg.cluster_bridge,
        forward_mode="none" if cfg.deployment_mode == "disconnected" else "nat",
        address=cfg.cluster_gateway,
        prefix=24,
        dhcp_start=f"192.168.{n}.10",
        dhcp_end=f"192.168.{n}.99",
        static_hosts=cluster_hosts,
    )
    if cfg.deployment_mode == "disconnected" and cfg.uplink_gateway:
        cluster_net_kwargs["dns_forwarder"] = cfg.uplink_gateway
    _define_network(
        conn,
        render("network-cluster.xml.j2", **cluster_net_kwargs),
        cfg.cluster_bridge,
    )

    # LZ uplink network: NAT, only in disconnected mode (LZ third NIC for internet access)
    if cfg.uplink_bridge:
        _define_network(
            conn,
            render(
                "network-cluster.xml.j2",
                name=cfg.uplink_bridge,
                forward_mode="nat",
                address=cfg.uplink_gateway,
                prefix=24,
                dhcp_start=f"172.16.{n}.10",
                dhcp_end=f"172.16.{n}.20",
                static_hosts=[
                    {"mac": macs[cfg.lz_vm_name]["uplink"], "name": "lz-uplink", "ip": f"172.16.{n}.2"},
                ],
            ),
            cfg.uplink_bridge,
        )

    _update_bridge_conf(cfg.all_bridges)
    _create_pool(conn, cfg)

    # Landing Zone VM
    lz_vol = f"{cfg.lz_vm_name}.qcow2"
    _create_volume(conn, cfg, lz_vol, cfg.lz.disk_gb)
    lz_nics = [
        {"mac": macs[cfg.lz_vm_name]["bmc"], "network": cfg.bmc_bridge},
        {"mac": macs[cfg.lz_vm_name]["cluster"], "network": cfg.cluster_bridge},
    ]
    if cfg.deployment_mode == "disconnected" and cfg.uplink_bridge:
        lz_nics.append({"mac": macs[cfg.lz_vm_name]["uplink"], "network": cfg.uplink_bridge})
    _define_domain(
        conn,
        render(
            "domain.xml.j2",
            name=cfg.lz_vm_name,
            memory_kb=cfg.lz.memory_mb * 1024,
            vcpu=cfg.lz.vcpu,
            pool_path=str(cfg.pool_dir),
            vol_name=lz_vol,
            nics=lz_nics,
            extra_disk_path="",
        ),
        cfg.lz_vm_name,
    )

    # Master VMs
    for i in range(cfg.num_masters):
        name = cfg.master_vm_name(i)
        vol = f"{name}.qcow2"
        extra_path = cfg.pool_dir / f"{name}-extra.img"
        _create_volume(conn, cfg, vol, cfg.master.disk_gb)
        if not extra_path.exists():
            _create_sparse_file(extra_path, cfg.master.extra_disk_gb)
        master_nics = [
            {"mac": macs[name]["bmc"], "network": cfg.bmc_bridge},
            {"mac": macs[name]["cluster"], "network": cfg.cluster_bridge},
        ]
        _define_domain(
            conn,
            render(
                "domain.xml.j2",
                name=name,
                memory_kb=cfg.master.memory_mb * 1024,
                vcpu=cfg.master.vcpu,
                pool_path=str(cfg.pool_dir),
                vol_name=vol,
                nics=master_nics,
                extra_disk_path=str(extra_path),
            ),
            name,
        )

    _write_cluster_env(cfg)

    LOG.info("=== Infrastructure created for cluster: %s ===", cfg.cluster_name)
    LOG.info("  BMC bridge:     %s  (%s)", cfg.bmc_bridge, cfg.bmc_gateway)
    LOG.info("  Cluster bridge: %s  (%s)", cfg.cluster_bridge, cfg.cluster_gateway)
    if cfg.uplink_bridge:
        LOG.info("  Uplink bridge:  %s  (%s)", cfg.uplink_bridge, cfg.uplink_gateway)
    LOG.info("  VMs: 1 LZ + %d masters (all defined, not started)", cfg.num_masters)


# ─── Destroy ──────────────────────────────────────────────────────────────────


def destroy(cfg: Config) -> None:
    """Tear down all cluster VMs, volumes, pool, and networks; logs warnings on per-resource failures."""
    LOG.info("=== Destroying infrastructure for cluster: %s ===", cfg.cluster_name)
    conn = _connect()

    # Undefine all domains matching the cluster prefix (cluster_name + "_" to avoid partial matches)
    for dom in conn.listAllDomains():
        if not dom.name().startswith(cfg.cluster_name + "_"):
            continue
        name = dom.name()
        try:
            if dom.isActive():
                dom.destroy()
            flags = (
                libvirt.VIR_DOMAIN_UNDEFINE_NVRAM
                | libvirt.VIR_DOMAIN_UNDEFINE_MANAGED_SAVE
            )
            try:
                dom.undefineFlags(flags)
            except libvirt.libvirtError:
                dom.undefine()
            LOG.info("Undefined domain: %s", name)
        except libvirt.libvirtError as exc:
            LOG.warning("Failed to undefine domain %s: %s", name, exc)

    # Remove storage pool and all its volumes
    if _pool_exists(conn, cfg.cluster_name):
        pool = conn.storagePoolLookupByName(cfg.cluster_name)
        try:
            for vol_name in pool.listVolumes():
                try:
                    pool.storageVolLookupByName(vol_name).delete(0)
                    LOG.info("Deleted volume: %s", vol_name)
                except libvirt.libvirtError as exc:
                    LOG.warning("Failed to delete volume %s: %s", vol_name, exc)
        except libvirt.libvirtError as exc:
            LOG.warning("Failed to list volumes: %s", exc)

        try:
            if pool.isActive():
                pool.destroy()
            pool.undefine()
            LOG.info("Removed storage pool: %s", cfg.cluster_name)
        except libvirt.libvirtError as exc:
            LOG.warning("Failed to remove pool: %s", exc)

    # Remove sparse extra disk files not tracked by the pool (run even if pool is already gone)
    for i in range(cfg.num_masters):
        extra = cfg.pool_dir / f"{cfg.master_vm_name(i)}-extra.img"
        if extra.exists():
            extra.unlink()
            LOG.info("Removed extra disk: %s", extra)

    # Destroy/undefine all networks matching the cluster prefix (cluster_name + "-" to avoid partial matches)
    for net in conn.listAllNetworks():
        if not net.name().startswith(cfg.cluster_name + "-"):
            continue
        name = net.name()
        try:
            if net.isActive():
                net.destroy()
            net.undefine()
            LOG.info("Removed network: %s", name)
        except libvirt.libvirtError as exc:
            LOG.warning("Failed to remove network %s: %s", name, exc)

    _update_bridge_conf(cfg.all_bridges, remove=True)

    if cfg.cluster_env_file.exists():
        cfg.cluster_env_file.unlink()
        LOG.info("Removed cluster-env.sh")

    LOG.info("=== Destroy complete for cluster: %s ===", cfg.cluster_name)


# ─── Entry point ──────────────────────────────────────────────────────────────


def main() -> None:
    """Parse CLI args, configure logging, and dispatch to create or destroy."""
    parser = argparse.ArgumentParser(
        description="Manage libvirt VM infrastructure for Enclave CI runs"
    )
    parser.add_argument("command", choices=["create", "destroy"])
    parser.add_argument("-v", "--verbose", action="store_true")
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
        datefmt="%H:%M:%S",
    )

    cfg = Config.from_env()

    if args.command == "create":
        create(cfg)
    else:
        destroy(cfg)


if __name__ == "__main__":
    main()
