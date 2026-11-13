"""OpenShift management cluster version upgrade."""

import json
import logging
import math
import re
import time
from typing import Any

from enclave.utils import (
    SemverKey,
    log_subprocess_output,
    parse_jsonpath_value,
    run_oc_command,
    semver_key,
    wait_for_resource_status,
)

VERSION_COMPONENTS = 3
CHANNEL_PATTERN = re.compile(r"^(?P<prefix>[a-z]+)-(?P<minor>\d+\.\d+)$")
CHANNEL_SWITCH_TIMEOUT_SECONDS = 300
CHANNEL_SWITCH_POLL_SECONDS = 10
DEFAULT_UPSTREAM = "<default: api.openshift.com>"

logger = logging.getLogger(__name__)


class ClusterUpgradeError(Exception):
    """Base exception for cluster upgrade operations."""


class InvalidVersionError(ClusterUpgradeError):
    """Raised when a version string does not conform to semantic versioning."""

    def __init__(self, version: str, reason: str) -> None:
        """Record the offending version and why it is invalid."""
        self.version = version
        self.reason = reason
        super().__init__(f"Invalid version '{version}': {reason}")


class VersionDowngradeError(ClusterUpgradeError):
    """Raised when attempting to downgrade to an older version."""

    def __init__(self, current: str, desired: str) -> None:
        """Record the current and desired versions."""
        self.current_version = current
        self.desired_version = desired
        super().__init__(
            f"Cannot downgrade from version {current} to {desired}. "
            f"Current version is newer than desired version."
        )


class UpdateGraphUnavailableError(ClusterUpgradeError):
    """Raised when the cluster's update graph is not available."""

    def __init__(self, diagnostics: str) -> None:
        """Record why the cluster could not retrieve its update graph."""
        self.diagnostics = diagnostics
        super().__init__(
            "Cluster update graph is unavailable (availableUpdates is null).\n"
            f"{diagnostics}"
        )


class VersionNotAvailableError(ClusterUpgradeError):
    """Raised when the desired version is not in available updates."""

    def __init__(self, desired: str, available: list[str], diagnostics: str) -> None:
        """Record the desired version, the offered versions and the update source."""
        self.desired_version = desired
        self.available_versions = available
        self.diagnostics = diagnostics
        offered = ", ".join(available) if available else "none"
        super().__init__(
            f"Version {desired} is not available for upgrade. "
            f"Available versions: {offered}\n{diagnostics}"
        )


class ClusterNotUpgradeableError(ClusterUpgradeError):
    """Raised when ClusterVersion reports Upgradeable=False for a minor upgrade."""

    def __init__(self, current: str, desired: str, reason: str, message: str) -> None:
        """Record the blocked minor upgrade and the CVO's reason and message."""
        self.current_version = current
        self.desired_version = desired
        self.reason = reason
        self.message = message
        super().__init__(
            f"Cluster cannot be upgraded from {current} to {desired}: "
            f"ClusterVersion is Upgradeable=False ({reason}): {message}"
        )


class ChannelError(ClusterUpgradeError):
    """Raised when the update channel for the desired version cannot be selected."""


class ClusterOperatorsNotReadyError(ClusterUpgradeError):
    """Raised when cluster operators are not ready for upgrade."""

    def __init__(self, issues: list[str]) -> None:
        """Record the cluster operator issues blocking the upgrade."""
        self.issues = issues
        joined = "\n".join(f"  - {issue}" for issue in issues)
        super().__init__(
            f"Cluster operators are not ready for upgrade. Issues found:\n{joined}"
        )


class ClusterVersionQueryError(ClusterUpgradeError):
    """Raised when querying ClusterVersion resource fails."""


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


def parse_version(version: str, context: str = "version") -> SemverKey:
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


