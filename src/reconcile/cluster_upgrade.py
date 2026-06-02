import json
import logging
import math
import time
from typing import Any

from utils import (
    log_subprocess_output,
    parse_jsonpath_value,
    run_oc_command,
    semver_key,
    wait_for_resource_status,
)

VERSION_COMPONENTS = 3

logger = logging.getLogger(__name__)


class ClusterUpgradeError(Exception):
    """Base exception for cluster upgrade operations."""


class InvalidVersionError(ClusterUpgradeError):
    """Raised when a version string does not conform to semantic versioning."""

    def __init__(self, version: str, reason: str) -> None:
        self.version = version
        self.reason = reason
        super().__init__(f"Invalid version '{version}': {reason}")


class VersionDowngradeError(ClusterUpgradeError):
    """Raised when attempting to downgrade to an older version."""

    def __init__(self, current: str, desired: str) -> None:
        self.current_version = current
        self.desired_version = desired
        super().__init__(
            f"Cannot downgrade from version {current} to {desired}. "
            f"Current version is newer than desired version."
        )


class UpdateGraphUnavailableError(ClusterUpgradeError):
    """Raised when the cluster's update graph is not available."""

    def __init__(self) -> None:
        super().__init__(
            "Cluster update graph is unavailable (availableUpdates is null). "
            "The cluster may need to sync with the update service."
        )


class VersionNotAvailableError(ClusterUpgradeError):
    """Raised when the desired version is not in available updates."""

    def __init__(self, desired: str, available: list[str]) -> None:
        self.desired_version = desired
        self.available_versions = available
        super().__init__(
            f"Version {desired} is not available for upgrade. "
            f"Available versions: {', '.join(available)}"
        )


class ClusterOperatorsNotReadyError(ClusterUpgradeError):
    """Raised when cluster operators are not ready for upgrade."""

    def __init__(self, issues: list[str]) -> None:
        self.issues = issues
        joined = "\n".join(f"  - {issue}" for issue in issues)
        super().__init__(
            f"Cluster operators are not ready for upgrade. Issues found:\n{joined}"
        )


def get_current_version() -> str:
    """Return the cluster's current desired version from ClusterVersion."""
    result = run_oc_command([
        "oc",
        "get",
        "clusterversion.config.openshift.io",
        "version",
        "-o",
        "jsonpath='{.status.desired.version}'",
    ])
    if result.returncode != 0:
        header = f"oc get clusterversion failed (exit {result.returncode})"
        if result.stderr:
            log_subprocess_output(f"{header} [stderr]", result.stderr, logging.ERROR)
        if result.stdout:
            log_subprocess_output(f"{header} [stdout]", result.stdout, logging.ERROR)
        raise RuntimeError(f"oc get clusterversion failed (exit {result.returncode})")

    version = parse_jsonpath_value(result.stdout)
    logger.debug("Current cluster version from API: %s", version)

    return version


def parse_version(
    version: str, context: str = "version"
) -> tuple[tuple[int, ...], tuple[int, str, int]]:
    """Parse and validate a semantic version string into a comparable tuple.

    OpenShift versions follow the format: major.minor.patch[-prerelease]
    Example: 4.20.0, 4.20.11, 4.20.0-rc1

    Args:
        version: Version string to validate
        context: Description of the version being validated (for error messages)

    Returns:
        A tuple suitable for version comparison (from semver_key)

    Raises:
        InvalidVersionError: If the version string is invalid
    """
    if not version or not version.strip():
        raise InvalidVersionError(version, f"{context} cannot be empty")

    # Validate basic format: must have 3 version components (x.y.z)
    main_part = version.split("-", maxsplit=1)[0]  # Remove optional prerelease suffix
    components = main_part.split(".")

    if len(components) != VERSION_COMPONENTS:
        raise InvalidVersionError(
            version,
            f"{context} must have 3 version components (e.g., 4.20.0 or 4.20.11)",
        )

    # Let semver_key do the parsing and validation
    # It will raise ValueError if the format is invalid
    try:
        return semver_key(version)
    except (ValueError, AttributeError, TypeError) as e:
        raise InvalidVersionError(version, f"{context} has invalid format: {e}") from e


def get_available_versions() -> list[str] | None:
    """Return the list of versions the cluster can upgrade to, or None if the update graph is unavailable."""
    result = run_oc_command([
        "oc",
        "get",
        "clusterversion.config.openshift.io",
        "version",
        "-o",
        "json",
    ])
    if result.returncode != 0:
        header = f"oc get clusterversion failed (exit {result.returncode})"
        if result.stderr:
            log_subprocess_output(f"{header} [stderr]", result.stderr, logging.ERROR)
        if result.stdout:
            log_subprocess_output(f"{header} [stdout]", result.stdout, logging.ERROR)
        raise RuntimeError(f"oc get clusterversion failed (exit {result.returncode})")
    try:
        raw_json = json.loads(result.stdout)
    except json.JSONDecodeError as exc:
        header = f"oc get clusterversion returned invalid JSON: {exc}"
        if result.stdout:
            log_subprocess_output(header, result.stdout, logging.ERROR)
        raise RuntimeError("oc get clusterversion returned invalid JSON") from exc
    raw = raw_json.get("status", {}).get("availableUpdates")
    if raw is None:
        return None
    versions = [update["version"] for update in raw]
    logger.debug("Available versions from API: %s", versions)
    return versions


