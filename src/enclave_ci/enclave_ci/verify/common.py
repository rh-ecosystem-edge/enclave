"""Building blocks shared by the verification commands.

Any cluster reachable from the Landing Zone (the hub, or a managed cluster whose
kubeconfig is kept there) is checked the same way: ``oc`` runs on the Landing Zone
over SSH with that cluster's kubeconfig, and the JSON is evaluated here.
"""

import json
import shlex
import subprocess
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol

SSH_OPTIONS = (
    "-o",
    "StrictHostKeyChecking=no",
    "-o",
    "UserKnownHostsFile=/dev/null",
    "-o",
    "ConnectTimeout=10",
    "-o",
    "LogLevel=ERROR",
)
SSH_TIMEOUT_EXIT = 124
SSH_FAILURE_EXIT = 255
MESSAGE_LIMIT = 200

Item = dict[str, Any]
Condition = dict[str, Any]

CLUSTER_HEALTHY_LINES = (
    "✅ ClusterVersion Available, not Progressing or Failing",
    "✅ All nodes Ready",
    "✅ All cluster operators Available, none Degraded",
    "✅ All MachineConfigPools updated",
)


@dataclass(frozen=True)
class CommandResult:
    """Outcome of a command run on the Landing Zone."""

    returncode: int
    stdout: str
    stderr: str


class Runner(Protocol):
    """Runs a shell command on the Landing Zone."""

    def run(self, command: str, timeout: int = 60) -> CommandResult:
        """Run ``command`` and return its result."""
        ...


@dataclass(frozen=True)
class LandingZone:
    """SSH access to the Landing Zone VM."""

    host: str
    user: str = "cloud-user"

    def run(self, command: str, timeout: int = 60) -> CommandResult:
        """Run ``command`` on the Landing Zone; a timeout is reported as exit 124."""
        try:
            proc = subprocess.run(
                ["ssh", *SSH_OPTIONS, f"{self.user}@{self.host}", command],
                capture_output=True,
                text=True,
                timeout=timeout,
                check=False,
            )
        except subprocess.TimeoutExpired:
            return CommandResult(SSH_TIMEOUT_EXIT, "", f"timed out after {timeout}s")
        return CommandResult(proc.returncode, proc.stdout, proc.stderr)


def hub_kubeconfig(session_dir: str) -> str:
    """Path of the hub kubeconfig in an Enclave session on the Landing Zone."""
    return f"{session_dir}/ocp-cluster/auth/kubeconfig"


@dataclass(frozen=True)
class Cluster:
    """A cluster reached through ``oc`` on the Landing Zone with ``kubeconfig``."""

    lz: Runner
    session_dir: str
    kubeconfig: str

    def oc(self, *args: str) -> CommandResult:
        """Run ``oc`` from the session ``bin/``; ``$PATH`` expands on the LZ."""
        env = (
            f'PATH={shlex.quote(self.session_dir + "/bin")}:"$PATH" '
            f"KUBECONFIG={shlex.quote(self.kubeconfig)}"
        )
        return self.lz.run(f"{env} oc --request-timeout=30s {shlex.join(args)}")


class Report:
    """Writes lines to stdout and, in GitHub Actions, to the step summary."""

    def __init__(self, summary_path: str | None, echo: Callable[[str], None]) -> None:
        """Append to ``summary_path`` (if set) and print through ``echo``."""
        self._summary_path = summary_path
        self._echo = echo

    def line(self, text: str = "") -> None:
        """Write one line (or a block of lines)."""
        self._echo(text)
        if self._summary_path:
            with Path(self._summary_path).open("a", encoding="utf-8") as summary:
                summary.write(text + "\n")


def one_line(text: str, limit: int) -> str:
    """Collapse whitespace and cut to ``limit`` characters."""
    return " ".join(text.split())[:limit]


def last_error(result: CommandResult) -> str:
    """Last stderr line (trimmed), or the exit code when stderr is empty."""
    lines = result.stderr.strip().splitlines()
    return one_line(lines[-1], MESSAGE_LIMIT) if lines else f"exit {result.returncode}"


def parse_json(result: CommandResult) -> Item | None:
    """Parsed JSON object output of a successful command, else None."""
    if result.returncode != 0:
        return None
    try:
        doc = json.loads(result.stdout)
    except json.JSONDecodeError:
        return None
    return doc if isinstance(doc, dict) else None


def conditions(item: Item) -> list[Condition]:
    """The ``status.conditions`` of a resource."""
    return list(item.get("status", {}).get("conditions") or [])


def _name(item: Item) -> str:
    return str(item.get("metadata", {}).get("name", "?"))


def _why(condition: Condition) -> str:
    """Return `` (reason) message``, with ``-`` for a missing reason."""
    reason = condition.get("reason") or "-"
    message = one_line(str(condition.get("message") or ""), MESSAGE_LIMIT)
    return f" ({reason})" + (f" {message}" if message else "")


