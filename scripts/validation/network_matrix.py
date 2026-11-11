"""Read-only node-origin connectivity checks; JSON input and directed JSON results."""

# ruff: file-ignore[implicit-namespace-package]

import ipaddress
import json
import os
import shlex
import stat
import subprocess
import sys
import time
from pathlib import Path
from typing import Any, TypedDict

CONTROL_PLANE_COUNT = 3
MATRIX_TIMEOUT = 630
READINESS_TIMEOUT = 30
SS_MIN_FIELDS = 4
SSH_FAILURE = 255
PRIVATE_DIRECTORY_MODE = 0o700
REASONS = {
    "connected",
    "path_failed",
    "address_missing",
    "address_ambiguous",
    "ssh_unavailable",
    "command_unavailable",
    "listener_unready",
    "ownership_unknown",
    "destination_unresolved",
    "input_invalid",
    "deferred",
    "optional_path",
    "mirror_not_required",
    "unknown",
}


class Node(TypedDict):
    """A configured node and its unambiguously discovered address."""

    name: str
    address: str | None
    reason_code: str


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
        reason = (
            "address_missing" if not candidates or not usable else "address_ambiguous"
        )
        address = next(iter(usable)) if candidates == 1 and len(usable) == 1 else None
        if address in used:
            address = None
            reason = "address_ambiguous"
        if address:
            used.add(address)
        nodes.append({"name": host["name"], "address": address, "reason_code": reason})
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
        self.reason_code = "unknown"

    def run(
        self, arguments: list[str], timeout: int, deadline: float | None = None
    ) -> subprocess.CompletedProcess[str] | None:
        """Keep every command within the same overall matrix deadline."""
        remaining = min(self.deadline, deadline or self.deadline) - time.monotonic()
        if remaining <= 0:
            self.reason_code = "command_unavailable"
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
            self.reason_code = "command_unavailable"
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
        if result is not None and result.returncode != 0:
            self.reason_code = "ssh_unavailable"
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
            self.reason_code = "destination_unresolved"
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
                self.reason_code = "command_unavailable"
                states.append(None)
                continue
            state = relevant_listener(result.stdout, destination, port)
            owned = snapshot_addresses(result.stdout)
            owners += owned is not None and destination in owned
            states.append(state)
        if None in states:
            self.reason_code = "command_unavailable"
            return None
        if owners != 1:
            self.reason_code = "ownership_unknown"
            return None
        ready = any(state is True for state in states)
        self.reason_code = "connected" if ready else "listener_unready"
        return ready

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
            self.reason_code = (
                "ssh_unavailable"
                if result is not None and result.returncode == SSH_FAILURE
                else "command_unavailable"
            )
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
            return "PASS", "connected"
        if connected is None:
            return "NOT TESTED", self.reason_code
        if ready is not True:
            return (
                "NOT TESTED",
                self.reason_code,
            )
        return (
            "FAIL",
            "path_failed",
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
    access = {}
    access_reasons = {}
    for node in nodes:
        access[node["name"]] = probe.accessible(node)
        access_reasons[node["name"]] = probe.reason_code
    results = []
    if config["stage"] == "preinstall":
        results.extend(
            {
                "status": "NOT TESTED",
                "service": service,
                "reason_code": "deferred",
                "applicability": "deferred",
            }
            for service in ("api:6443", "etcd:2379,2380", "ingress:80,443")
        )
        results.append({
            "status": "SKIP",
            "service": "landing-zone:80",
            "reason_code": "optional_path",
            "applicability": "excluded",
        })
        if not config["disconnected"]:
            results.append({
                "status": "SKIP",
                "service": "mirror:8443",
                "reason_code": "mirror_not_required",
                "applicability": "excluded",
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
            if not source["address"]:
                status, reason = "NOT TESTED", source["reason_code"]
            elif not address:
                status, reason = (
                    "NOT TESTED",
                    (
                        destinations[0]["reason_code"]
                        if destinations
                        else "address_missing"
                    ),
                )
            elif not access[source["name"]]:
                status, reason = "NOT TESTED", access_reasons[source["name"]]
            else:
                status, reason = probe.path_status(
                    source, address, port, destinations, config["stage"]
                )
            row.update(status=status, reason_code=reason, applicability="applicable")
            results.append(row)
            incomplete |= status == "NOT TESTED"
            failed |= status == "FAIL"
    return {
        "stage": config["stage"],
        "results": results,
    }, 1 if failed else 2 if incomplete else 0


def summary_counts(report: dict[str, Any]) -> dict[str, Any]:
    """Aggregate only fixed status, applicability and reason tokens."""
    counts = dict.fromkeys(("PASS", "FAIL", "SKIP", "NOT TESTED"), 0)
    reasons: dict[str, dict[str, int]] = {
        scope: {} for scope in ("applicable", "deferred", "excluded")
    }
    rows = report["results"]
    if not isinstance(rows, list) or not rows:
        raise ValueError("invalid rows")
    for row in rows:
        counts[row["status"]] += 1
        scope = row.get("applicability", "applicable")
        if scope not in reasons:
            raise ValueError("invalid applicability")
        reason = row.get("reason_code", "unknown")
        if not isinstance(reason, str) or reason not in REASONS:
            reason = "unknown"
        if scope == "deferred" and (
            reason != "deferred" or row["status"] != "NOT TESTED"
        ):
            raise ValueError("invalid deferral")
        reasons[scope][reason] = reasons[scope].get(reason, 0) + 1
    return {"result_format": "valid", "counts": counts, "reason_counts": reasons}


def summarize() -> int:
    """Parse protected command output without echoing topology or parser errors."""
    status = 2
    summary: dict[str, Any] = {"result_format": "invalid"}
    try:
        summary = summary_counts(json.load(sys.stdin))
    except (ValueError, KeyError, TypeError, AttributeError, RecursionError):
        pass
    else:
        status = 0
    sys.stdout.write(json.dumps(summary) + "\n")
    return status


def capture_input(request: dict[str, Any]) -> int:
    """Create fresh private diagnostic input without credentials or SSH options."""
    config = request["input"]
    stage = config["stage"]
    if stage not in {"preinstall", "postinstall"}:
        raise ValueError("invalid stage")
    captured = {
        key: config[key]
        for key in (
            "stage",
            "machine_network",
            "disconnected",
            "mirror",
            "api_vip",
            "ingress_vip",
        )
    }
    captured["hosts"] = [
        {key: host[key] for key in ("name", "macAddress", "ipAddress") if key in host}
        | {
            "mapInterfaces": [
                {"macAddress": interface["macAddress"]}
                for interface in host.get("mapInterfaces", [])
            ]
        }
        for host in config["hosts"]
    ]
    inventory = []
    for node in config["inventory"]:
        if stage == "postinstall":
            inventory.append({
                "metadata": {"name": node["metadata"]["name"]},
                "status": {
                    "addresses": [
                        {"type": entry["type"], "address": entry["address"]}
                        for entry in node["status"]["addresses"]
                        if entry["type"] == "InternalIP"
                    ]
                },
            })
        else:
            discovered = json.loads(node.get("inventory") or "{}")
            inventory.append({
                "requested_hostname": node.get("requested_hostname", ""),
                "inventory": json.dumps({
                    "hostname": discovered.get("hostname", ""),
                    "interfaces": [
                        {
                            key: interface[key]
                            for key in ("mac_address", "ipv4_addresses")
                            if key in interface
                        }
                        for interface in discovered.get("interfaces", [])
                    ],
                }),
            })
    captured["inventory"] = inventory
    payload = json.dumps(captured).encode()
    destination = Path(request["directory"])
    if not destination.is_absolute() or ".." in destination.parts:
        raise ValueError("invalid destination")
    directory = os.open("/", os.O_RDONLY | os.O_DIRECTORY)
    try:
        for component in destination.parts[1:-1]:
            child = os.open(
                component,
                os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW,
                dir_fd=directory,
            )
            os.close(directory)
            directory = child
        write_private_input(directory, destination.name, payload)
    finally:
        os.close(directory)
    return 0


def write_private_input(parent_fd: int, name: str, payload: bytes) -> None:
    """Refuse reuse and write only inside a fresh owner-private directory."""
    parent = os.fstat(parent_fd)
    if (
        parent.st_uid != os.getuid()
        or stat.S_IMODE(parent.st_mode) != PRIVATE_DIRECTORY_MODE
    ):
        raise ValueError("private parent required")
    os.mkdir(name, PRIVATE_DIRECTORY_MODE, dir_fd=parent_fd)
    directory = os.open(
        name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=parent_fd
    )
    try:
        descriptor = os.open(
            "input.json",
            os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
            0o600,
            dir_fd=directory,
        )
        with os.fdopen(descriptor, "wb") as output:
            output.write(payload)
    finally:
        os.close(directory)


def main() -> int:
    """Emit a sanitized JSON result even when topology cannot be evaluated."""
    if sys.argv[1:] == ["--summary"]:
        return summarize()
    try:
        if sys.argv[1:] == ["--capture-input"]:
            return capture_input(json.load(sys.stdin))
        result, status = validate(json.load(sys.stdin))
    except (ValueError, KeyError, TypeError, AttributeError, OSError, RecursionError):
        result, status = (
            {
                "results": [
                    {
                        "status": "NOT TESTED",
                        "reason_code": "input_invalid",
                        "applicability": "applicable",
                    }
                ]
            },
            2,
        )
    sys.stdout.write(json.dumps(result) + "\n")
    return status


if __name__ == "__main__":
    sys.exit(main())
