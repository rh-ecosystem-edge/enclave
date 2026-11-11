"""Read-only node-origin connectivity checks; JSON input and directed JSON results."""

import ipaddress
import json
import shlex
import subprocess
import sys
from pathlib import Path
from typing import Any, TypedDict

CONTROL_PLANE_COUNT = 3


class Node(TypedDict):
    """A configured node and its unambiguously discovered address."""

    name: str
    address: str | None


def discovered_addresses(
    config: dict[str, Any], host: dict[str, Any], macs: set[str]
) -> tuple[int, list[str]]:
    """Match configured identities against stage-specific discovered inventory."""
    candidates = 0
    addresses = []
    for discovered in config["inventory"]:
        if config["stage"] == "postinstall":
            if discovered["metadata"]["name"] == host["name"]:
                candidates += 1
                addresses = [
                    entry["address"]
                    for entry in discovered["status"]["addresses"]
                    if entry["type"] == "InternalIP"
                ]
        else:
            inventory = json.loads(discovered.get("inventory") or "{}")
            interfaces = inventory.get("interfaces", [])
            discovered_macs = {
                interface.get("mac_address", "").lower() for interface in interfaces
            }
            if (
                discovered.get("requested_hostname") == host["name"]
                or inventory.get("hostname") == host["name"]
                or bool(macs & discovered_macs)
            ):
                candidates += 1
                addresses = [
                    address
                    for interface in interfaces
                    for address in interface.get("ipv4_addresses", [])
                ]
    return candidates, addresses


def node_addresses(config: dict[str, Any]) -> list[Node]:
    """Choose usable machine-network addresses without guessing ambiguous hosts."""
    network = ipaddress.ip_network(config["machine_network"])
    nodes = []
    used = set()
    for host in config["hosts"]:
        macs = {host.get("macAddress", "").lower()} | {
            interface["macAddress"].lower()
            for interface in host.get("mapInterfaces", [])
        }
        macs.discard("")
        candidates, addresses = discovered_addresses(config, host, macs)
        usable = set()
        for address in addresses:
            try:
                parsed = ipaddress.ip_interface(address).ip
            except ValueError:
                continue
            if (
                parsed in network
                and not parsed.is_loopback
                and not parsed.is_unspecified
                and not parsed.is_multicast
                and parsed not in {network.network_address, network.broadcast_address}
            ):
                usable.add(str(parsed))
        if host.get("ipAddress"):
            usable &= {host["ipAddress"]}
        address = next(iter(usable)) if candidates == 1 and len(usable) == 1 else None
        if address in used:
            address = None
        if address:
            used.add(address)
        nodes.append({"name": host["name"], "address": address})
    return nodes


def endpoints(
    config: dict[str, Any], nodes: list[Node]
) -> list[tuple[str, str | None, int, list[Node] | None, list[Node]]]:
    """Enumerate only the services applicable to this deployment stage."""
    if config["stage"] == "preinstall":
        if config["disconnected"]:
            return [("mirror", config["mirror"], 8443, None, nodes)]
        return []
    targets = [
        ("api", config["api_vip"], 6443, nodes, nodes),
        ("ingress", config["ingress_vip"], 80, nodes, nodes),
        ("ingress", config["ingress_vip"], 443, nodes, nodes),
    ]
    for destination in nodes:
        targets.extend(
            (
                "etcd",
                destination["address"],
                port,
                [destination],
                [source for source in nodes if source["name"] != destination["name"]],
            )
            for port in (2379, 2380)
        )
    return targets


