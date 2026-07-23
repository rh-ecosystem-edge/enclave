"""Tests for cert_utils: pem_blocks, is_self_signed, openssl_verify.

This module is the authoritative test source for openssl subprocess calls and
certificate parsing. Mocking subprocess.run is intentionally absent — if the
real openssl invocations are wrong (wrong flags, wrong arg order, wrong
temp-file handling), mocked tests cannot catch it. All tests use real openssl
binaries via module-scoped fixtures that generate certificates once per run.
"""

from pathlib import Path

import pytest

from enclave.tools.cert_utils import (
    cert_covers_hostname,
    cert_key_pair_matches,
    cert_validity_window_ok,
    is_self_signed,
    openssl_verify,
    pem_blocks,
)
from tests.cert_helpers import (
    generate_ca,
    generate_expired_cert,
    generate_expired_self_signed,
    generate_self_issued_not_self_signed_cert,
    generate_signed_leaf,
)


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


def test_is_self_signed_returns_true_for_expired_self_signed(tmp_path: Path) -> None:
    """Return True for a self-signed cert whose validity window has passed.

    is_self_signed is a structural check: it verifies that the cert's signature was
    made by its own key, regardless of whether the cert is currently within its
    validity period.
    """
    expired_ss_pem = generate_expired_self_signed(tmp_path, "Expired CA")
    assert is_self_signed(expired_ss_pem) is True


def test_openssl_verify_expired_cert_returns_false(tmp_path: Path) -> None:
    """openssl_verify returns False when the cert is expired."""
    ca_pem, ca_cert_path, ca_key_path = generate_ca(tmp_path, "Test CA")
    expired_pem = generate_expired_cert(tmp_path, ca_cert_path, ca_key_path, "Leaf")
    assert openssl_verify(ca_pem, expired_pem) is False


def test_cert_key_pair_matches_returns_true(tmp_path: Path) -> None:
    """cert_key_pair_matches returns True when the key was used to generate the cert.

    generate_signed_leaf writes the leaf key to tmp_path/leaf.key; read it back
    and verify it matches the returned cert PEM.
    """
    _, ca_cert_path, ca_key_path = generate_ca(tmp_path, "CA")
    leaf_pem = generate_signed_leaf(tmp_path, ca_cert_path, ca_key_path, "Leaf")
    leaf_key_pem = (tmp_path / "leaf.key").read_text(encoding="utf-8")
    assert cert_key_pair_matches(leaf_pem, leaf_key_pem) is True


def test_cert_key_pair_matches_returns_false(tmp_path: Path) -> None:
    """cert_key_pair_matches returns False when the key belongs to a different pair."""
    _, ca_cert_path, ca_key_path = generate_ca(tmp_path, "CA")
    leaf_pem = generate_signed_leaf(tmp_path, ca_cert_path, ca_key_path, "Leaf")
    # The CA key is unrelated to the leaf cert's key.
    assert (
        cert_key_pair_matches(leaf_pem, ca_key_path.read_text(encoding="utf-8"))
        is False
    )


def test_cert_validity_window_ok_returns_true(module_ca_pem: str) -> None:
    """cert_validity_window_ok returns True for a freshly generated cert."""
    assert cert_validity_window_ok(module_ca_pem) is True


def test_cert_validity_window_ok_returns_false_for_expired(tmp_path: Path) -> None:
    """cert_validity_window_ok returns False for a cert with a past validity window."""
    _, ca_cert_path, ca_key_path = generate_ca(tmp_path, "CA")
    expired_pem = generate_expired_cert(tmp_path, ca_cert_path, ca_key_path, "Leaf")
    assert cert_validity_window_ok(expired_pem) is False


def test_cert_covers_hostname_exact_match(tmp_path: Path) -> None:
    """cert_covers_hostname returns True for an exact DNS SAN match."""
    _, ca_cert_path, ca_key_path = generate_ca(tmp_path, "CA")
    leaf_pem = generate_signed_leaf(
        tmp_path, ca_cert_path, ca_key_path, "Leaf", sans=["foo.example.com"]
    )
    assert cert_covers_hostname(leaf_pem, "foo.example.com") is True


def test_cert_covers_hostname_wildcard_match(tmp_path: Path) -> None:
    """cert_covers_hostname returns True when a wildcard SAN covers the hostname."""
    _, ca_cert_path, ca_key_path = generate_ca(tmp_path, "CA")
    leaf_pem = generate_signed_leaf(
        tmp_path, ca_cert_path, ca_key_path, "Leaf", sans=["*.example.com"]
    )
    assert cert_covers_hostname(leaf_pem, "foo.example.com") is True


def test_cert_covers_hostname_wildcard_does_not_match_parent(tmp_path: Path) -> None:
    """cert_covers_hostname returns False when the wildcard does not cover the apex domain."""
    _, ca_cert_path, ca_key_path = generate_ca(tmp_path, "CA")
    leaf_pem = generate_signed_leaf(
        tmp_path, ca_cert_path, ca_key_path, "Leaf", sans=["*.example.com"]
    )
    assert cert_covers_hostname(leaf_pem, "example.com") is False


def test_cert_covers_hostname_wildcard_does_not_match_subdomain(tmp_path: Path) -> None:
    """cert_covers_hostname returns False when the wildcard does not span multiple labels."""
    _, ca_cert_path, ca_key_path = generate_ca(tmp_path, "CA")
    leaf_pem = generate_signed_leaf(
        tmp_path, ca_cert_path, ca_key_path, "Leaf", sans=["*.example.com"]
    )
    assert cert_covers_hostname(leaf_pem, "foo.bar.example.com") is False


def test_cert_covers_hostname_returns_false_when_no_san(tmp_path: Path) -> None:
    """cert_covers_hostname returns False when the cert has no SAN extension."""
    _, ca_cert_path, ca_key_path = generate_ca(tmp_path, "CA")
    leaf_pem = generate_signed_leaf(
        tmp_path, ca_cert_path, ca_key_path, "foo.example.com"
    )
    assert cert_covers_hostname(leaf_pem, "foo.example.com") is False


def test_cert_covers_hostname_mismatch(tmp_path: Path) -> None:
    """cert_covers_hostname returns False when no SAN covers the requested hostname."""
    _, ca_cert_path, ca_key_path = generate_ca(tmp_path, "CA")
    leaf_pem = generate_signed_leaf(
        tmp_path, ca_cert_path, ca_key_path, "Leaf", sans=["bar.example.com"]
    )
    assert cert_covers_hostname(leaf_pem, "foo.example.com") is False
