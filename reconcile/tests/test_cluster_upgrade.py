from collections.abc import Callable
from subprocess import CompletedProcess, TimeoutExpired
from unittest.mock import MagicMock

import pytest
from pytest_mock import MockerFixture

from reconcile.cluster_upgrade import (
    ClusterOperatorsNotReadyError,
    InvalidVersionError,
    UpdateGraphUnavailableError,
    VersionDowngradeError,
    VersionNotAvailableError,
    check_cluster_operators_ready,
    get_available_versions,
    get_cluster_operators,
    get_current_version,
    parse_jsonpath_value,
    parse_version,
    reconcile,
    run_oc_command,
    semver_key,
    upgrade_cluster,
    wait_for_resource_status,
)
from reconcile.tests.fixtures import Fixtures

fxt = Fixtures("cluster_upgrade")

MODULE = "reconcile.cluster_upgrade"

OcResultFactory = Callable[..., CompletedProcess[str]]


@pytest.fixture
def oc_result() -> OcResultFactory:
    def _make(
        stdout: str = "", returncode: int = 0, stderr: str = ""
    ) -> CompletedProcess[str]:
        result: CompletedProcess[str] = CompletedProcess(args=[], returncode=returncode)
        result.stdout = stdout
        result.stderr = stderr
        return result

    return _make


# ---------------------------------------------------------------------------
# parse_jsonpath_value
# ---------------------------------------------------------------------------


def test_parse_jsonpath_value_strips_whitespace() -> None:
    assert parse_jsonpath_value("  4.20.16  ") == "4.20.16"


def test_parse_jsonpath_value_strips_single_quotes() -> None:
    assert parse_jsonpath_value("'4.20.16'") == "4.20.16"


def test_parse_jsonpath_value_strips_double_quotes() -> None:
    assert parse_jsonpath_value('"4.20.16"') == "4.20.16"


def test_parse_jsonpath_value_empty_string() -> None:
    assert parse_jsonpath_value("") == ""


# ---------------------------------------------------------------------------
# parse_version
# ---------------------------------------------------------------------------


def test_parse_version_valid_release() -> None:
    assert parse_version("4.20.16") == semver_key("4.20.16")


def test_parse_version_valid_prerelease() -> None:
    assert parse_version("4.20.0-rc1") == semver_key("4.20.0-rc1")


def test_parse_version_empty_raises() -> None:
    with pytest.raises(InvalidVersionError):
        parse_version("")


def test_parse_version_two_components_raises() -> None:
    with pytest.raises(InvalidVersionError):
        parse_version("4.20")


def test_parse_version_one_component_raises() -> None:
    with pytest.raises(InvalidVersionError):
        parse_version("4")


def test_parse_version_four_components_raises() -> None:
    with pytest.raises(InvalidVersionError):
        parse_version("4.20.16.1")


def test_parse_version_non_numeric_component_raises() -> None:
    with pytest.raises(InvalidVersionError):
        parse_version("4.a.0")


# ---------------------------------------------------------------------------
# semver_key / version ordering
# ---------------------------------------------------------------------------


def test_semver_patch_increment() -> None:
    assert semver_key("4.16.4") > semver_key("4.16.3")


def test_semver_minor_increment() -> None:
    assert semver_key("4.17.0") > semver_key("4.16.9")


def test_semver_release_beats_prerelease() -> None:
    assert semver_key("4.16.3") > semver_key("4.16.3-rc1")


def test_semver_numeric_prerelease_ordering() -> None:
    assert semver_key("4.16.3-rc10") > semver_key("4.16.3-rc9")


# ---------------------------------------------------------------------------
# run_oc_command
# ---------------------------------------------------------------------------


def test_run_oc_command_timeout(mocker: MockerFixture) -> None:
    mocker.patch(
        f"{MODULE}.subprocess.run", side_effect=TimeoutExpired(cmd="oc", timeout=60)
    )
    with pytest.raises(TimeoutError, match="timed out after 60 seconds"):
        run_oc_command(["oc", "get", "clusterversion"])


# ---------------------------------------------------------------------------
# get_current_version
# ---------------------------------------------------------------------------


