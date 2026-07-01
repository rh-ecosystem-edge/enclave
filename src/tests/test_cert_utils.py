"""Tests for cert_utils: pem_blocks, is_self_signed, openssl_verify."""

import subprocess
from pathlib import Path
from subprocess import CompletedProcess

import pytest
from pytest_mock import MockerFixture

from enclave.tools.cert_utils import is_self_signed, openssl_verify, pem_blocks

_LEAF = """-----BEGIN CERTIFICATE-----
leaf
-----END CERTIFICATE-----"""
_INTERMEDIATE = """-----BEGIN CERTIFICATE-----
intermediate
-----END CERTIFICATE-----"""
_ROOT = """-----BEGIN CERTIFICATE-----
root
-----END CERTIFICATE-----"""
_ROUTER_CA = """-----BEGIN CERTIFICATE-----
router-ca
-----END CERTIFICATE-----"""


def test_pem_blocks_splits_multiple_certificates() -> None:
    """Split a multi-cert PEM string into individual blocks."""
    chain = f"{_LEAF}\n{_ROOT}\n"
    assert pem_blocks(chain) == [_LEAF, _ROOT]


def test_is_self_signed_returns_true_when_issuer_equals_subject(
    mocker: MockerFixture,
) -> None:
    """Return True when issuer and subject are identical and self-signature check passes."""
    mocker.patch(
        "enclave.tools.cert_utils.subprocess.run",
        side_effect=[
            CompletedProcess(
                args=["openssl"],
                returncode=0,
                stdout="issuer=CN=Root CA,O=Test,C=US\nsubject=CN=Root CA,O=Test,C=US\n",
                stderr="",
            ),
            CompletedProcess(
                args=["openssl"],
                returncode=0,
                stdout="cert.pem: OK\n",
                stderr="",
            ),
        ],
    )
    assert is_self_signed(_ROOT) is True


def test_is_self_signed_returns_false_when_issuer_differs(
    mocker: MockerFixture,
) -> None:
    """Return False when issuer and subject differ."""
    mocker.patch(
        "enclave.tools.cert_utils.subprocess.run",
        return_value=CompletedProcess(
            args=["openssl"],
            returncode=0,
            stdout="issuer=CN=Root CA,O=Test,C=US\nsubject=CN=Intermediate CA,O=Test,C=US\n",
            stderr="",
        ),
    )
    assert is_self_signed(_INTERMEDIATE) is False


def test_is_self_signed_returns_false_on_openssl_error(mocker: MockerFixture) -> None:
    """Return False when openssl exits non-zero."""
    mocker.patch(
        "enclave.tools.cert_utils.subprocess.run",
        return_value=CompletedProcess(
            args=["openssl"],
            returncode=1,
            stdout="",
            stderr="unable to load certificate",
        ),
    )
    assert is_self_signed(_LEAF) is False


def test_is_self_signed_returns_false_on_os_error(mocker: MockerFixture) -> None:
    """Return False when openssl is not found."""
    mocker.patch(
        "enclave.tools.cert_utils.subprocess.run",
        side_effect=OSError("openssl not found"),
    )
    assert is_self_signed(_ROOT) is False


def test_openssl_verify_missing_openssl_raises_runtime_error(
    mocker: MockerFixture,
) -> None:
    """Raise RuntimeError when openssl binary is missing."""
    mocker.patch(
        "enclave.tools.cert_utils.subprocess.run",
        side_effect=FileNotFoundError(2, "No such file or directory", "openssl"),
    )
    with pytest.raises(RuntimeError, match="openssl not found"):
        openssl_verify(_ROUTER_CA, _LEAF)


def test_openssl_verify_isolates_from_system_trust_store(
    mocker: MockerFixture,
) -> None:
    """Pass -no-CAfile and -no-CApath to isolate from the system trust store."""
    mock_run = mocker.patch(
        "enclave.tools.cert_utils.subprocess.run",
        return_value=CompletedProcess(
            args=["openssl"], returncode=0, stdout="", stderr=""
        ),
    )
    openssl_verify(_ROUTER_CA, _LEAF)
    cmd = mock_run.call_args[0][0]
    assert "-no-CAfile" in cmd
    assert "-no-CApath" in cmd


