"""Check of OpenShift AI on the SNO managed cluster (CI).

After the SNO is labeled ``sovcloud.open-cluster-management.io/openshift-ai=true``,
the openshift-ai plugin's ACM policies install it there. Checks, with a settle
window:

- on the hub, the plugin's policies replicated to the SNO namespace are Compliant:
  the four ``model-*`` policies and ``plugin-mirrors-openshift-ai`` (the plugin's
  registry mirrors, delivered by policy because the SNO was installed without them);
- on the SNO, every Subscription has an installed CSV in phase Succeeded;
- on the SNO, the DataScienceCluster ``default-dsc`` is Ready with KServe Managed.

Every run logs the hub and SNO state of the install (job log only on success,
also in the step summary on failure) and every problem of each retry.
"""

import time

from enclave_ci.verify.common import (
    Cluster,
    Item,
    Report,
    kubeconfig_found,
    last_error,
    parse_json,
    poll,
    report_command,
)

POLICY_NS = "openshift-acm-policies"
POLICIES = (
    "model-catalogsources",
    "model-prerequisites",
    "model-operators-crd-ready",
    "model-configuration",
    "plugin-mirrors-openshift-ai",
)
DSC = "default-dsc"


def policy_problems(hub: Cluster, name: str) -> list[str]:
    """Plugin policies replicated to the SNO namespace that are not Compliant."""
    result = hub.oc("get", "policies", "-n", name, "-o", "json")
    doc = parse_json(result)
    if doc is None:
        return [f"query-failed: oc get policies -n {name}: {last_error(result)}"]
    compliance = {
        str(item.get("metadata", {}).get("name")): str(
            item.get("status", {}).get("compliant") or ""
        )
        for item in doc.get("items") or []
    }
    problems = []
    for policy in POLICIES:
        state = compliance.get(f"{POLICY_NS}.{policy}")
        if state != "Compliant":
            problems.append(
                f"policy/{name}/{POLICY_NS}.{policy}: {state or 'not replicated yet'}"
            )
    return problems


def subscription_problems(subscriptions: list[Item], csvs: list[Item]) -> list[str]:
    """Subscriptions without an installed CSV in phase Succeeded."""
    if not subscriptions:
        return ["subscriptions: none found"]
    phases = {
        (
            str(c.get("metadata", {}).get("namespace")),
            str(c.get("metadata", {}).get("name")),
        ): str(c.get("status", {}).get("phase") or "")
        for c in csvs
    }
    problems = []
    for sub in subscriptions:
        ns = str(sub.get("metadata", {}).get("namespace"))
        sub_name = str(sub.get("metadata", {}).get("name"))
        installed = str(sub.get("status", {}).get("installedCSV") or "")
        phase = phases.get((ns, installed), "")
        if not installed:
            problems.append(f"subscription/{ns}/{sub_name}: no installed CSV yet")
        elif phase != "Succeeded":
            problems.append(f"csv/{ns}/{installed}: {phase or 'not found'}")
    return problems


def dsc_problems(dsc: Item | None) -> list[str]:
    """The DataScienceCluster is missing, not Ready, or KServe is not Managed."""
    if dsc is None:
        return [f"datasciencecluster/{DSC}: not found"]
    problems = []
    kserve = (
        dsc
        .get("spec", {})
        .get("components", {})
        .get("kserve", {})
        .get("managementState")
    )
    if kserve != "Managed":
        problems.append(f"datasciencecluster/{DSC}: kserve managementState={kserve}")
    status = dsc.get("status", {})
    ready = next(
        (c for c in status.get("conditions") or [] if c.get("type") == "Ready"),
        None,
    )
    if status.get("phase") != "Ready" and (ready or {}).get("status") != "True":
        detail = (ready or {}).get("message") or ""
        problems.append(
            f"datasciencecluster/{DSC}: phase={status.get('phase')}"
            + (f" ({detail})" if detail else "")
        )
    return problems


def _items(cluster: Cluster, *args: str) -> tuple[list[Item], str | None]:
    result = cluster.oc("get", *args, "-o", "json")
    doc = parse_json(result)
    if doc is None:
        return [], f"query-failed: oc get {' '.join(args)}: {last_error(result)}"
    return list(doc.get("items") or []), None


def sno_problems(sno: Cluster) -> list[str]:
    """Operators and the DataScienceCluster on the SNO."""
    subs, failed = _items(sno, "subscriptions.operators.coreos.com", "-A")
    if failed:
        return [failed]
    csvs, failed = _items(sno, "clusterserviceversions.operators.coreos.com", "-A")
    if failed:
        return [failed]
    problems = subscription_problems(subs, csvs)
    result = sno.oc("get", "datasciencecluster", DSC, "-o", "json")
    if result.returncode != 0 and "notfound" not in last_error(result).lower().replace(
        " ", ""
    ):
        return [
            *problems,
            f"query-failed: oc get datasciencecluster {DSC}: {last_error(result)}",
        ]
    return problems + dsc_problems(parse_json(result))