def test_get_current_version_success(
    mocker: MockerFixture, oc_result: OcResultFactory
) -> None:
    mocker.patch(f"{MODULE}.run_oc_command", return_value=oc_result(stdout="'4.20.16'"))
    assert get_current_version() == "4.20.16"


def test_get_current_version_oc_failure(
    mocker: MockerFixture, oc_result: OcResultFactory
) -> None:
    mocker.patch(
        f"{MODULE}.run_oc_command",
        return_value=oc_result(stdout="", returncode=1),
    )
    with pytest.raises(RuntimeError):
        get_current_version()


def test_get_current_version_oc_failure_logs_output(
    mocker: MockerFixture, oc_result: OcResultFactory
) -> None:
    mocker.patch(
        f"{MODULE}.run_oc_command",
        return_value=oc_result(
            stdout="some output", stderr="connection refused", returncode=1
        ),
    )
    with pytest.raises(RuntimeError):
        get_current_version()


# ---------------------------------------------------------------------------
# get_available_versions
# ---------------------------------------------------------------------------

AVAILABLE_VERSIONS = ["4.20.21", "4.20.20", "4.20.19", "4.20.18", "4.20.17"]


def test_get_available_versions_from_fixture(
    mocker: MockerFixture, oc_result: OcResultFactory
) -> None:
    mocker.patch(
        f"{MODULE}.run_oc_command",
        return_value=oc_result(stdout=fxt.get_json("clusterversion.yaml")),
    )
    assert get_available_versions() == AVAILABLE_VERSIONS


def test_get_available_versions_null_returns_none(
    mocker: MockerFixture, oc_result: OcResultFactory
) -> None:
    payload = '{"status": {"availableUpdates": null}}'
    mocker.patch(f"{MODULE}.run_oc_command", return_value=oc_result(stdout=payload))
    assert get_available_versions() is None


def test_get_available_versions_oc_failure(
    mocker: MockerFixture, oc_result: OcResultFactory
) -> None:
    mocker.patch(
        f"{MODULE}.run_oc_command",
        return_value=oc_result(stdout="", returncode=1),
    )
    with pytest.raises(RuntimeError):
        get_available_versions()


def test_get_available_versions_oc_failure_logs_output(
    mocker: MockerFixture, oc_result: OcResultFactory
) -> None:
    mocker.patch(
        f"{MODULE}.run_oc_command",
        return_value=oc_result(
            stdout="some output", stderr="connection refused", returncode=1
        ),
    )
    with pytest.raises(RuntimeError):
        get_available_versions()


def test_get_available_versions_invalid_json(
    mocker: MockerFixture, oc_result: OcResultFactory
) -> None:
    mocker.patch(
        f"{MODULE}.run_oc_command",
        return_value=oc_result(stdout="not-json"),
    )
    with pytest.raises(RuntimeError):
        get_available_versions()


def test_get_available_versions_invalid_json_empty_stdout(
    mocker: MockerFixture, oc_result: OcResultFactory
) -> None:
    mocker.patch(f"{MODULE}.run_oc_command", return_value=oc_result(stdout=""))
    with pytest.raises(RuntimeError):
        get_available_versions()


# ---------------------------------------------------------------------------
# get_cluster_operators
# ---------------------------------------------------------------------------


def test_get_cluster_operators_from_fixture(
    mocker: MockerFixture, oc_result: OcResultFactory
) -> None:
    mocker.patch(
        f"{MODULE}.run_oc_command",
        return_value=oc_result(stdout=fxt.get_json("clusteroperators.yaml")),
    )
    operators = get_cluster_operators()
    assert isinstance(operators, list)
    assert len(operators) == 34


def test_get_cluster_operators_oc_failure(
    mocker: MockerFixture, oc_result: OcResultFactory
) -> None:
    mocker.patch(
        f"{MODULE}.run_oc_command",
        return_value=oc_result(stdout="", returncode=1),
    )
    with pytest.raises(RuntimeError):
        get_cluster_operators()


def test_get_cluster_operators_oc_failure_logs_output(
    mocker: MockerFixture, oc_result: OcResultFactory
) -> None:
    mocker.patch(
        f"{MODULE}.run_oc_command",
        return_value=oc_result(
            stdout="some output", stderr="connection refused", returncode=1
        ),
    )
    with pytest.raises(RuntimeError):
        get_cluster_operators()


