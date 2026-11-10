"""Unit tests for the ``enclave reconcile`` CLI."""

import yaml
from click.testing import CliRunner
from pytest_mock import MockerFixture

from enclave.reconcile.cli import cli, defaults_path

_KC = {"KUBECONFIG": "/fake/kubeconfig"}


def test_cli_help() -> None:
    """Top-level help shows the reconcile CLI without tools commands."""
    result = CliRunner().invoke(cli, ["--help"])
    assert result.exit_code == 0
    assert "Reconcile CLI" in result.output
    assert "resolve-quay-registry-ca" not in result.output
    assert "collect-node-image-digests" not in result.output


def test_operator_versions_help() -> None:
    """operator-versions help lists its options."""
    result = CliRunner().invoke(cli, ["operator-versions", "--help"], env=_KC)
    assert result.exit_code == 0
    assert "--name" in result.output
    assert "--version" in result.output
    assert "--namespace" in result.output
    assert "--channel-only" in result.output
    assert "--csv-name" in result.output
    assert "--dry-run" in result.output
    assert "--use-defaults" in result.output
    assert "--operators" not in result.output


def test_mgmt_cluster_version_help() -> None:
    """mgmt-cluster-version help lists its options."""
    result = CliRunner().invoke(cli, ["mgmt-cluster-version", "--help"], env=_KC)
    assert result.exit_code == 0
    assert "--version" in result.output
    assert "--use-defaults" in result.output
    assert "--timeout-minutes" in result.output
    assert "--sleep-interval" in result.output


def test_log_level_option() -> None:
    """A valid --log-level is accepted."""
    result = CliRunner().invoke(cli, ["--log-level", "DEBUG", "--help"])
    assert result.exit_code == 0


def test_invalid_log_level() -> None:
    """An invalid --log-level is rejected."""
    result = CliRunner().invoke(cli, ["--log-level", "INVALID"])
    assert result.exit_code != 0


def test_operator_versions_csv_name_defaults_to_name(mocker: MockerFixture) -> None:
    """The CSV name defaults to the operator name."""
    mock_reconcile = mocker.patch("enclave.reconcile.cli.operator_versions_reconcile")
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
        env=_KC,
    )
    assert result.exit_code == 0
    mock_reconcile.assert_called_once_with(
        "3.15.3", "quay-enterprise", ["quay-operator"], dry_run
    )


def test_operator_versions_multiple_csv_names(mocker: MockerFixture) -> None:
    """Repeated --csv-name options are all passed through."""
    mock_reconcile = mocker.patch("enclave.reconcile.cli.operator_versions_reconcile")
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
        env=_KC,
    )
    assert result.exit_code == 0
    mock_reconcile.assert_called_once_with(
        "4.20.0",
        "metallb-system",
        ["metallb-operator", "metallb-operator-bundle"],
        dry_run,
    )


def test_operator_versions_passes_channel_to_reconcile(
    mocker: MockerFixture,
) -> None:
    """Pass explicit Subscription channel and name through the CLI."""
    mock_reconcile = mocker.patch("enclave.reconcile.cli.operator_versions_reconcile")
    dry_run = False
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
            "--channel",
            "stable-3.15",
        ],
        env=_KC,
    )

    assert result.exit_code == 0
    mock_reconcile.assert_called_once_with(
        "3.15.3",
        "quay-enterprise",
        ["quay-operator"],
        dry_run,
        channel="stable-3.15",
        subscription_name="quay-operator",
        channel_only=False,
    )


def test_operator_versions_passes_channel_only_to_reconcile(
    mocker: MockerFixture,
) -> None:
    """Pass channel-only mode through the CLI."""
    mock_reconcile = mocker.patch("enclave.reconcile.cli.operator_versions_reconcile")
    dry_run = False
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
            "--channel",
            "stable-3.15",
            "--channel-only",
        ],
        env=_KC,
    )

    assert result.exit_code == 0, result.output
    mock_reconcile.assert_called_once_with(
        "3.15.3",
        "quay-enterprise",
        ["quay-operator"],
        dry_run,
        channel="stable-3.15",
        subscription_name="quay-operator",
        channel_only=True,
    )


def test_operator_versions_channel_only_requires_channel() -> None:
    """--channel-only without --channel is rejected."""
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
            "--channel-only",
        ],
        env=_KC,
    )
    assert result.exit_code != 0
    assert "--channel-only requires --channel" in result.output


