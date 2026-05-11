#!/usr/bin/env python3

import json
import logging
import math
import re
import subprocess
import sys
import time
from typing import Any, Optional


class ClusterUpgradeError(Exception):
    """Base exception for cluster upgrade operations."""

    pass


class InvalidVersionError(ClusterUpgradeError):
    """Raised when a version string does not conform to semantic versioning."""

    def __init__(self, version: str, reason: str):
        self.version = version
        self.reason = reason
        super().__init__(f"Invalid version '{version}': {reason}")


class VersionDowngradeError(ClusterUpgradeError):
    """Raised when attempting to downgrade to an older version."""

    def __init__(self, current: str, desired: str):
        self.current_version = current
        self.desired_version = desired
        super().__init__(
            f"Cannot downgrade from version {current} to {desired}. "
            f"Current version is newer than desired version."
        )


class UpdateGraphUnavailableError(ClusterUpgradeError):
    """Raised when the cluster's update graph is not available."""

    def __init__(self):
        super().__init__(
            "Cluster update graph is unavailable (availableUpdates is null). "
            "The cluster may need to sync with the update service."
        )


class VersionNotAvailableError(ClusterUpgradeError):
    """Raised when the desired version is not in available updates."""

    def __init__(self, desired: str, available: list[str]):
        self.desired_version = desired
        self.available_versions = available
        super().__init__(
            f"Version {desired} is not available for upgrade. "
            f"Available versions: {', '.join(available)}"
        )


class ClusterOperatorsNotReadyError(ClusterUpgradeError):
    """Raised when cluster operators are not ready for upgrade."""

    def __init__(self, issues: list[str]):
        self.issues = issues
        joined = "\n".join(f"  - {issue}" for issue in issues)
        super().__init__(
            f"Cluster operators are not ready for upgrade. Issues found:\n{joined}"
        )


def parse_jsonpath_value(raw: str) -> str:
    """Strip surrounding whitespace and quotes from a JSONPath output value."""
    return raw.strip().strip("'\"")


def semver_key(v_string: str) -> tuple[tuple[int, ...], tuple[int, str, int]]:
    """Return a sort key for semantic version strings so release versions rank above pre-releases.

    A version without a suffix (e.g. '4.20.0') sorts higher than one with a suffix
    (e.g. '4.20.0-rc1'), matching the convention that release > pre-release.
    Suffixes are split into a label and a number (e.g. 'rc10' -> ('rc', 10)) so that
    numeric ordering is used instead of lexicographic ('rc9' < 'rc10').
    """
    # Separate the numeric part from the tag (e.g., '1.2.0-rc1' -> ['1.2.0', 'rc1'])
    parts = v_string.split("-", 1)
    main_version = tuple(map(int, parts[0].split(".")))

    if len(parts) == 1:
        # No suffix: (sys.maxsize, "", 0) beats any (0, "rcN", n) in element-wise tuple comparison
        return (main_version, (sys.maxsize, "", 0))
    else:
        # Split suffix into label + number so 'rc9' < 'rc10' (not 'rc9' > 'rc10' lexicographically)
        m = re.match(r'^([a-zA-Z]*)(\d*)$', parts[1])
        label = m.group(1) if m else parts[1]
        num = int(m.group(2)) if m and m.group(2) else 0
        return (main_version, (0, label, num))


def log_subprocess_output(
    header: str, output: str, level: int = logging.WARNING
) -> None:
    """Log multiline subprocess output with proper formatting.

    Args:
        header: Description of the command (e.g., "oc get clusterversion failed")
        output: The stdout or stderr string to log
        level: Logging level (default: WARNING)
    """
    if not output or not output.strip():
        return

    lines = [line for line in output.splitlines() if line.strip()]
    if not lines:
        return

    # Log header with first line
    logging.log(level, "%s: %s", header, lines[0])

    # Log remaining lines with indentation
    for line in lines[1:]:
        logging.log(level, "  %s", line)


def run_oc_command(args: list[str]) -> subprocess.CompletedProcess[str]:
    """Run an oc command with timeout and standard options.

    Args:
        args: Command arguments (should start with "oc")

    Returns:
        CompletedProcess object with stdout/stderr

    Raises:
        TimeoutError: If command exceeds 60-second timeout
    """
    try:
        result = subprocess.run(
            args,
            capture_output=True,
            text=True,
            timeout=60,
        )
        return result
    except subprocess.TimeoutExpired as exc:
        cmd_str = " ".join(args)
        raise TimeoutError(f"Command timed out after 60 seconds: {cmd_str}") from exc