def _report_state(
    hub: Cluster, sno: Cluster, name: str, report: Report, summary: bool
) -> None:
    """Hub and SNO state of the OpenShift AI install (job log; summary on failure)."""

    def show(cluster: Cluster, title: str, *args: str, tail: int = 0) -> None:
        report_command(cluster, report, title, *args, tail=tail, summary=summary)

    show(hub, f"Policies replicated to {name} (hub)", "get", "policies", "-n", name)
    show(
        hub,
        "ManagedCluster labels (hub)",
        "get",
        "managedcluster",
        name,
        "--show-labels",
    )
    show(sno, "MachineConfigPools (SNO)", "get", "mcp")
    show(
        sno,
        "ImageDigestMirrorSets / ImageTagMirrorSets (SNO)",
        "get",
        "imagedigestmirrorsets,imagetagmirrorsets",
    )
    show(
        sno, "CatalogSources (SNO)", "get", "catalogsources.operators.coreos.com", "-A"
    )
    show(
        sno,
        "Subscriptions (SNO)",
        "get",
        "subscriptions.operators.coreos.com",
        "-A",
        "-o",
        "custom-columns=NAMESPACE:.metadata.namespace,NAME:.metadata.name,"
        "CHANNEL:.spec.channel,SOURCE:.spec.source,CSV:.status.installedCSV,"
        "STATE:.status.state",
    )
    show(
        sno,
        "ClusterServiceVersions (SNO)",
        "get",
        "clusterserviceversions.operators.coreos.com",
        "-A",
    )
    show(
        sno,
        f"DataScienceCluster {DSC} (SNO)",
        "get",
        "datasciencecluster",
        DSC,
        "-o",
        'jsonpath=phase={.status.phase}{"\\n"}'
        "{range .status.conditions[*]}{.type}={.status} ({.reason}) {.message}"
        '{"\\n"}{end}',
    )
    show(
        sno,
        "DSCInitialization (SNO)",
        "get",
        "dscinitialization",
        "-o",
        'jsonpath={range .items[*]}{.metadata.name}: phase={.status.phase}{"\\n"}{end}',
    )
    show(
        sno,
        "OpenShift AI pods (SNO)",
        "get",
        "pods",
        "-n",
        "redhat-ods-applications",
        "-o",
        "wide",
    )
    show(
        sno,
        "OpenShift AI operator pods (SNO)",
        "get",
        "pods",
        "-n",
        "redhat-ods-operator",
        "-o",
        "wide",
    )


def _report_failure_diagnostics(sno: Cluster, report: Report) -> None:
    report_command(
        sno,
        report,
        "InstallPlans (SNO)",
        "get",
        "installplans.operators.coreos.com",
        "-A",
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
        tail=40,
    )


def verify_sno_openshift_ai(
    hub: Cluster,
    sno: Cluster,
    name: str,
    report: Report,
    attempts: int = 90,
    delay: float = 30,
) -> bool:
    """Check OpenShift AI is installed and ready on the SNO; print diagnostics if not.

    The window is long (45 min by default): the policies install several operators
    and the mirror sets roll out the SNO before the DataScienceCluster can be Ready.
    """
    report.line("## Verifying OpenShift AI on the SNO")
    report.line()
    if not kubeconfig_found(sno, report):
        report.line("❌ OpenShift AI verification failed")
        return False

    started = time.monotonic()
    problems = poll(
        lambda: policy_problems(hub, name) + sno_problems(sno),
        attempts,
        delay,
        report,
        verbose=True,
    )
    elapsed = int(time.monotonic() - started)
    if not problems:
        report.line(
            f"OpenShift AI ready after {elapsed // 60}m{elapsed % 60}s of checking"
        )
        report.line(
            "✅ openshift-ai policies (model-*, plugin-mirrors) Compliant (hub)"
        )
        report.line("✅ All operators installed (SNO)")
        report.line(f"✅ DataScienceCluster {DSC} Ready with KServe Managed (SNO)")
        report.log()
        report.log("State (informational):")
        _report_state(hub, sno, name, report, summary=False)
        return True

    report.line(f"❌ OpenShift AI is not ready on the SNO after {elapsed // 60}m:")
    report.line("```")
    report.line("\n".join(problems))
    report.line("```")
    report.line("Diagnostics:")
    _report_state(hub, sno, name, report, summary=True)
    _report_failure_diagnostics(sno, report)
    report.line()
    report.line("❌ OpenShift AI verification failed")
    return False
