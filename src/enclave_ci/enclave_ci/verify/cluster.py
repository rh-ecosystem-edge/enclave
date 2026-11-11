"""Health check of the hub cluster right after bootstrap.

Runs ``oc``, ``curl`` and ``podman`` on the Landing Zone over SSH and checks, once,
that:

- the ClusterVersion is Available, and not Progressing or Failing;
- every node is Ready;
- every cluster operator is Available and not Degraded;
- every MachineConfigPool is Updated, and not Updating or Degraded;
- the hub Quay (QuayRegistry quay-enterprise/registry) is Available and its
  ``/health/instance`` answers 200;
- disconnected only: the Landing Zone mirror registry (``quayHostname``, default
  ``mirror.<baseDomain>``) ``/health/instance`` answers 200.

A missing required condition counts as ``Unknown`` and an empty list as a problem.
Every problem found is reported, each with its condition reason and message (or the
registry's health body, or the ``oc`` error of a failed query), so one run shows
everything that is wrong. The first four checks come from ``common`` and apply to
any cluster; the registry checks are the hub's own. The report also prints the
nodes, operators, pools and cluster version and, disconnected only, the Landing
Zone mirror registry containers (informational).
"""

import re
import shlex

import yaml

from enclave_ci.verify.common import (
    CLUSTER_HEALTHY_LINES,
    MESSAGE_LIMIT,
    Cluster,
    CommandResult,
    Item,
    Report,
    Runner,
    cluster_problems,
    kubeconfig_found,
    last_error,
    one_line,
    parse_json,
    report_snapshots,
    required_condition_problems,
)

HEALTH_BODY_LIMIT = 300

HUB_SNAPSHOTS = (
    ("nodes", "Cluster Nodes"),
    ("co", "Cluster Operators"),
    ("mcp", "Machine Config Pools"),
    ("clusterversion", "Cluster Version"),
)


def health_problem(label: str, result: CommandResult) -> str | None:
    r"""Check ``curl -w '\n%{http_code}'`` output of a ``/health/instance`` call.

    A non-200 answer is reported with its trimmed body, which names the failing
    Quay component; no answer or an incomplete transfer with curl's exit code.
    """
    body, _, code = result.stdout.rpartition("\n")
    code = code.strip()
    if code in {"", "000"}:
        return f"{label}: /health/instance=no answer (curl exit {result.returncode})"
    if code == "200":
        if result.returncode == 0:
            return None
        return f"{label}: /health/instance=200, cut off (curl exit {result.returncode})"
    body = one_line(body, HEALTH_BODY_LIMIT)
    return f"{label}: /health/instance={code}" + (f" {body}" if body else "")


def lz_mirror_host(config: Item) -> str | None:
    """Landing Zone mirror hostname from the parsed global.yaml.

    ``quayHostname`` when set and resolvable, otherwise ``mirror.<baseDomain>``.
    """
    base = str(config.get("baseDomain") or "")
    host = str(config.get("quayHostname") or "")
    host = re.sub(r"{{\s*baseDomain\s*}}", base, host)
    if host and "{{" not in host:
        return host
    return f"mirror.{base}" if base else None


def _curl_health(lz: Runner, url: str) -> CommandResult:
    return lz.run(f"curl -sk -m 10 -w '\\n%{{http_code}}' {shlex.quote(url)}")


def registry_problems(hub: Cluster, mode: str, config_path: str) -> list[str]:
    """Hub Quay problems and, disconnected only, Landing Zone mirror problems."""
    problems: list[str] = []
    registry = hub.oc(
        "get", "quayregistry", "registry", "-n", "quay-enterprise", "-o", "json"
    )
    registry_doc = parse_json(registry)
    if registry.returncode != 0:
        problems.append(
            f"query-failed: oc get quayregistry registry: {last_error(registry)}"
        )
    elif registry_doc is None:
        problems.append("query-failed: oc get quayregistry registry: invalid JSON")
    else:
        problems += required_condition_problems(
            "quayregistry", registry_doc, ["Available"]
        )

    route = hub.oc(
        "get",
        "route",
        "registry-quay",
        "-n",
        "quay-enterprise",
        "-o",
        "jsonpath={.spec.host}",
    )
    host = route.stdout.strip()
    if route.returncode != 0:
        problems.append(
            f"query-failed: oc get route registry-quay: {last_error(route)}"
        )
    elif not host:
        problems.append("quay/hub: route quay-enterprise/registry-quay has no host")
    else:
        hub_health = health_problem(
            "quay/hub", _curl_health(hub.lz, f"https://{host}/health/instance")
        )
        if hub_health:
            problems.append(hub_health)

    if mode == "disconnected":
        problems += _lz_mirror_problems(hub.lz, config_path)
    return problems


def _lz_mirror_problems(lz: Runner, config_path: str) -> list[str]:
    """The Landing Zone mirror answers on its hostname, not on localhost."""
    config = lz.run(f"cat {shlex.quote(config_path)}")
    if config.returncode != 0:
        return [f"quay/lz-mirror: cannot read {config_path}"]
    try:
        parsed = yaml.safe_load(config.stdout) or {}
    except yaml.YAMLError as err:
        return [
            f"quay/lz-mirror: invalid YAML in {config_path}: {one_line(str(err), MESSAGE_LIMIT)}"
        ]
    if not isinstance(parsed, dict):
        return [f"quay/lz-mirror: {config_path} is not a YAML mapping"]
    mirror = lz_mirror_host(parsed)
    if not mirror:
        return [f"quay/lz-mirror: no quayHostname or baseDomain in {config_path}"]
    problem = health_problem(
        "quay/lz-mirror", _curl_health(lz, f"https://{mirror}:8443/health/instance")
    )
    return [problem] if problem else []


def _report_lz_mirror_containers(lz: Runner, report: Report) -> None:
    """Print the Landing Zone mirror registry containers (informational)."""
    report.line()
    report.line("### Mirror Registry Status")
    result = lz.run(
        "podman ps --filter name=quay --format 'table {{.Names}}\\t{{.Status}}'"
    )
    if result.returncode == 0:
        report.line(result.stdout.rstrip("\n"))
    else:
        report.line(
            f"⚠️ Could not list the mirror registry containers: {last_error(result)}"
        )


def verify_cluster(hub: Cluster, mode: str, config_path: str, report: Report) -> bool:
    """Run the hub checks, print the report and return whether it is healthy."""
    report.line("## Verifying Cluster Deployment")
    report.line()
    if not kubeconfig_found(hub, report):
        report.line("❌ Cluster verification failed")
        return False
    report.line("✅ Kubeconfig found")

    report.line()
    report.line(f"### Cluster Health ({mode})")
    problems = cluster_problems(hub) + registry_problems(hub, mode, config_path)
    if problems:
        report.line("❌ Cluster not healthy:")
        report.line("```")
        report.line("\n".join(problems))
        report.line("```")
    else:
        for text in CLUSTER_HEALTHY_LINES:
            report.line(text)
        report.line("✅ Hub Quay healthy")
        if mode == "disconnected":
            report.line("✅ Landing Zone mirror registry healthy")

    report_snapshots(hub, report, HUB_SNAPSHOTS)
    if mode == "disconnected":
        _report_lz_mirror_containers(hub.lz, report)

    report.line()
    if problems:
        report.line("❌ Cluster verification failed")
        return False
    report.line("✅ Cluster verification complete")
    return True
