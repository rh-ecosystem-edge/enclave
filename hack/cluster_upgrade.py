#!/usr/bin/env python3

import json
import logging
import os
import re
import subprocess
import sys
import time
from typing import Any, Optional

# This script is meant to be run under python3.9

def parse_jsonpath_value(raw: str) -> str:
    """Strip surrounding whitespace and quotes from a JSONPath output value."""
    return raw.strip().strip("'\"")


def wait_for_resource_status(
    kind: str, name: str, status_field: str, desired_state: str
) -> None:
    """Poll a resource's status field until it reaches the desired state or 3 hours elapse."""
    timeout = time.time() + (3 * 60 * 60)  # 3 hours
    while True:
        result = subprocess.run(
            [
                "oc",
                "get",
                kind,
                name,
                "-o",
                f"jsonpath='{{.status.{status_field}}}'",
            ],
            capture_output=True,
            text=True,
        )
        if result.returncode != 0:
            logging.warning(
                "oc get %s/%s failed (exit %d): %s",
                kind,
                name,
                result.returncode,
                result.stderr.strip() or "<no stderr>",
            )
        current_state = parse_jsonpath_value(result.stdout or "")
        if current_state == desired_state:
            logging.info(
                f"{kind}/{name} has reached status.{status_field}={desired_state}."
            )
            return
        if time.time() > timeout:
            raise TimeoutError(
                f"{kind}/{name} did not reach status.{status_field}={desired_state} within 3 hours"
                f" (last observed: {current_state!r})"
            )
        time.sleep(10)