def test_use_defaults_calls_reconcile_per_operator(mocker: MockerFixture) -> None:
    """Pass each default operator's channel and Subscription name to reconcile."""
    mock_reconcile = mocker.patch("enclave.reconcile.cli.operator_versions_reconcile")
    defaults_file = defaults_path("operators.yaml")
    with defaults_file.open(encoding="utf-8") as fh:
        operators = yaml.safe_load(fh)["operators"]
    dry_run = True
    result = CliRunner().invoke(
        cli, ["operator-versions", "--use-defaults", "--dry-run"], env=_KC
    )
    assert result.exit_code == 0, result.output
    assert mock_reconcile.call_count == len(operators)
    quay_op = next(op for op in operators if op["name"] == "quay-operator")
    mock_reconcile.assert_any_call(
        quay_op["version"],
        quay_op["namespace"],
        quay_op.get("csvNames") or [quay_op["name"]],
        dry_run,
        channel=quay_op["channel"],
        subscription_name=quay_op["name"],
        channel_only=False,
    )


def test_use_defaults_passes_channel_only(mocker: MockerFixture) -> None:
    """--use-defaults forwards --channel-only."""
    mock_reconcile = mocker.patch("enclave.reconcile.cli.operator_versions_reconcile")
    result = CliRunner().invoke(
        cli,
        ["operator-versions", "--use-defaults", "--channel-only"],
        env=_KC,
    )

    assert result.exit_code == 0, result.output
    assert mock_reconcile.call_count > 0
    assert all(call.kwargs["channel_only"] for call in mock_reconcile.call_args_list)


def test_use_defaults_mutual_exclusive_name() -> None:
    """--use-defaults and --name are mutually exclusive."""
    result = CliRunner().invoke(
        cli, ["operator-versions", "--use-defaults", "--name", "foo"], env=_KC
    )
    assert result.exit_code != 0
    assert "mutually exclusive" in result.output


def test_use_defaults_mutual_exclusive_version() -> None:
    """--use-defaults and --version are mutually exclusive."""
    result = CliRunner().invoke(
        cli, ["operator-versions", "--use-defaults", "--version", "1.0.0"], env=_KC
    )
    assert result.exit_code != 0
    assert "mutually exclusive" in result.output


def test_use_defaults_mutual_exclusive_csv_name() -> None:
    """--use-defaults and --csv-name are mutually exclusive."""
    result = CliRunner().invoke(
        cli, ["operator-versions", "--use-defaults", "--csv-name", "foo"], env=_KC
    )
    assert result.exit_code != 0
    assert "mutually exclusive" in result.output


def test_operator_versions_missing_required_without_defaults() -> None:
    """Without --use-defaults the explicit options are required."""
    result = CliRunner().invoke(cli, ["operator-versions", "--name", "foo"], env=_KC)
    assert result.exit_code != 0
    assert "Missing option" in result.output


def test_mgmt_cluster_version_with_version(mocker: MockerFixture) -> None:
    """An explicit --version is passed to the upgrade."""
    mock_reconcile = mocker.patch("enclave.reconcile.cli.cluster_upgrade_reconcile")
    dry_run = True
    allow_not_recommended = False
    result = CliRunner().invoke(
        cli, ["mgmt-cluster-version", "--version", "4.20.32", "--dry-run"], env=_KC
    )
    assert result.exit_code == 0, result.output
    mock_reconcile.assert_called_once_with(
        "4.20.32", dry_run, 180, 60, allow_not_recommended
    )


def test_mgmt_cluster_version_unknown_version_rejected(mocker: MockerFixture) -> None:
    """A version not in the bundled defaults is rejected."""
    mock_reconcile = mocker.patch("enclave.reconcile.cli.cluster_upgrade_reconcile")
    result = CliRunner().invoke(
        cli, ["mgmt-cluster-version", "--version", "4.99.0", "--dry-run"], env=_KC
    )
    assert result.exit_code != 0
    assert "Version '4.99.0'" in result.output
    assert "not in defaults/platforms.yaml" in result.output
    assert "4.20.32" in result.output
    assert "4.21.32" in result.output
    mock_reconcile.assert_not_called()