def test_get_cluster_operators_invalid_json(
    mocker: MockerFixture, oc_result: OcResultFactory
) -> None:
    mocker.patch(f"{MODULE}.run_oc_command", return_value=oc_result(stdout="not-json"))
    with pytest.raises(RuntimeError):
        get_cluster_operators()


def test_get_cluster_operators_invalid_json_empty_stdout(
    mocker: MockerFixture, oc_result: OcResultFactory
) -> None:
    mocker.patch(f"{MODULE}.run_oc_command", return_value=oc_result(stdout=""))
    with pytest.raises(RuntimeError):
        get_cluster_operators()


# ---------------------------------------------------------------------------
# check_cluster_operators_ready
# ---------------------------------------------------------------------------


def _make_operator(name: str, conditions: list[dict]) -> dict:
    return {"metadata": {"name": name}, "status": {"conditions": conditions}}


def _healthy_conditions() -> list[dict]:
    return [
        {"type": "Degraded", "status": "False"},
        {"type": "Available", "status": "True"},
        {"type": "Upgradeable", "status": "True"},
    ]


def test_check_operators_ready_all_healthy(mocker: MockerFixture) -> None:
    mocker.patch(
        f"{MODULE}.get_cluster_operators",
        return_value=[_make_operator("auth", _healthy_conditions())],
    )
    ready, issues = check_cluster_operators_ready()
    assert ready is True
    assert issues == []


def test_check_operators_ready_degraded(mocker: MockerFixture) -> None:
    conditions = [{"type": "Degraded", "status": "True"}]
    mocker.patch(
        f"{MODULE}.get_cluster_operators",
        return_value=[_make_operator("auth", conditions)],
    )
    ready, issues = check_cluster_operators_ready()
    assert ready is False
    assert any("Degraded" in i for i in issues)


def test_check_operators_ready_not_available(mocker: MockerFixture) -> None:
    conditions = [{"type": "Available", "status": "False"}]
    mocker.patch(
        f"{MODULE}.get_cluster_operators",
        return_value=[_make_operator("auth", conditions)],
    )
    ready, issues = check_cluster_operators_ready()
    assert ready is False
    assert any("not Available" in i for i in issues)


def test_check_operators_ready_not_upgradeable(mocker: MockerFixture) -> None:
    conditions = [{"type": "Upgradeable", "status": "False"}]
    mocker.patch(
        f"{MODULE}.get_cluster_operators",
        return_value=[_make_operator("auth", conditions)],
    )
    ready, issues = check_cluster_operators_ready()
    assert ready is False
    assert any("not Upgradeable" in i for i in issues)


def test_check_operators_ready_multiple_issues(mocker: MockerFixture) -> None:
    conditions = [
        {"type": "Degraded", "status": "True"},
        {"type": "Available", "status": "False"},
    ]
    mocker.patch(
        f"{MODULE}.get_cluster_operators",
        return_value=[_make_operator("auth", conditions)],
    )
    ready, issues = check_cluster_operators_ready()
    assert ready is False
    assert len(issues) == 2


# ---------------------------------------------------------------------------
# reconcile
# ---------------------------------------------------------------------------


def _patch_reconcile_deps(
    mocker: MockerFixture, *, current: str, available: list[str] | None
) -> MagicMock:
    mocker.patch(f"{MODULE}.get_current_version", return_value=current)
    mocker.patch(f"{MODULE}.get_available_versions", return_value=available)
    mocker.patch(f"{MODULE}.check_cluster_operators_ready", return_value=(True, []))
    mock_upgrade = mocker.patch(f"{MODULE}.upgrade_cluster")
    mocker.patch(f"{MODULE}.wait_for_resource_status")
    return mock_upgrade


def test_reconcile_downgrade_raises(mocker: MockerFixture) -> None:
    _patch_reconcile_deps(mocker, current="4.20.16", available=["4.20.17"])
    with pytest.raises(VersionDowngradeError):
        reconcile("4.20.15", dry_run=False)


