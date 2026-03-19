import sys
import subprocess
import json
import time
import logging


def parse_jsonpath_value(raw: str) -> str:
    return raw.strip().strip("'\"")


def wait_for_resource_status(
    kind: str, name: str, status_field: str, desired_state: str
) -> None:
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
        if parse_jsonpath_value(result.stdout or "") == desired_state:
            logging.info(
                f"{kind}/{name} has reached status.{status_field}={desired_state}."
            )
            return
        if time.time() > timeout:
            raise TimeoutError(
                f"{kind}/{name} did not reach status.{status_field}={desired_state} within 3 hours"
            )
        time.sleep(10)


def get_current_version():
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
    return parse_jsonpath_value(result.stdout)


def semver_key(v_string):
    # Separate the numeric part from the tag (e.g., '1.2.0-rc1' -> ['1.2.0', 'rc1'])
    parts = v_string.split("-", 1)
    main_version = tuple(map(int, parts[0].split(".")))

    if len(parts) == 1:
        # No suffix: Use a high-value marker so 1.2.0 > 1.2.0-rc1
        return (main_version, (float("inf"),))
    else:
        # Has suffix: Return the numeric part and the string tag
        return (main_version, (0, parts[1]))


def get_available_versions():
    result = subprocess.run(
        ["oc", "get", "clusterversion.config.openshift.io", "version", "-o", "json"],
        capture_output=True,
        text=True,
    )
    return [
        update["version"]
        for update in json.loads(result.stdout)["status"]["availableUpdates"]
    ]


def get_cluster_operators():
    result = subprocess.run(
        ["oc", "get", "clusteroperator.config.openshift.io", "-o", "json"],
        capture_output=True,
        text=True,
    )
    return json.loads(result.stdout)["items"]


def check_cluster_operators_ready() -> bool:
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


def upgrade_cluster(desired_version: str):
    subprocess.run(
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
    wait_for_resource_status(
        "clusterversion.config.openshift.io", "version", "desired.version", desired_version
    )
    wait_for_resource_status(
        "clusterversion.config.openshift.io", "version", "history[0].state", "Completed"
    )


def main():
    desired_version = sys.argv[1]

    raw_dry_run = sys.argv[2] if len(sys.argv) > 2 else "False"
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
        print(f"Upgrade to {desired_version} is Completed.")
        return

    available_versions = get_available_versions()
    if desired_version not in available_versions:
        raise Exception(
            f"Desired version {desired_version} is not available. Available versions: {available_versions}."
        )

    if not check_cluster_operators_ready():
        raise Exception(f"At least one Cluster Operator is not ready.")

    if dry_run:
        logging.info(f"Execution is set to DRY-RUN. Exiting.")
        print(f"Cluster upgrade to version {desired_version} is ready to be performed.")
        return

    upgrade_cluster(desired_version)
    print(f"Upgrade to {desired_version} is Completed.")


if __name__ == "__main__":
    main()
