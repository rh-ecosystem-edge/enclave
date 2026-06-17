import os
from subprocess import TimeoutExpired
from unittest.mock import MagicMock

import pytest
from pytest_mock import MockerFixture

from enclave.utils import (
    KubeconfigNotFoundError,
    parse_jsonpath_value,
    run_oc_command,
    semver_key,
    setup_kubeconfig,
    wait_for_resource_status,
)
from tests.fixtures import OcResultFactory


def _patch_time(mocker: MockerFixture, times: list[float]) -> MagicMock:
    """Stub time.time() in utils with a predetermined sequence of values."""
    return mocker.patch("enclave.utils.time.time", side_effect=times)


# ---------------------------------------------------------------------------
# parse_jsonpath_value
# ---------------------------------------------------------------------------


def test_parse_jsonpath_value_strips_whitespace() -> None:
    """Leading and trailing whitespace is removed from the value."""
    assert parse_jsonpath_value("  4.20.16  ") == "4.20.16"


def test_parse_jsonpath_value_strips_single_quotes() -> None:
    """Surrounding single quotes (as emitted by some JSONPath tools) are stripped."""
    assert parse_jsonpath_value("'4.20.16'") == "4.20.16"


def test_parse_jsonpath_value_strips_double_quotes() -> None:
    """Surrounding double quotes are stripped."""
    assert parse_jsonpath_value('"4.20.16"') == "4.20.16"


def test_parse_jsonpath_value_empty_string() -> None:
    """An empty input returns an empty string without error."""
    assert parse_jsonpath_value("") == ""


# ---------------------------------------------------------------------------
# semver_key / version ordering
# ---------------------------------------------------------------------------


def test_semver_patch_increment() -> None:
    """A higher patch number sorts above a lower one."""
    assert semver_key("4.16.4") > semver_key("4.16.3")


def test_semver_minor_increment() -> None:
    """A higher minor number sorts above any patch number in a lower minor."""
    assert semver_key("4.17.0") > semver_key("4.16.9")


def test_semver_release_beats_prerelease() -> None:
    """A release version ranks above the equivalent pre-release."""
    assert semver_key("4.16.3") > semver_key("4.16.3-rc1")


def test_semver_numeric_prerelease_ordering() -> None:
    """Pre-release numbers are compared numerically, not lexicographically."""
    assert semver_key("4.16.3-rc10") > semver_key("4.16.3-rc9")


# ---------------------------------------------------------------------------
# run_oc_command
# ---------------------------------------------------------------------------


def test_run_oc_command_timeout(mocker: MockerFixture) -> None:
    """A subprocess.TimeoutExpired is converted to TimeoutError with a clear message."""
    mocker.patch(
        "enclave.utils.subprocess.run",
        side_effect=TimeoutExpired(cmd="oc", timeout=60),
    )
    with pytest.raises(TimeoutError, match="timed out after 60 seconds"):
        run_oc_command(["oc", "get", "clusterversion"])


# ---------------------------------------------------------------------------
# wait_for_resource_status
# ---------------------------------------------------------------------------


def test_wait_success_on_first_poll(
    mocker: MockerFixture, oc_result: OcResultFactory
) -> None:
    """Returns immediately when the desired state is observed on the first poll."""
    mocker.patch(
        "enclave.utils.run_oc_command", return_value=oc_result(stdout="Completed")
    )
    wait_for_resource_status("cv", "version", "history[0].state", "Completed")


def test_wait_success_after_one_retry(
    mocker: MockerFixture, oc_result: OcResultFactory
) -> None:
    """Sleeps and retries when the first poll returns a non-matching state."""
    mocker.patch(
        "enclave.utils.run_oc_command",
        side_effect=[oc_result(stdout="Pending"), oc_result(stdout="Completed")],
    )
    mock_sleep = mocker.patch("enclave.utils.time.sleep")
    wait_for_resource_status("cv", "version", "history[0].state", "Completed")
    mock_sleep.assert_called_once()


def test_wait_global_timeout_exceeded(
    mocker: MockerFixture, oc_result: OcResultFactory
) -> None:
    """Raises TimeoutError when the global deadline is exceeded before the state matches."""
    # timeout_minutes=1 → deadline = 0 + 60; third time.time() call returns 61 > 60
    _patch_time(mocker, [0, 1, 61])
    mocker.patch("enclave.utils.time.sleep")
    mocker.patch(
        "enclave.utils.run_oc_command", return_value=oc_result(stdout="Pending")
    )
    with pytest.raises(TimeoutError):
        wait_for_resource_status(
            "cv", "version", "history[0].state", "Completed", timeout_minutes=1
        )


def test_wait_oc_per_call_timeout_retries(
    mocker: MockerFixture, oc_result: OcResultFactory
) -> None:
    """A per-call oc timeout is logged and retried if the global deadline allows it."""
    _patch_time(mocker, [0, 1, 2])
    mocker.patch("enclave.utils.time.sleep")
    mocker.patch(
        "enclave.utils.run_oc_command",
        side_effect=[TimeoutError("Command timed out"), oc_result(stdout="Completed")],
    )
    wait_for_resource_status("cv", "version", "history[0].state", "Completed")


