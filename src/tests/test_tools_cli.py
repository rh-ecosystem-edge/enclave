from pathlib import Path

from click.testing import CliRunner
from pytest_mock import MockerFixture

from enclave.tools.cli import cli

_KC = {"KUBECONFIG": "/fake/kubeconfig"}


def test_tools_cli_help() -> None:
    result = CliRunner().invoke(cli, ["--help"])
    assert result.exit_code == 0
    assert "Enclave tools CLI" in result.output


def test_resolve_quay_registry_ca_help() -> None:
    result = CliRunner().invoke(cli, ["resolve-quay-registry-ca", "--help"], env=_KC)
    assert result.exit_code == 0
    assert "--hostname" in result.output


def test_resolve_quay_registry_ca_forwards_args(mocker: MockerFixture) -> None:
    mock_reconcile = mocker.patch("enclave.tools.cli.quay_registry_ca_main")
    result = CliRunner().invoke(
        cli,
        [
            "resolve-quay-registry-ca",
            "--hostname",
            "registry.example.com",
            "--oc",
            "/usr/bin/oc",
        ],
        env=_KC,
    )
    assert result.exit_code == 0
    mock_reconcile.assert_called_once_with("registry.example.com", oc="/usr/bin/oc")


def test_resolve_quay_registry_ca_runtime_error(mocker: MockerFixture) -> None:
    mocker.patch(
        "enclave.tools.cli.quay_registry_ca_main",
        side_effect=RuntimeError(
            "unable to resolve registry CA for registry.example.com"
        ),
    )
    result = CliRunner().invoke(
        cli,
        ["resolve-quay-registry-ca", "--hostname", "registry.example.com"],
        env=_KC,
    )
    assert result.exit_code != 0
    assert "unable to resolve registry CA for registry.example.com" in result.output


def test_collect_node_image_digests_help() -> None:
    result = CliRunner().invoke(cli, ["collect-node-image-digests", "--help"], env=_KC)
    assert result.exit_code == 0
    assert "--node" in result.output
    assert "--exclude-contains" in result.output
    assert "--raw-output-file" in result.output


def test_collect_node_image_digests_forwards_args(
    mocker: MockerFixture, tmp_path: Path
) -> None:
    mock_main = mocker.patch("enclave.tools.cli.collect_node_image_digests_main")
    raw_output_file = tmp_path / "node-0.log"
    result = CliRunner().invoke(
        cli,
        [
            "collect-node-image-digests",
            "--node",
            "node-0",
            "--oc",
            "/usr/bin/oc",
            "--raw-output-file",
            str(raw_output_file),
        ],
        env=_KC,
    )
    assert result.exit_code == 0
    mock_main.assert_called_once_with(
        "node-0",
        oc="/usr/bin/oc",
        exclude_contains_raw=None,
        raw_output_file=str(raw_output_file),
    )


def test_kubeconfig_missing_fails(mocker: MockerFixture) -> None:
    mocker.patch("enclave.utils.Path.exists", return_value=False)
    result = CliRunner().invoke(
        cli,
        ["resolve-quay-registry-ca", "--hostname", "reg.example.com"],
        env={"KUBECONFIG": ""},
    )
    assert result.exit_code != 0
    assert "KUBECONFIG" in result.output
