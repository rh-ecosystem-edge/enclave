"""Checks of the SNO managed cluster (CI).

Both checks reach the SNO through its kubeconfig on the Landing Zone
(``<session>/<sno>-kubeconfig``, written by ``deploy_sno.sh``) and the hub through
the hub kubeconfig, and give the cluster a short window to settle:

- ``verify_sno``: the SNO is healthy (the same ClusterVersion, node, cluster
  operator and MachineConfigPool checks as the hub) and, on the hub, is a Joined and
  Available ManagedCluster;
- ``verify_sno_update_service``: the hub ``update-service`` policy replicated to the
  SNO is Compliant, and the SNO ClusterVersion ``spec.upstream`` is the hub graph URL
  from that policy and owned by the ACM config-policy-controller (both read from the
  same ClusterVersion response).

On failure, both print diagnostics from the hub and the SNO.
"""

from enclave_ci.verify.common import (
    CLUSTER_HEALTHY_LINES,
    Cluster,
    Item,
    Report,
    cluster_problems,
    kubeconfig_found,
    last_error,
    parse_json,
    poll,
    report_command,
    required_condition_problems,
)

POLICY_NS = "openshift-acm-policies"
POLICY = "update-service"
# The ConfigurationPolicy controller updates objects without an explicit field
# manager, so the API server records its binary name.
ACM_MANAGER = "config-policy-controller"


def managed_cluster_problems(hub: Cluster, name: str) -> list[str]:
    """The ManagedCluster is not Joined or not Available on the hub."""
    result = hub.oc("get", "managedcluster", name, "-o", "json")
    doc = parse_json(result)
    if doc is None:
        return [f"query-failed: oc get managedcluster {name}: {last_error(result)}"]
    return required_condition_problems(
        "managedcluster",
        doc,
        ("ManagedClusterJoined", "ManagedClusterConditionAvailable"),
    )


def verify_sno(
    hub: Cluster,
    sno: Cluster,
    name: str,
    report: Report,
    attempts: int = 10,
    delay: float = 30,
) -> bool:
    """Check the SNO and its ManagedCluster; print diagnostics if unhealthy."""
    report.line("## Verifying SNO Managed Cluster")
    report.line()
    report.line(f"SNO cluster: {name}")
    if not kubeconfig_found(sno, report):
        report.line("❌ SNO managed cluster verification failed")
        return False

    problems = poll(
        lambda: cluster_problems(sno) + managed_cluster_problems(hub, name),
        attempts,
        delay,
        report,
    )
    if not problems:
        for text in CLUSTER_HEALTHY_LINES:
            report.line(text)
        report.line("✅ ManagedCluster Joined and Available (hub)")
        report.line("✅ SNO managed cluster is healthy")
        return True

    report.line("❌ SNO managed cluster is not healthy:")
    report.line("```")
    report.line("\n".join(problems))
    report.line("```")
    report.line("Diagnostics:")
    report_command(sno, report, "ClusterVersion (SNO)", "get", "clusterversion")
    report_command(sno, report, "Nodes (SNO)", "get", "nodes", "-o", "wide")
    report_command(sno, report, "Cluster operators (SNO)", "get", "clusteroperators")
    report_command(sno, report, "MachineConfigPools (SNO)", "get", "mcp")
    report_command(
        hub,
        report,
        "ManagedCluster (hub)",
        "get",
        "managedcluster",
        name,
        "--show-labels",
    )
    report_command(
        hub,
        report,
        "ManagedClusterAddons (hub)",
        "get",
        "managedclusteraddons",
        "-n",
        name,
    )
    report_command(
        sno,
        report,
        "Pods not Running or Completed (SNO)",
        "get",
        "pods",
        "-A",
        "--field-selector=status.phase!=Running,status.phase!=Succeeded",
    )
    report_command(
        sno,
        report,
        "Recent events (SNO)",
        "get",
        "events",
        "-A",
        "--sort-by=.lastTimestamp",
        tail=30,
    )
    report.line()
    report.line("❌ SNO managed cluster verification failed")
    return False


def expected_upstream(policy: Item) -> str | None:
    """``spec.upstream`` of the ClusterVersion object template in the policy."""
    for template in policy.get("spec", {}).get("policy-templates") or []:
        config = template.get("objectDefinition", {})
        for obj in config.get("spec", {}).get("object-templates") or []:
            definition = obj.get("objectDefinition", {})
            if definition.get("kind") == "ClusterVersion":
                upstream = definition.get("spec", {}).get("upstream")
                return str(upstream) if upstream else None
    return None


def upstream_owners(clusterversion: Item) -> list[str]:
    """``<manager> <operation> <time>`` of every field manager owning ``spec.upstream``.

    Field ownership can be shared, and the managedFields order doesn't tell which
    manager wrote last, so this lists all owners rather than picking one.
    """
    return [
        f"{entry.get('manager')} {entry.get('operation')} {entry.get('time')}"
        for entry in clusterversion.get("metadata", {}).get("managedFields") or []
        if "f:upstream" in (entry.get("fieldsV1") or {}).get("f:spec", {})
    ]


