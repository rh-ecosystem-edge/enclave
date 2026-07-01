"""Tests for system_ca: RHEL 10 trust store CA discovery."""

import subprocess
from pathlib import Path

import pytest
from pytest_mock import MockerFixture

from enclave.tools.system_ca import find_system_ca_for_chain

_INTERMEDIATE = """-----BEGIN CERTIFICATE-----
intermediate
-----END CERTIFICATE-----"""
_ROOT = """-----BEGIN CERTIFICATE-----
root
-----END CERTIFICATE-----"""


def test_find_returns_none_when_chain_empty() -> None:
    """Return None when the chain has no PEM blocks."""
    assert find_system_ca_for_chain("") is None


def test_find_returns_none_when_chain_has_no_pem_blocks() -> None:
    """Return None when the chain string contains no PEM certificate blocks."""
    assert find_system_ca_for_chain("not a certificate") is None


def test_find_returns_none_when_chain_self_signed(mocker: MockerFixture) -> None:
    """Return None when the last certificate in the chain is self-signed."""
    mocker.patch("enclave.tools.system_ca.is_self_signed", return_value=True)
    assert find_system_ca_for_chain(_ROOT) is None


def test_find_returns_none_when_trust_store_missing(mocker: MockerFixture) -> None:
    """Return None when the RHEL trust store file is not available."""
    mocker.patch("enclave.tools.system_ca.is_self_signed", return_value=False)
    mocker.patch(
        "enclave.tools.system_ca._RHEL_TRUST_STORE",
        new=Path("/nonexistent/ca-bundle.crt"),
    )
    assert find_system_ca_for_chain(_INTERMEDIATE) is None


def test_find_returns_none_when_no_ca_matches(
    mocker: MockerFixture, tmp_path: Path
) -> None:
    """Return None when no CA in the trust store verifies the chain's last cert."""
    mocker.patch("enclave.tools.system_ca.is_self_signed", return_value=False)
    mocker.patch("enclave.tools.system_ca.openssl_verify", return_value=False)
    bundle = tmp_path / "ca-bundle.crt"
    bundle.write_text(_ROOT, encoding="utf-8")
    mocker.patch("enclave.tools.system_ca._RHEL_TRUST_STORE", new=bundle)
    assert find_system_ca_for_chain(_INTERMEDIATE) is None


def test_find_returns_first_matching_ca(mocker: MockerFixture, tmp_path: Path) -> None:
    """Return the first CA that verifies the chain's last certificate."""
    mocker.patch("enclave.tools.system_ca.is_self_signed", return_value=False)
    verify = mocker.patch(
        "enclave.tools.system_ca.openssl_verify", side_effect=[False, True]
    )
    ca_a = "-----BEGIN CERTIFICATE-----\nca-a\n-----END CERTIFICATE-----"
    ca_b = "-----BEGIN CERTIFICATE-----\nca-b\n-----END CERTIFICATE-----"
    bundle = tmp_path / "ca-bundle.crt"
    bundle.write_text(f"{ca_a}\n{ca_b}\n", encoding="utf-8")
    mocker.patch("enclave.tools.system_ca._RHEL_TRUST_STORE", new=bundle)
    result = find_system_ca_for_chain(_INTERMEDIATE)
    assert result == ca_b
    assert verify.call_count == 2


def _generate_ca_and_leaf(tmp_path: Path, prefix: str) -> tuple[str, str]:
    """Generate a CA cert and a leaf cert signed by it. Returns (ca_pem, leaf_pem)."""
    ca_key = tmp_path / f"{prefix}-ca.key"
    ca_cert = tmp_path / f"{prefix}-ca.crt"
    leaf_key = tmp_path / f"{prefix}-leaf.key"
    leaf_csr = tmp_path / f"{prefix}-leaf.csr"
    leaf_cert = tmp_path / f"{prefix}-leaf.crt"
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
            str(ca_key),
            "-out",
            str(ca_cert),
            "-days",
            "1",
            "-nodes",
            "-subj",
            f"/CN={prefix} CA",
        ],
        check=True,
        capture_output=True,
    )
    subprocess.run(
        [
            "openssl",
            "req",
            "-newkey",
            "ec",
            "-pkeyopt",
            "ec_paramgen_curve:P-256",
            "-keyout",
            str(leaf_key),
            "-out",
            str(leaf_csr),
            "-nodes",
            "-subj",
            f"/CN={prefix} Leaf",
        ],
        check=True,
        capture_output=True,
    )
    subprocess.run(
        [
            "openssl",
            "x509",
            "-req",
            "-in",
            str(leaf_csr),
            "-CA",
            str(ca_cert),
            "-CAkey",
            str(ca_key),
            "-out",
            str(leaf_cert),
            "-days",
            "1",
        ],
        check=True,
        capture_output=True,
    )
    return ca_cert.read_text(encoding="utf-8"), leaf_cert.read_text(encoding="utf-8")


def test_find_integration_matches_ca_in_bundle(
    mocker: MockerFixture, tmp_path: Path
) -> None:
    """Integration: discover the real CA from a synthetic trust store bundle."""
    ca_pem, leaf_pem = _generate_ca_and_leaf(tmp_path, "test")
    bundle = tmp_path / "ca-bundle.crt"
    bundle.write_text(ca_pem, encoding="utf-8")
    mocker.patch("enclave.tools.system_ca._RHEL_TRUST_STORE", new=bundle)
    result = find_system_ca_for_chain(leaf_pem)
    assert result is not None
    assert "BEGIN CERTIFICATE" in result


def test_find_integration_returns_none_when_ca_not_in_bundle(
    mocker: MockerFixture, tmp_path: Path
) -> None:
    """Integration: return None when no CA in the bundle signs the chain."""
    _, leaf_a = _generate_ca_and_leaf(tmp_path, "a")
    ca_b, _ = _generate_ca_and_leaf(tmp_path, "b")
    bundle = tmp_path / "ca-bundle.crt"
    bundle.write_text(ca_b, encoding="utf-8")
    mocker.patch("enclave.tools.system_ca._RHEL_TRUST_STORE", new=bundle)
    assert find_system_ca_for_chain(leaf_a) is None


def test_find_integration_returns_none_when_chain_is_self_signed(
    mocker: MockerFixture, tmp_path: Path
) -> None:
    """Integration: return None when the chain ends with a self-signed root."""
    ca_a, _ = _generate_ca_and_leaf(tmp_path, "a")
    bundle = tmp_path / "ca-bundle.crt"
    bundle.write_text(ca_a, encoding="utf-8")
    mocker.patch("enclave.tools.system_ca._RHEL_TRUST_STORE", new=bundle)
    assert find_system_ca_for_chain(ca_a) is None


@pytest.mark.parametrize("exc", [OSError("no permission"), PermissionError("denied")])
def test_find_returns_none_on_trust_store_read_error(
    mocker: MockerFixture, exc: Exception
) -> None:
    """Return None when reading the trust store raises an OSError."""
    mocker.patch("enclave.tools.system_ca.is_self_signed", return_value=False)
    mocker.patch(
        "enclave.tools.system_ca._RHEL_TRUST_STORE",
        new=Path("/fake/ca-bundle.crt"),
    )
    mocker.patch.object(Path, "read_text", side_effect=exc)
    assert find_system_ca_for_chain(_INTERMEDIATE) is None
