from pathlib import Path
from subprocess import CompletedProcess

import pytest
from pytest_mock import MockerFixture

from enclave.environment.check_leftovers import (
    LeftoverCheckError,
    check_podman,
    check_systemd,
    check_working_dir,
    main,
)


def _proc(
    stdout: str = "", returncode: int = 0, stderr: str = ""
) -> CompletedProcess[str]:
    return CompletedProcess(
        args=[], returncode=returncode, stdout=stdout, stderr=stderr
    )


# --- _run ---


def test_run_raises_on_command_failure(mocker: MockerFixture) -> None:
    mocker.patch(
        "enclave.environment.check_leftovers.subprocess.run",
        return_value=_proc("", returncode=1, stderr="Failed to connect to bus"),
    )
    with pytest.raises(LeftoverCheckError):
        check_systemd()


# --- check_systemd ---


def testcheck_systemd_detects_units(mocker: MockerFixture) -> None:
    mocker.patch(
        "enclave.environment.check_leftovers.subprocess.run",
        return_value=_proc("metal3-bmo.service enabled"),
    )
    assert check_systemd() is True


def testcheck_systemd_no_units(mocker: MockerFixture) -> None:
    mocker.patch(
        "enclave.environment.check_leftovers.subprocess.run",
        return_value=_proc(""),
    )
    assert check_systemd() is False


def test_check_systemd_exit1_no_output_treated_as_clean(mocker: MockerFixture) -> None:
    mocker.patch(
        "enclave.environment.check_leftovers.subprocess.run",
        return_value=_proc("", returncode=1),
    )
    assert check_systemd() is False


# --- check_podman ---


def _podman_clean() -> list[CompletedProcess[str]]:
    return [_proc(""), _proc(""), _proc("")]


def testcheck_podman_detects_metal3_pod(mocker: MockerFixture) -> None:
    mocker.patch(
        "enclave.environment.check_leftovers.subprocess.run",
        side_effect=[_proc("metal3-ironic"), _proc(""), _proc("")],
    )
    assert check_podman(sudo=False) is True


def testcheck_podman_detects_quay_pod(mocker: MockerFixture) -> None:
    mocker.patch(
        "enclave.environment.check_leftovers.subprocess.run",
        side_effect=[_proc("quay-pod"), _proc(""), _proc("")],
    )
    assert check_podman(sudo=True) is True


def testcheck_podman_detects_ironic_container(mocker: MockerFixture) -> None:
    mocker.patch(
        "enclave.environment.check_leftovers.subprocess.run",
        side_effect=[_proc(""), _proc("ironic"), _proc("")],
    )
    assert check_podman(sudo=False) is True


def testcheck_podman_detects_metal3_volume(mocker: MockerFixture) -> None:
    mocker.patch(
        "enclave.environment.check_leftovers.subprocess.run",
        side_effect=[_proc(""), _proc(""), _proc("metal3-ironic-data")],
    )
    assert check_podman(sudo=False) is True


def testcheck_podman_clean(mocker: MockerFixture) -> None:
    mocker.patch(
        "enclave.environment.check_leftovers.subprocess.run",
        side_effect=_podman_clean(),
    )
    assert check_podman(sudo=False) is False


def testcheck_podman_sudo_prefixes_commands(mocker: MockerFixture) -> None:
    run_mock = mocker.patch(
        "enclave.environment.check_leftovers.subprocess.run",
        side_effect=_podman_clean(),
    )
    check_podman(sudo=True)
    for call in run_mock.call_args_list:
        assert call.args[0][0] == "sudo"


# --- check_working_dir ---


def testcheck_working_dir_none_returns_false() -> None:
    assert check_working_dir(None) is False


def testcheck_working_dir_empty_string_returns_false() -> None:
    assert check_working_dir("") is False


def testcheck_working_dir_not_a_dir() -> None:
    assert check_working_dir("/nonexistent/path/xyz") is False


def testcheck_working_dir_empty_dir(tmp_path: Path) -> None:
    assert check_working_dir(str(tmp_path)) is False


def testcheck_working_dir_non_empty(tmp_path: Path) -> None:
    (tmp_path / "some_file").write_text("data")
    assert check_working_dir(str(tmp_path)) is True


def testcheck_working_dir_logs_only_is_clean(tmp_path: Path) -> None:
    (tmp_path / "logs").mkdir()
    assert check_working_dir(str(tmp_path)) is False


def testcheck_working_dir_logs_plus_other_is_dirty(tmp_path: Path) -> None:
    (tmp_path / "logs").mkdir()
    (tmp_path / "bin").mkdir()
    assert check_working_dir(str(tmp_path)) is True


# --- main ---


def test_main_returns_false_when_clean(mocker: MockerFixture) -> None:
    mocker.patch(
        "enclave.environment.check_leftovers.check_systemd", return_value=False
    )
    mocker.patch("enclave.environment.check_leftovers.check_podman", return_value=False)
    mocker.patch(
        "enclave.environment.check_leftovers.check_working_dir", return_value=False
    )
    assert main() is False


def test_main_returns_true_when_systemd_leftovers(mocker: MockerFixture) -> None:
    mocker.patch("enclave.environment.check_leftovers.check_systemd", return_value=True)
    mocker.patch("enclave.environment.check_leftovers.check_podman", return_value=False)
    mocker.patch(
        "enclave.environment.check_leftovers.check_working_dir", return_value=False
    )
    assert main() is True


def test_main_returns_true_when_podman_leftovers(mocker: MockerFixture) -> None:
    mocker.patch(
        "enclave.environment.check_leftovers.check_systemd", return_value=False
    )
    mocker.patch(
        "enclave.environment.check_leftovers.check_podman",
        side_effect=[True, False],
    )
    mocker.patch(
        "enclave.environment.check_leftovers.check_working_dir", return_value=False
    )
    assert main() is True


def test_main_returns_true_when_working_dir_non_empty(
    mocker: MockerFixture, tmp_path: Path
) -> None:
    mocker.patch(
        "enclave.environment.check_leftovers.check_systemd", return_value=False
    )
    mocker.patch("enclave.environment.check_leftovers.check_podman", return_value=False)
    mocker.patch(
        "enclave.environment.check_leftovers.check_working_dir", return_value=True
    )
    assert main(working_dir=str(tmp_path)) is True


def test_main_runs_all_checks_even_if_first_returns_true(mocker: MockerFixture) -> None:
    systemd_mock = mocker.patch(
        "enclave.environment.check_leftovers.check_systemd", return_value=True
    )
    podman_mock = mocker.patch(
        "enclave.environment.check_leftovers.check_podman", return_value=False
    )
    wd_mock = mocker.patch(
        "enclave.environment.check_leftovers.check_working_dir", return_value=False
    )
    result = main()
    assert result is True
    systemd_mock.assert_called_once()
    assert podman_mock.call_count == 2  # called for sudo=True and sudo=False
    wd_mock.assert_called_once()
