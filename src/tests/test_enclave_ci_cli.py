"""Unit tests for the enclave-ci top-level CLI."""

from click.testing import CliRunner

from enclave_ci.cli import cli


def test_cert_gen_group_is_mounted() -> None:
    result = CliRunner().invoke(cli, ["cert-gen", "--help"])
    assert result.exit_code == 0
    assert "ironic" in result.output
    assert "ingress-api" in result.output


def test_top_level_help_lists_cert_gen() -> None:
    result = CliRunner().invoke(cli, ["--help"])
    assert result.exit_code == 0
    assert "cert-gen" in result.output


def test_scrub_artifacts_command_is_mounted() -> None:
    result = CliRunner().invoke(cli, ["scrub-artifacts", "--help"])
    assert result.exit_code == 0
    assert "DIRECTORIES" in result.output
