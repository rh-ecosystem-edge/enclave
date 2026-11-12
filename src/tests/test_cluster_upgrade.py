"""Unit tests for enclave.reconcile.cluster_upgrade."""

import json
from collections.abc import Sequence
from typing import Any
from unittest.mock import MagicMock

import pytest
from pytest_mock import MockerFixture

from enclave.reconcile.cluster_upgrade import (
    DEFAULT_UPSTREAM,
    ChannelError,
    ClusterNotUpgradeableError,
    ClusterOperatorsNotReadyError,
    ClusterVersionQueryError,
    InvalidVersionError,
    UpdateGraphUnavailableError,
    VersionDowngradeError,
    VersionNotAvailableError,
    check_cluster_operators_ready,
    check_cluster_upgradeable,
    describe_update_source,
    get_available_versions,
    get_cluster_operators,
    get_cluster_version,
    get_condition,
    get_conditional_updates,
    get_current_version,
    is_minor_upgrade,
    minor_version,
    parse_version,
    reconcile,
    resolve_channel,
    semver_key,
    switch_channel,
    upgrade_cluster,
    wait_for_channel_updates,
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
# ClusterVersion helpers
# ---------------------------------------------------------------------------

AVAILABLE_VERSIONS = ["4.20.21", "4.20.20", "4.20.19", "4.20.18", "4.20.17"]
OSUS_URL = "https://osus.example/api/upgrades_info/v1/graph"
STABLE_CHANNELS = ["stable-4.20", "stable-4.21", "fast-4.20", "fast-4.21", "eus-4.20"]


def _cv(
    *,
    version: str = "4.20.16",
    channel: str | None = "stable-4.20",
    upstream: str | None = OSUS_URL,
    available: Sequence[str] | None = ("4.20.17",),
    conditional: list[str] | None = None,
    channels: list[str] | None = None,
    conditions: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Build a minimal ClusterVersion dict for reconcile tests."""
    spec: dict[str, Any] = {}
    if channel is not None:
        spec["channel"] = channel
    if upstream is not None:
        spec["upstream"] = upstream
    status: dict[str, Any] = {
        "desired": {
            "version": version,
            "channels": STABLE_CHANNELS if channels is None else channels,
        },
        "availableUpdates": None
        if available is None
        else [{"version": v} for v in available],
        "conditionalUpdates": [{"release": {"version": v}} for v in conditional or []],
        "conditions": conditions
        if conditions is not None
        else [{"type": "RetrievedUpdates", "status": "True"}],
    }
    return {"spec": spec, "status": status}


def test_get_cluster_version_from_fixture(
    mocker: MockerFixture, oc_result: OcResultFactory
) -> None:
    """Real cluster data is returned as a dict."""
    mocker.patch(
        "enclave.reconcile.cluster_upgrade.run_oc_command",
        return_value=oc_result(stdout=fxt.get_json("clusterversion.yaml")),
    )
    assert get_cluster_version()["spec"]["channel"] == "stable-4.20"


def test_get_cluster_version_oc_failure(
    mocker: MockerFixture, oc_result: OcResultFactory
) -> None:
    """A non-zero exit code from oc raises ClusterVersionQueryError."""
    mocker.patch(
        "enclave.reconcile.cluster_upgrade.run_oc_command",
        return_value=oc_result(
            stdout="some output", stderr="connection refused", returncode=1
        ),
    )
    with pytest.raises(ClusterVersionQueryError):
        get_cluster_version()


@pytest.mark.parametrize("stdout", ["null", "[]", '"text"'])
def test_get_cluster_version_non_object_json(
    mocker: MockerFixture, oc_result: OcResultFactory, stdout: str
) -> None:
    """Valid JSON that isn't an object raises ClusterVersionQueryError."""
    mocker.patch(
        "enclave.reconcile.cluster_upgrade.run_oc_command",
        return_value=oc_result(stdout=stdout),
    )
    with pytest.raises(ClusterVersionQueryError):
        get_cluster_version()


@pytest.mark.parametrize("stdout", ["not-json", ""])
def test_get_cluster_version_invalid_json(
    mocker: MockerFixture, oc_result: OcResultFactory, stdout: str
) -> None:
    """Malformed or empty stdout raises ClusterVersionQueryError."""
    mocker.patch(
        "enclave.reconcile.cluster_upgrade.run_oc_command",
        return_value=oc_result(stdout=stdout),
    )
    with pytest.raises(ClusterVersionQueryError):
        get_cluster_version()


def test_get_available_versions_from_fixture() -> None:
    """Real cluster data returns the expected sorted list of available versions."""
    cluster_version = json.loads(fxt.get_json("clusterversion.yaml"))
    assert get_available_versions(cluster_version) == AVAILABLE_VERSIONS


def test_get_available_versions_null_returns_none() -> None:
    """A null availableUpdates field (update graph unavailable) returns None."""
    assert get_available_versions({"status": {"availableUpdates": None}}) is None


def test_get_conditional_updates_from_fixture() -> None:
    """Real cluster data returns conditional update versions."""
    versions = get_conditional_updates(json.loads(fxt.get_json("clusterversion.yaml")))
    assert "4.20.19" in versions
    assert "4.20.18" in versions


@pytest.mark.parametrize("status", [{"conditionalUpdates": None}, {}])
def test_get_conditional_updates_null_or_missing_returns_empty(
    status: dict[str, Any],
) -> None:
    """A null or missing conditionalUpdates field returns an empty list."""
    assert get_conditional_updates({"status": status}) == []


def test_get_condition_present_and_absent() -> None:
    """get_condition returns the matching condition or None."""
    cluster_version = _cv(conditions=[{"type": "Upgradeable", "status": "False"}])
    assert get_condition(cluster_version, "Upgradeable") == {
        "type": "Upgradeable",
        "status": "False",
    }
    assert get_condition(cluster_version, "RetrievedUpdates") is None


def test_describe_update_source_default_upstream_and_failure() -> None:
    """An empty upstream is shown as the default, with the RetrievedUpdates failure."""
    cluster_version = _cv(
        upstream=None,
        conditions=[
            {
                "type": "RetrievedUpdates",
                "status": "False",
                "reason": "RemoteFailed",
                "message": "dial tcp api.openshift.com: connection timed out",
            }
        ],
    )
    text = describe_update_source(cluster_version)
    assert f"upstream: {DEFAULT_UPSTREAM}" in text
    assert "channel: stable-4.20" in text
    assert "RetrievedUpdates=False (RemoteFailed): dial tcp" in text


def test_describe_update_source_without_condition() -> None:
    """A missing RetrievedUpdates condition and channel are reported explicitly."""
    text = describe_update_source(_cv(channel=None, conditions=[]))
    assert f"upstream: {OSUS_URL}" in text
    assert "channel: <none>" in text
    assert "RetrievedUpdates: <condition not reported>" in text


@pytest.mark.parametrize(
    ("version", "expected"),
    [("4.21.32", "4.21"), ("4.20.0-rc.1", "4.20"), ("4.22.1", "4.22")],
)
def test_minor_version(version: str, expected: str) -> None:
    """minor_version returns major.minor, ignoring any prerelease suffix."""
    assert minor_version(version) == expected


def test_is_minor_upgrade() -> None:
    """Crossing a minor version is a minor upgrade; a z-stream is not."""
    assert is_minor_upgrade("4.20.32", "4.21.32")
    assert not is_minor_upgrade("4.20.32", "4.20.46")


# ---------------------------------------------------------------------------
# check_cluster_upgradeable
# ---------------------------------------------------------------------------

ADMIN_ACK = {
    "type": "Upgradeable",
    "status": "False",
    "reason": "AdminAckRequired",
    "message": "4.21 will require Sigstore signatures",
}


def test_check_cluster_upgradeable_blocks_minor_upgrade() -> None:
    """Upgradeable=False blocks a minor upgrade and reports the CVO reason and message."""
    with pytest.raises(ClusterNotUpgradeableError) as exc_info:
        check_cluster_upgradeable(_cv(conditions=[ADMIN_ACK]), "4.20.32", "4.21.32")
    assert exc_info.value.reason == "AdminAckRequired"
    assert "Sigstore signatures" in str(exc_info.value)


def test_check_cluster_upgradeable_ignores_z_stream() -> None:
    """Upgradeable=False does not block a z-stream upgrade."""
    check_cluster_upgradeable(_cv(conditions=[ADMIN_ACK]), "4.20.32", "4.20.46")


@pytest.mark.parametrize(
    "conditions", [[], [{"type": "Upgradeable", "status": "True"}]]
)
def test_check_cluster_upgradeable_absent_or_true(
    conditions: list[dict[str, Any]],
) -> None:
    """A missing or True Upgradeable condition allows a minor upgrade."""
    check_cluster_upgradeable(_cv(conditions=conditions), "4.20.32", "4.21.32")


# ---------------------------------------------------------------------------
# resolve_channel / switch_channel / wait_for_channel_updates
# ---------------------------------------------------------------------------


def test_resolve_channel_same_minor_keeps_channel() -> None:
    """A z-stream target keeps the current channel."""
    assert resolve_channel(_cv(), "4.20.16", "4.20.46") == "stable-4.20"


@pytest.mark.parametrize(
    ("channel", "expected"),
    [("stable-4.20", "stable-4.21"), ("fast-4.20", "fast-4.21")],
)
def test_resolve_channel_minor_upgrade_keeps_prefix(
    channel: str, expected: str
) -> None:
    """A minor upgrade moves to the target minor with the same channel prefix."""
    assert resolve_channel(_cv(channel=channel), "4.20.32", "4.21.32") == expected


def test_resolve_channel_not_offered_raises() -> None:
    """A target channel missing from status.desired.channels raises ChannelError."""
    with pytest.raises(ChannelError) as exc_info:
        resolve_channel(_cv(channel="eus-4.20"), "4.20.32", "4.21.32")
    assert "eus-4.21 is not offered" in str(exc_info.value)
    assert "stable-4.21" in str(exc_info.value)


@pytest.mark.parametrize("channel", [None, "", "custom"])
def test_resolve_channel_unparseable_raises(channel: str | None) -> None:
    """A missing or non-standard current channel raises ChannelError."""
    with pytest.raises(ChannelError):
        resolve_channel(_cv(channel=channel), "4.20.32", "4.21.32")


@pytest.mark.parametrize("channel", ["custom", ""])
def test_resolve_channel_z_stream_keeps_any_channel(channel: str) -> None:
    """A z-stream target keeps a non-standard or empty channel without parsing it."""
    assert resolve_channel(_cv(channel=channel), "4.20.16", "4.20.17") == channel


def test_switch_channel_runs_oc(
    mocker: MockerFixture, oc_result: OcResultFactory
) -> None:
    """switch_channel runs oc adm upgrade channel with the target channel."""
    mock_oc = mocker.patch(
        "enclave.reconcile.cluster_upgrade.run_oc_command",
        return_value=oc_result(stdout=""),
    )
    switch_channel("stable-4.21")
    mock_oc.assert_called_once_with(["oc", "adm", "upgrade", "channel", "stable-4.21"])


def test_switch_channel_failure_raises(
    mocker: MockerFixture, oc_result: OcResultFactory
) -> None:
    """A failing oc adm upgrade channel raises ChannelError."""
    mocker.patch(
        "enclave.reconcile.cluster_upgrade.run_oc_command",
        return_value=oc_result(stderr="forbidden", returncode=1),
    )
    with pytest.raises(ChannelError):
        switch_channel("stable-4.21")


def test_wait_for_channel_updates_polls_until_offered(mocker: MockerFixture) -> None:
    """Polls until the CVO reports the new channel and offers the target."""
    before = _cv(channel="stable-4.20", available=["4.20.46"])
    after = _cv(channel="stable-4.21", available=["4.20.46", "4.21.32"])
    mocker.patch(
        "enclave.reconcile.cluster_upgrade.get_cluster_version",
        side_effect=[before, after],
    )
    mocker.patch("enclave.reconcile.cluster_upgrade.time.time", return_value=0)
    mock_sleep = mocker.patch("enclave.reconcile.cluster_upgrade.time.sleep")
    assert (
        wait_for_channel_updates("stable-4.21", "4.21.32", allow_not_recommended=False)
        is after
    )
    mock_sleep.assert_called_once()


def test_wait_for_channel_updates_retries_failed_reads(mocker: MockerFixture) -> None:
    """A failed ClusterVersion read is retried before the deadline."""
    after = _cv(channel="stable-4.21", available=["4.21.32"])
    mocker.patch(
        "enclave.reconcile.cluster_upgrade.get_cluster_version",
        side_effect=[ClusterVersionQueryError("boom"), TimeoutError("slow"), after],
    )
    mocker.patch("enclave.reconcile.cluster_upgrade.time.time", return_value=0)
    mock_sleep = mocker.patch("enclave.reconcile.cluster_upgrade.time.sleep")
    assert (
        wait_for_channel_updates("stable-4.21", "4.21.32", allow_not_recommended=False)
        is after
    )
    assert mock_sleep.call_count == 2


def test_wait_for_channel_updates_raises_failed_read_after_deadline(
    mocker: MockerFixture,
) -> None:
    """A failed read past the deadline is raised."""
    mocker.patch(
        "enclave.reconcile.cluster_upgrade.get_cluster_version",
        side_effect=ClusterVersionQueryError("boom"),
    )
    mocker.patch("enclave.reconcile.cluster_upgrade.time.time", side_effect=[0, 10_000])
    mocker.patch("enclave.reconcile.cluster_upgrade.time.sleep")
    with pytest.raises(ClusterVersionQueryError):
        wait_for_channel_updates("stable-4.21", "4.21.32", allow_not_recommended=False)


def test_wait_for_channel_updates_returns_last_on_timeout(
    mocker: MockerFixture,
) -> None:
    """After the timeout, the last ClusterVersion read is returned without raising."""
    stale = _cv(channel="stable-4.21", available=["4.20.46"])
    mocker.patch(
        "enclave.reconcile.cluster_upgrade.get_cluster_version", return_value=stale
    )
    mocker.patch(
        "enclave.reconcile.cluster_upgrade.time.time", side_effect=[0, 1, 10_000]
    )
    mocker.patch("enclave.reconcile.cluster_upgrade.time.sleep")
    assert (
        wait_for_channel_updates("stable-4.21", "4.21.32", allow_not_recommended=False)
        is stale
    )


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
    mocker: MockerFixture,
    *,
    current: str,
    cluster_versions: list[dict[str, Any]],
    operators_ready: bool = True,
) -> dict[str, MagicMock]:
    """Stub reconcile's cluster interactions; return the mocks callers assert on."""
    mocker.patch(
        "enclave.reconcile.cluster_upgrade.get_current_version", return_value=current
    )
    mocker.patch(
        "enclave.reconcile.cluster_upgrade.get_cluster_version",
        side_effect=cluster_versions,
    )
    mocker.patch(
        "enclave.reconcile.cluster_upgrade.check_cluster_operators_ready",
        return_value=(operators_ready, [] if operators_ready else ["auth is Degraded"]),
    )
    mocker.patch("enclave.reconcile.cluster_upgrade.time.sleep")
    return {
        "upgrade": mocker.patch("enclave.reconcile.cluster_upgrade.upgrade_cluster"),
        "wait": mocker.patch(
            "enclave.reconcile.cluster_upgrade.wait_for_resource_status"
        ),
        "switch": mocker.patch("enclave.reconcile.cluster_upgrade.switch_channel"),
    }


def test_reconcile_downgrade_raises(mocker: MockerFixture) -> None:
    """Requesting a version older than the current one raises VersionDowngradeError."""
    _patch_reconcile_deps(mocker, current="4.20.16", cluster_versions=[_cv()])
    with pytest.raises(VersionDowngradeError):
        reconcile("4.20.15", dry_run=False)


def test_reconcile_already_at_version_waits(mocker: MockerFixture) -> None:
    """If the cluster is already at the desired version, wait is called but upgrade is skipped."""
    mocks = _patch_reconcile_deps(mocker, current="4.20.16", cluster_versions=[_cv()])
    reconcile("4.20.16", dry_run=False)
    mocks["wait"].assert_called_once()
    mocks["upgrade"].assert_not_called()


def test_reconcile_update_graph_unavailable_reports_source(
    mocker: MockerFixture,
) -> None:
    """A null graph raises UpdateGraphUnavailableError with the upstream and CVO message."""
    cluster_version = _cv(
        upstream=None,
        available=None,
        conditions=[
            {
                "type": "RetrievedUpdates",
                "status": "False",
                "reason": "RemoteFailed",
                "message": "dial tcp api.openshift.com:443: connect: connection timed out",
            }
        ],
    )
    _patch_reconcile_deps(mocker, current="4.20.16", cluster_versions=[cluster_version])
    with pytest.raises(UpdateGraphUnavailableError) as exc_info:
        reconcile("4.20.17", dry_run=False)
    assert DEFAULT_UPSTREAM in str(exc_info.value)
    assert "RetrievedUpdates=False (RemoteFailed)" in str(exc_info.value)


def test_reconcile_version_not_available_reports_channel(
    mocker: MockerFixture,
) -> None:
    """A target absent from the offered list raises VersionNotAvailableError with the channel."""
    _patch_reconcile_deps(mocker, current="4.20.16", cluster_versions=[_cv()])
    with pytest.raises(VersionNotAvailableError) as exc_info:
        reconcile("4.20.99", dry_run=False)
    assert "Available versions: 4.20.17" in str(exc_info.value)
    assert "channel: stable-4.20" in str(exc_info.value)


def test_reconcile_operators_not_ready(mocker: MockerFixture) -> None:
    """Unready cluster operators raise ClusterOperatorsNotReadyError before attempting an upgrade."""
    mocks = _patch_reconcile_deps(
        mocker, current="4.20.16", cluster_versions=[_cv()], operators_ready=False
    )
    with pytest.raises(ClusterOperatorsNotReadyError):
        reconcile("4.20.17", dry_run=False)
    mocks["upgrade"].assert_not_called()


def test_reconcile_dry_run_skips_upgrade(mocker: MockerFixture) -> None:
    """dry_run=True skips the upgrade_cluster call entirely."""
    mocks = _patch_reconcile_deps(mocker, current="4.20.16", cluster_versions=[_cv()])
    reconcile("4.20.17", dry_run=True)
    mocks["upgrade"].assert_not_called()


def test_reconcile_performs_z_stream_upgrade(mocker: MockerFixture) -> None:
    """A z-stream target triggers upgrade_cluster without switching the channel."""
    mocks = _patch_reconcile_deps(mocker, current="4.20.16", cluster_versions=[_cv()])
    reconcile("4.20.17", dry_run=False)
    mocks["switch"].assert_not_called()
    allow_not_recommended = False
    mocks["upgrade"].assert_called_once_with("4.20.17", 180, 60, allow_not_recommended)


def test_reconcile_allow_not_recommended_checks_conditional_updates(
    mocker: MockerFixture,
) -> None:
    """When allow_not_recommended is True and version not in availableUpdates, check conditionalUpdates."""
    cluster_version = _cv(available=["4.20.17"], conditional=["4.20.18", "4.20.19"])
    mocks = _patch_reconcile_deps(
        mocker, current="4.20.16", cluster_versions=[cluster_version]
    )
    reconcile("4.20.18", dry_run=False, allow_not_recommended=True)
    allow_not_recommended = True
    mocks["upgrade"].assert_called_once_with("4.20.18", 180, 60, allow_not_recommended)


def test_reconcile_allow_not_recommended_rejects_version_not_in_either_list(
    mocker: MockerFixture,
) -> None:
    """When version is in neither availableUpdates nor conditionalUpdates, it's rejected."""
    cluster_version = _cv(available=["4.20.17"], conditional=["4.20.18"])
    _patch_reconcile_deps(mocker, current="4.20.16", cluster_versions=[cluster_version])
    with pytest.raises(VersionNotAvailableError) as exc_info:
        reconcile("4.20.99", dry_run=False, allow_not_recommended=True)
    assert "4.20.17" in str(exc_info.value)
    assert "4.20.18" in str(exc_info.value)


def test_reconcile_conditional_update_not_allowed_by_default(
    mocker: MockerFixture,
) -> None:
    """Without allow_not_recommended, a conditional-only version is not offered."""
    cluster_version = _cv(available=["4.20.17"], conditional=["4.20.18"])
    _patch_reconcile_deps(mocker, current="4.20.16", cluster_versions=[cluster_version])
    with pytest.raises(VersionNotAvailableError):
        reconcile("4.20.18", dry_run=False)


def test_reconcile_minor_upgrade_blocked_by_upgradeable_false(
    mocker: MockerFixture,
) -> None:
    """Upgradeable=False stops a minor upgrade before the channel is touched."""
    cluster_version = _cv(
        version="4.20.32", available=["4.20.46"], conditions=[ADMIN_ACK]
    )
    mocks = _patch_reconcile_deps(
        mocker, current="4.20.32", cluster_versions=[cluster_version]
    )
    with pytest.raises(ClusterNotUpgradeableError):
        reconcile("4.21.32", dry_run=False)
    mocks["switch"].assert_not_called()
    mocks["upgrade"].assert_not_called()


def test_reconcile_minor_upgrade_switches_channel(mocker: MockerFixture) -> None:
    """A minor upgrade switches to the target channel, then upgrades."""
    before = _cv(version="4.20.32", available=["4.20.46"])
    after = _cv(
        version="4.20.32", channel="stable-4.21", available=["4.20.46", "4.21.32"]
    )
    mocks = _patch_reconcile_deps(
        mocker, current="4.20.32", cluster_versions=[before, after]
    )
    reconcile("4.21.32", dry_run=False)
    mocks["switch"].assert_called_once_with("stable-4.21")
    allow_not_recommended = False
    mocks["upgrade"].assert_called_once_with("4.21.32", 180, 60, allow_not_recommended)


@pytest.mark.parametrize("channel", ["custom", "", None])
def test_reconcile_z_stream_keeps_channel(
    mocker: MockerFixture, channel: str | None
) -> None:
    """A z-stream upgrade with a custom, empty or absent channel doesn't switch it."""
    mocks = _patch_reconcile_deps(
        mocker, current="4.20.16", cluster_versions=[_cv(channel=channel)]
    )
    reconcile("4.20.17", dry_run=False)
    mocks["switch"].assert_not_called()
    mocks["upgrade"].assert_called_once()


def test_reconcile_z_stream_dry_run_absent_channel_checks_offered(
    mocker: MockerFixture,
) -> None:
    """A z-stream dry run with no channel still checks that the target is offered."""
    _patch_reconcile_deps(
        mocker, current="4.20.16", cluster_versions=[_cv(channel=None)]
    )
    with pytest.raises(VersionNotAvailableError):
        reconcile("4.20.99", dry_run=True)


def test_reconcile_operators_not_ready_before_channel_switch(
    mocker: MockerFixture,
) -> None:
    """Unready operators stop a minor upgrade before the channel is switched."""
    before = _cv(version="4.20.32", available=["4.20.46"])
    mocks = _patch_reconcile_deps(
        mocker, current="4.20.32", cluster_versions=[before], operators_ready=False
    )
    with pytest.raises(ClusterOperatorsNotReadyError):
        reconcile("4.21.32", dry_run=False)
    mocks["switch"].assert_not_called()


def test_reconcile_minor_upgrade_dry_run_does_not_switch(
    mocker: MockerFixture,
) -> None:
    """In dry-run, a needed channel switch is only logged and nothing is changed."""
    before = _cv(version="4.20.32", available=["4.20.46"])
    mocks = _patch_reconcile_deps(mocker, current="4.20.32", cluster_versions=[before])
    reconcile("4.21.32", dry_run=True)
    mocks["switch"].assert_not_called()
    mocks["upgrade"].assert_not_called()


def test_reconcile_minor_upgrade_not_offered_after_switch(
    mocker: MockerFixture,
) -> None:
    """If the target is still not offered after the switch, the channel is reported."""
    before = _cv(version="4.20.32", available=["4.20.46"])
    stale = _cv(version="4.20.32", channel="stable-4.21", available=["4.20.46"])
    mocks = _patch_reconcile_deps(
        mocker, current="4.20.32", cluster_versions=[before, stale, stale]
    )
    mocker.patch(
        "enclave.reconcile.cluster_upgrade.time.time", side_effect=[0, 1, 10_000]
    )
    with pytest.raises(VersionNotAvailableError) as exc_info:
        reconcile("4.21.32", dry_run=False)
    assert "channel: stable-4.21" in str(exc_info.value)
    mocks["upgrade"].assert_not_called()


def test_reconcile_minor_upgrade_channel_not_offered(mocker: MockerFixture) -> None:
    """A minor upgrade whose target channel is not offered raises ChannelError."""
    cluster_version = _cv(version="4.20.32", channel="eus-4.20", available=["4.20.46"])
    mocks = _patch_reconcile_deps(
        mocker, current="4.20.32", cluster_versions=[cluster_version]
    )
    with pytest.raises(ChannelError):
        reconcile("4.21.32", dry_run=False)
    mocks["switch"].assert_not_called()


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
