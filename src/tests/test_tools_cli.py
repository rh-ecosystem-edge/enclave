from click.testing import CliRunner
from pytest_mock import MockerFixture

from tools.cli import cli


def test_tools_cli_help() -> None:
    result = CliRunner().invoke(cli, ["--help"])
    assert result.exit_code == 0
    assert "Enclave tools CLI" in result.output


def test_resolve_quay_registry_ca_help() -> None:
    result = CliRunner().invoke(cli, ["resolve-quay-registry-ca", "--help"])
    assert result.exit_code == 0
    assert "--hostname" in result.output


def test_resolve_quay_registry_ca_forwards_args(mocker: MockerFixture) -> None:
    mock_reconcile = mocker.patch("tools.cli.quay_registry_ca_main")
    result = CliRunner().invoke(
        cli,
        [
            "resolve-quay-registry-ca",
            "--hostname",
            "registry.example.com",
            "--oc",
            "/usr/bin/oc",
        ],
    )
    assert result.exit_code == 0
    mock_reconcile.assert_called_once_with("registry.example.com", oc="/usr/bin/oc")


def test_resolve_quay_registry_ca_runtime_error(mocker: MockerFixture) -> None:
    mocker.patch(
        "tools.cli.quay_registry_ca_main",
        side_effect=RuntimeError(
            "unable to resolve registry CA for registry.example.com"
        ),
    )
    result = CliRunner().invoke(
        cli,
        ["resolve-quay-registry-ca", "--hostname", "registry.example.com"],
    )
    assert result.exit_code != 0
    assert "unable to resolve registry CA for registry.example.com" in result.output