def _expected_upstream_from_hub(hub: Cluster) -> tuple[str | None, str | None]:
    """The expected upstream from the hub root policy, or the problem reading it."""
    root = hub.oc("get", "policy", POLICY, "-n", POLICY_NS, "-o", "json")
    root_doc = parse_json(root)
    if root_doc is None:
        return None, (
            f"query-failed: oc get policy {POLICY} -n {POLICY_NS}: {last_error(root)}"
        )
    expected = expected_upstream(root_doc)
    if not expected:
        return None, (
            f"policy/{POLICY_NS}/{POLICY}: no ClusterVersion spec.upstream"
            " in its object templates"
        )
    return expected, None


def _replicated_policy_problems(hub: Cluster, name: str) -> list[str]:
    """The update service policy replicated to the SNO namespace is not Compliant."""
    replicated = f"{POLICY_NS}.{POLICY}"
    policy = hub.oc(
        "get", "policy", replicated, "-n", name, "-o", "jsonpath={.status.compliant}"
    )
    compliant = policy.stdout.strip()
    not_found = "notfound" in last_error(policy).lower().replace(" ", "")
    if policy.returncode != 0 and not not_found:
        return [f"query-failed: oc get policy {replicated}: {last_error(policy)}"]
    if compliant != "Compliant":
        return [f"policy/{name}/{replicated}: {compliant or 'not replicated yet'}"]
    return []


def _upstream_problems(sno: Cluster, expected: str) -> tuple[list[str], list[str]]:
    """Problems and owners of the SNO ``spec.upstream``, from one ClusterVersion read."""
    result = sno.oc(
        "get", "clusterversion", "version", "--show-managed-fields", "-o", "json"
    )
    clusterversion = parse_json(result)
    if clusterversion is None:
        return [
            f"query-failed: oc get clusterversion version: {last_error(result)}"
        ], []
    upstream = str(clusterversion.get("spec", {}).get("upstream") or "")
    owners = upstream_owners(clusterversion)
    if upstream != expected:
        return [f"clusterversion/version: spec.upstream='{upstream}'"], owners
    if not any(owner.split(" ")[0] == ACM_MANAGER for owner in owners):
        return [
            f"clusterversion/version: spec.upstream not owned by {ACM_MANAGER}"
        ], owners
    return [], owners


def _report_update_service_diagnostics(
    hub: Cluster, sno: Cluster, name: str, report: Report
) -> None:
    replicated = f"{POLICY_NS}.{POLICY}"
    report.line("Diagnostics:")
    report_command(
        hub,
        report,
        "Replicated policy status (hub)",
        "get",
        "policy",
        replicated,
        "-n",
        name,
        "-o",
        'jsonpath={range .status.details[*]}{.templateMeta.name}: {.compliant}{"\\n"}'
        '{range .history[0:3]}  {.lastTimestamp} {.message}{"\\n"}{end}{end}',
    )
    report_command(
        hub, report, f"Policies replicated to {name} (hub)", "get", "policy", "-n", name
    )
    report_command(
        hub,
        report,
        f"PlacementRule {POLICY_NS}/{POLICY} decisions (hub)",
        "get",
        "placementrule",
        POLICY,
        "-n",
        POLICY_NS,
        "-o",
        'jsonpath={range .status.decisions[*]}{.clusterName}{"\\n"}{end}',
    )
    report_command(
        sno,
        report,
        "ClusterVersion spec (SNO)",
        "get",
        "clusterversion",
        "version",
        "-o",
        "jsonpath={.spec}",
    )
    report_command(
        sno, report, "ConfigurationPolicies (SNO)", "get", "configurationpolicy", "-A"
    )


def verify_sno_update_service(
    hub: Cluster,
    sno: Cluster,
    name: str,
    report: Report,
    attempts: int = 20,
    delay: float = 30,
) -> bool:
    """Check the update service policy patched the SNO ClusterVersion upstream.

    Every read, including the hub root policy, happens inside the retry budget.
    """
    report.line("## Verifying Update Service Patch on the SNO")
    report.line()
    state: dict[str, str] = {}
    owners: list[str] = []

    def check() -> list[str]:
        nonlocal owners
        if "expected" not in state:
            expected, problem = _expected_upstream_from_hub(hub)
            if expected is None:
                return [problem or "no expected upstream"]
            state["expected"] = expected
            report.line(f"Expected upstream (from the hub policy): {expected}")
        problems = _replicated_policy_problems(hub, name)
        upstream_problems, owners = _upstream_problems(sno, state["expected"])
        return problems + upstream_problems

    problems = poll(check, attempts, delay, report)

    retrieved = sno.oc(
        "get",
        "clusterversion",
        "version",
        "-o",
        'jsonpath={range .status.conditions[?(@.type=="RetrievedUpdates")]}'
        "{.status} ({.reason}) {.message}{end}",
    ).stdout.strip()
    report.line(f"RetrievedUpdates (informational): {retrieved or 'unknown'}")
    report.line(f"spec.upstream owned by: {', '.join(owners) or 'unknown'}")

    if not problems:
        report.line("✅ Update service policy patched the SNO ClusterVersion")
        return True

    report.line("❌ Update service patch not applied:")
    report.line("```")
    report.line("\n".join(problems))
    report.line("```")
    _report_update_service_diagnostics(hub, sno, name, report)
    report.line()
    report.line("❌ Update service patch verification failed")
    return False