def get_cluster_version() -> dict[str, Any]:
    """Return the ClusterVersion resource as a dict."""
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
        raise ClusterVersionQueryError(
            f"Failed to query ClusterVersion resource (exit {result.returncode})"
        )
    try:
        parsed = json.loads(result.stdout)
    except json.JSONDecodeError as exc:
        if result.stdout:
            log_subprocess_output(
                f"oc get clusterversion returned invalid JSON: {exc}",
                result.stdout,
                logging.ERROR,
            )
        raise ClusterVersionQueryError(
            "ClusterVersion resource returned invalid JSON"
        ) from exc
    if not isinstance(parsed, dict):
        raise ClusterVersionQueryError("ClusterVersion resource is not a JSON object")
    cluster_version: dict[str, Any] = parsed
    return cluster_version


def get_available_versions(cluster_version: dict[str, Any]) -> list[str] | None:
    """Return the versions in availableUpdates, or None if the update graph is unavailable."""
    raw = cluster_version.get("status", {}).get("availableUpdates")
    if raw is None:
        return None
    versions = [update["version"] for update in raw]
    logger.debug("Available versions from API: %s", versions)
    return versions


def get_conditional_updates(cluster_version: dict[str, Any]) -> list[str]:
    """Return the versions in conditionalUpdates, or an empty list if absent or null."""
    raw = cluster_version.get("status", {}).get("conditionalUpdates")
    if raw is None:
        return []
    versions = [update["release"]["version"] for update in raw]
    logger.debug("Conditional update versions from API: %s", versions)
    return versions


def get_condition(
    cluster_version: dict[str, Any], condition_type: str
) -> dict[str, Any] | None:
    """Return the ClusterVersion status condition of the given type, if present."""
    for condition in cluster_version.get("status", {}).get("conditions") or []:
        if condition.get("type") == condition_type:
            return dict(condition)
    return None


def describe_update_source(cluster_version: dict[str, Any]) -> str:
    """Describe where the CVO looks for updates and the result of its last attempt."""
    spec = cluster_version.get("spec", {})
    upstream = spec.get("upstream") or DEFAULT_UPSTREAM
    channel = spec.get("channel") or "<none>"
    lines = [f"  upstream: {upstream}", f"  channel: {channel}"]
    retrieved = get_condition(cluster_version, "RetrievedUpdates")
    if retrieved is None:
        lines.append("  RetrievedUpdates: <condition not reported>")
    else:
        status = retrieved.get("status", "Unknown")
        reason = retrieved.get("reason") or "no reason"
        message = retrieved.get("message") or "no message"
        lines.append(f"  RetrievedUpdates={status} ({reason}): {message}")
    return "\n".join(lines)


def minor_version(version: str) -> str:
    """Return the major.minor part of a version string (4.21.32 -> 4.21)."""
    return ".".join(version.split("-", maxsplit=1)[0].split(".")[:2])


def is_minor_upgrade(current_version: str, desired_version: str) -> bool:
    """Return whether moving from current to desired crosses a minor version."""
    return minor_version(current_version) != minor_version(desired_version)


def check_cluster_upgradeable(
    cluster_version: dict[str, Any], current_version: str, desired_version: str
) -> None:
    """Raise if ClusterVersion blocks a minor upgrade with Upgradeable=False.

    A missing Upgradeable condition means the cluster is upgradeable. Upgradeable=False
    only blocks minor upgrades, so z-stream upgrades are not checked.
    """
    if not is_minor_upgrade(current_version, desired_version):
        return
    upgradeable = get_condition(cluster_version, "Upgradeable")
    if upgradeable is None or upgradeable.get("status") != "False":
        return
    raise ClusterNotUpgradeableError(
        current_version,
        desired_version,
        upgradeable.get("reason") or "no reason",
        upgradeable.get("message") or "no message",
    )


