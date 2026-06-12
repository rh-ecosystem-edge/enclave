import json
import logging
import time

from enclave.utils import (
    log_subprocess_output,
    run_oc_command,
    semver_key,
)

logger = logging.getLogger(__name__)


def _plan_version_ok(
    install_plan_name: str,
    csv_names: list[str],
    approved_op_version_map: dict[str, str],
) -> tuple[bool, str, str]:
    """Check whether every CSV in an InstallPlan is within the approved version range.

    Each CSV name is split on '.v' to extract the operator name and version.
    A plan is rejected if any CSV belongs to an operator not in
    approved_op_version_map (unmanaged) or if its version exceeds the
    approved one (would install a newer release than desired).

    Returns a (ok, csv_op_name, csv_version) triple where ok is False on
    the first rejected CSV, with the name and version of that CSV for logging.
    """
    csv_op_name = ""
    csv_version = ""
    for csv in csv_names:
        try:
            csv_op_name, csv_version = csv.rsplit(".v", 1)
        except ValueError:
            logger.info(
                "Install plan %s has malformed CSV name %s. Skipping.",
                install_plan_name,
                csv,
            )
            return False, "", ""
        desired_op_version = approved_op_version_map.get(csv_op_name)
        if desired_op_version is None:
            logger.info(
                "Install plan %s includes unmanaged CSV %s. Skipping.",
                install_plan_name,
                csv_op_name,
            )
            return False, csv_op_name, csv_version
        if semver_key(csv_version) > semver_key(desired_op_version):
            logger.info(
                "Install plan %s for %s %s is not at desired version %s. Skipping.",
                install_plan_name,
                csv_op_name,
                csv_version,
                desired_op_version,
            )
            return False, csv_op_name, csv_version
    return True, csv_op_name, csv_version


def _approve_plan(
    dry_run: bool,
    namespace: str,
    install_plan_name: str,
    csv_op_name: str,
    csv_version: str,
) -> None:
    """Patch a single InstallPlan's spec.approved field to True.

    Logs the approval intent before acting. In dry-run mode the patch is
    skipped. Raises RuntimeError if the oc patch command fails.
    """
    logger.info(
        "Approving InstallPlan %s for %s %s.",
        install_plan_name,
        csv_op_name,
        csv_version,
    )
    logger.info("[UPDATE] %s/%s:%s", namespace, csv_op_name, csv_version)
    if not dry_run:
        patch_result = run_oc_command([
            "oc",
            "patch",
            "installplan.operators.coreos.com",
            install_plan_name,
            "-n",
            namespace,
            "--type",
            "merge",
            "-p",
            json.dumps({"spec": {"approved": True}}),
        ])
        if patch_result.returncode != 0:
            log_subprocess_output(
                f"oc patch installplan/{install_plan_name} failed (exit {patch_result.returncode})",
                patch_result.stderr or "",
            )
            raise RuntimeError(
                f"oc patch installplan/{install_plan_name} failed (exit {patch_result.returncode})"
            )
        logger.info(
            "Approved InstallPlan %s for %s %s.",
            install_plan_name,
            csv_op_name,
            csv_version,
        )


