"""Tests for system_ca: RHEL 10 trust store CA discovery."""

from pathlib import Path

import pytest
from pytest_mock import MockerFixture

from enclave.tools.system_ca import find_system_ca_for_chain
from tests.cert_helpers import generate_ca


def test_find_raises_when_chain_empty() -> None:
    """Raise ValueError when the chain has no PEM blocks."""
    with pytest.raises(ValueError, match="no PEM blocks in chain"):
        find_system_ca_for_chain("")


def test_find_raises_when_chain_has_no_pem_blocks() -> None:
    """Raise ValueError when the chain string contains no PEM certificate blocks."""
    with pytest.raises(ValueError, match="no PEM blocks in chain"):
        find_system_ca_for_chain("not a certificate")


def test_find_returns_none_when_chain_self_signed(
    mocker: MockerFixture, root_pem: str
) -> None:
    """Return None when the last certificate in the chain is self-signed."""
    mocker.patch("enclave.tools.system_ca.is_self_signed", return_value=True)
    assert find_system_ca_for_chain(root_pem) is None


def test_find_raises_when_trust_store_missing(
    mocker: MockerFixture, intermediate_pem: str
) -> None:
    """Raise RuntimeError when the RHEL trust store file is not available."""
    mocker.patch("enclave.tools.system_ca.is_self_signed", return_value=False)
    mocker.patch(
        "enclave.tools.system_ca.RHEL_TRUST_STORE",
        new=Path("/nonexistent/ca-bundle.crt"),
    )
    with pytest.raises(RuntimeError, match="system trust store unavailable"):
        find_system_ca_for_chain(intermediate_pem)


def test_find_returns_none_when_no_ca_matches(
    mocker: MockerFixture, tmp_path: Path, intermediate_pem: str, root_pem: str
) -> None:
    """Return None when no CA in the trust store verifies the chain's last cert."""
    mocker.patch("enclave.tools.system_ca.is_self_signed", return_value=False)
    mocker.patch("enclave.tools.system_ca.openssl_verify", return_value=False)
    bundle = tmp_path / "ca-bundle.crt"
    bundle.write_text(root_pem, encoding="utf-8")
    mocker.patch("enclave.tools.system_ca.RHEL_TRUST_STORE", new=bundle)
    assert find_system_ca_for_chain(intermediate_pem) is None


def test_find_returns_first_matching_ca(
    mocker: MockerFixture, tmp_path: Path, intermediate_pem: str
) -> None:
    """Return the first CA that verifies the chain's last certificate."""
    mocker.patch("enclave.tools.system_ca.is_self_signed", return_value=False)
    verify = mocker.patch(
        "enclave.tools.system_ca.openssl_verify", side_effect=[False, True]
    )
    ca_a = "-----BEGIN CERTIFICATE-----\nca-a\n-----END CERTIFICATE-----"
    ca_b = "-----BEGIN CERTIFICATE-----\nca-b\n-----END CERTIFICATE-----"
    bundle = tmp_path / "ca-bundle.crt"
    bundle.write_text(f"{ca_a}\n{ca_b}\n", encoding="utf-8")
    mocker.patch("enclave.tools.system_ca.RHEL_TRUST_STORE", new=bundle)
    result = find_system_ca_for_chain(intermediate_pem)
    assert result == ca_b
    assert verify.call_count == 2


def test_find_integration_matches_ca_in_bundle(
    mocker: MockerFixture, tmp_path: Path, module_ca_pem: str, module_leaf_pem: str
) -> None:
    """Integration: discover the real CA from a synthetic trust store bundle."""
    bundle = tmp_path / "ca-bundle.crt"
    bundle.write_text(module_ca_pem, encoding="utf-8")
    mocker.patch("enclave.tools.system_ca.RHEL_TRUST_STORE", new=bundle)
    result = find_system_ca_for_chain(module_leaf_pem)
    assert result is not None
    assert "BEGIN CERTIFICATE" in result


def test_find_integration_returns_none_when_ca_not_in_bundle(
    mocker: MockerFixture, tmp_path: Path, module_leaf_pem: str
) -> None:
    """Integration: return None when no CA in the bundle signs the chain."""
    # Generate a second, unrelated CA and put only that in the bundle.
    # module_leaf_pem was signed by module_ca, which is absent from the bundle.
    ca_b_pem, _, _ = generate_ca(tmp_path, "CA B")
    bundle = tmp_path / "ca-bundle.crt"
    bundle.write_text(ca_b_pem, encoding="utf-8")
    mocker.patch("enclave.tools.system_ca.RHEL_TRUST_STORE", new=bundle)
    assert find_system_ca_for_chain(module_leaf_pem) is None


def test_find_integration_returns_none_when_chain_is_self_signed(
    mocker: MockerFixture, tmp_path: Path, module_ca_pem: str
) -> None:
    """Integration: return None when the chain ends with a self-signed root."""
    bundle = tmp_path / "ca-bundle.crt"
    bundle.write_text(module_ca_pem, encoding="utf-8")
    mocker.patch("enclave.tools.system_ca.RHEL_TRUST_STORE", new=bundle)
    assert find_system_ca_for_chain(module_ca_pem) is None


@pytest.mark.parametrize("exc", [OSError("no permission"), PermissionError("denied")])
def test_find_raises_on_trust_store_read_error(
    mocker: MockerFixture, exc: Exception, intermediate_pem: str
) -> None:
    """Raise RuntimeError when reading the trust store raises an OSError."""
    mocker.patch("enclave.tools.system_ca.is_self_signed", return_value=False)
    mocker.patch(
        "enclave.tools.system_ca.RHEL_TRUST_STORE",
        new=Path("/fake/ca-bundle.crt"),
    )
    mocker.patch.object(Path, "read_text", side_effect=exc)
    with pytest.raises(RuntimeError, match="system trust store unavailable"):
        find_system_ca_for_chain(intermediate_pem)
