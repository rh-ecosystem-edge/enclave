"""Unit tests for the enclave-ci top-level CLI."""

from click.testing import CliRunner

from enclave_ci.cli import cli


def test_cert_gen_group_is_mounted() -> None:
    """The cert-gen group is mounted with its subcommands."""
    result = CliRunner().invoke(cli, ["cert-gen", "--help"])
    assert result.exit_code == 0
    assert "ironic" in result.output
    assert "ingress-api" in result.output


def test_top_level_help_lists_cert_gen() -> None:
    """Top-level help lists the cert-gen group."""
    result = CliRunner().invoke(cli, ["--help"])
    assert result.exit_code == 0
    assert "cert-gen" in result.output
