from unittest.mock import MagicMock

import pytest
from pytest_mock import MockerFixture

from enclave.reconcile.cluster_upgrade import (
    ClusterOperatorsNotReadyError,
    InvalidVersionError,
    UpdateGraphUnavailableError,
    VersionDowngradeError,
    VersionNotAvailableError,
    check_cluster_operators_ready,
    get_available_versions,
    get_cluster_operators,
    get_current_version,
    parse_version,
    reconcile,
    semver_key,
    upgrade_cluster,
)
from tests.fixtures import Fixtures, OcResultFactory

fxt = Fixtures("cluster_upgrade")


# ---------------------------------------------------------------------------
# parse_version
# ---------------------------------------------------------------------------


def test_parse_version_valid_release() -> None:
    """A well-formed release version string returns the corresponding semver_key."""
    assert parse_version("4.20.16") == semver_key("4.20.16")


def test_parse_version_valid_prerelease() -> None:
    """A pre-release version string (with dash suffix) returns its semver_key."""
    assert parse_version("4.20.0-rc1") == semver_key("4.20.0-rc1")


def test_parse_version_empty_raises() -> None:
    """An empty string raises InvalidVersionError."""
    with pytest.raises(InvalidVersionError):
        parse_version("")


def test_parse_version_two_components_raises() -> None:
    """A two-component string like '4.20' raises InvalidVersionError."""
    with pytest.raises(InvalidVersionError):
        parse_version("4.20")


def test_parse_version_one_component_raises() -> None:
    """A single-component string raises InvalidVersionError."""
    with pytest.raises(InvalidVersionError):
        parse_version("4")


def test_parse_version_four_components_raises() -> None:
    """A four-component string raises InvalidVersionError."""
    with pytest.raises(InvalidVersionError):
        parse_version("4.20.16.1")


def test_parse_version_non_numeric_component_raises() -> None:
    """A non-numeric version component raises InvalidVersionError."""
    with pytest.raises(InvalidVersionError):
        parse_version("4.a.0")


# ---------------------------------------------------------------------------
# get_current_version
# ---------------------------------------------------------------------------


def test_get_current_version_success(
    mocker: MockerFixture, oc_result: OcResultFactory
) -> None:
    """Quoted output from oc is stripped and returned as a plain version string."""
    mocker.patch(
        "enclave.reconcile.cluster_upgrade.run_oc_command",
        return_value=oc_result(stdout="'4.20.16'"),
    )
    assert get_current_version() == "4.20.16"


def test_get_current_version_oc_failure(
    mocker: MockerFixture, oc_result: OcResultFactory
) -> None:
    """A non-zero exit code from oc raises RuntimeError."""
    mocker.patch(
        "enclave.reconcile.cluster_upgrade.run_oc_command",
        return_value=oc_result(stdout="", returncode=1),
    )
    with pytest.raises(RuntimeError):
        get_current_version()