def get_cluster_operators() -> list[dict[str, Any]]:
    """Return all ClusterOperator objects from the cluster."""
    result = run_oc_command([
        "oc",
        "get",
        "clusteroperator.config.openshift.io",
        "-o",
        "json",
    ])
    if result.returncode != 0:
        header = f"oc get clusteroperator failed (exit {result.returncode})"
        if result.stderr:
            log_subprocess_output(f"{header} [stderr]", result.stderr, logging.ERROR)
        if result.stdout:
            log_subprocess_output(f"{header} [stdout]", result.stdout, logging.ERROR)
        raise RuntimeError(f"oc get clusteroperator failed (exit {result.returncode})")

    try:
        raw_json = json.loads(result.stdout)
    except json.JSONDecodeError as exc:
        header = f"oc get clusteroperator returned invalid JSON: {exc}"
        if result.stdout:
            log_subprocess_output(header, result.stdout, logging.ERROR)
        raise RuntimeError("oc get clusteroperator returned invalid JSON") from exc

    return raw_json.get("items", [])


def check_cluster_operators_ready() -> tuple[bool, list[str]]:
    """Return (ready, issues) where issues is a list of operator problems.

    Returns:
        Tuple of (ready status, list of issue descriptions)
    """
    cluster_operators = get_cluster_operators()

    issues = []
    for co in cluster_operators:
        co_name = co.get("metadata", {}).get("name", "<unknown>")
        co_status_conditions = co.get("status", {}).get("conditions", [])
        for condition in co_status_conditions:
            condition_status = condition.get("status", "")
            condition_type = condition.get("type", "")
            if condition_type == "Degraded" and condition_status == "True":
                issues.append(f"Cluster Operator {co_name} is Degraded")
            if condition_type == "Available" and condition_status == "False":
                issues.append(f"Cluster Operator {co_name} is not Available")
            if condition_type == "Upgradeable" and condition_status == "False":
                issues.append(f"Cluster Operator {co_name} is not Upgradeable")

    return (len(issues) == 0, issues)


def upgrade_cluster(
    desired_version: str, timeout_minutes: int = 180, sleep_interval: int = 60
) -> None:
    """Patch ClusterVersion to trigger an upgrade and wait for it to complete.

    The timeout applies to the entire upgrade operation (both wait phases combined).
    """
    logger.info("Patching clusterversion to desired_version=%s", desired_version)

    # Calculate shared deadline for both wait operations
    deadline = time.time() + (timeout_minutes * 60)

    patch_payload = json.dumps({
        "spec": {"desiredUpdate": {"version": desired_version}}
    })
    result = run_oc_command([
        "oc",
        "patch",
        "clusterversion.config.openshift.io",
        "version",
        "--type",
        "merge",
        "-p",
        patch_payload,
    ])
    if result.returncode != 0:
        header = f"oc patch clusterversion to {desired_version} failed (exit {result.returncode})"
        if result.stderr:
            log_subprocess_output(f"{header} [stderr]", result.stderr, logging.ERROR)
        if result.stdout:
            log_subprocess_output(f"{header} [stdout]", result.stdout, logging.ERROR)
        raise RuntimeError(
            f"oc patch clusterversion to {desired_version} failed (exit {result.returncode})"
        )

    # First wait: desired.version update
    remaining_minutes = math.ceil((deadline - time.time()) / 60)
    if remaining_minutes <= 0:
        raise TimeoutError("Timeout exceeded before waiting for desired.version update")

    wait_for_resource_status(
        "clusterversion.config.openshift.io",
        "version",
        "desired.version",
        desired_version,
        timeout_minutes=remaining_minutes,
        sleep_interval=sleep_interval,
    )

    # Second wait: history[0].state = Completed
    remaining_minutes = math.ceil((deadline - time.time()) / 60)
    if remaining_minutes <= 0:
        raise TimeoutError("Timeout exceeded before waiting for upgrade completion")

    wait_for_resource_status(
        "clusterversion.config.openshift.io",
        "version",
        "history[0].state",
        "Completed",
        timeout_minutes=remaining_minutes,
        sleep_interval=sleep_interval,
    )


def reconcile(
    desired_version: str,
    dry_run: bool,
    timeout_minutes: int = 180,
    sleep_interval: int = 60,
) -> None:
    """Validate preconditions and upgrade the cluster to the version set in desired_version.

    Raises if the desired version is older than the current one, not listed in available
    updates, or if any ClusterOperator is not ready. In dry-run mode, stops before
    applying the upgrade.
    """
    logger.debug(
        "reconcile() called with desired_version=%s, dry_run=%s, timeout_minutes=%d, sleep_interval=%d",
        desired_version,
        dry_run,
        timeout_minutes,
        sleep_interval,
    )

    # Parse and validate versions at entry point
    desired_ver = parse_version(desired_version, "desired_version")
    current_version = get_current_version()
    current_ver = parse_version(current_version, "current cluster version")

    logger.debug(
        "Version comparison: desired=%s, current=%s", desired_version, current_version
    )

    if desired_ver < current_ver:
        raise VersionDowngradeError(current_version, desired_version)

    if desired_ver == current_ver:
        logger.info(
            "Cluster is already at or moving towards version %s", desired_version
        )
        wait_for_resource_status(
            "clusterversion.config.openshift.io",
            "version",
            "history[0].state",
            "Completed",
            timeout_minutes=timeout_minutes,
            sleep_interval=sleep_interval,
        )
        return

    available_versions = get_available_versions()

    if available_versions is None:
        raise UpdateGraphUnavailableError

    logger.debug("Checking if desired version %s is in available list", desired_version)

    if desired_version not in available_versions:
        raise VersionNotAvailableError(desired_version, available_versions)

    logger.debug("Checking cluster operators readiness...")
    ready, issues = check_cluster_operators_ready()
    if not ready:
        raise ClusterOperatorsNotReadyError(issues)

    logger.info("Upgrading cluster to %s", desired_version)

    if dry_run:
        logger.info("Execution is set to DRY-RUN. Exiting.")
        return

    upgrade_cluster(desired_version, timeout_minutes, sleep_interval)
