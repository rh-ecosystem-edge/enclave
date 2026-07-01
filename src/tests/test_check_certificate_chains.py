"""Tests for check_certificate_chains: chain completeness and CA consistency validation."""

from pathlib import Path

import pytest
from pytest_mock import MockerFixture

from enclave.tools.check_certificate_chains import (
    CertificateValidationError,
    check_certificate_chains,
)

_LEAF = """-----BEGIN CERTIFICATE-----
leaf
-----END CERTIFICATE-----"""
_INTERMEDIATE = """-----BEGIN CERTIFICATE-----
intermediate
-----END CERTIFICATE-----"""
_ROOT = """-----BEGIN CERTIFICATE-----
root
-----END CERTIFICATE-----"""


def _write_certs(tmp_path: Path, **kwargs: str) -> str:
    content = "\n".join(
        f"{k}: |\n  " + v.replace("\n", "\n  ") for k, v in kwargs.items()
    )
    path = tmp_path / "certificates.yaml"
    path.write_text(content, encoding="utf-8")
    return str(path)


def test_check_passes_when_chain_ends_with_self_signed_root(
    mocker: MockerFixture, tmp_path: Path
) -> None:
    """Pass when the chain ends with a self-signed root."""
    mocker.patch(
        "enclave.tools.check_certificate_chains.is_self_signed", return_value=True
    )
    path = _write_certs(
        tmp_path,
        sslAPICertificateFullChain=f"{_LEAF}\n{_ROOT}",
    )
    check_certificate_chains(path)


def test_check_passes_when_ca_matches_self_signed_root(
    mocker: MockerFixture, tmp_path: Path
) -> None:
    """Pass when sslCACertificate verifies a self-signed root."""
    mocker.patch(
        "enclave.tools.check_certificate_chains.is_self_signed", return_value=True
    )
    mocker.patch(
        "enclave.tools.check_certificate_chains.openssl_verify", return_value=True
    )
    path = _write_certs(
        tmp_path,
        sslAPICertificateFullChain=f"{_LEAF}\n{_ROOT}",
        sslCACertificate=_ROOT,
    )
    check_certificate_chains(path)


def test_check_raises_when_ca_does_not_match_self_signed_root(
    mocker: MockerFixture, tmp_path: Path
) -> None:
    """Raise when sslCACertificate does not verify the self-signed root."""
    mocker.patch(
        "enclave.tools.check_certificate_chains.is_self_signed", return_value=True
    )
    mocker.patch(
        "enclave.tools.check_certificate_chains.openssl_verify", return_value=False
    )
    path = _write_certs(
        tmp_path,
        sslAPICertificateFullChain=f"{_LEAF}\n{_ROOT}",
        sslCACertificate=_INTERMEDIATE,
    )
    with pytest.raises(CertificateValidationError, match="sslAPICertificateFullChain"):
        check_certificate_chains(path)


def test_check_passes_when_chain_incomplete_but_ca_pem_set(
    mocker: MockerFixture, tmp_path: Path
) -> None:
    """Pass when an incomplete chain is completed by sslCACertificate."""
    mocker.patch(
        "enclave.tools.check_certificate_chains.is_self_signed", return_value=False
    )
    mocker.patch(
        "enclave.tools.check_certificate_chains.openssl_verify", return_value=True
    )
    path = _write_certs(
        tmp_path,
        sslAPICertificateFullChain=f"{_LEAF}\n{_INTERMEDIATE}",
        sslCACertificate=_ROOT,
    )
    check_certificate_chains(path)


def test_check_raises_when_ca_does_not_sign_last_chain_cert(
    mocker: MockerFixture, tmp_path: Path
) -> None:
    """Raise when sslCACertificate does not sign the last chain cert."""
    mocker.patch(
        "enclave.tools.check_certificate_chains.is_self_signed", return_value=False
    )
    mocker.patch(
        "enclave.tools.check_certificate_chains.openssl_verify", return_value=False
    )
    path = _write_certs(
        tmp_path,
        sslAPICertificateFullChain=f"{_LEAF}\n{_INTERMEDIATE}",
        sslCACertificate=_ROOT,
    )
    with pytest.raises(CertificateValidationError, match="sslAPICertificateFullChain"):
        check_certificate_chains(path)


def test_check_raises_when_api_chain_incomplete_without_ca(
    mocker: MockerFixture, tmp_path: Path
) -> None:
    """Raise when API chain is incomplete and no sslCACertificate is set."""
    mocker.patch(
        "enclave.tools.check_certificate_chains.is_self_signed", return_value=False
    )
    path = _write_certs(
        tmp_path,
        sslAPICertificateFullChain=f"{_LEAF}\n{_INTERMEDIATE}",
    )
    with pytest.raises(CertificateValidationError, match="sslAPICertificateFullChain"):
        check_certificate_chains(path)


def test_check_raises_when_ingress_chain_incomplete_without_ca(
    mocker: MockerFixture, tmp_path: Path
) -> None:
    """Raise when ingress chain is incomplete and no sslCACertificate is set."""
    mocker.patch(
        "enclave.tools.check_certificate_chains.is_self_signed", return_value=False
    )
    path = _write_certs(
        tmp_path,
        sslIngressCertificateFullChain=f"{_LEAF}\n{_INTERMEDIATE}",
    )
    with pytest.raises(
        CertificateValidationError, match="sslIngressCertificateFullChain"
    ):
        check_certificate_chains(path)


