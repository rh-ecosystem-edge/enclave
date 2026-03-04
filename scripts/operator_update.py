import ast
import json
import subprocess
import sys
import time


def parse_jsonpath_value(raw: str) -> str:
    return raw.strip().strip("'\"")


def wait_for_resource_status(
    kind: str, name: str, namespace: str, status_field: str, desired_state: str
) -> None:
    timeout_minutes = 30
    timeout = time.time() + (timeout_minutes * 60)
    while True:
        result = subprocess.run(
            [
                "oc",
                "get",
                kind,
                name,
                "-n",
                namespace,
                "-o",
                f"jsonpath='{{.status.{status_field}}}'",
            ],
            capture_output=True,
            text=True,
        )
        current_state = parse_jsonpath_value(result.stdout or "")
        if current_state == desired_state:
            print(
                f"{kind}/{name} in namespace {namespace} has reached status.{status_field}={desired_state}."
            )
            return
        if time.time() > timeout:
            raise TimeoutError(
                f"{kind}/{name} in namespace {namespace} did not reach status.{status_field}={desired_state} within {timeout_minutes} minutes (current state: {current_state})"
            )
        time.sleep(10)


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


def approve_install_plans(
    dry_run: bool, namespace: str, approved_op_version_map: dict[str, str]
) -> None:
    result = subprocess.run(
        [
            "oc",
            "get",
            "installplan.operators.coreos.com",
            "-n",
            namespace,
            "-o",
            "json",
        ],
        capture_output=True,
        check=True,
    ).stdout
    install_plans = json.loads(result)["items"]
    for install_plan in install_plans:
        install_plan_name = install_plan["metadata"]["name"]
        install_plan_status_phase = install_plan["status"]["phase"]
        install_plan_spec_csv_names = install_plan["spec"]["clusterServiceVersionNames"]
        if install_plan_status_phase != "RequiresApproval":
            print(
                f"Install plan {install_plan_name} is in phase {install_plan_status_phase} with csvNames {install_plan_spec_csv_names}"
            )
            continue
        version_ok = True
        for csv in install_plan_spec_csv_names:
            csv_op_name, csv_version = csv.split(".v")
            desired_op_version = approved_op_version_map.get(csv_op_name)
            version_ok = semver_key(csv_version) <= semver_key(desired_op_version)
            if not version_ok:
                print(
                    f"Install plan {install_plan_name} for {csv_op_name} {csv_version} is not at a desired version {desired_op_version}. Skipping."
                )
                break
        if not version_ok:
            continue

        print(
            f"Approving InstallPlan {install_plan_name} for {csv_op_name} {csv_version}."
        )
        print(f"[UPDATE] {namespace}/{csv_op_name}:{csv_version}")
        if not dry_run:
            subprocess.run(
                [
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
                ],
                capture_output=True,
                check=True,
            )
            print(
                f"Approved InstallPlan {install_plan_name} for {csv_op_name} {csv_version}."
            )


def init_ns_op_version_map(
    operators: list[dict[str, str]],
) -> dict[str, dict[str, str]]:
    ns_op_version_map: dict[str, dict[str, str]] = {}

    for op in operators:
        op_name = op["name"]
        op_version = op["version"]
        op_namespace = op["namespace"]
        if op_namespace == "openshift-operators":
            continue
        op_csv_name = op.get("csvName")
        ns_op_version_map.setdefault(op_namespace, {})[
            op_csv_name or op_name
        ] = op_version

    return ns_op_version_map


def main():
    try:
        operators = ast.literal_eval(sys.argv[1])
    except (IndexError, ValueError, SyntaxError) as e:
        print(f"Error parsing operator list: {e}")
        sys.exit(1)

    raw_dry_run = sys.argv[2] if len(sys.argv) > 2 else "False"
    dry_run = raw_dry_run.lower() in ("true", "yes")

    ns_op_version_map = init_ns_op_version_map(operators)
    for op_namespace, op_name_version_map in ns_op_version_map.items():
        approve_install_plans(dry_run, op_namespace, op_name_version_map)
        for op_name, op_version in op_name_version_map.items():
            print(
                f"Waiting for CSV {op_name}.v{op_version} in namespace {op_namespace} to reach status.phase=Succeeded."
            )
            if not dry_run:
                wait_for_resource_status(
                    "clusterserviceversion.operators.coreos.com",
                    f"{op_name}.v{op_version}",
                    op_namespace,
                    "phase",
                    "Succeeded",
                )
            print(
                f"CSV {op_name}.v{op_version} in namespace {op_namespace} reached status.phase=Succeeded."
            )


if __name__ == "__main__":
    main()
