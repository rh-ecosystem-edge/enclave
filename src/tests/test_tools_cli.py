"""Tests for the enclave tools CLI: resolve-quay-registry-ca, check-certificate-chains, collect-node-image-digests."""

from pathlib import Path

import pytest
from click.testing import CliRunner
from pytest_mock import MockerFixture

from enclave.tools.check_certificate_chains import CertificateValidationError
from enclave.tools.cli import cli

_KC = {"KUBECONFIG": "/fake/kubeconfig"}


def test_tools_cli_help() -> None:
    """Show help and exit 0."""
    result = CliRunner().invoke(cli, ["--help"])
    assert result.exit_code == 0
    assert "Enclave tools CLI" in result.output


def test_resolve_quay_registry_ca_help() -> None:
    """Show --hostname option in help."""
    result = CliRunner().invoke(cli, ["resolve-quay-registry-ca", "--help"], env=_KC)
    assert result.exit_code == 0
    assert "--hostname" in result.output


def test_resolve_quay_registry_ca_forwards_args(mocker: MockerFixture) -> None:
    """Forward hostname and oc to quay_registry_ca_main."""
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
    mock_reconcile.assert_called_once_with(
        "registry.example.com", oc="/usr/bin/oc", ca_pem=""
    )


def test_resolve_quay_registry_ca_forwards_ca_pem(
    mocker: MockerFixture, root_pem: str
) -> None:
    """Forward --ca-pem to quay_registry_ca_main."""
    mock_main = mocker.patch("enclave.tools.cli.quay_registry_ca_main")
    result = CliRunner().invoke(
        cli,
        [
            "resolve-quay-registry-ca",
            "--hostname",
            "registry.example.com",
            "--ca-pem",
            root_pem + "\n",
        ],
        env=_KC,
    )
    assert result.exit_code == 0
    mock_main.assert_called_once_with(
        "registry.example.com",
        oc="oc",
        ca_pem=root_pem + "\n",
    )


def test_resolve_quay_registry_ca_rejects_empty_ca_pem() -> None:
    """Reject a blank --ca-pem value."""
    result = CliRunner().invoke(
        cli,
        [
            "resolve-quay-registry-ca",
            "--hostname",
            "registry.example.com",
            "--ca-pem",
            "   ",
        ],
        env=_KC,
    )
    assert result.exit_code != 0
    assert "must not be empty" in result.output


def test_resolve_quay_registry_ca_forwards_certificates_config(
    mocker: MockerFixture, tmp_path: Path, root_pem: str
) -> None:
    """Read sslCACertificate from --certificates-config and forward it."""
    mock_main = mocker.patch("enclave.tools.cli.quay_registry_ca_main")
    config_file = tmp_path / "certificates.yaml"
    # Build a valid YAML block scalar: each PEM line must be indented.
    indented = "\n".join("  " + line for line in root_pem.split("\n"))
    config_file.write_text(f"sslCACertificate: |\n{indented}\n", encoding="utf-8")
    result = CliRunner().invoke(
        cli,
        [
            "resolve-quay-registry-ca",
            "--hostname",
            "registry.example.com",
            "--certificates-config",
            str(config_file),
        ],
        env=_KC,
    )
    assert result.exit_code == 0
    mock_main.assert_called_once_with(
        "registry.example.com",
        oc="oc",
        ca_pem=root_pem,
    )


def test_resolve_quay_registry_ca_forwards_empty_ca_pem_when_ssl_ca_certificate_absent(
    mocker: MockerFixture, tmp_path: Path
) -> None:
    """Call through with ca_pem='' when sslCACertificate is absent from config."""
    mock_main = mocker.patch("enclave.tools.cli.quay_registry_ca_main")
    config_file = tmp_path / "certificates.yaml"
    config_file.write_text(
        "sslAPICertificateFullChain: |\n  something\n", encoding="utf-8"
    )
    result = CliRunner().invoke(
        cli,
        [
            "resolve-quay-registry-ca",
            "--hostname",
            "registry.example.com",
            "--certificates-config",
            str(config_file),
        ],
        env=_KC,
    )
    assert result.exit_code == 0
    mock_main.assert_called_once_with("registry.example.com", oc="oc", ca_pem="")