def wait_for_resource_status(
    kind: str,
    name: str,
    status_field: str,
    desired_state: str,
    timeout_minutes: int = 180,
    sleep_interval: int = 60,
) -> None:
    """Poll a resource's status field until it reaches the desired state or timeout elapse."""

    logging.debug(
        "Waiting for resource %s/%s to reach status.%s=%s (timeout: %d minutes, interval: %d seconds)",
        kind,
        name,
        status_field,
        desired_state,
        timeout_minutes,
        sleep_interval,
    )

    timeout = time.time() + (timeout_minutes * 60)  # Convert minutes to seconds

    while True:
        try:
            result = run_oc_command(
                [
                    "oc",
                    "get",
                    kind,
                    name,
                    "-o",
                    f"jsonpath={{.status.{status_field}}}",
                ]
            )
        except TimeoutError as e:
            logging.warning(
                "oc get %s/%s timed out after 60 seconds, retrying",
                kind,
                name,
            )
            # Check if we've exceeded global timeout before retrying. This is to avoid
            # oc timeout errors potentially consuming the whole timeout.
            if time.time() > timeout:
                raise TimeoutError(
                    f"{kind}/{name} polling exceeded global timeout of {timeout_minutes} minutes"
                ) from e
            continue

        if result.returncode != 0:
            header = f"oc get {kind}/{name} failed (exit {result.returncode})"
            log_subprocess_output(header, result.stderr or "", logging.WARNING)

        current_state = parse_jsonpath_value(result.stdout or "")
        if current_state == desired_state:
            logging.info(
                "%s/%s has reached status.%s=%s.",
                kind,
                name,
                status_field,
                desired_state,
            )
            return

        current_time = time.time()
        if current_time > timeout:
            raise TimeoutError(
                f"{kind}/{name} did not reach status.{status_field}={desired_state} within {timeout_minutes} minutes"
                f" (last observed: {current_state!r})"
            )

        minutes_to_limit = round((timeout - current_time) / 60)
        logging.debug(
            "Current status '%s' != '%s'. We still have %d minutes to reach desired status",
            current_state,
            desired_state,
            minutes_to_limit,
        )

        time.sleep(sleep_interval)


def get_current_version() -> str:
    """Return the cluster's current desired version from ClusterVersion."""
    result = run_oc_command(
        [
            "oc",
            "get",
            "clusterversion.config.openshift.io",
            "version",
            "-o",
            "jsonpath='{.status.desired.version}'",
        ]
    )
    if result.returncode != 0:
        header = f"oc get clusterversion failed (exit {result.returncode})"
        if result.stderr:
            log_subprocess_output(f"{header} [stderr]", result.stderr, logging.ERROR)
        if result.stdout:
            log_subprocess_output(f"{header} [stdout]", result.stdout, logging.ERROR)
        raise RuntimeError(f"oc get clusterversion failed (exit {result.returncode})")

    version = parse_jsonpath_value(result.stdout)
    logging.debug("Current cluster version from API: %s", version)

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
    main_part = version.split("-")[0]  # Remove optional prerelease suffix
    components = main_part.split(".")

    if len(components) != 3:
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


def get_available_versions() -> Optional[list[str]]:
    """Return the list of versions the cluster can upgrade to, or None if the update graph is unavailable."""
    result = run_oc_command(
        ["oc", "get", "clusterversion.config.openshift.io", "version", "-o", "json"]
    )
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
    logging.debug("Available versions from API: %s", versions)
    return versions


def get_cluster_operators() -> list[dict[str, Any]]:
    """Return all ClusterOperator objects from the cluster."""
    result = run_oc_command(
        ["oc", "get", "clusteroperator.config.openshift.io", "-o", "json"]
    )
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

    operators = raw_json.get("items", [])
    return operators


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
    logging.info("Patching clusterversion to desired_version=%s", desired_version)

    # Calculate shared deadline for both wait operations
    deadline = time.time() + (timeout_minutes * 60)

    patch_payload = json.dumps(
        {"spec": {"desiredUpdate": {"version": desired_version}}}
    )
    result = run_oc_command(
        [
            "oc",
            "patch",
            "clusterversion.config.openshift.io",
            "version",
            "--type",
            "merge",
            "-p",
            patch_payload,
        ]
    )
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
        remaining_minutes,
        sleep_interval,
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
        remaining_minutes,
        sleep_interval,
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
    logging.debug(
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

    logging.debug(
        "Version comparison: desired=%s, current=%s", desired_version, current_version
    )

    if desired_ver < current_ver:
        raise VersionDowngradeError(current_version, desired_version)

    if desired_ver == current_ver:
        logging.info(
            "Cluster is already at or moving towards version %s", desired_version
        )
        wait_for_resource_status(
            "clusterversion.config.openshift.io",
            "version",
            "history[0].state",
            "Completed",
            timeout_minutes,
            sleep_interval,
        )
        return

    available_versions = get_available_versions()

    if available_versions is None:
        raise UpdateGraphUnavailableError()

    logging.debug(
        "Checking if desired version %s is in available list", desired_version
    )

    if desired_version not in available_versions:
        raise VersionNotAvailableError(desired_version, available_versions)

    logging.debug("Checking cluster operators readiness...")
    ready, issues = check_cluster_operators_ready()
    if not ready:
        raise ClusterOperatorsNotReadyError(issues)

    logging.info("Upgrading cluster to %s", desired_version)

    if dry_run:
        logging.info("Execution is set to DRY-RUN. Exiting.")
        return

    upgrade_cluster(desired_version, timeout_minutes, sleep_interval)