def resolve_channel(
    cluster_version: dict[str, Any], current_version: str, desired_version: str
) -> str:
    """Return the update channel that offers desired_version.

    A z-stream target keeps the current channel, whatever its name. For a minor
    upgrade, keeps the prefix of the current channel (stable, fast, candidate, eus) and
    replaces its minor with the desired version's minor. The resulting channel must be
    listed in status.desired.channels for the current release.
    """
    current_channel = cluster_version.get("spec", {}).get("channel") or ""
    if not is_minor_upgrade(current_version, desired_version):
        return current_channel
    match = CHANNEL_PATTERN.match(current_channel)
    if match is None:
        raise ChannelError(
            f"Cannot derive the update channel for {desired_version} from the current "
            f"channel '{current_channel}'; expected <prefix>-<major>.<minor>, "
            "for example stable-4.20"
        )
    target_minor = minor_version(desired_version)
    if match.group("minor") == target_minor:
        return current_channel

    target_channel = f"{match.group('prefix')}-{target_minor}"
    offered = cluster_version.get("status", {}).get("desired", {}).get("channels") or []
    if target_channel not in offered:
        raise ChannelError(
            f"Channel {target_channel} is not offered for the current release. "
            f"Offered channels: {', '.join(offered) if offered else 'none'}"
        )
    return target_channel


def switch_channel(channel: str) -> None:
    """Set the ClusterVersion update channel."""
    result = run_oc_command(["oc", "adm", "upgrade", "channel", channel])
    if result.returncode != 0:
        header = f"oc adm upgrade channel {channel} failed (exit {result.returncode})"
        if result.stderr:
            log_subprocess_output(f"{header} [stderr]", result.stderr, logging.ERROR)
        if result.stdout:
            log_subprocess_output(f"{header} [stdout]", result.stdout, logging.ERROR)
        raise ChannelError(header)


def offered_versions(
    cluster_version: dict[str, Any], allow_not_recommended: bool
) -> list[str] | None:
    """Return availableUpdates, plus conditionalUpdates when allowed.

    Returns None if the update graph is unavailable.
    """
    available = get_available_versions(cluster_version)
    if available is None:
        return None
    if not allow_not_recommended:
        return available
    conditional = get_conditional_updates(cluster_version)
    return available + [v for v in conditional if v not in available]


def wait_for_channel_updates(
    channel: str, desired_version: str, allow_not_recommended: bool
) -> dict[str, Any]:
    """Return the ClusterVersion once the CVO offers desired_version after a channel switch.

    Polls until desired_version is offered or CHANNEL_SWITCH_TIMEOUT_SECONDS elapse,
    then returns the last ClusterVersion read either way. Failed reads are retried
    until the deadline.
    """
    deadline = time.time() + CHANNEL_SWITCH_TIMEOUT_SECONDS
    while True:
        try:
            cluster_version = get_cluster_version()
        except (ClusterVersionQueryError, TimeoutError) as exc:
            if time.time() >= deadline:
                raise
            logger.warning("Retrying ClusterVersion read after error: %s", exc)
            time.sleep(CHANNEL_SWITCH_POLL_SECONDS)
            continue
        offered = offered_versions(cluster_version, allow_not_recommended) or []
        if (
            cluster_version.get("spec", {}).get("channel") == channel
            and desired_version in offered
        ):
            return cluster_version
        if time.time() >= deadline:
            return cluster_version
        time.sleep(CHANNEL_SWITCH_POLL_SECONDS)


def check_version_offered(
    cluster_version: dict[str, Any], desired_version: str, allow_not_recommended: bool
) -> None:
    """Raise unless desired_version is offered by the cluster's update graph."""
    offered = offered_versions(cluster_version, allow_not_recommended)
    if offered is None:
        raise UpdateGraphUnavailableError(describe_update_source(cluster_version))
    logger.debug("Checking if desired version %s is offered", desired_version)
    if desired_version not in offered:
        raise VersionNotAvailableError(
            desired_version, offered, describe_update_source(cluster_version)
        )
    if desired_version not in (get_available_versions(cluster_version) or []):
        logger.info(
            "Version %s found in conditionalUpdates, proceeding with allow_not_recommended",
            desired_version,
        )