def test_resolve_quay_registry_ca_rejects_non_mapping_certificates_config(
    tmp_path: Path,
) -> None:
    """Reject a --certificates-config that is not a YAML mapping."""
    config_file = tmp_path / "certificates.yaml"
    config_file.write_text("- item1\n- item2\n", encoding="utf-8")
    result = CliRunner().invoke(
        cli,
        [
            "resolve-quay-registry-ca",
            "--hostname",
            "registry.example.com",
            "--certificates-config",
            str(config_file),
        ],
        env=_KC,
    )
    assert result.exit_code != 0
    assert "YAML mapping" in result.output


def test_resolve_quay_registry_ca_treats_non_string_ssl_ca_certificate_as_empty(
    mocker: MockerFixture, tmp_path: Path
) -> None:
    """Treat a non-string sslCACertificate (e.g. list) as absent and call through with ca_pem=''."""
    mock_main = mocker.patch("enclave.tools.cli.quay_registry_ca_main")
    config_file = tmp_path / "certificates.yaml"
    config_file.write_text(
        "sslCACertificate:\n  - item1\n  - item2\n", encoding="utf-8"
    )
    result = CliRunner().invoke(
        cli,
        [
            "resolve-quay-registry-ca",
            "--hostname",
            "registry.example.com",
            "--certificates-config",
            str(config_file),
        ],
        env=_KC,
    )
    assert result.exit_code == 0
    mock_main.assert_called_once_with("registry.example.com", oc="oc", ca_pem="")


def test_resolve_quay_registry_ca_reports_yaml_parse_error(
    tmp_path: Path,
) -> None:
    """Report 'cannot parse' (not 'cannot read') for invalid YAML syntax."""
    config_file = tmp_path / "certificates.yaml"
    config_file.write_text("key: [\nbad yaml", encoding="utf-8")
    result = CliRunner().invoke(
        cli,
        [
            "resolve-quay-registry-ca",
            "--hostname",
            "registry.example.com",
            "--certificates-config",
            str(config_file),
        ],
        env=_KC,
    )
    assert result.exit_code != 0
    assert "cannot parse" in result.output


def test_resolve_quay_registry_ca_rejects_missing_certificates_config() -> None:
    """Reject a non-existent --certificates-config path."""
    result = CliRunner().invoke(
        cli,
        [
            "resolve-quay-registry-ca",
            "--hostname",
            "registry.example.com",
            "--certificates-config",
            "/nonexistent/certificates.yaml",
        ],
        env=_KC,
    )
    assert result.exit_code != 0


def test_resolve_quay_registry_ca_rejects_both_ca_pem_and_certificates_config(
    tmp_path: Path, root_pem: str
) -> None:
    """Reject when both --ca-pem and --certificates-config are supplied."""
    config_file = tmp_path / "certificates.yaml"
    config_file.write_text(f"sslCACertificate: |\n  {root_pem}\n", encoding="utf-8")
    result = CliRunner().invoke(
        cli,
        [
            "resolve-quay-registry-ca",
            "--hostname",
            "registry.example.com",
            "--ca-pem",
            f"{root_pem}\n",
            "--certificates-config",
            str(config_file),
        ],
        env=_KC,
    )
    assert result.exit_code != 0
    assert "mutually exclusive" in result.output


def test_resolve_quay_registry_ca_runtime_error(mocker: MockerFixture) -> None:
    """Surface a RuntimeError from quay_registry_ca_main as a non-zero exit."""
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


def test_check_certificate_chains_help() -> None:
    """Show --config option in help."""
    result = CliRunner().invoke(cli, ["check-certificate-chains", "--help"])
    assert result.exit_code == 0
    assert "--config" in result.output


def test_check_certificate_chains_forwards_config(
    mocker: MockerFixture, tmp_path: Path
) -> None:
    """Forward cert_type, --config path, and --hostname values to check_certificate_chains_helper."""
    mock_main = mocker.patch("enclave.tools.cli.check_certificate_chains_helper")
    config_file = tmp_path / "certificates.yaml"
    config_file.write_text("", encoding="utf-8")
    result = CliRunner().invoke(
        cli,
        [
            "check-certificate-chains",
            "api",
            "--config",
            str(config_file),
            "--hostname",
            "api.cluster.example.com",
        ],
    )
    assert result.exit_code == 0
    mock_main.assert_called_once_with(
        str(config_file),
        cert_type="api",
        hostnames=["api.cluster.example.com"],
    )