def test_reconcile_already_at_version_waits(mocker: MockerFixture) -> None:
    mock_upgrade = _patch_reconcile_deps(
        mocker, current="4.20.16", available=["4.20.17"]
    )
    mock_wait = mocker.patch(f"{MODULE}.wait_for_resource_status")
    reconcile("4.20.16", dry_run=False)
    mock_wait.assert_called_once()
    mock_upgrade.assert_not_called()


def test_reconcile_update_graph_unavailable(mocker: MockerFixture) -> None:
    _patch_reconcile_deps(mocker, current="4.20.16", available=None)
    with pytest.raises(UpdateGraphUnavailableError):
        reconcile("4.20.17", dry_run=False)


def test_reconcile_version_not_available(mocker: MockerFixture) -> None:
    _patch_reconcile_deps(mocker, current="4.20.16", available=["4.20.17"])
    with pytest.raises(VersionNotAvailableError):
        reconcile("4.20.99", dry_run=False)


def test_reconcile_operators_not_ready(mocker: MockerFixture) -> None:
    mocker.patch(f"{MODULE}.get_current_version", return_value="4.20.16")
    mocker.patch(f"{MODULE}.get_available_versions", return_value=["4.20.17"])
    mocker.patch(
        f"{MODULE}.check_cluster_operators_ready",
        return_value=(False, ["auth is Degraded"]),
    )
    with pytest.raises(ClusterOperatorsNotReadyError):
        reconcile("4.20.17", dry_run=False)


def test_reconcile_dry_run_skips_upgrade(mocker: MockerFixture) -> None:
    mock_upgrade = _patch_reconcile_deps(
        mocker, current="4.20.16", available=["4.20.17"]
    )
    reconcile("4.20.17", dry_run=True)
    mock_upgrade.assert_not_called()


def test_reconcile_performs_upgrade(mocker: MockerFixture) -> None:
    mock_upgrade = _patch_reconcile_deps(
        mocker, current="4.20.16", available=["4.20.17"]
    )
    reconcile("4.20.17", dry_run=False)
    mock_upgrade.assert_called_once_with("4.20.17", 180, 60)


# ---------------------------------------------------------------------------
# wait_for_resource_status
# ---------------------------------------------------------------------------


def _patch_time(mocker: MockerFixture, times: list[float]) -> MagicMock:
    return mocker.patch(f"{MODULE}.time.time", side_effect=times)


def test_wait_success_on_first_poll(
    mocker: MockerFixture, oc_result: OcResultFactory
) -> None:
    mocker.patch(f"{MODULE}.run_oc_command", return_value=oc_result(stdout="Completed"))
    wait_for_resource_status("cv", "version", "history[0].state", "Completed")


def test_wait_success_after_one_retry(
    mocker: MockerFixture, oc_result: OcResultFactory
) -> None:
    mocker.patch(
        f"{MODULE}.run_oc_command",
        side_effect=[oc_result(stdout="Pending"), oc_result(stdout="Completed")],
    )
    mock_sleep = mocker.patch(f"{MODULE}.time.sleep")
    wait_for_resource_status("cv", "version", "history[0].state", "Completed")
    mock_sleep.assert_called_once()


def test_wait_global_timeout_exceeded(
    mocker: MockerFixture, oc_result: OcResultFactory
) -> None:
    # timeout_minutes=1 → deadline = 0 + 60; third time.time() call returns 61 > 60
    _patch_time(mocker, [0, 1, 61])
    mocker.patch(f"{MODULE}.time.sleep")
    mocker.patch(f"{MODULE}.run_oc_command", return_value=oc_result(stdout="Pending"))
    with pytest.raises(TimeoutError):
        wait_for_resource_status(
            "cv", "version", "history[0].state", "Completed", timeout_minutes=1
        )


def test_wait_oc_per_call_timeout_retries(
    mocker: MockerFixture, oc_result: OcResultFactory
) -> None:
    _patch_time(mocker, [0, 1, 2])
    mocker.patch(f"{MODULE}.time.sleep")
    mocker.patch(
        f"{MODULE}.run_oc_command",
        side_effect=[TimeoutError("Command timed out"), oc_result(stdout="Completed")],
    )
    wait_for_resource_status("cv", "version", "history[0].state", "Completed")