def ensure_channel_offers_version(
    cluster_version: dict[str, Any],
    current_version: str,
    desired_version: str,
    dry_run: bool,
    allow_not_recommended: bool,
) -> None:
    """Switch to the channel for desired_version if needed and check that it is offered.

    In dry-run mode the channel is not switched, and the offered-version check is
    skipped when a switch would be needed.
    """
    current_channel = cluster_version.get("spec", {}).get("channel") or ""
    target_channel = resolve_channel(cluster_version, current_version, desired_version)
    if target_channel != current_channel:
        if dry_run:
            logger.info(
                "DRY-RUN: would switch the update channel from %s to %s; "
                "availability of %s is not checked until the channel is switched",
                current_channel,
                target_channel,
                desired_version,
            )
            return
        logger.info(
            "Switching the update channel from %s to %s",
            current_channel,
            target_channel,
        )
        switch_channel(target_channel)
        cluster_version = wait_for_channel_updates(
            target_channel, desired_version, allow_not_recommended
        )
    check_version_offered(cluster_version, desired_version, allow_not_recommended)


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
    desired_version: str,
    timeout_minutes: int = 180,
    sleep_interval: int = 60,
    allow_not_recommended: bool = False,
) -> None:
    """Trigger Cluster upgrade and wait for it to complete.

    The timeout applies to the entire upgrade operation (both wait phases combined).
    """
    logger.info("Upgrading cluster to desired_version=%s", desired_version)

    # Calculate shared deadline for both wait operations
    deadline = time.time() + (timeout_minutes * 60)

    upgrade_command = ["oc", "adm", "upgrade", "--to", desired_version]
    if allow_not_recommended:
        upgrade_command += ["--allow-not-recommended"]

    result = run_oc_command(upgrade_command)
    if result.returncode != 0:
        header = (
            f"oc adm upgrade to {desired_version} failed (exit {result.returncode})"
        )
        if result.stderr:
            log_subprocess_output(f"{header} [stderr]", result.stderr, logging.ERROR)
        if result.stdout:
            log_subprocess_output(f"{header} [stdout]", result.stdout, logging.ERROR)
        raise RuntimeError(
            f"oc adm upgrade to {desired_version} failed (exit {result.returncode})"
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
    allow_not_recommended: bool = False,
) -> None:
    """Validate preconditions and upgrade the cluster to the version set in desired_version.

    For a minor upgrade, ClusterVersion must not report Upgradeable=False, and the update
    channel is switched to the desired minor (keeping the current channel prefix) before
    checking that the version is offered. Raises if the desired version is older than the
    current one, the update graph is unavailable, the version is not offered, or any
    ClusterOperator is not ready. In dry-run mode, nothing on the cluster is changed: the
    channel switch is only logged, and the offered-version check is skipped when a switch
    would be needed.
    """
    logger.debug(
        "reconcile() called with desired_version=%s, dry_run=%s, timeout_minutes=%d, sleep_interval=%d, allow_not_recommended=%s",
        desired_version,
        dry_run,
        timeout_minutes,
        sleep_interval,
        allow_not_recommended,
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

    cluster_version = get_cluster_version()

    check_cluster_upgradeable(cluster_version, current_version, desired_version)

    if get_available_versions(cluster_version) is None:
        raise UpdateGraphUnavailableError(describe_update_source(cluster_version))

    logger.debug("Checking cluster operators readiness...")
    ready, issues = check_cluster_operators_ready()
    if not ready:
        raise ClusterOperatorsNotReadyError(issues)

    ensure_channel_offers_version(
        cluster_version,
        current_version,
        desired_version,
        dry_run,
        allow_not_recommended,
    )

    logger.info("Upgrading cluster to %s", desired_version)

    if dry_run:
        logger.info("Execution is set to DRY-RUN. Exiting.")
        return

    upgrade_cluster(
        desired_version, timeout_minutes, sleep_interval, allow_not_recommended
    )
