import json
import logging
import re
import time
from typing import Any

from enclave.utils import (
    log_subprocess_output,
    run_oc_command,
    semver_key,
    wait_for_resource_status,
)

logger = logging.getLogger(__name__)


def _update_subscription_channel(
    dry_run: bool,
    namespace: str,
    subscription_name: str,
    channel: str,
) -> tuple[bool, str | None]:
    """Update the Subscription channel and return change state and current CSV."""
    if re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]*", channel) is None:
        raise ValueError(f"Invalid operator channel {channel!r}")

    result = run_oc_command([
        "oc",
        "get",
        "subscription.operators.coreos.com",
        subscription_name,
        "-n",
        namespace,
        "-o",
        "json",
    ])
    if result.returncode != 0:
        log_subprocess_output(
            f"oc get subscription/{subscription_name} failed "
            f"(exit {result.returncode})",
            result.stderr or "",
        )
        raise RuntimeError(
            f"oc get subscription/{subscription_name} in {namespace} failed "
            f"(exit {result.returncode})"
        )
    try:
        subscription = json.loads(result.stdout)
        current_channel = (subscription.get("spec") or {}).get("channel")
        current_csv = (subscription.get("status") or {}).get("currentCSV")
    except (json.JSONDecodeError, AttributeError) as exc:
        raise RuntimeError(
            f"oc get subscription/{subscription_name} in {namespace} "
            "returned unexpected output"
        ) from exc

    if current_channel == channel:
        logger.info(
            "Subscription %s in namespace %s already uses channel %s.",
            subscription_name,
            namespace,
            channel,
        )
        return False, current_csv

    logger.info(
        "Updating Subscription %s in namespace %s from channel %s to %s.",
        subscription_name,
        namespace,
        current_channel,
        channel,
    )
    logger.info("[UPDATE] %s/%s channel=%s", namespace, subscription_name, channel)
    if dry_run:
        return True, current_csv

    patch_result = run_oc_command([
        "oc",
        "patch",
        "subscription.operators.coreos.com",
        subscription_name,
        "-n",
        namespace,
        "--type",
        "merge",
        "-p",
        json.dumps({"spec": {"channel": channel}}),
    ])
    if patch_result.returncode != 0:
        log_subprocess_output(
            f"oc patch subscription/{subscription_name} failed "
            f"(exit {patch_result.returncode})",
            patch_result.stderr or "",
        )
        raise RuntimeError(
            f"oc patch subscription/{subscription_name} in {namespace} failed "
            f"(exit {patch_result.returncode})"
        )
    logger.info(
        "Updated Subscription %s in namespace %s to channel %s.",
        subscription_name,
        namespace,
        channel,
    )
    return True, current_csv


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


def _retry_install_plan_poll(
    deadline: float,
    failure_message: str,
    timeout_message: str,
    cause: Exception | None = None,
) -> None:
    """Sleep before retrying InstallPlan retrieval, or raise at the deadline."""
    if time.monotonic() >= deadline:
        if cause is not None:
            raise TimeoutError(timeout_message) from cause
        raise TimeoutError(timeout_message)
    logger.warning("%s; retrying in 10 seconds.", failure_message)
    time.sleep(10)


def _get_install_plans(
    namespace: str,
    wait_for_csvs: set[str] | None,
    deadline: float,
) -> list[dict[str, Any]]:
    """Fetch InstallPlans, retrying transient failures when waiting for targets."""
    while True:
        try:
            result = run_oc_command([
                "oc",
                "get",
                "installplan.operators.coreos.com",
                "-n",
                namespace,
                "-o",
                "json",
            ])
        except TimeoutError as exc:
            if wait_for_csvs is None:
                raise
            _retry_install_plan_poll(
                deadline,
                f"oc get installplan in {namespace} timed out",
                f"oc get installplan in {namespace} timed out for 30 minutes",
                exc,
            )
            continue

        if result.returncode != 0:
            log_subprocess_output(
                f"oc get installplan failed (exit {result.returncode})",
                result.stderr or "",
            )
            if wait_for_csvs is None:
                raise RuntimeError(
                    f"oc get installplan in {namespace} failed "
                    f"(exit {result.returncode})"
                )
            _retry_install_plan_poll(
                deadline,
                f"oc get installplan in {namespace} failed",
                f"oc get installplan in {namespace} failed for 30 minutes "
                f"(exit {result.returncode})",
            )
            continue

        try:
            install_plans = json.loads(result.stdout)["items"]
        except (json.JSONDecodeError, KeyError) as exc:
            raise RuntimeError(
                f"oc get installplan in {namespace} returned unexpected output"
            ) from exc

        if wait_for_csvs is not None:
            plan_csv_names = {
                csv_name
                for install_plan in install_plans
                for csv_name in install_plan["spec"]["clusterServiceVersionNames"]
            }
            if not wait_for_csvs.issubset(plan_csv_names):
                _retry_install_plan_poll(
                    deadline,
                    "Waiting for InstallPlans containing target CSVs "
                    f"{sorted(wait_for_csvs)} in namespace {namespace}",
                    f"No InstallPlan containing target CSVs "
                    f"{sorted(wait_for_csvs)} appeared in namespace "
                    f"{namespace} within 30 minutes",
                )
                continue
        return install_plans