def test_mgmt_cluster_version_unknown_version_rejected_even_with_allow_not_recommended(
    mocker: MockerFixture,
) -> None:
    """Versions not in platforms.yaml are rejected even with --allow-not-recommended."""
    mock_reconcile = mocker.patch("enclave.reconcile.cli.cluster_upgrade_reconcile")
    result = CliRunner().invoke(
        cli,
        [
            "mgmt-cluster-version",
            "--version",
            "4.99.0",
            "--allow-not-recommended",
            "--dry-run",
        ],
        env=_KC,
    )
    assert result.exit_code != 0
    assert "Version '4.99.0'" in result.output
    assert "not in defaults/platforms.yaml" in result.output
    mock_reconcile.assert_not_called()


def test_mgmt_cluster_version_use_defaults(mocker: MockerFixture) -> None:
    """--use-defaults upgrades to the default version."""
    mock_reconcile = mocker.patch("enclave.reconcile.cli.cluster_upgrade_reconcile")
    dry_run = True
    allow_not_recommended = False
    result = CliRunner().invoke(
        cli, ["mgmt-cluster-version", "--use-defaults", "--dry-run"], env=_KC
    )
    assert result.exit_code == 0, result.output
    mock_reconcile.assert_called_once_with(
        "4.21.32", dry_run, 180, 60, allow_not_recommended
    )


def test_mgmt_cluster_version_use_defaults_mutual_exclusive_version() -> None:
    """--use-defaults and --version are mutually exclusive."""
    result = CliRunner().invoke(
        cli, ["mgmt-cluster-version", "--use-defaults", "--version", "4.20.8"], env=_KC
    )
    assert result.exit_code != 0
    assert "mutually exclusive" in result.output


def test_mgmt_cluster_version_latest(mocker: MockerFixture) -> None:
    """--latest upgrades to the newest bundled version."""
    mock_reconcile = mocker.patch("enclave.reconcile.cli.cluster_upgrade_reconcile")
    dry_run = True
    allow_not_recommended = False
    result = CliRunner().invoke(
        cli, ["mgmt-cluster-version", "--latest", "--dry-run"], env=_KC
    )
    assert result.exit_code == 0, result.output
    mock_reconcile.assert_called_once_with(
        "4.21.32", dry_run, 180, 60, allow_not_recommended
    )


def test_mgmt_cluster_version_latest_mutual_exclusive_version() -> None:
    """--latest and --version are mutually exclusive."""
    result = CliRunner().invoke(
        cli, ["mgmt-cluster-version", "--latest", "--version", "4.20.21"], env=_KC
    )
    assert result.exit_code != 0
    assert "mutually exclusive" in result.output


def test_mgmt_cluster_version_latest_mutual_exclusive_use_defaults() -> None:
    """--latest and --use-defaults are mutually exclusive."""
    result = CliRunner().invoke(
        cli, ["mgmt-cluster-version", "--latest", "--use-defaults"], env=_KC
    )
    assert result.exit_code != 0
    assert "mutually exclusive" in result.output


def test_mgmt_cluster_version_allow_not_recommended(mocker: MockerFixture) -> None:
    """--allow-not-recommended is passed to the upgrade."""
    mock_reconcile = mocker.patch("enclave.reconcile.cli.cluster_upgrade_reconcile")
    dry_run = True
    allow_not_recommended = True
    result = CliRunner().invoke(
        cli,
        [
            "mgmt-cluster-version",
            "--version",
            "4.20.32",
            "--allow-not-recommended",
            "--dry-run",
        ],
        env=_KC,
    )
    assert result.exit_code == 0, result.output
    mock_reconcile.assert_called_once_with(
        "4.20.32", dry_run, 180, 60, allow_not_recommended
    )


def test_mgmt_cluster_version_no_args_shows_help() -> None:
    """mgmt-cluster-version with no arguments shows help."""
    result = CliRunner().invoke(cli, ["mgmt-cluster-version"])
    assert result.exit_code == 2
    assert "--version" in result.output
    assert "--use-defaults" in result.output


def test_reconcile_no_args_shows_help() -> None:
    """The reconcile group with no arguments shows help."""
    result = CliRunner().invoke(cli, [])
    assert result.exit_code == 2
    assert "Reconcile CLI" in result.output


def test_kubeconfig_missing_fails(mocker: MockerFixture) -> None:
    """A missing kubeconfig makes a cluster command fail."""
    mocker.patch("enclave.utils.Path.exists", return_value=False)
    result = CliRunner().invoke(
        cli, ["mgmt-cluster-version", "--version", "4.14"], env={"KUBECONFIG": ""}
    )
    assert result.exit_code != 0
    assert "KUBECONFIG" in result.output