def get_current_version() -> str:
    """Return the cluster's current desired version from ClusterVersion."""
    result = subprocess.run(
        [
            "oc",
            "get",
            "clusterversion.config.openshift.io",
            "version",
            "-o",
            "jsonpath='{.status.desired.version}'",
        ],
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        raise RuntimeError(
            f"oc get clusterversion failed (exit {result.returncode}):"
            f" stderr={result.stderr.strip()!r} stdout={result.stdout.strip()!r}"
        )
    return parse_jsonpath_value(result.stdout)


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


def get_available_versions() -> Optional[list[str]]:
    """Return the list of versions the cluster can upgrade to, or None if the update graph is unavailable."""
    result = subprocess.run(
        ["oc", "get", "clusterversion.config.openshift.io", "version", "-o", "json"],
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        raise RuntimeError(
            f"oc get clusterversion failed (exit {result.returncode}):"
            f" stderr={result.stderr.strip()!r} stdout={result.stdout.strip()!r}"
        )
    try:
        raw_json = json.loads(result.stdout)
    except json.JSONDecodeError as exc:
        raise RuntimeError(
            f"oc get clusterversion returned invalid JSON: {exc};"
            f" stdout={result.stdout.strip()!r}"
        ) from exc
    raw = raw_json.get("status", {}).get("availableUpdates")
    if raw is None:
        return None
    return [update["version"] for update in raw]


def get_cluster_operators() -> list[dict[str, Any]]:
    """Return all ClusterOperator objects from the cluster."""
    result = subprocess.run(
        ["oc", "get", "clusteroperator.config.openshift.io", "-o", "json"],
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        raise RuntimeError(
            f"oc get clusteroperator failed (exit {result.returncode}):"
            f" stderr={result.stderr.strip()!r} stdout={result.stdout.strip()!r}"
        )
    try:
        raw_json = json.loads(result.stdout)
    except json.JSONDecodeError as exc:
        raise RuntimeError(
            f"oc get clusteroperator returned invalid JSON: {exc};"
            f" stdout={result.stdout.strip()!r}"
        ) from exc
    return raw_json["items"]


def check_cluster_operators_ready() -> bool:
    """Return True if all ClusterOperators are Available, not Degraded, and Upgradeable."""
    cluster_operators = get_cluster_operators()
    cluster_operators_success = True
    for co in cluster_operators:
        co_name = co["metadata"]["name"]
        co_status_conditions = co["status"]["conditions"]
        for condition in co_status_conditions:
            condition_status = condition["status"]
            condition_type = condition["type"]
            if condition_type == "Degraded" and condition_status == "True":
                logging.error(f"Cluster Operator {co_name} is Degraded.")
                cluster_operators_success = False
            if condition_type == "Available" and condition_status == "False":
                logging.error(f"Cluster Operator {co_name} is not Available.")
                cluster_operators_success = False
            if condition_type == "Upgradeable" and condition_status == "False":
                logging.error(f"Cluster Operator {co_name} is not Upgradeable.")
                cluster_operators_success = False
    return cluster_operators_success


def upgrade_cluster(desired_version: str) -> None:
    """Patch ClusterVersion to trigger an upgrade and wait for it to complete."""
    logging.info(f"Patching clusterversion to desired_version={desired_version}")
    result = subprocess.run(
        [
            "oc",
            "patch",
            "clusterversion.config.openshift.io",
            "version",
            "--type",
            "merge",
            "-p",
            f'{{"spec": {{"desiredUpdate": {{"version": "{desired_version}"}}}}}}',
        ],
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        logging.error(
            "oc patch clusterversion to %s failed (exit %d): stderr=%r stdout=%r",
            desired_version,
            result.returncode,
            result.stderr.strip(),
            result.stdout.strip(),
        )
        raise RuntimeError(
            f"oc patch clusterversion to {desired_version} failed (exit {result.returncode})"
        )
    wait_for_resource_status(
        "clusterversion.config.openshift.io",
        "version",
        "desired.version",
        desired_version,
    )
    wait_for_resource_status(
        "clusterversion.config.openshift.io", "version", "history[0].state", "Completed"
    )


def write_status_report(message: str) -> None:
    """Write message to the Tekton result file path set in STATUS_REPORT_PATH, if present."""
    path = os.environ.get("STATUS_REPORT_PATH")
    if path:
        with open(path, "w") as f:
            f.write(message)


def main() -> None:
    """Validate preconditions and upgrade the cluster to the version set in OCP_VERSION.

    Reads OCP_VERSION and DRY_RUN from environment variables. Raises if the desired
    version is older than the current one, not listed in available updates, or if any
    ClusterOperator is not ready. In dry-run mode, stops before applying the upgrade.
    """
    desired_version = os.environ["OCP_VERSION"]

    raw_dry_run = os.environ.get("DRY_RUN", "false")
    dry_run = raw_dry_run.lower() in ("true", "yes")

    current_version = get_current_version()

    if semver_key(desired_version) < semver_key(current_version):
        raise Exception(
            f"Current version {current_version} is newer than desired version {desired_version}."
        )

    if current_version == desired_version:
        logging.info(
            f"Cluster is already at or moving towards version {desired_version}."
        )
        wait_for_resource_status(
            "clusterversion.config.openshift.io",
            "version",
            "history[0].state",
            "Completed",
        )
        write_status_report(f"Upgrade to {desired_version} is Completed.")
        return

    available_versions = get_available_versions()
    if available_versions is None:
        raise Exception("clusterversion availableUpdates is null (update graph not fetched).")
    elif desired_version not in available_versions:
        raise Exception(
            f"Desired version {desired_version} is not available. Available versions: {available_versions}."
        )

    if not check_cluster_operators_ready():
        raise Exception("At least one Cluster Operator is not ready.")

    if dry_run:
        logging.info("Execution is set to DRY-RUN. Exiting.")
        write_status_report(f"Cluster upgrade to version {desired_version} is ready to be performed.")
        return

    upgrade_cluster(desired_version)
    write_status_report(f"Upgrade to {desired_version} is Completed.")


if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)-8s %(message)s",
        datefmt="%Y-%m-%dT%H:%M:%S",
    )
    main()