def test_wait_oc_per_call_timeout_exceeds_global(mocker: MockerFixture) -> None:
    # deadline = 0 + 60; logger.warning() consumes one call, then the check at line 190 returns 61 > 60
    _patch_time(mocker, [0, 1, 61])
    mocker.patch(f"{MODULE}.time.sleep")
    mocker.patch(
        f"{MODULE}.run_oc_command", side_effect=TimeoutError("Command timed out")
    )
    with pytest.raises(TimeoutError):
        wait_for_resource_status(
            "cv", "version", "history[0].state", "Completed", timeout_minutes=1
        )


def test_wait_nonzero_returncode_continues(
    mocker: MockerFixture, oc_result: OcResultFactory
) -> None:
    _patch_time(mocker, [0, 1, 2, 3])
    mocker.patch(f"{MODULE}.time.sleep")
    mocker.patch(
        f"{MODULE}.run_oc_command",
        side_effect=[oc_result(stdout="", returncode=1), oc_result(stdout="Completed")],
    )
    wait_for_resource_status("cv", "version", "history[0].state", "Completed")


# ---------------------------------------------------------------------------
# upgrade_cluster
# ---------------------------------------------------------------------------


def test_upgrade_cluster_success(
    mocker: MockerFixture, oc_result: OcResultFactory
) -> None:
    _patch_time(mocker, [0, 1, 2])
    mocker.patch(f"{MODULE}.run_oc_command", return_value=oc_result(stdout="patched"))
    mock_wait = mocker.patch(f"{MODULE}.wait_for_resource_status")
    upgrade_cluster("4.20.21")
    assert mock_wait.call_count == 2
    first_call = mock_wait.call_args_list[0]
    assert first_call.args[2] == "desired.version"
    assert first_call.args[3] == "4.20.21"
    second_call = mock_wait.call_args_list[1]
    assert second_call.args[2] == "history[0].state"
    assert second_call.args[3] == "Completed"


def test_upgrade_cluster_patch_fails(
    mocker: MockerFixture, oc_result: OcResultFactory
) -> None:
    _patch_time(mocker, [0])
    mocker.patch(f"{MODULE}.run_oc_command", return_value=oc_result(returncode=1))
    mock_wait = mocker.patch(f"{MODULE}.wait_for_resource_status")
    with pytest.raises(RuntimeError):
        upgrade_cluster("4.20.21")
    mock_wait.assert_not_called()


def test_upgrade_cluster_patch_fails_logs_output(
    mocker: MockerFixture, oc_result: OcResultFactory
) -> None:
    _patch_time(mocker, [0, 1, 2])  # deadline + one time.time() per ERROR log record
    mocker.patch(
        f"{MODULE}.run_oc_command",
        return_value=oc_result(
            stdout="some output", stderr="connection refused", returncode=1
        ),
    )
    mocker.patch(f"{MODULE}.wait_for_resource_status")
    with pytest.raises(RuntimeError):
        upgrade_cluster("4.20.21")


def test_upgrade_cluster_timeout_before_first_wait(
    mocker: MockerFixture, oc_result: OcResultFactory
) -> None:
    # deadline = 0 + 60; second time.time() returns 61 → remaining_minutes ≤ 0
    _patch_time(mocker, [0, 61])
    mocker.patch(f"{MODULE}.run_oc_command", return_value=oc_result(stdout="patched"))
    mock_wait = mocker.patch(f"{MODULE}.wait_for_resource_status")
    with pytest.raises(TimeoutError):
        upgrade_cluster("4.20.21", timeout_minutes=1)
    mock_wait.assert_not_called()


def test_upgrade_cluster_timeout_before_second_wait(
    mocker: MockerFixture, oc_result: OcResultFactory
) -> None:
    # deadline = 0 + 60; third time.time() returns 61 → second remaining_minutes ≤ 0
    _patch_time(mocker, [0, 1, 61])
    mocker.patch(f"{MODULE}.run_oc_command", return_value=oc_result(stdout="patched"))
    mock_wait = mocker.patch(f"{MODULE}.wait_for_resource_status")
    with pytest.raises(TimeoutError):
        upgrade_cluster("4.20.21", timeout_minutes=1)
    mock_wait.assert_called_once()
