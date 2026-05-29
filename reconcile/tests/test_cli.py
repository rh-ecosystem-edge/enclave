import pathlib

import yaml
from click.testing import CliRunner
from pytest_mock import MockerFixture

from reconcile.cli import cli


def test_cli_help() -> None:
    result = CliRunner().invoke(cli, ["--help"])
    assert result.exit_code == 0
    assert "Reconcile CLI" in result.output


def test_operator_versions_help() -> None:
    result = CliRunner().invoke(cli, ["operator-versions", "--help"])
    assert result.exit_code == 0
    assert "--name" in result.output
    assert "--version" in result.output
    assert "--namespace" in result.output
    assert "--csv-name" in result.output
    assert "--dry-run" in result.output
    assert "--use-defaults" in result.output
    assert "--operators" not in result.output


def test_mgmt_cluster_version_help() -> None:
    result = CliRunner().invoke(cli, ["mgmt-cluster-version", "--help"])
    assert result.exit_code == 0
    assert "--version" in result.output
    assert "--use-defaults" in result.output
    assert "--timeout-minutes" in result.output
    assert "--sleep-interval" in result.output


def test_log_level_option() -> None:
    result = CliRunner().invoke(cli, ["--log-level", "DEBUG", "--help"])
    assert result.exit_code == 0


def test_invalid_log_level() -> None:
    result = CliRunner().invoke(cli, ["--log-level", "INVALID"])
    assert result.exit_code != 0


def test_operator_versions_csv_name_defaults_to_name(mocker: MockerFixture) -> None:
    mock_reconcile = mocker.patch("reconcile.cli.operator_versions_reconcile")
    dry_run = True
    result = CliRunner().invoke(
        cli,
        [
            "operator-versions",
            "--name",
            "quay-operator",
            "--version",
            "3.15.3",
            "--namespace",
            "quay-enterprise",
            "--dry-run",
        ],
    )
    assert result.exit_code == 0
    mock_reconcile.assert_called_once_with(
        "3.15.3", "quay-enterprise", ["quay-operator"], dry_run
    )


def test_operator_versions_multiple_csv_names(mocker: MockerFixture) -> None:
    mock_reconcile = mocker.patch("reconcile.cli.operator_versions_reconcile")
    dry_run = True
    result = CliRunner().invoke(
        cli,
        [
            "operator-versions",
            "--name",
            "metallb-operator",
            "--version",
            "4.20.0",
            "--namespace",
            "metallb-system",
            "--csv-name",
            "metallb-operator",
            "--csv-name",
            "metallb-operator-bundle",
            "--dry-run",
        ],
    )
    assert result.exit_code == 0
    mock_reconcile.assert_called_once_with(
        "4.20.0",
        "metallb-system",
        ["metallb-operator", "metallb-operator-bundle"],
        dry_run,
    )


def test_use_defaults_calls_reconcile_per_operator(mocker: MockerFixture) -> None:
    mock_reconcile = mocker.patch("reconcile.cli.operator_versions_reconcile")
    with pathlib.Path("defaults/operators.yaml").open(encoding="utf-8") as fh:
        operators = yaml.safe_load(fh)["operators"]
    dry_run = True
    result = CliRunner().invoke(
        cli, ["operator-versions", "--use-defaults", "--dry-run"]
    )
    assert result.exit_code == 0, result.output
    assert mock_reconcile.call_count == len(operators)
    quay_op = next(op for op in operators if op["name"] == "quay-operator")
    mock_reconcile.assert_any_call(
        quay_op["version"],
        quay_op["namespace"],
        quay_op.get("csvNames") or [quay_op["name"]],
        dry_run,
    )


def test_use_defaults_mutual_exclusive_name() -> None:
    result = CliRunner().invoke(
        cli, ["operator-versions", "--use-defaults", "--name", "foo"]
    )
    assert result.exit_code != 0
    assert "mutually exclusive" in result.output


def test_use_defaults_mutual_exclusive_version() -> None:
    result = CliRunner().invoke(
        cli, ["operator-versions", "--use-defaults", "--version", "1.0.0"]
    )
    assert result.exit_code != 0
    assert "mutually exclusive" in result.output


def test_use_defaults_mutual_exclusive_csv_name() -> None:
    result = CliRunner().invoke(
        cli, ["operator-versions", "--use-defaults", "--csv-name", "foo"]
    )
    assert result.exit_code != 0
    assert "mutually exclusive" in result.output


def test_operator_versions_missing_required_without_defaults() -> None:
    result = CliRunner().invoke(cli, ["operator-versions", "--name", "foo"])
    assert result.exit_code != 0
    assert "Missing option" in result.output


def test_mgmt_cluster_version_with_version(mocker: MockerFixture) -> None:
    mock_reconcile = mocker.patch("reconcile.cli.cluster_upgrade_reconcile")
    dry_run = True
    result = CliRunner().invoke(
        cli, ["mgmt-cluster-version", "--version", "4.20.21", "--dry-run"]
    )
    assert result.exit_code == 0, result.output
    mock_reconcile.assert_called_once_with("4.20.21", dry_run, 180, 60)


def test_mgmt_cluster_version_use_defaults(mocker: MockerFixture) -> None:
    mock_reconcile = mocker.patch("reconcile.cli.cluster_upgrade_reconcile")
    dry_run = True
    result = CliRunner().invoke(
        cli, ["mgmt-cluster-version", "--use-defaults", "--dry-run"]
    )
    assert result.exit_code == 0, result.output
    mock_reconcile.assert_called_once_with("4.20.21", dry_run, 180, 60)


def test_mgmt_cluster_version_use_defaults_mutual_exclusive_version() -> None:
    result = CliRunner().invoke(
        cli, ["mgmt-cluster-version", "--use-defaults", "--version", "4.20.8"]
    )
    assert result.exit_code != 0
    assert "mutually exclusive" in result.output


def test_mgmt_cluster_version_neither_version_nor_defaults() -> None:
    result = CliRunner().invoke(cli, ["mgmt-cluster-version"])
    assert result.exit_code != 0
    assert "Either --version or --use-defaults" in result.output