class Probe:
    """Perform bounded read-only SSH commands from the actual source nodes."""

    def __init__(self, config: dict[str, Any]) -> None:
        """Honor explicit identities or existing conventional private counterparts."""
        self.ssh = [
            "ssh",
            "-o",
            "BatchMode=yes",
            "-o",
            "ConnectTimeout=5",
            "-o",
            "ConnectionAttempts=1",
        ]
        self.ssh += shlex.split(config.get("ssh_args", ""))
        key = config.get("ssh_key")
        if not key:
            public = config.get("ssh_public_key_path", "")
            candidate = public[:-4] if public.endswith(".pub") else ""
            if candidate and Path(candidate).is_file():
                key = candidate
        if key:
            self.ssh += ["-i", key]
        self.user = config.get("ssh_user", "core")

    def command(
        self, node: Node, command: str
    ) -> subprocess.CompletedProcess[str] | None:
        """Bound the entire SSH process, including connection setup."""
        if not node["address"]:
            return None
        try:
            return subprocess.run(
                [*self.ssh, f"{self.user}@{node['address']}", command],
                stdin=subprocess.DEVNULL,
                capture_output=True,
                text=True,
                timeout=10,
                check=False,
            )
        except (OSError, subprocess.TimeoutExpired):
            return None

    def accessible(self, node: Node) -> bool:
        """Establish whether the node can originate commands at all."""
        result = self.command(node, "true")
        return result is not None and result.returncode == 0

    def listening(self, nodes: list[Node] | None, port: int) -> bool:
        """Read listener state without creating services or changing policy."""
        command = f"ss -H -ltn '( sport = :{port} )'"
        if nodes is None:
            try:
                result = subprocess.run(
                    ["ss", "-H", "-ltn", f"( sport = :{port} )"],
                    capture_output=True,
                    text=True,
                    timeout=5,
                    check=False,
                )
            except (OSError, subprocess.TimeoutExpired):
                return False
            return result.returncode == 0 and bool(result.stdout.strip())
        return any(
            result is not None
            and result.returncode == 0
            and bool(result.stdout.strip())
            for result in (self.command(node, command) for node in nodes)
        )

    def connect(self, source: Node, address: str, port: int) -> bool | None:
        """Connect from the node, distinguishing missing access from TCP failure."""
        command = (
            "timeout 4 bash -c "
            + shlex.quote('exec 3<>"/dev/tcp/$1/$2"')
            + " -- "
            + shlex.quote(address)
            + f" {port}"
        )
        result = self.command(source, command)
        if result is None or result.returncode in {126, 127, 255}:
            return None
        return result.returncode == 0


def validate(
    config: dict[str, Any], probe: Probe | None = None
) -> tuple[dict[str, Any], int]:
    """Collect the whole directed matrix; incomplete applicable paths fail closed."""
    if (
        config["stage"] not in {"preinstall", "postinstall"}
        or len(config["hosts"]) != CONTROL_PLANE_COUNT
        or len({host["name"] for host in config["hosts"]}) != CONTROL_PLANE_COUNT
    ):
        raise ValueError(
            "expected a known stage and three distinct control-plane hosts"
        )
    nodes = node_addresses(config)
    probe = probe or Probe(config)
    access = {node["name"]: probe.accessible(node) for node in nodes}
    results = []
    if config["stage"] == "preinstall":
        results.extend(
            {
                "status": "NOT TESTED",
                "service": service,
                "reason": "deferred until postinstall listeners exist",
            }
            for service in ("api:6443", "etcd:2379,2380", "ingress:80,443")
        )
        results.append({
            "status": "SKIP",
            "service": "landing-zone:80",
            "reason": "optional ISO HTTP is not a required node installation path",
        })
        if not config["disconnected"]:
            results.append({
                "status": "SKIP",
                "service": "mirror:8443",
                "reason": "no disconnected Landing Zone mirror required",
            })
    incomplete = False
    failed = False
    for service, address, port, destinations, sources in endpoints(config, nodes):
        for source in sources:
            row = {
                "source": source["name"],
                "source_address": source["address"],
                "destination": address,
                "port": port,
                "protocol": "TCP",
                "service": service,
            }
            if not address or not access[source["name"]]:
                status, reason = (
                    "NOT TESTED",
                    "missing unambiguous address or usable SSH access",
                )
            else:
                connected = probe.connect(source, address, port)
                if connected is True:
                    status, reason = "PASS", "node-origin TCP connection succeeded"
                elif connected is None:
                    status, reason = (
                        "NOT TESTED",
                        "node command unavailable or SSH access lost",
                    )
                elif not probe.listening(destinations, port):
                    status, reason = (
                        "NOT TESTED",
                        "destination listener readiness not established",
                    )
                else:
                    status, reason = (
                        "FAIL",
                        "node-origin connectivity failed to a listening service; not proof of a firewall fault",
                    )
            row.update(status=status, reason=reason)
            results.append(row)
            incomplete |= status == "NOT TESTED"
            failed |= status == "FAIL"
    return {
        "stage": config["stage"],
        "results": results,
    }, 1 if failed else 2 if incomplete else 0


def main() -> int:
    """Emit a sanitized JSON result even when topology cannot be evaluated."""
    try:
        result, status = validate(json.load(sys.stdin))
    except (ValueError, KeyError, TypeError, AttributeError, OSError):
        result, status = (
            {
                "results": [
                    {
                        "status": "NOT TESTED",
                        "reason": "invalid or incomplete topology input",
                    }
                ]
            },
            2,
        )
    sys.stdout.write(json.dumps(result) + "\n")
    return status


if __name__ == "__main__":
    sys.exit(main())