def approve_install_plans(
    dry_run: bool,
    namespace: str,
    approved_op_version_map: dict[str, str],
    *,
    wait_for_csvs: set[str] | None = None,
) -> None:
    """Approve pending InstallPlans whose CSV versions are within the approved range.

    Fetches all InstallPlans in the namespace and iterates over those in
    RequiresApproval phase. Each plan is approved only if all its CSVs are
    known (present in approved_op_version_map) and their versions do not
    exceed the desired version. Plans in other phases or with unmanaged /
    too-new CSVs are skipped with an info log.

    When wait_for_csvs is set, retries failed or timed-out queries until
    InstallPlans contain all of those CSV names or the 30-minute deadline is
    reached. This is used after changing a Subscription channel, which can take
    OLM time to resolve into a plan.

    Raises RuntimeError on oc command failures or unexpected API output, and
    TimeoutError if a channel change does not produce the target InstallPlans.
    """
    deadline = time.monotonic() + 30 * 60
    install_plans = _get_install_plans(namespace, wait_for_csvs, deadline)

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


def reconcile(
    version: str,
    namespace: str,
    csv_names: list[str],
    dry_run: bool,
    channel: str | None = None,
    subscription_name: str | None = None,
) -> None:
    """Reconcile an operator Subscription and wait for each CSV to reach Succeeded.

    Normalises the version string ('+' → '-') to match the format OLM uses
    in CSV names. When a channel is provided, updates the Subscription before
    approving InstallPlans. After a channel change, waits for InstallPlans with
    the desired CSVs if the target CSV is not already installed. For each CSV,
    waits up to 30 minutes (polling every 10 s) for its phase to become
    Succeeded. The writes and CSV wait are skipped in dry-run mode.
    """
    op_version_map = {csv: version.replace("+", "-") for csv in csv_names}
    wait_for_csvs: set[str] | None = None
    if channel is not None:
        name = subscription_name or (csv_names[0] if csv_names else "")
        if not name:
            raise ValueError("A Subscription name is required when a channel is set")
        channel_changed, current_csv = _update_subscription_channel(
            dry_run, namespace, name, channel
        )
        target_csvs = {
            f"{csv_name}.v{csv_version}"
            for csv_name, csv_version in op_version_map.items()
        }
        if channel_changed and not dry_run and current_csv not in target_csvs:
            wait_for_csvs = target_csvs
    if wait_for_csvs is None:
        approve_install_plans(dry_run, namespace, op_version_map)
    else:
        approve_install_plans(
            dry_run, namespace, op_version_map, wait_for_csvs=wait_for_csvs
        )
    for csv_name, csv_version in op_version_map.items():
        if not dry_run:
            logger.info(
                "Waiting for CSV %s.v%s in namespace %s to reach status.phase=Succeeded.",
                csv_name,
                csv_version,
                namespace,
            )
            wait_for_resource_status(
                "clusterserviceversion.operators.coreos.com",
                f"{csv_name}.v{csv_version}",
                "phase",
                "Succeeded",
                namespace=namespace,
                timeout_minutes=30,
                sleep_interval=10,
            )
            logger.info(
                "CSV %s.v%s in namespace %s reached status.phase=Succeeded.",
                csv_name,
                csv_version,
                namespace,
            )