def test_check_certificate_chains_reports_runtime_error(
    mocker: MockerFixture, tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    """Surface a CertificateValidationError as a logger.error call and exit code 1."""
    mocker.patch(
        "enclave.tools.cli.check_certificate_chains_helper",
        side_effect=CertificateValidationError(
            "sslAPICertificateFullChain: chain ends with a non-self-signed certificate"
        ),
    )
    config_file = tmp_path / "certificates.yaml"
    config_file.write_text("", encoding="utf-8")
    result = CliRunner().invoke(
        cli,
        [
            "check-certificate-chains",
            "api",
            "--config",
            str(config_file),
            "--hostname",
            "api.cluster.example.com",
        ],
    )
    assert result.exit_code == 1
    assert "sslAPICertificateFullChain" in caplog.text


def test_check_certificate_chains_rejects_missing_file() -> None:
    """Reject a non-existent --config path."""
    result = CliRunner().invoke(
        cli,
        [
            "check-certificate-chains",
            "api",
            "--config",
            "/nonexistent/certificates.yaml",
            "--hostname",
            "api.example.com",
        ],
    )
    assert result.exit_code != 0


def test_check_certificate_chains_ironic_type(
    mocker: MockerFixture, tmp_path: Path
) -> None:
    """Forward ironic cert_type with --hostname to check_certificate_chains_helper."""
    mock_main = mocker.patch("enclave.tools.cli.check_certificate_chains_helper")
    config_file = tmp_path / "certificates.yaml"
    config_file.write_text("", encoding="utf-8")
    result = CliRunner().invoke(
        cli,
        [
            "check-certificate-chains",
            "ironic",
            "--config",
            str(config_file),
            "--hostname",
            "bmc.example.com",
        ],
    )
    assert result.exit_code == 0
    mock_main.assert_called_once_with(
        str(config_file),
        cert_type="ironic",
        hostnames=["bmc.example.com"],
    )


@pytest.mark.parametrize("cert_type", ["api", "ingress", "ironic"])
def test_check_certificate_chains_requires_hostname(
    tmp_path: Path, cert_type: str
) -> None:
    """Reject any cert_type when --hostname is omitted."""
    config_file = tmp_path / "certificates.yaml"
    config_file.write_text("", encoding="utf-8")
    result = CliRunner().invoke(
        cli,
        ["check-certificate-chains", cert_type, "--config", str(config_file)],
    )
    assert result.exit_code != 0
    assert "--hostname" in result.output


def test_collect_node_image_digests_help() -> None:
    """Show expected options in help."""
    result = CliRunner().invoke(cli, ["collect-node-image-digests", "--help"], env=_KC)
    assert result.exit_code == 0
    assert "--node" in result.output
    assert "--exclude-contains" in result.output
    assert "--raw-output-file" in result.output


def test_collect_node_image_digests_forwards_args(
    mocker: MockerFixture, tmp_path: Path
) -> None:
    """Forward node, oc, and raw-output-file to collect_node_image_digests_main."""
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


def test_get_root_ca_help() -> None:
    """Show --config and --chain-pem options in help."""
    result = CliRunner().invoke(cli, ["get-root-ca", "--help"])
    assert result.exit_code == 0
    assert "--config" in result.output
    assert "--chain-pem" in result.output


def test_get_root_ca_from_chain_pem_option(
    mocker: MockerFixture, ca_pem: str, leaf_pem: str
) -> None:
    """Print the CA PEM returned by find_system_ca_for_chain when --chain-pem is given."""
    mocker.patch(
        "enclave.tools.cli.find_system_ca_for_chain", return_value=ca_pem + "\n"
    )
    result = CliRunner().invoke(
        cli,
        [
            "get-root-ca",
            "--chain-pem",
            leaf_pem + "\n",
        ],
    )
    assert result.exit_code == 0
    assert ca_pem + "\n" in result.output


def test_get_root_ca_from_config_prints_ca(
    mocker: MockerFixture, tmp_path: Path, ca_pem: str, chain_pem: str
) -> None:
    """Read the fullchain from --config and print the discovered CA PEM."""
    mocker.patch(
        "enclave.tools.cli.find_system_ca_for_chain", return_value=ca_pem + "\n"
    )
    config_file = tmp_path / "certificates.yaml"
    indented = "\n".join("  " + line for line in (chain_pem + "\n").splitlines())
    config_file.write_text(
        f"sslAPICertificateFullChain: |\n{indented}\n", encoding="utf-8"
    )
    result = CliRunner().invoke(
        cli,
        ["get-root-ca", "--config", str(config_file)],
    )
    assert result.exit_code == 0
    assert ca_pem + "\n" in result.output


def test_get_root_ca_from_config_falls_back_to_ingress_chain(
    mocker: MockerFixture, tmp_path: Path, ca_pem: str
) -> None:
    """Fall back to sslIngressCertificateFullChain when API chain is absent."""
    mock_fn = mocker.patch(
        "enclave.tools.cli.find_system_ca_for_chain", return_value=ca_pem + "\n"
    )
    chain = "-----BEGIN CERTIFICATE-----\ningress-chain\n-----END CERTIFICATE-----\n"
    config_file = tmp_path / "certificates.yaml"
    indented = "\n".join("  " + line for line in chain.splitlines())
    config_file.write_text(
        f"sslIngressCertificateFullChain: |\n{indented}\n", encoding="utf-8"
    )
    result = CliRunner().invoke(
        cli,
        ["get-root-ca", "--config", str(config_file)],
    )
    assert result.exit_code == 0
    called_chain = mock_fn.call_args[0][0]
    assert "ingress-chain" in called_chain


def test_get_root_ca_not_found(mocker: MockerFixture, leaf_pem: str) -> None:
    """Exit non-zero when find_system_ca_for_chain returns None."""
    mocker.patch("enclave.tools.cli.find_system_ca_for_chain", return_value=None)
    result = CliRunner().invoke(
        cli,
        [
            "get-root-ca",
            "--chain-pem",
            leaf_pem + "\n",
        ],
    )
    assert result.exit_code != 0
    assert "no matching CA" in result.output


def test_get_root_ca_rejects_both_options(tmp_path: Path, leaf_pem: str) -> None:
    """Exit non-zero when both --config and --chain-pem are supplied."""
    config_file = tmp_path / "certificates.yaml"
    config_file.write_text("sslAPICertificateFullChain: ''\n", encoding="utf-8")
    result = CliRunner().invoke(
        cli,
        [
            "get-root-ca",
            "--config",
            str(config_file),
            "--chain-pem",
            leaf_pem + "\n",
        ],
    )
    assert result.exit_code != 0
    assert "mutually exclusive" in result.output


def test_get_root_ca_rejects_neither_option() -> None:
    """Exit non-zero when neither --config nor --chain-pem is supplied."""
    result = CliRunner().invoke(cli, ["get-root-ca"])
    assert result.exit_code != 0


def test_get_root_ca_rejects_config_with_no_chains(tmp_path: Path) -> None:
    """Exit non-zero when the config has no fullchain fields set."""
    config_file = tmp_path / "certificates.yaml"
    config_file.write_text("sslCACertificate: |\n  root\n", encoding="utf-8")
    result = CliRunner().invoke(
        cli,
        ["get-root-ca", "--config", str(config_file)],
    )
    assert result.exit_code != 0
    assert "sslAPICertificateFullChain" in result.output


def test_get_root_ca_rejects_non_mapping_config(tmp_path: Path) -> None:
    """Exit non-zero when --config is not a YAML mapping."""
    config_file = tmp_path / "certificates.yaml"
    config_file.write_text("- item1\n- item2\n", encoding="utf-8")
    result = CliRunner().invoke(
        cli,
        ["get-root-ca", "--config", str(config_file)],
    )
    assert result.exit_code != 0
    assert "YAML mapping" in result.output


def test_get_root_ca_rejects_non_string_fullchain(tmp_path: Path) -> None:
    """Exit non-zero when sslAPICertificateFullChain is a non-string (e.g. list)."""
    config_file = tmp_path / "certificates.yaml"
    config_file.write_text(
        "sslAPICertificateFullChain:\n  - item1\n  - item2\n", encoding="utf-8"
    )
    result = CliRunner().invoke(
        cli,
        ["get-root-ca", "--config", str(config_file)],
    )
    assert result.exit_code != 0


def test_get_root_ca_reports_yaml_parse_error(tmp_path: Path) -> None:
    """Report 'cannot parse' (not 'cannot read') for invalid YAML syntax."""
    config_file = tmp_path / "certificates.yaml"
    config_file.write_text("key: [\nbad yaml", encoding="utf-8")
    result = CliRunner().invoke(
        cli,
        ["get-root-ca", "--config", str(config_file)],
    )
    assert result.exit_code != 0
    assert "cannot parse" in result.output


def test_check_root_ca_help() -> None:
    """Show --config option in help."""
    result = CliRunner().invoke(cli, ["check-root-ca", "--help"])
    assert result.exit_code == 0
    assert "--config" in result.output


def test_check_root_ca_passes_when_no_chain_configured(tmp_path: Path) -> None:
    """Exit 0 when no TLS chains are configured."""
    config_file = tmp_path / "certificates.yaml"
    config_file.write_text("{}\n", encoding="utf-8")
    result = CliRunner().invoke(
        cli,
        ["check-root-ca", "--config", str(config_file)],
    )
    assert result.exit_code == 0


def test_check_root_ca_passes_when_ssl_ca_certificate_valid(
    tmp_path: Path, chain_pem: str, ca_pem: str
) -> None:
    """Exit 0 when sslCACertificate is set and contains valid PEM blocks."""
    config_file = tmp_path / "certificates.yaml"
    chain_indented = "\n".join("  " + line for line in (chain_pem + "\n").splitlines())
    ca_indented = "\n".join("  " + line for line in (ca_pem + "\n").splitlines())
    config_file.write_text(
        f"sslAPICertificateFullChain: |\n{chain_indented}\n"
        f"sslCACertificate: |\n{ca_indented}\n",
        encoding="utf-8",
    )
    result = CliRunner().invoke(
        cli,
        ["check-root-ca", "--config", str(config_file)],
    )
    assert result.exit_code == 0


def test_check_root_ca_fails_when_ssl_ca_certificate_not_pem(
    tmp_path: Path, chain_pem: str
) -> None:
    """Exit non-zero when sslCACertificate is set but contains no PEM blocks."""
    config_file = tmp_path / "certificates.yaml"
    chain_indented = "\n".join("  " + line for line in (chain_pem + "\n").splitlines())
    config_file.write_text(
        f"sslAPICertificateFullChain: |\n{chain_indented}\n"
        "sslCACertificate: not-a-pem\n",
        encoding="utf-8",
    )
    result = CliRunner().invoke(
        cli,
        ["check-root-ca", "--config", str(config_file)],
    )
    assert result.exit_code != 0
    assert "no valid PEM certificate blocks" in result.output


def test_check_root_ca_passes_when_trust_store_resolves(
    mocker: MockerFixture, tmp_path: Path, ca_pem: str, chain_pem: str
) -> None:
    """Exit 0 when no sslCACertificate and the trust store resolves the CA."""
    mocker.patch(
        "enclave.tools.cli.find_system_ca_for_chain", return_value=ca_pem + "\n"
    )
    config_file = tmp_path / "certificates.yaml"
    chain_indented = "\n".join("  " + line for line in (chain_pem + "\n").splitlines())
    config_file.write_text(
        f"sslAPICertificateFullChain: |\n{chain_indented}\n",
        encoding="utf-8",
    )
    result = CliRunner().invoke(
        cli,
        ["check-root-ca", "--config", str(config_file)],
    )
    assert result.exit_code == 0


def test_check_root_ca_fails_when_trust_store_returns_none(
    mocker: MockerFixture, tmp_path: Path, chain_pem: str
) -> None:
    """Exit non-zero when no sslCACertificate and trust store finds nothing."""
    mocker.patch("enclave.tools.cli.find_system_ca_for_chain", return_value=None)
    config_file = tmp_path / "certificates.yaml"
    chain_indented = "\n".join("  " + line for line in (chain_pem + "\n").splitlines())
    config_file.write_text(
        f"sslAPICertificateFullChain: |\n{chain_indented}\n",
        encoding="utf-8",
    )
    result = CliRunner().invoke(
        cli,
        ["check-root-ca", "--config", str(config_file)],
    )
    assert result.exit_code != 0
    assert "ca-bundle.crt" in result.output


def test_check_root_ca_rejects_missing_file() -> None:
    """Exit non-zero when --config path does not exist."""
    result = CliRunner().invoke(
        cli,
        ["check-root-ca", "--config", "/nonexistent/certificates.yaml"],
    )
    assert result.exit_code != 0
