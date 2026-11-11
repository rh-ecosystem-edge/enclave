"""Read-only node-origin connectivity checks; JSON input and directed JSON results."""

# ruff: file-ignore[implicit-namespace-package]

import ipaddress
import json
import shlex
import subprocess
import sys
import time
from pathlib import Path
from typing import Any, TypedDict

CONTROL_PLANE_COUNT = 3
MATRIX_TIMEOUT = 630
READINESS_TIMEOUT = 30
SS_MIN_FIELDS = 4


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


def snapshot_addresses(
    snapshot: str,
) -> set[ipaddress.IPv4Address | ipaddress.IPv6Address] | None:
    """Extract interface ownership only from complete command snapshots."""
    addresses, separator, _listeners = snapshot.partition("\nENCLAVE_LISTENERS\n")
    if not separator:
        return None
    return {
        ipaddress.ip_interface(fields[index + 1]).ip
        for line in addresses.splitlines()
        if (fields := line.split())
        for index, field in enumerate(fields[:-1])
        if field in {"inet", "inet6"}
    }


def relevant_listener(
    snapshot: str, destination: ipaddress.IPv4Address | ipaddress.IPv6Address, port: int
) -> bool | None:
    """Interpret only destination-owned, address-family-specific listener evidence."""
    owned = snapshot_addresses(snapshot)
    if owned is None:
        return None
    if destination not in owned:
        return False
    ready = False
    for line in snapshot.partition("\nENCLAVE_LISTENERS\n")[2].splitlines():
        fields = line.split()
        if len(fields) < SS_MIN_FIELDS or fields[0] != "LISTEN":
            return None
        binding, _, bound_port = fields[3].rpartition(":")
        if bound_port != str(port):
            return None
        binding = binding.strip("[]")
        if binding == "*":
            ready = True
        else:
            bound = ipaddress.ip_address(binding)
            ready |= bound.version == destination.version and (
                bound == destination or bound.is_unspecified
            )
    return ready


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
        self.deadline = time.monotonic() + MATRIX_TIMEOUT
        self.readiness_deadline: float | None = None

    def run(
        self, arguments: list[str], timeout: int, deadline: float | None = None
    ) -> subprocess.CompletedProcess[str] | None:
        """Keep every command within the same overall matrix deadline."""
        remaining = min(self.deadline, deadline or self.deadline) - time.monotonic()
        if remaining <= 0:
            return None
        try:
            return subprocess.run(
                arguments,
                stdin=subprocess.DEVNULL,
                capture_output=True,
                text=True,
                timeout=min(timeout, remaining),
                check=False,
            )
        except (OSError, subprocess.TimeoutExpired):
            return None

    def command(
        self, node: Node, command: str, deadline: float | None = None
    ) -> subprocess.CompletedProcess[str] | None:
        """Bound the entire SSH process, including connection setup."""
        if not node["address"]:
            return None
        return self.run(
            [*self.ssh, f"{self.user}@{node['address']}", command], 10, deadline
        )

    def accessible(self, node: Node) -> bool:
        """Establish whether the node can originate commands at all."""
        result = self.command(node, "true")
        return result is not None and result.returncode == 0

    def destination_address(
        self, address: str, deadline: float | None
    ) -> ipaddress.IPv4Address | ipaddress.IPv6Address | None:
        """Require one usable destination address, never guess among DNS answers."""
        try:
            destination = ipaddress.ip_address(address)
        except ValueError:
            result = self.run(["getent", "ahosts", address], 5, deadline)
            if result is None or result.returncode != 0:
                return None
            answers = {line.split()[0] for line in result.stdout.splitlines() if line}
            if len(answers) != 1:
                return None
            destination = ipaddress.ip_address(answers.pop())
        if (
            destination.is_loopback
            or destination.is_unspecified
            or destination.is_multicast
        ):
            return None
        return destination

    def listening(
        self,
        nodes: list[Node] | None,
        address: str,
        port: int,
        deadline: float | None = None,
    ) -> bool | None:
        """Require owned destination IP and an exact or same-family wildcard bind."""
        destination = self.destination_address(address, deadline)
        if destination is None:
            return None
        family = "-4" if isinstance(destination, ipaddress.IPv4Address) else "-6"
        command = (
            f"ip -o {family} address show && printf '\\nENCLAVE_LISTENERS\\n' && "
            f"ss -H {family} -ltn '( sport = :{port} )'"
        )
        states = []
        owners = 0
        for node in nodes if nodes is not None else [None]:
            result = (
                self.run(["bash", "-c", command], 5, deadline)
                if node is None
                else self.command(node, command, deadline)
            )
            if result is None or result.returncode != 0:
                states.append(None)
                continue
            state = relevant_listener(result.stdout, destination, port)
            owned = snapshot_addresses(result.stdout)
            owners += owned is not None and destination in owned
            states.append(state)
        if None in states or owners != 1:
            return None
        return any(state is True for state in states)

    def wait_listening(
        self, nodes: list[Node] | None, address: str, port: int
    ) -> bool | None:
        """Spend at most one shared readiness budget, only after known absence."""
        if self.readiness_deadline is None:
            self.readiness_deadline = min(
                self.deadline, time.monotonic() + READINESS_TIMEOUT
            )
        while (remaining := self.readiness_deadline - time.monotonic()) > 0:
            time.sleep(min(2, remaining))
            state = self.listening(nodes, address, port, self.readiness_deadline)
            if state is not False:
                return state
        return False

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

    def path_status(
        self,
        source: Node,
        address: str,
        port: int,
        destinations: list[Node] | None,
        stage: str,
    ) -> tuple[str, str]:
        """Retry only missing postinstall listeners, never an established path fault."""
        connected = self.connect(source, address, port)
        ready = None
        if connected is False:
            ready = self.listening(destinations, address, port)
            if ready is False and stage == "postinstall":
                ready = self.wait_listening(destinations, address, port)
                if ready is True:
                    connected = self.connect(source, address, port)
        if connected is True:
            return "PASS", "node-origin TCP connection succeeded"
        if connected is None:
            return "NOT TESTED", "node command unavailable or SSH access lost"
        if ready is not True:
            return (
                "NOT TESTED",
                "destination ownership or relevant listener readiness not established",
            )
        return (
            "FAIL",
            "node-origin connectivity failed to a listening service; not proof of a firewall fault",
        )


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
                status, reason = probe.path_status(
                    source, address, port, destinations, config["stage"]
                )
            row.update(status=status, reason=reason)
            results.append(row)
            incomplete |= status == "NOT TESTED"
            failed |= status == "FAIL"
    return {
        "stage": config["stage"],
        "results": results,
    }, 1 if failed else 2 if incomplete else 0


def summarize() -> int:
    """Parse protected command output without echoing topology or parser errors."""
    counts = dict.fromkeys(("PASS", "FAIL", "SKIP", "NOT TESTED"), 0)
    status = 2
    try:
        rows = json.load(sys.stdin)["results"]
        if isinstance(rows, list) and rows:
            for row in rows:
                counts[row["status"]] += 1
            status = 0
    except (ValueError, KeyError, TypeError):
        pass
    summary = (
        {"result_format": "valid", "counts": counts}
        if status == 0
        else {"result_format": "invalid"}
    )
    sys.stdout.write(json.dumps(summary) + "\n")
    return status


def main() -> int:
    """Emit a sanitized JSON result even when topology cannot be evaluated."""
    if sys.argv[1:] == ["--summary"]:
        return summarize()
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