def test_check_reports_both_fields_when_both_incomplete(
    mocker: MockerFixture, tmp_path: Path
) -> None:
    """Report both field names when both chains are incomplete."""
    mocker.patch(
        "enclave.tools.check_certificate_chains.is_self_signed", return_value=False
    )
    path = _write_certs(
        tmp_path,
        sslAPICertificateFullChain=f"{_LEAF}\n{_INTERMEDIATE}",
        sslIngressCertificateFullChain=f"{_LEAF}\n{_INTERMEDIATE}",
    )
    with pytest.raises(CertificateValidationError) as exc_info:
        check_certificate_chains(path)
    msg = str(exc_info.value)
    assert "sslAPICertificateFullChain" in msg
    assert "sslIngressCertificateFullChain" in msg


def test_check_skips_absent_chain_fields(tmp_path: Path) -> None:
    """Skip fields that are absent or empty in the config."""
    path = tmp_path / "certificates.yaml"
    path.write_text("ironicHTTPSCertificate: ''\n", encoding="utf-8")
    check_certificate_chains(str(path))


def test_check_raises_leaf_only_chain_without_ca(
    mocker: MockerFixture, tmp_path: Path
) -> None:
    """Raise when only a leaf cert is present and no sslCACertificate is set."""
    mocker.patch(
        "enclave.tools.check_certificate_chains.is_self_signed", return_value=False
    )
    path = _write_certs(tmp_path, sslAPICertificateFullChain=_LEAF)
    with pytest.raises(CertificateValidationError, match="sslAPICertificateFullChain"):
        check_certificate_chains(str(path))


def test_check_passes_leaf_only_chain_when_ca_set(
    mocker: MockerFixture, tmp_path: Path
) -> None:
    """Pass when a leaf-only chain is completed by sslCACertificate."""
    mocker.patch(
        "enclave.tools.check_certificate_chains.is_self_signed", return_value=False
    )
    mocker.patch(
        "enclave.tools.check_certificate_chains.openssl_verify", return_value=True
    )
    path = _write_certs(
        tmp_path,
        sslAPICertificateFullChain=_LEAF,
        sslCACertificate=_ROOT,
    )
    check_certificate_chains(str(path))


def test_check_raises_when_chain_has_content_but_no_pem_blocks(
    tmp_path: Path,
) -> None:
    """Raise when a chain field is set to text that contains no PEM certificate blocks."""
    path = _write_certs(tmp_path, sslAPICertificateFullChain="not a certificate")
    with pytest.raises(CertificateValidationError, match="no PEM certificate blocks"):
        check_certificate_chains(str(path))


def test_check_raises_on_missing_file() -> None:
    """Raise when the config file does not exist."""
    with pytest.raises(CertificateValidationError, match="cannot read"):
        check_certificate_chains("/nonexistent/path/certificates.yaml")


def test_check_raises_on_invalid_yaml(tmp_path: Path) -> None:
    """Raise when the config file contains invalid YAML."""
    path = tmp_path / "certificates.yaml"
    path.write_text("key: [\nbad yaml", encoding="utf-8")
    with pytest.raises(CertificateValidationError, match="cannot parse"):
        check_certificate_chains(str(path))


def test_check_raises_on_non_mapping_yaml(tmp_path: Path) -> None:
    """Raise when the config file is not a YAML mapping."""
    path = tmp_path / "certificates.yaml"
    path.write_text("- item1\n- item2\n", encoding="utf-8")
    with pytest.raises(CertificateValidationError, match="expected a YAML mapping"):
        check_certificate_chains(str(path))


def test_check_passes_via_system_store_when_no_ca_set(
    mocker: MockerFixture, tmp_path: Path
) -> None:
    """Pass when no sslCACertificate is set but the system trust store provides one."""
    mocker.patch(
        "enclave.tools.check_certificate_chains.is_self_signed", return_value=False
    )
    mocker.patch(
        "enclave.tools.check_certificate_chains.find_system_ca_for_chain",
        return_value=_ROOT,
    )
    mocker.patch(
        "enclave.tools.check_certificate_chains.openssl_verify", return_value=True
    )
    path = _write_certs(
        tmp_path,
        sslAPICertificateFullChain=f"{_LEAF}\n{_INTERMEDIATE}",
    )
    check_certificate_chains(path)


def test_check_raises_when_system_store_also_has_no_match(
    mocker: MockerFixture, tmp_path: Path
) -> None:
    """Raise when neither sslCACertificate nor the system trust store has a match."""
    mocker.patch(
        "enclave.tools.check_certificate_chains.is_self_signed", return_value=False
    )
    mocker.patch(
        "enclave.tools.check_certificate_chains.find_system_ca_for_chain",
        return_value=None,
    )
    path = _write_certs(
        tmp_path,
        sslAPICertificateFullChain=f"{_LEAF}\n{_INTERMEDIATE}",
    )
    with pytest.raises(CertificateValidationError, match="sslAPICertificateFullChain"):
        check_certificate_chains(path)
