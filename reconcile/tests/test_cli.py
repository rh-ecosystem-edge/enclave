from click.testing import CliRunner

from reconcile.cli import cli


def test_cli_help() -> None:
    result = CliRunner().invoke(cli, ["--help"])
    assert result.exit_code == 0
    assert "Reconcile CLI" in result.output


def test_operator_versions_help() -> None:
    result = CliRunner().invoke(cli, ["operator-versions", "--help"])
    assert result.exit_code == 0
    assert "--operators" in result.output
    assert "--dry-run" in result.output


def test_mgmt_cluster_version_help() -> None:
    result = CliRunner().invoke(cli, ["mgmt-cluster-version", "--help"])
    assert result.exit_code == 0
    assert "--timeout-minutes" in result.output
    assert "--sleep-interval" in result.output


def test_log_level_option() -> None:
    result = CliRunner().invoke(cli, ["--log-level", "DEBUG", "--help"])
    assert result.exit_code == 0


def test_invalid_log_level() -> None:
    result = CliRunner().invoke(cli, ["--log-level", "INVALID"])
    assert result.exit_code != 0