def test_wait_oc_per_call_timeout_exceeds_global(mocker: MockerFixture) -> None:
    """A per-call oc timeout raises TimeoutError when the global deadline is also exceeded."""
    # deadline = 0 + 60; warning log consumes one call, then the global-timeout check returns 61 > 60
    _patch_time(mocker, [0, 1, 61])
    mocker.patch("enclave.utils.time.sleep")
    mocker.patch(
        "enclave.utils.run_oc_command", side_effect=TimeoutError("Command timed out")
    )
    with pytest.raises(TimeoutError):
        wait_for_resource_status(
            "cv", "version", "history[0].state", "Completed", timeout_minutes=1
        )


def test_wait_global_timeout_at_exact_deadline(
    mocker: MockerFixture, oc_result: OcResultFactory
) -> None:
    """Raises TimeoutError when time.time() equals the deadline exactly (>= boundary)."""
    _patch_time(mocker, [0, 0, 0, 60])
    mocker.patch("enclave.utils.time.sleep")
    mocker.patch(
        "enclave.utils.run_oc_command", return_value=oc_result(stdout="Pending")
    )
    with pytest.raises(TimeoutError):
        wait_for_resource_status(
            "cv", "version", "history[0].state", "Completed", timeout_minutes=1
        )


def test_wait_oc_per_call_timeout_at_exact_deadline(mocker: MockerFixture) -> None:
    """Raises TimeoutError when a per-call oc timeout lands exactly on the deadline (>= boundary)."""
    # deadline=60; logger.warning consumes one time.time() call per iteration via LogRecord creation:
    # setup(0), iter1-warn(0), iter1-guard(0→sleep), iter2-warn(60), iter2-guard(60→raise)
    _patch_time(mocker, [0, 0, 0, 60, 60])
    mocker.patch("enclave.utils.time.sleep")
    mocker.patch(
        "enclave.utils.run_oc_command", side_effect=TimeoutError("Command timed out")
    )
    with pytest.raises(TimeoutError):
        wait_for_resource_status(
            "cv", "version", "history[0].state", "Completed", timeout_minutes=1
        )


def test_wait_nonzero_returncode_continues(
    mocker: MockerFixture, oc_result: OcResultFactory
) -> None:
    """A non-zero oc exit code is logged but polling continues rather than raising."""
    _patch_time(mocker, [0, 1, 2, 3])
    mocker.patch("enclave.utils.time.sleep")
    mocker.patch(
        "enclave.utils.run_oc_command",
        side_effect=[oc_result(stdout="", returncode=1), oc_result(stdout="Completed")],
    )
    wait_for_resource_status("cv", "version", "history[0].state", "Completed")


def test_wait_with_namespace_includes_n_flag(
    mocker: MockerFixture, oc_result: OcResultFactory
) -> None:
    """When namespace is provided the oc get command includes -n <namespace>."""
    mock_run = mocker.patch(
        "enclave.utils.run_oc_command", return_value=oc_result(stdout="Succeeded")
    )
    wait_for_resource_status(
        "clusterserviceversion.operators.coreos.com",
        "quay-operator.v3.15.3",
        "phase",
        "Succeeded",
        namespace="quay-enterprise",
    )
    args = mock_run.call_args.args[0]
    assert "-n" in args
    assert "quay-enterprise" in args


def test_wait_without_namespace_omits_n_flag(
    mocker: MockerFixture, oc_result: OcResultFactory
) -> None:
    """When namespace is omitted the oc get command has no -n flag."""
    mock_run = mocker.patch(
        "enclave.utils.run_oc_command", return_value=oc_result(stdout="Completed")
    )
    wait_for_resource_status("cv", "version", "history[0].state", "Completed")
    args = mock_run.call_args.args[0]
    assert "-n" not in args


# ---------------------------------------------------------------------------
# setup_kubeconfig
# ---------------------------------------------------------------------------


def test_setup_kubeconfig_noop_when_already_set(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Does nothing when KUBECONFIG is already set in the environment."""
    monkeypatch.setenv("KUBECONFIG", "/existing/kubeconfig")
    before = os.environ["KUBECONFIG"]
    setup_kubeconfig()
    assert os.environ["KUBECONFIG"] == before


def test_setup_kubeconfig_uses_fallback(
    monkeypatch: pytest.MonkeyPatch, mocker: MockerFixture
) -> None:
    """Sets KUBECONFIG from the fallback path when it exists and KUBECONFIG is unset."""
    monkeypatch.delenv("KUBECONFIG", raising=False)
    mocker.patch("enclave.utils.Path.exists", return_value=True)
    setup_kubeconfig()
    assert "KUBECONFIG" in os.environ
    assert os.environ["KUBECONFIG"].endswith("kubeconfig")


def test_setup_kubeconfig_raises_when_neither_set(
    monkeypatch: pytest.MonkeyPatch, mocker: MockerFixture
) -> None:
    """Raises KubeconfigNotFoundError with a helpful message when nothing is available."""
    monkeypatch.delenv("KUBECONFIG", raising=False)
    mocker.patch("enclave.utils.Path.exists", return_value=False)
    with pytest.raises(KubeconfigNotFoundError, match="KUBECONFIG"):
        setup_kubeconfig()


def test_setup_kubeconfig_empty_string_treated_as_unset(
    monkeypatch: pytest.MonkeyPatch, mocker: MockerFixture
) -> None:
    """An empty KUBECONFIG string falls through to the fallback path."""
    monkeypatch.setenv("KUBECONFIG", "")
    mocker.patch("enclave.utils.Path.exists", return_value=False)
    with pytest.raises(KubeconfigNotFoundError):
        setup_kubeconfig()