def test_get_current_version_oc_failure_logs_output(
    mocker: MockerFixture, oc_result: OcResultFactory
) -> None:
    """stderr is captured and logged before raising RuntimeError on failure."""
    mocker.patch(
        "enclave.reconcile.cluster_upgrade.run_oc_command",
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
    """Real cluster data returns the expected sorted list of available versions."""
    mocker.patch(
        "enclave.reconcile.cluster_upgrade.run_oc_command",
        return_value=oc_result(stdout=fxt.get_json("clusterversion.yaml")),
    )
    assert get_available_versions() == AVAILABLE_VERSIONS


def test_get_available_versions_null_returns_none(
    mocker: MockerFixture, oc_result: OcResultFactory
) -> None:
    """A null availableUpdates field (update graph unavailable) returns None."""
    payload = '{"status": {"availableUpdates": null}}'
    mocker.patch(
        "enclave.reconcile.cluster_upgrade.run_oc_command",
        return_value=oc_result(stdout=payload),
    )
    assert get_available_versions() is None


def test_get_available_versions_oc_failure(
    mocker: MockerFixture, oc_result: OcResultFactory
) -> None:
    """A non-zero exit code from oc raises RuntimeError."""
    mocker.patch(
        "enclave.reconcile.cluster_upgrade.run_oc_command",
        return_value=oc_result(stdout="", returncode=1),
    )
    with pytest.raises(RuntimeError):
        get_available_versions()


def test_get_available_versions_oc_failure_logs_output(
    mocker: MockerFixture, oc_result: OcResultFactory
) -> None:
    """stderr is captured and logged before raising RuntimeError on failure."""
    mocker.patch(
        "enclave.reconcile.cluster_upgrade.run_oc_command",
        return_value=oc_result(
            stdout="some output", stderr="connection refused", returncode=1
        ),
    )
    with pytest.raises(RuntimeError):
        get_available_versions()


def test_get_available_versions_invalid_json(
    mocker: MockerFixture, oc_result: OcResultFactory
) -> None:
    """Malformed JSON stdout raises RuntimeError."""
    mocker.patch(
        "enclave.reconcile.cluster_upgrade.run_oc_command",
        return_value=oc_result(stdout="not-json"),
    )
    with pytest.raises(RuntimeError):
        get_available_versions()


def test_get_available_versions_invalid_json_empty_stdout(
    mocker: MockerFixture, oc_result: OcResultFactory
) -> None:
    """Empty stdout (no JSON at all) raises RuntimeError."""
    mocker.patch(
        "enclave.reconcile.cluster_upgrade.run_oc_command",
        return_value=oc_result(stdout=""),
    )
    with pytest.raises(RuntimeError):
        get_available_versions()


# ---------------------------------------------------------------------------
# get_cluster_operators
# ---------------------------------------------------------------------------


def test_get_cluster_operators_from_fixture(
    mocker: MockerFixture, oc_result: OcResultFactory
) -> None:
    """Real cluster data returns the expected list of 34 operator dicts."""
    mocker.patch(
        "enclave.reconcile.cluster_upgrade.run_oc_command",
        return_value=oc_result(stdout=fxt.get_json("clusteroperators.yaml")),
    )
    operators = get_cluster_operators()
    assert isinstance(operators, list)
    assert len(operators) == 34


def test_get_cluster_operators_oc_failure(
    mocker: MockerFixture, oc_result: OcResultFactory
) -> None:
    """A non-zero exit code from oc raises RuntimeError."""
    mocker.patch(
        "enclave.reconcile.cluster_upgrade.run_oc_command",
        return_value=oc_result(stdout="", returncode=1),
    )
    with pytest.raises(RuntimeError):
        get_cluster_operators()


def test_get_cluster_operators_oc_failure_logs_output(
    mocker: MockerFixture, oc_result: OcResultFactory
) -> None:
    """stderr is captured and logged before raising RuntimeError on failure."""
    mocker.patch(
        "enclave.reconcile.cluster_upgrade.run_oc_command",
        return_value=oc_result(
            stdout="some output", stderr="connection refused", returncode=1
        ),
    )
    with pytest.raises(RuntimeError):
        get_cluster_operators()


def test_get_cluster_operators_invalid_json(
    mocker: MockerFixture, oc_result: OcResultFactory
) -> None:
    """Malformed JSON stdout raises RuntimeError."""
    mocker.patch(
        "enclave.reconcile.cluster_upgrade.run_oc_command",
        return_value=oc_result(stdout="not-json"),
    )
    with pytest.raises(RuntimeError):
        get_cluster_operators()


def test_get_cluster_operators_invalid_json_empty_stdout(
    mocker: MockerFixture, oc_result: OcResultFactory
) -> None:
    """Empty stdout (no JSON at all) raises RuntimeError."""
    mocker.patch(
        "enclave.reconcile.cluster_upgrade.run_oc_command",
        return_value=oc_result(stdout=""),
    )
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
    """All healthy conditions returns (True, [])."""
    mocker.patch(
        "enclave.reconcile.cluster_upgrade.get_cluster_operators",
        return_value=[_make_operator("auth", _healthy_conditions())],
    )
    ready, issues = check_cluster_operators_ready()
    assert ready is True
    assert issues == []


def test_check_operators_ready_degraded(mocker: MockerFixture) -> None:
    """Degraded=True returns (False, issues) with 'Degraded' in the issue message."""
    conditions = [{"type": "Degraded", "status": "True"}]
    mocker.patch(
        "enclave.reconcile.cluster_upgrade.get_cluster_operators",
        return_value=[_make_operator("auth", conditions)],
    )
    ready, issues = check_cluster_operators_ready()
    assert ready is False
    assert any("Degraded" in i for i in issues)


def test_check_operators_ready_not_available(mocker: MockerFixture) -> None:
    """Available=False returns (False, issues) with 'not Available' in the issue message."""
    conditions = [{"type": "Available", "status": "False"}]
    mocker.patch(
        "enclave.reconcile.cluster_upgrade.get_cluster_operators",
        return_value=[_make_operator("auth", conditions)],
    )
    ready, issues = check_cluster_operators_ready()
    assert ready is False
    assert any("not Available" in i for i in issues)


def test_check_operators_ready_not_upgradeable(mocker: MockerFixture) -> None:
    """Upgradeable=False returns (False, issues) with 'not Upgradeable' in the issue message."""
    conditions = [{"type": "Upgradeable", "status": "False"}]
    mocker.patch(
        "enclave.reconcile.cluster_upgrade.get_cluster_operators",
        return_value=[_make_operator("auth", conditions)],
    )
    ready, issues = check_cluster_operators_ready()
    assert ready is False
    assert any("not Upgradeable" in i for i in issues)


def test_check_operators_ready_multiple_issues(mocker: MockerFixture) -> None:
    """Multiple bad conditions on a single operator accumulate multiple issue strings."""
    conditions = [
        {"type": "Degraded", "status": "True"},
        {"type": "Available", "status": "False"},
    ]
    mocker.patch(
        "enclave.reconcile.cluster_upgrade.get_cluster_operators",
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
    mocker.patch(
        "enclave.reconcile.cluster_upgrade.get_current_version", return_value=current
    )
    mocker.patch(
        "enclave.reconcile.cluster_upgrade.get_available_versions",
        return_value=available,
    )
    mocker.patch(
        "enclave.reconcile.cluster_upgrade.check_cluster_operators_ready",
        return_value=(True, []),
    )
    mock_upgrade = mocker.patch("enclave.reconcile.cluster_upgrade.upgrade_cluster")
    mocker.patch("enclave.reconcile.cluster_upgrade.wait_for_resource_status")
    return mock_upgrade


def test_reconcile_downgrade_raises(mocker: MockerFixture) -> None:
    """Requesting a version older than the current one raises VersionDowngradeError."""
    _patch_reconcile_deps(mocker, current="4.20.16", available=["4.20.17"])
    with pytest.raises(VersionDowngradeError):
        reconcile("4.20.15", dry_run=False)


def test_reconcile_already_at_version_waits(mocker: MockerFixture) -> None:
    """If the cluster is already at the desired version, wait is called but upgrade is skipped."""
    mock_upgrade = _patch_reconcile_deps(
        mocker, current="4.20.16", available=["4.20.17"]
    )
    mock_wait = mocker.patch(
        "enclave.reconcile.cluster_upgrade.wait_for_resource_status"
    )
    reconcile("4.20.16", dry_run=False)
    mock_wait.assert_called_once()
    mock_upgrade.assert_not_called()


def test_reconcile_update_graph_unavailable(mocker: MockerFixture) -> None:
    """None availableUpdates (update graph unavailable) raises UpdateGraphUnavailableError."""
    _patch_reconcile_deps(mocker, current="4.20.16", available=None)
    with pytest.raises(UpdateGraphUnavailableError):
        reconcile("4.20.17", dry_run=False)


def test_reconcile_version_not_available(mocker: MockerFixture) -> None:
    """A target version absent from the available list raises VersionNotAvailableError."""
    _patch_reconcile_deps(mocker, current="4.20.16", available=["4.20.17"])
    with pytest.raises(VersionNotAvailableError):
        reconcile("4.20.99", dry_run=False)


def test_reconcile_operators_not_ready(mocker: MockerFixture) -> None:
    """Unready cluster operators raise ClusterOperatorsNotReadyError before attempting an upgrade."""
    mocker.patch(
        "enclave.reconcile.cluster_upgrade.get_current_version", return_value="4.20.16"
    )
    mocker.patch(
        "enclave.reconcile.cluster_upgrade.get_available_versions",
        return_value=["4.20.17"],
    )
    mocker.patch(
        "enclave.reconcile.cluster_upgrade.check_cluster_operators_ready",
        return_value=(False, ["auth is Degraded"]),
    )
    with pytest.raises(ClusterOperatorsNotReadyError):
        reconcile("4.20.17", dry_run=False)


def test_reconcile_dry_run_skips_upgrade(mocker: MockerFixture) -> None:
    """dry_run=True skips the upgrade_cluster call entirely."""
    mock_upgrade = _patch_reconcile_deps(
        mocker, current="4.20.16", available=["4.20.17"]
    )
    reconcile("4.20.17", dry_run=True)
    mock_upgrade.assert_not_called()


def test_reconcile_performs_upgrade(mocker: MockerFixture) -> None:
    """A valid target version triggers upgrade_cluster with the version and default timeouts."""
    mock_upgrade = _patch_reconcile_deps(
        mocker, current="4.20.16", available=["4.20.17"]
    )
    reconcile("4.20.17", dry_run=False)
    mock_upgrade.assert_called_once_with("4.20.17", 180, 60)


# ---------------------------------------------------------------------------
# upgrade_cluster
# ---------------------------------------------------------------------------


def _patch_time(mocker: MockerFixture, times: list[float]) -> MagicMock:
    """Stub time.time() in reconcile.cluster_upgrade with a predetermined sequence of values."""
    return mocker.patch(
        "enclave.reconcile.cluster_upgrade.time.time", side_effect=times
    )


def test_upgrade_cluster_success(
    mocker: MockerFixture, oc_result: OcResultFactory
) -> None:
    """A successful oc patch triggers two wait calls: first for desired.version, then history[0].state."""
    _patch_time(mocker, [0, 1, 2])
    mocker.patch(
        "enclave.reconcile.cluster_upgrade.run_oc_command",
        return_value=oc_result(stdout="patched"),
    )
    mock_wait = mocker.patch(
        "enclave.reconcile.cluster_upgrade.wait_for_resource_status"
    )
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
    """A non-zero oc patch exit code raises RuntimeError without calling wait."""
    _patch_time(mocker, [0])
    mocker.patch(
        "enclave.reconcile.cluster_upgrade.run_oc_command",
        return_value=oc_result(returncode=1),
    )
    mock_wait = mocker.patch(
        "enclave.reconcile.cluster_upgrade.wait_for_resource_status"
    )
    with pytest.raises(RuntimeError):
        upgrade_cluster("4.20.21")
    mock_wait.assert_not_called()


def test_upgrade_cluster_patch_fails_logs_output(
    mocker: MockerFixture, oc_result: OcResultFactory
) -> None:
    """stderr is captured and logged before raising RuntimeError on patch failure."""
    _patch_time(mocker, [0, 1, 2])  # deadline + one time.time() per ERROR log record
    mocker.patch(
        "enclave.reconcile.cluster_upgrade.run_oc_command",
        return_value=oc_result(
            stdout="some output", stderr="connection refused", returncode=1
        ),
    )
    mocker.patch("enclave.reconcile.cluster_upgrade.wait_for_resource_status")
    with pytest.raises(RuntimeError):
        upgrade_cluster("4.20.21")


def test_upgrade_cluster_timeout_before_first_wait(
    mocker: MockerFixture, oc_result: OcResultFactory
) -> None:
    """Global deadline exceeded before the first wait raises TimeoutError without calling wait."""
    # deadline = 0 + 60; second time.time() returns 61 → remaining_minutes ≤ 0
    _patch_time(mocker, [0, 61])
    mocker.patch(
        "enclave.reconcile.cluster_upgrade.run_oc_command",
        return_value=oc_result(stdout="patched"),
    )
    mock_wait = mocker.patch(
        "enclave.reconcile.cluster_upgrade.wait_for_resource_status"
    )
    with pytest.raises(TimeoutError):
        upgrade_cluster("4.20.21", timeout_minutes=1)
    mock_wait.assert_not_called()


def test_upgrade_cluster_timeout_before_second_wait(
    mocker: MockerFixture, oc_result: OcResultFactory
) -> None:
    """Global deadline exceeded before the second wait raises TimeoutError after the first wait completes."""
    # deadline = 0 + 60; third time.time() returns 61 → second remaining_minutes ≤ 0
    _patch_time(mocker, [0, 1, 61])
    mocker.patch(
        "enclave.reconcile.cluster_upgrade.run_oc_command",
        return_value=oc_result(stdout="patched"),
    )
    mock_wait = mocker.patch(
        "enclave.reconcile.cluster_upgrade.wait_for_resource_status"
    )
    with pytest.raises(TimeoutError):
        upgrade_cluster("4.20.21", timeout_minutes=1)
    mock_wait.assert_called_once()