def _generate_self_signed_ca(tmp_path: Path, cn: str) -> str:
    cert = tmp_path / f"{cn}.crt"
    subprocess.run(
        [
            "openssl",
            "req",
            "-x509",
            "-newkey",
            "ec",
            "-pkeyopt",
            "ec_paramgen_curve:P-256",
            "-keyout",
            str(tmp_path / f"{cn}.key"),
            "-out",
            str(cert),
            "-days",
            "1",
            "-nodes",
            "-subj",
            f"/CN={cn}",
        ],
        check=True,
        capture_output=True,
    )
    return cert.read_text(encoding="utf-8")


def test_openssl_verify_self_signed_cert_verifies_against_itself(
    tmp_path: Path,
) -> None:
    """A self-signed CA certificate verifies against itself."""
    ca_pem = _generate_self_signed_ca(tmp_path, "Test CA")
    assert openssl_verify(ca_pem, ca_pem) is True


def test_openssl_verify_self_signed_cert_fails_against_different_ca(
    tmp_path: Path,
) -> None:
    """A self-signed CA does not verify a certificate from a different CA."""
    ca_a = _generate_self_signed_ca(tmp_path, "CA-A")
    ca_b = _generate_self_signed_ca(tmp_path, "CA-B")
    assert openssl_verify(ca_a, ca_b) is False


def _generate_self_issued_not_self_signed_cert(tmp_path: Path) -> str:
    """Generate a cert that is self-issued (issuer==subject) but NOT self-signed.

    The cert has subject=CN=Cross CA and issuer=CN=Cross CA because the signing CA
    was created with the same subject DN. However, the public key embedded in the cert
    belongs to a different keypair, so the self-signature check fails.
    """
    signing_key = tmp_path / "signing.key"
    signing_cert = tmp_path / "signing.crt"
    subprocess.run(
        [
            "openssl",
            "req",
            "-x509",
            "-newkey",
            "ec",
            "-pkeyopt",
            "ec_paramgen_curve:P-256",
            "-keyout",
            str(signing_key),
            "-out",
            str(signing_cert),
            "-days",
            "1",
            "-nodes",
            "-subj",
            "/CN=Cross CA",
        ],
        check=True,
        capture_output=True,
    )
    subject_key = tmp_path / "subject.key"
    subprocess.run(
        [
            "openssl",
            "genpkey",
            "-algorithm",
            "EC",
            "-pkeyopt",
            "ec_paramgen_curve:P-256",
            "-out",
            str(subject_key),
        ],
        check=True,
        capture_output=True,
    )
    subject_csr = tmp_path / "subject.csr"
    subprocess.run(
        [
            "openssl",
            "req",
            "-new",
            "-key",
            str(subject_key),
            "-out",
            str(subject_csr),
            "-subj",
            "/CN=Cross CA",
        ],
        check=True,
        capture_output=True,
    )
    cross_cert = tmp_path / "cross.crt"
    subprocess.run(
        [
            "openssl",
            "x509",
            "-req",
            "-in",
            str(subject_csr),
            "-CA",
            str(signing_cert),
            "-CAkey",
            str(signing_key),
            "-out",
            str(cross_cert),
            "-days",
            "1",
            "-set_serial",
            "01",
        ],
        check=True,
        capture_output=True,
    )
    return cross_cert.read_text(encoding="utf-8")


def test_is_self_signed_returns_true_for_genuinely_self_signed_cert(
    tmp_path: Path,
) -> None:
    """A genuinely self-signed CA cert is correctly identified as self-signed."""
    ca_pem = _generate_self_signed_ca(tmp_path, "Root CA")
    assert is_self_signed(ca_pem) is True


def test_is_self_signed_returns_false_for_self_issued_but_not_self_signed(
    tmp_path: Path,
) -> None:
    """A cert with issuer==subject but signed by a different key is not self-signed."""
    cross_pem = _generate_self_issued_not_self_signed_cert(tmp_path)
    assert is_self_signed(cross_pem) is False