def approve_install_plans(
    dry_run: bool,
    namespace: str,
    approved_op_version_map: dict[str, str],
) -> None:
    """Approve pending InstallPlans whose CSV versions are within the approved range.

    Fetches all InstallPlans in the namespace and iterates over those in
    RequiresApproval phase. Each plan is approved only if all its CSVs are
    known (present in approved_op_version_map) and their versions do not
    exceed the desired version. Plans in other phases or with unmanaged /
    too-new CSVs are skipped with an info log.

    Raises RuntimeError on oc command failures or unexpected API output.
    """
    result = run_oc_command([
        "oc",
        "get",
        "installplan.operators.coreos.com",
        "-n",
        namespace,
        "-o",
        "json",
    ])
    if result.returncode != 0:
        log_subprocess_output(
            f"oc get installplan failed (exit {result.returncode})",
            result.stderr or "",
        )
        raise RuntimeError(
            f"oc get installplan in {namespace} failed (exit {result.returncode})"
        )
    try:
        install_plans = json.loads(result.stdout)["items"]
    except (json.JSONDecodeError, KeyError) as exc:
        raise RuntimeError(
            f"oc get installplan in {namespace} returned unexpected output"
        ) from exc
    for install_plan in install_plans:
        install_plan_name = install_plan["metadata"]["name"]
        install_plan_status_phase = install_plan["status"]["phase"]
        install_plan_spec_csv_names = install_plan["spec"]["clusterServiceVersionNames"]
        if install_plan_status_phase != "RequiresApproval":
            logger.info(
                "Install plan %s is in phase %s with csvNames %s",
                install_plan_name,
                install_plan_status_phase,
                install_plan_spec_csv_names,
            )
            continue
        ok, csv_op_name, csv_version = _plan_version_ok(
            install_plan_name, install_plan_spec_csv_names, approved_op_version_map
        )
        if ok:
            _approve_plan(
                dry_run, namespace, install_plan_name, csv_op_name, csv_version
            )


def approve_and_wait_for_csv(
    dry_run: bool,
    namespace: str,
    op_version_map: dict[str, str],
    csv_name: str,
    csv_version: str,
    *,
    timeout_minutes: int = 30,
    sleep_interval: int = 10,
) -> None:
    """Approve pending InstallPlans and poll until the target CSV reaches Succeeded.

    Calls approve_install_plans on every iteration so that InstallPlans created
    by OLM after an intermediate version installs are not missed by a single
    upfront approval pass.
    """
    deadline = time.time() + timeout_minutes * 60
    logger.info(
        "Waiting for CSV %s.v%s in namespace %s to reach status.phase=Succeeded.",
        csv_name,
        csv_version,
        namespace,
    )
    while True:
        approve_install_plans(dry_run, namespace, op_version_map)
        result = run_oc_command([
            "oc",
            "get",
            "clusterserviceversion.operators.coreos.com",
            f"{csv_name}.v{csv_version}",
            "-n",
            namespace,
            "-o",
            "jsonpath={.status.phase}",
        ])
        if result.returncode != 0:
            log_subprocess_output(
                f"oc get clusterserviceversion/{csv_name}.v{csv_version} failed"
                f" (exit {result.returncode})",
                result.stderr or "",
            )
            phase = ""
        else:
            phase = (result.stdout or "").strip()
        if phase == "Succeeded":
            logger.info(
                "CSV %s.v%s in namespace %s reached status.phase=Succeeded.",
                csv_name,
                csv_version,
                namespace,
            )
            return
        if time.time() >= deadline:
            raise TimeoutError(
                f"CSV {csv_name}.v{csv_version} did not reach phase=Succeeded"
                f" within {timeout_minutes} minutes (last observed: {phase!r})"
            )
        time.sleep(sleep_interval)


def reconcile(
    version: str,
    namespace: str,
    csv_names: list[str],
    dry_run: bool,
) -> None:
    """Approve pending InstallPlans and wait for each CSV to reach Succeeded.

    Normalises the version string ('+' → '-') to match the format OLM uses
    in CSV names, then delegates to approve_and_wait_for_csv per CSV.

    Dry-run limitation: only the initial set of InstallPlans visible at call
    time is inspected and logged; no approval is issued and the CSV wait is
    skipped entirely. InstallPlans that OLM would create after each
    intermediate version installs are therefore invisible in dry-run mode,
    so the output does not reflect the full sequence of approvals a real run
    would perform.
    """
    op_version_map = {csv: version.replace("+", "-") for csv in csv_names}
    if dry_run:
        approve_install_plans(dry_run, namespace, op_version_map)
        return
    for csv_name, csv_version in op_version_map.items():
        approve_and_wait_for_csv(
            dry_run, namespace, op_version_map, csv_name, csv_version
        )
