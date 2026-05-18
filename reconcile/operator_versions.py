import ast
import json
import logging
import subprocess
import sys
import time

logger = logging.getLogger(__name__)


def parse_jsonpath_value(raw: str) -> str:
    return raw.strip().strip("'\"")


def wait_for_resource_status(
    kind: str,
    name: str,
    namespace: str,
    status_field: str,
    desired_state: str,
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
            check=False,
        )
        if result.returncode != 0:
            stderr = (result.stderr or "").strip()
            logger.warning(
                "Failed to read %s/%s in namespace %s: %s",
                kind,
                name,
                namespace,
                stderr,
            )

        current_state = parse_jsonpath_value(result.stdout or "")
        if current_state == desired_state:
            logger.info(
                "%s/%s in namespace %s has reached status.%s=%s.",
                kind,
                name,
                namespace,
                status_field,
                desired_state,
            )
            return
        if time.time() > timeout:
            msg = (
                f"{kind}/{name} in namespace {namespace}"
                f" did not reach status.{status_field}"
                f"={desired_state}"
                f" within {timeout_minutes} minutes"
                f" (current state: {current_state})"
            )
            raise TimeoutError(msg)
        time.sleep(10)


def semver_key(
    v_string: str,
) -> tuple[tuple[int, ...], tuple[tuple[int, object], ...]]:
    main, sep, prerelease = v_string.partition("-")
    main_version = tuple(map(int, main.split(".")))

    if not sep:
        return (main_version, ((2, 0),))

    parsed = [
        (0, int(token)) if token.isdigit() else (1, token)
        for token in prerelease.split(".")
    ]
    return (main_version, tuple(parsed))


def approve_install_plans(
    dry_run: bool,
    namespace: str,
    approved_op_version_map: dict[str, str],
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
            logger.info(
                "Install plan %s is in phase %s with csvNames %s",
                install_plan_name,
                install_plan_status_phase,
                install_plan_spec_csv_names,
            )
            continue
        version_ok = True
        csv_op_name = ""
        csv_version = ""
        for csv in install_plan_spec_csv_names:
            csv_op_name, csv_version = csv.rsplit(".v", 1)
            desired_op_version = approved_op_version_map.get(csv_op_name)
            if desired_op_version is None:
                version_ok = False
                logger.info(
                    "Install plan %s includes unmanaged CSV %s. Skipping.",
                    install_plan_name,
                    csv_op_name,
                )
                break
            version_ok = semver_key(csv_version) <= semver_key(desired_op_version)
            if not version_ok:
                logger.info(
                    "Install plan %s for %s %s is not at desired version %s. Skipping.",
                    install_plan_name,
                    csv_op_name,
                    csv_version,
                    desired_op_version,
                )
                break
        if not version_ok:
            continue

        logger.info(
            "Approving InstallPlan %s for %s %s.",
            install_plan_name,
            csv_op_name,
            csv_version,
        )
        logger.info(
            "[UPDATE] %s/%s:%s",
            namespace,
            csv_op_name,
            csv_version,
        )
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
            logger.info(
                "Approved InstallPlan %s for %s %s.",
                install_plan_name,
                csv_op_name,
                csv_version,
            )


def init_ns_op_version_map(
    operators: list[dict[str, str]],
) -> dict[str, dict[str, str]]:
    ns_op_version_map: dict[str, dict[str, str]] = {}

    for op in operators:
        op_name = op["name"]
        op_version = op["version"]
        op_namespace = op["namespace"]
        op_csv_names = op.get("csvNames") or [op_name]
        for op_csv_name in op_csv_names:
            ns_op_version_map.setdefault(op_namespace, {})[op_csv_name] = (
                op_version.replace("+", "-")
            )

    return ns_op_version_map


_ARG_OPERATORS = 1
_ARG_DRY_RUN = 2


def reconcile() -> None:
    try:
        operators = ast.literal_eval(sys.argv[_ARG_OPERATORS])
    except (IndexError, ValueError, SyntaxError):
        logger.exception("Error parsing operator list")
        sys.exit(1)

    raw_dry_run = sys.argv[_ARG_DRY_RUN] if len(sys.argv) > _ARG_DRY_RUN else "False"
    dry_run = raw_dry_run.lower() in {"true", "yes"}

    ns_op_version_map = init_ns_op_version_map(operators)
    for op_namespace, op_name_version_map in ns_op_version_map.items():
        approve_install_plans(dry_run, op_namespace, op_name_version_map)
        for op_name, op_version in op_name_version_map.items():
            logger.info(
                "Waiting for CSV %s.v%s in namespace %s"
                " to reach status.phase=Succeeded.",
                op_name,
                op_version,
                op_namespace,
            )
            if not dry_run:
                wait_for_resource_status(
                    "clusterserviceversion.operators.coreos.com",
                    f"{op_name}.v{op_version}",
                    op_namespace,
                    "phase",
                    "Succeeded",
                )
            logger.info(
                "CSV %s.v%s in namespace %s reached status.phase=Succeeded.",
                op_name,
                op_version,
                op_namespace,
            )


if __name__ == "__main__":
    reconcile()