def required_condition_problems(
    kind: str, item: Item, types: Iterable[str]
) -> list[str]:
    """Each of ``types`` that is not ``True`` on ``item`` (missing is ``Unknown``)."""
    by_type = {c.get("type"): c for c in conditions(item)}
    problems = []
    for ctype in types:
        cond = by_type.get(ctype, {"type": ctype, "status": "Unknown"})
        if cond.get("status") != "True":
            problems.append(
                f"{kind}/{_name(item)}: {ctype}={cond.get('status')}{_why(cond)}"
            )
    return problems


def _condition_problems(
    kind: str,
    items: Iterable[Item],
    bad: Callable[[Condition], bool],
    required: str,
) -> list[str]:
    """List ``kind/name: Type=Status (reason) message`` for every bad condition.

    A missing ``required`` condition is reported as ``Unknown``: its absence does
    not prove the resource is healthy.
    """
    problems = []
    for item in items:
        conds = conditions(item)
        if not any(c.get("type") == required for c in conds):
            problems.append(f"{kind}/{_name(item)}: {required}=Unknown (-)")
        problems += [
            f"{kind}/{_name(item)}: {c.get('type')}={c.get('status')}{_why(c)}"
            for c in conds
            if bad(c)
        ]
    return problems


def _condition_not_true(cond: Condition, *types: str) -> bool:
    return cond.get("type") in types and cond.get("status") != "True"


def _condition_true(cond: Condition, *types: str) -> bool:
    return cond.get("type") in types and cond.get("status") == "True"


def clusterversion_problems(items: list[Item]) -> list[str]:
    """ClusterVersion missing, not Available, or Progressing or Failing."""
    if not items:
        return ["clusterversion: none found"]
    return _condition_problems(
        "clusterversion",
        items,
        lambda c: (
            _condition_not_true(c, "Available")
            or _condition_true(c, "Progressing", "Failing")
        ),
        required="Available",
    )


def node_problems(items: list[Item]) -> list[str]:
    """Nodes that are not Ready (a missing Ready condition counts as Unknown)."""
    if not items:
        return ["nodes: none found"]
    problems = []
    for item in items:
        ready = next(
            (c for c in conditions(item) if c.get("type") == "Ready"),
            {"status": "Unknown"},
        )
        if ready.get("status") != "True":
            problems.append(
                f"node/{_name(item)}: Ready={ready.get('status')}{_why(ready)}"
            )
    return problems


def operator_problems(items: list[Item]) -> list[str]:
    """Cluster operators missing, not Available, or Degraded."""
    if not items:
        return ["co: none found"]
    return _condition_problems(
        "co",
        items,
        lambda c: _condition_not_true(c, "Available") or _condition_true(c, "Degraded"),
        required="Available",
    )


def mcp_problems(items: list[Item]) -> list[str]:
    """MachineConfigPools missing, not Updated, or Updating or Degraded."""
    if not items:
        return ["mcp: none found"]
    return _condition_problems(
        "mcp",
        items,
        lambda c: (
            _condition_not_true(c, "Updated")
            or _condition_true(c, "Updating", "Degraded")
        ),
        required="Updated",
    )


def get_items(cluster: Cluster, resource: str) -> tuple[list[Item], str | None]:
    """``oc get <resource> -o json`` items, or a ``query-failed`` problem."""
    result = cluster.oc("get", resource, "-o", "json")
    if result.returncode != 0:
        return [], f"query-failed: oc get {resource}: {last_error(result)}"
    doc = parse_json(result)
    if doc is None:
        return [], f"query-failed: oc get {resource}: invalid JSON output"
    return list(doc.get("items") or []), None


def cluster_problems(cluster: Cluster) -> list[str]:
    """ClusterVersion, node, cluster operator and MachineConfigPool problems.

    A failed query is reported as such and does not hide the problems found by the
    other queries.
    """
    checks = (
        ("clusterversion", clusterversion_problems),
        ("nodes", node_problems),
        ("co", operator_problems),
        ("mcp", mcp_problems),
    )
    problems: list[str] = []
    for resource, check in checks:
        items, query_failed = get_items(cluster, resource)
        problems += [query_failed] if query_failed else check(items)
    return problems


def kubeconfig_found(cluster: Cluster, report: Report) -> bool:
    """Check the cluster kubeconfig is on the Landing Zone, reporting why if not."""
    result = cluster.lz.run(f"test -f {shlex.quote(cluster.kubeconfig)}")
    if result.returncode == 0:
        return True
    if result.returncode in {SSH_FAILURE_EXIT, SSH_TIMEOUT_EXIT}:
        report.line(f"❌ Landing Zone did not answer: {last_error(result)}")
    else:
        report.line(f"❌ Kubeconfig not found on Landing Zone: {cluster.kubeconfig}")
    return False


def report_snapshots(
    cluster: Cluster, report: Report, snapshots: Iterable[tuple[str, str]]
) -> None:
    """Print ``oc get <resource>`` tables (informational, never fails)."""
    for resource, title in snapshots:
        report.line()
        report.line(f"### {title}")
        table = cluster.oc("get", resource)
        if table.returncode == 0:
            report.line(table.stdout.rstrip("\n"))
        else:
            report.line(f"⚠️ Could not run 'oc get {resource}': {last_error(table)}")
