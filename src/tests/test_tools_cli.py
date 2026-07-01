"""Tests for the enclave tools CLI: resolve-quay-registry-ca, check-certificate-chains, collect-node-image-digests."""

from pathlib import Path

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


def test_resolve_quay_registry_ca_forwards_ca_pem(mocker: MockerFixture) -> None:
    """Forward --ca-pem to quay_registry_ca_main."""
    mock_main = mocker.patch("enclave.tools.cli.quay_registry_ca_main")
    result = CliRunner().invoke(
        cli,
        [
            "resolve-quay-registry-ca",
            "--hostname",
            "registry.example.com",
            "--ca-pem",
            "-----BEGIN CERTIFICATE-----\nroot\n-----END CERTIFICATE-----\n",
        ],
        env=_KC,
    )
    assert result.exit_code == 0
    mock_main.assert_called_once_with(
        "registry.example.com",
        oc="oc",
        ca_pem="-----BEGIN CERTIFICATE-----\nroot\n-----END CERTIFICATE-----\n",
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
    mocker: MockerFixture, tmp_path: Path
) -> None:
    """Read sslCACertificate from --certificates-config and forward it."""
    mock_main = mocker.patch("enclave.tools.cli.quay_registry_ca_main")
    pem = "-----BEGIN CERTIFICATE-----\nroot\n-----END CERTIFICATE-----"
    config_file = tmp_path / "certificates.yaml"
    # Build a valid YAML block scalar: each PEM line must be indented.
    indented = "\n".join("  " + line for line in pem.split("\n"))
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
        ca_pem=pem,
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
    tmp_path: Path,
) -> None:
    """Reject when both --ca-pem and --certificates-config are supplied."""
    pem = "-----BEGIN CERTIFICATE-----\nroot\n-----END CERTIFICATE-----"
    config_file = tmp_path / "certificates.yaml"
    config_file.write_text(f"sslCACertificate: |\n  {pem}\n", encoding="utf-8")
    result = CliRunner().invoke(
        cli,
        [
            "resolve-quay-registry-ca",
            "--hostname",
            "registry.example.com",
            "--ca-pem",
            f"{pem}\n",
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
    """Forward --config path to check_certificate_chains_main."""
    mock_main = mocker.patch("enclave.tools.cli.check_certificate_chains_main")
    config_file = tmp_path / "certificates.yaml"
    config_file.write_text("", encoding="utf-8")
    result = CliRunner().invoke(
        cli,
        ["check-certificate-chains", "--config", str(config_file)],
    )
    assert result.exit_code == 0
    mock_main.assert_called_once_with(str(config_file))


def test_check_certificate_chains_reports_runtime_error(
    mocker: MockerFixture, tmp_path: Path
) -> None:
    """Surface a CertificateValidationError as a non-zero exit with field name."""
    mocker.patch(
        "enclave.tools.cli.check_certificate_chains_main",
        side_effect=CertificateValidationError(
            "sslAPICertificateFullChain: chain ends with a non-self-signed certificate"
        ),
    )
    config_file = tmp_path / "certificates.yaml"
    config_file.write_text("", encoding="utf-8")
    result = CliRunner().invoke(
        cli,
        ["check-certificate-chains", "--config", str(config_file)],
    )
    assert result.exit_code != 0
    assert "sslAPICertificateFullChain" in result.output


def test_check_certificate_chains_rejects_missing_file() -> None:
    """Reject a non-existent --config path."""
    result = CliRunner().invoke(
        cli,
        ["check-certificate-chains", "--config", "/nonexistent/certificates.yaml"],
    )
    assert result.exit_code != 0


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


def test_get_root_ca_from_chain_pem_option(mocker: MockerFixture) -> None:
    """Print the CA PEM returned by find_system_ca_for_chain when --chain-pem is given."""
    ca_pem = "-----BEGIN CERTIFICATE-----\nca\n-----END CERTIFICATE-----\n"
    mocker.patch("enclave.tools.cli.find_system_ca_for_chain", return_value=ca_pem)
    result = CliRunner().invoke(
        cli,
        [
            "get-root-ca",
            "--chain-pem",
            "-----BEGIN CERTIFICATE-----\nleaf\n-----END CERTIFICATE-----\n",
        ],
    )
    assert result.exit_code == 0
    assert ca_pem in result.output


def test_get_root_ca_from_config_prints_ca(
    mocker: MockerFixture, tmp_path: Path
) -> None:
    """Read the fullchain from --config and print the discovered CA PEM."""
    ca_pem = "-----BEGIN CERTIFICATE-----\nca\n-----END CERTIFICATE-----\n"
    mocker.patch("enclave.tools.cli.find_system_ca_for_chain", return_value=ca_pem)
    chain = "-----BEGIN CERTIFICATE-----\nchain\n-----END CERTIFICATE-----\n"
    config_file = tmp_path / "certificates.yaml"
    indented = "\n".join("  " + line for line in chain.splitlines())
    config_file.write_text(
        f"sslAPICertificateFullChain: |\n{indented}\n", encoding="utf-8"
    )
    result = CliRunner().invoke(
        cli,
        ["get-root-ca", "--config", str(config_file)],
    )
    assert result.exit_code == 0
    assert ca_pem in result.output


def test_get_root_ca_from_config_falls_back_to_ingress_chain(
    mocker: MockerFixture, tmp_path: Path
) -> None:
    """Fall back to sslIngressCertificateFullChain when API chain is absent."""
    ca_pem = "-----BEGIN CERTIFICATE-----\nca\n-----END CERTIFICATE-----\n"
    mock_fn = mocker.patch(
        "enclave.tools.cli.find_system_ca_for_chain", return_value=ca_pem
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


def test_get_root_ca_not_found(mocker: MockerFixture) -> None:
    """Exit non-zero when find_system_ca_for_chain returns None."""
    mocker.patch("enclave.tools.cli.find_system_ca_for_chain", return_value=None)
    result = CliRunner().invoke(
        cli,
        [
            "get-root-ca",
            "--chain-pem",
            "-----BEGIN CERTIFICATE-----\nleaf\n-----END CERTIFICATE-----\n",
        ],
    )
    assert result.exit_code != 0
    assert "no matching CA" in result.output


def test_get_root_ca_rejects_both_options(tmp_path: Path) -> None:
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
            "-----BEGIN CERTIFICATE-----\nleaf\n-----END CERTIFICATE-----\n",
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
