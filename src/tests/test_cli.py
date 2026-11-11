"""Unit tests for the ``enclave reconcile`` CLI."""

from pathlib import Path

import yaml
from click.testing import CliRunner
from pytest_mock import MockerFixture

from enclave.reconcile.cli import cli, config_path, defaults_path

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
    assert "--channel" in result.output
    assert "--channel-only" not in result.output
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
    assert "--platforms-vars-file" in result.output


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
    )


def test_operator_versions_rejects_removed_option(
    mocker: MockerFixture,
) -> None:
    """Reject the removed option before invoking reconciliation."""
    mock_reconcile = mocker.patch("enclave.reconcile.cli.operator_versions_reconcile")
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

    assert result.exit_code == 2
    assert "No such option '--channel-only'" in result.output
    mock_reconcile.assert_not_called()


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
    )


def test_use_defaults_rejects_removed_option(mocker: MockerFixture) -> None:
    """Defaults do not accept the removed option or invoke reconciliation."""
    mock_reconcile = mocker.patch("enclave.reconcile.cli.operator_versions_reconcile")
    result = CliRunner().invoke(
        cli,
        ["operator-versions", "--use-defaults", "--channel-only"],
        env=_KC,
    )

    assert result.exit_code == 2
    assert "No such option '--channel-only'" in result.output
    mock_reconcile.assert_not_called()


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


def test_mgmt_cluster_version_unknown_version_rejected(
    mocker: MockerFixture, tmp_path: Path
) -> None:
    """Reject versions outside the bundled defaults."""
    mocker.patch(
        "enclave.reconcile.cli.config_path", return_value=tmp_path / "missing.yaml"
    )
    mock_reconcile = mocker.patch("enclave.reconcile.cli.cluster_upgrade_reconcile")
    result = CliRunner().invoke(
        cli, ["mgmt-cluster-version", "--version", "4.99.0", "--dry-run"], env=_KC
    )
    assert result.exit_code != 0
    assert "Version '4.99.0'" in result.output
    assert "not in the effective openshift_versions list" in result.output
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
    assert "not in the effective openshift_versions list" in result.output
    mock_reconcile.assert_not_called()


def test_mgmt_cluster_version_use_defaults(
    mocker: MockerFixture, tmp_path: Path
) -> None:
    """--use-defaults selects the bundled default without an override."""
    mocker.patch(
        "enclave.reconcile.cli.config_path", return_value=tmp_path / "missing.yaml"
    )
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


def test_mgmt_cluster_version_uses_config_platforms_override(
    mocker: MockerFixture, tmp_path: Path
) -> None:
    """The config version list replaces defaults for CLI selection."""
    config_file = tmp_path / "platforms.yaml"
    config_file.write_text(
        "openshift_versions:\n"
        "  - version: 4.21.7\n"
        "    default: true\n"
        "  - version: 4.21.8\n",
        encoding="utf-8",
    )
    mocker.patch("enclave.reconcile.cli.config_path", return_value=config_file)
    mock_reconcile = mocker.patch("enclave.reconcile.cli.cluster_upgrade_reconcile")
    dry_run = True
    allow_not_recommended = False

    default_result = CliRunner().invoke(
        cli, ["mgmt-cluster-version", "--use-defaults", "--dry-run"], env=_KC
    )
    assert default_result.exit_code == 0, default_result.output
    mock_reconcile.assert_called_once_with(
        "4.21.7", dry_run, 180, 60, allow_not_recommended
    )

    mock_reconcile.reset_mock()
    override_result = CliRunner().invoke(
        cli,
        ["mgmt-cluster-version", "--version", "4.21.8", "--dry-run"],
        env=_KC,
    )
    assert override_result.exit_code == 0, override_result.output
    mock_reconcile.assert_called_once_with(
        "4.21.8", dry_run, 180, 60, allow_not_recommended
    )

    mock_reconcile.reset_mock()
    replaced_default_result = CliRunner().invoke(
        cli,
        ["mgmt-cluster-version", "--version", "4.20.32", "--dry-run"],
        env=_KC,
    )
    assert replaced_default_result.exit_code != 0
    assert "Allowed: 4.21.7, 4.21.8" in replaced_default_result.output
    mock_reconcile.assert_not_called()


