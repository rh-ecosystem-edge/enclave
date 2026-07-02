"""Tests for cert_utils: pem_blocks, is_self_signed, openssl_verify.

This module is the authoritative test source for openssl subprocess calls and
certificate parsing. Mocking subprocess.run is intentionally absent — if the
real openssl invocations are wrong (wrong flags, wrong arg order, wrong
temp-file handling), mocked tests cannot catch it. All tests use real openssl
binaries via module-scoped fixtures that generate certificates once per run.
"""

from pathlib import Path

import pytest

from enclave.tools.cert_utils import is_self_signed, openssl_verify, pem_blocks
from tests.cert_helpers import generate_ca, generate_self_issued_not_self_signed_cert


def test_pem_blocks_splits_multiple_certificates(
    module_ca_pem: str, module_leaf_pem: str
) -> None:
    """Split a multi-cert PEM string into individual blocks using real certs."""
    chain = f"{module_ca_pem}\n{module_leaf_pem}\n"
    assert pem_blocks(chain) == [module_ca_pem, module_leaf_pem]


def test_is_self_signed_returns_false_when_issuer_differs(module_leaf_pem: str) -> None:
    """Return False when issuer (CA) and subject (Leaf) differ."""
    assert is_self_signed(module_leaf_pem) is False


def test_is_self_signed_returns_false_on_invalid_input() -> None:
    """Return False when the input is not a valid PEM certificate."""
    assert is_self_signed("not a certificate") is False


def test_is_self_signed_returns_false_when_openssl_missing(
    monkeypatch: pytest.MonkeyPatch, module_ca_pem: str
) -> None:
    """Return False when openssl is not found in PATH."""
    monkeypatch.setenv("PATH", "")
    assert is_self_signed(module_ca_pem) is False


def test_openssl_verify_raises_when_openssl_missing(
    monkeypatch: pytest.MonkeyPatch, module_ca_pem: str
) -> None:
    """Raise RuntimeError when openssl is not found in PATH."""
    monkeypatch.setenv("PATH", "")
    with pytest.raises(RuntimeError, match="openssl not found"):
        openssl_verify(module_ca_pem, module_ca_pem)


def test_openssl_verify_self_signed_cert_verifies_against_itself(
    module_ca_pem: str,
) -> None:
    """A self-signed CA certificate verifies against itself."""
    assert openssl_verify(module_ca_pem, module_ca_pem) is True


def test_openssl_verify_self_signed_cert_fails_against_different_ca(
    tmp_path: Path, module_ca_pem: str
) -> None:
    """A self-signed CA does not verify a certificate from a different CA."""
    ca2_pem, _, _ = generate_ca(tmp_path, "CA 2")
    assert openssl_verify(module_ca_pem, ca2_pem) is False


def test_is_self_signed_returns_true_for_genuinely_self_signed_cert(
    module_ca_pem: str,
) -> None:
    """A genuinely self-signed CA cert is correctly identified as self-signed."""
    assert is_self_signed(module_ca_pem) is True


def test_is_self_signed_returns_false_for_self_issued_but_not_self_signed(
    tmp_path: Path,
) -> None:
    """A cert with issuer==subject but signed by a different key is not self-signed."""
    cross_pem = generate_self_issued_not_self_signed_cert(tmp_path)
    assert is_self_signed(cross_pem) is False