def test_mgmt_cluster_version_uses_platforms_vars_file_option(
    mocker: MockerFixture, tmp_path: Path
) -> None:
    """An explicit platforms file takes precedence over checkout config."""
    fallback_file = tmp_path / "config-platforms.yaml"
    fallback_file.write_text(
        "openshift_versions:\n  - version: 4.20.32\n    default: true\n",
        encoding="utf-8",
    )
    override_file = tmp_path / "ansible-platforms.yaml"
    override_file.write_text(
        "openshift_versions:\n  - version: 4.22.1\n    default: true\n",
        encoding="utf-8",
    )
    mocker.patch("enclave.reconcile.cli.config_path", return_value=fallback_file)
    mock_reconcile = mocker.patch("enclave.reconcile.cli.cluster_upgrade_reconcile")

    result = CliRunner().invoke(
        cli,
        [
            "mgmt-cluster-version",
            "--platforms-vars-file",
            str(override_file),
            "--use-defaults",
            "--dry-run",
        ],
        env=_KC,
    )

    assert result.exit_code == 0, result.output
    dry_run = True
    allow_not_recommended = False
    mock_reconcile.assert_called_once_with(
        "4.22.1", dry_run, 180, 60, allow_not_recommended
    )


def test_config_path_uses_enclave_checkout_from_another_working_directory(
    mocker: MockerFixture, tmp_path: Path
) -> None:
    """Config resolves from the Enclave checkout when run elsewhere."""
    checkout = tmp_path / "enclave"
    (checkout / "defaults").mkdir(parents=True)
    (checkout / "defaults/platforms.yaml").touch()
    (checkout / "playbooks/common").mkdir(parents=True)
    (checkout / "playbooks/common/load-vars.yaml").touch()
    (checkout / "config").mkdir()

    other_project = tmp_path / "other-project"
    (other_project / "config").mkdir(parents=True)
    (other_project / "config/platforms.yaml").touch()

    source_file = checkout / "src/enclave/reconcile/cli.py"
    source_file.parent.mkdir(parents=True)
    mocker.patch("enclave.reconcile.cli.__file__", str(source_file))
    mocker.patch("enclave.reconcile.cli.Path.cwd", return_value=other_project)

    assert config_path("platforms.yaml") == checkout / "config/platforms.yaml"


def test_mgmt_cluster_version_rejects_invalid_config_version(
    mocker: MockerFixture, tmp_path: Path
) -> None:
    """Invalid version strings are rejected before reconciliation."""
    config_file = tmp_path / "platforms.yaml"
    config_file.write_text(
        "openshift_versions:\n  - version: 4.20.x\n    default: true\n",
        encoding="utf-8",
    )
    mocker.patch("enclave.reconcile.cli.config_path", return_value=config_file)
    mock_reconcile = mocker.patch("enclave.reconcile.cli.cluster_upgrade_reconcile")

    result = CliRunner().invoke(
        cli, ["mgmt-cluster-version", "--latest", "--dry-run"], env=_KC
    )

    assert result.exit_code != 0
    assert "openshift_versions[0].version" in result.output
    assert "platforms schema version format" in result.output
    assert "ValueError" not in result.output
    mock_reconcile.assert_not_called()


def test_mgmt_cluster_version_rejects_empty_prerelease_suffix(
    mocker: MockerFixture, tmp_path: Path
) -> None:
    """An empty prerelease suffix is rejected by the platforms format."""
    config_file = tmp_path / "platforms.yaml"
    config_file.write_text(
        "openshift_versions:\n  - version: 4.20.1-\n    default: true\n",
        encoding="utf-8",
    )
    mocker.patch("enclave.reconcile.cli.config_path", return_value=config_file)
    mock_reconcile = mocker.patch("enclave.reconcile.cli.cluster_upgrade_reconcile")

    result = CliRunner().invoke(
        cli, ["mgmt-cluster-version", "--latest", "--dry-run"], env=_KC
    )

    assert result.exit_code != 0
    assert "openshift_versions[0].version" in result.output
    assert "platforms schema version format" in result.output
    mock_reconcile.assert_not_called()


def test_mgmt_cluster_version_hides_yaml_parse_contents(
    mocker: MockerFixture, tmp_path: Path
) -> None:
    """YAML parsing errors do not reveal the invalid configuration contents."""
    config_file = tmp_path / "platforms.yaml"
    config_file.write_text(
        "openshift_versions:\n  - version: 'secret-token\n", encoding="utf-8"
    )
    mocker.patch("enclave.reconcile.cli.config_path", return_value=config_file)

    result = CliRunner().invoke(
        cli, ["mgmt-cluster-version", "--use-defaults", "--dry-run"], env=_KC
    )

    assert result.exit_code != 0
    assert "Failed to parse" in result.output
    assert "secret-token" not in result.output


def test_mgmt_cluster_version_use_defaults_mutual_exclusive_version() -> None:
    """--use-defaults and --version are mutually exclusive."""
    result = CliRunner().invoke(
        cli, ["mgmt-cluster-version", "--use-defaults", "--version", "4.20.8"], env=_KC
    )
    assert result.exit_code != 0
    assert "mutually exclusive" in result.output


def test_mgmt_cluster_version_latest(
    mocker: MockerFixture, tmp_path: Path
) -> None:
    """--latest selects the highest version from bundled defaults."""
    mocker.patch(
        "enclave.reconcile.cli.config_path", return_value=tmp_path / "missing.yaml"
    )
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
