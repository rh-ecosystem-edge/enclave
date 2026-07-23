"""Tests for check_certificate_chains: chain completeness and CA consistency validation."""

from pathlib import Path

import pytest
from pytest_mock import MockerFixture

from enclave.tools.check_certificate_chains import (
    CertificateValidationError,
    check_certificate_chains,
)
from tests.cert_helpers import (
    generate_ca,
    generate_expired_cert,
    generate_expired_intermediate_ca,
    generate_expired_self_signed,
    generate_intermediate_ca,
    generate_signed_leaf,
)


def _write_certs(tmp_path: Path, **kwargs: str) -> str:
    content = "\n".join(
        f"{k}: |\n  " + v.replace("\n", "\n  ") for k, v in kwargs.items()
    )
    path = tmp_path / "certificates.yaml"
    path.write_text(content, encoding="utf-8")
    return str(path)


def test_check_passes_when_chain_ends_with_self_signed_root(
    mocker: MockerFixture, tmp_path: Path, leaf_pem: str, root_pem: str
) -> None:
    """Pass when the chain ends with a self-signed root."""
    mocker.patch(
        "enclave.tools.check_certificate_chains.is_self_signed", return_value=True
    )
    mocker.patch(
        "enclave.tools.check_certificate_chains.openssl_verify", return_value=True
    )
    mocker.patch(
        "enclave.tools.check_certificate_chains.cert_validity_window_ok",
        return_value=True,
    )
    mocker.patch(
        "enclave.tools.check_certificate_chains.cert_covers_hostname",
        return_value=True,
    )
    mocker.patch(
        "enclave.tools.check_certificate_chains.cert_key_pair_matches",
        return_value=True,
    )
    path = _write_certs(
        tmp_path,
        sslAPICertificateFullChain=f"{leaf_pem}\n{root_pem}",
        sslAPICertificateKey="fake-key",
    )
    check_certificate_chains(path, "api", ["api.cluster.example.com"])


def test_check_passes_when_ca_matches_self_signed_root(
    mocker: MockerFixture, tmp_path: Path, leaf_pem: str, root_pem: str
) -> None:
    """Pass when sslCACertificate verifies a self-signed root."""
    mock_is_self_signed = mocker.patch(
        "enclave.tools.check_certificate_chains.is_self_signed", return_value=True
    )
    mock_openssl_verify = mocker.patch(
        "enclave.tools.check_certificate_chains.openssl_verify", return_value=True
    )
    mocker.patch(
        "enclave.tools.check_certificate_chains.cert_validity_window_ok",
        return_value=True,
    )
    mocker.patch(
        "enclave.tools.check_certificate_chains.cert_covers_hostname",
        return_value=True,
    )
    mocker.patch(
        "enclave.tools.check_certificate_chains.cert_key_pair_matches",
        return_value=True,
    )
    path = _write_certs(
        tmp_path,
        sslAPICertificateFullChain=f"{leaf_pem}\n{root_pem}",
        sslCACertificate=root_pem,
        sslAPICertificateKey="fake-key",
    )
    check_certificate_chains(path, "api", ["api.cluster.example.com"])
    mock_is_self_signed.assert_called_once_with(root_pem)
    assert mock_openssl_verify.call_count == 2


def test_check_raises_when_self_signed_root_fails_self_verification(
    mocker: MockerFixture,
    tmp_path: Path,
    leaf_pem: str,
    root_pem: str,
    intermediate_pem: str,
) -> None:
    """Raise when openssl cannot verify the self-signed root against itself (e.g. expired key)."""
    mocker.patch(
        "enclave.tools.check_certificate_chains.is_self_signed", return_value=True
    )
    mocker.patch(
        "enclave.tools.check_certificate_chains.openssl_verify", return_value=False
    )
    path = _write_certs(
        tmp_path,
        sslAPICertificateFullChain=f"{leaf_pem}\n{root_pem}",
        sslCACertificate=intermediate_pem,
        sslAPICertificateKey="fake-key",
    )
    with pytest.raises(
        CertificateValidationError, match="self-signed root that has expired"
    ):
        check_certificate_chains(path, "api", ["api.cluster.example.com"])


def test_check_raises_when_ca_does_not_match_self_signed_root(
    mocker: MockerFixture,
    tmp_path: Path,
    leaf_pem: str,
    root_pem: str,
    intermediate_pem: str,
) -> None:
    """Raise when sslCACertificate does not verify a self-signed root that is otherwise valid."""
    mocker.patch(
        "enclave.tools.check_certificate_chains.is_self_signed", return_value=True
    )
    # First call: root verifies itself (True). Second call: CA does not verify root (False).
    mocker.patch(
        "enclave.tools.check_certificate_chains.openssl_verify",
        side_effect=[True, False],
    )
    mocker.patch(
        "enclave.tools.check_certificate_chains.cert_validity_window_ok",
        return_value=True,
    )
    mocker.patch(
        "enclave.tools.check_certificate_chains.cert_covers_hostname",
        return_value=True,
    )
    mocker.patch(
        "enclave.tools.check_certificate_chains.cert_key_pair_matches",
        return_value=True,
    )
    path = _write_certs(
        tmp_path,
        sslAPICertificateFullChain=f"{leaf_pem}\n{root_pem}",
        sslCACertificate=intermediate_pem,
        sslAPICertificateKey="fake-key",
    )
    with pytest.raises(CertificateValidationError, match="does not verify it"):
        check_certificate_chains(path, "api", ["api.cluster.example.com"])


def test_check_passes_when_chain_incomplete_but_ca_pem_set(
    mocker: MockerFixture,
    tmp_path: Path,
    leaf_pem: str,
    intermediate_pem: str,
    root_pem: str,
) -> None:
    """Pass when an incomplete chain is completed by sslCACertificate."""
    mocker.patch(
        "enclave.tools.check_certificate_chains.is_self_signed", return_value=False
    )
    mocker.patch(
        "enclave.tools.check_certificate_chains.openssl_verify", return_value=True
    )
    mocker.patch(
        "enclave.tools.check_certificate_chains.cert_validity_window_ok",
        return_value=True,
    )
    mocker.patch(
        "enclave.tools.check_certificate_chains.cert_covers_hostname",
        return_value=True,
    )
    mocker.patch(
        "enclave.tools.check_certificate_chains.cert_key_pair_matches",
        return_value=True,
    )
    path = _write_certs(
        tmp_path,
        sslAPICertificateFullChain=f"{leaf_pem}\n{intermediate_pem}",
        sslCACertificate=root_pem,
        sslAPICertificateKey="fake-key",
    )
    check_certificate_chains(path, "api", ["api.cluster.example.com"])


def test_check_raises_when_ca_does_not_sign_last_chain_cert(
    mocker: MockerFixture,
    tmp_path: Path,
    leaf_pem: str,
    intermediate_pem: str,
    root_pem: str,
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
        sslAPICertificateFullChain=f"{leaf_pem}\n{intermediate_pem}",
        sslCACertificate=root_pem,
        sslAPICertificateKey="fake-key",
    )
    with pytest.raises(
        CertificateValidationError, match="does not sign its last certificate"
    ):
        check_certificate_chains(path, "api", ["api.cluster.example.com"])


def test_check_raises_when_api_chain_incomplete_without_ca(
    mocker: MockerFixture, tmp_path: Path, leaf_pem: str, intermediate_pem: str
) -> None:
    """Raise when API chain is incomplete and no sslCACertificate is set."""
    mocker.patch(
        "enclave.tools.check_certificate_chains.is_self_signed", return_value=False
    )
    path = _write_certs(
        tmp_path,
        sslAPICertificateFullChain=f"{leaf_pem}\n{intermediate_pem}",
        sslAPICertificateKey="fake-key",
    )
    with pytest.raises(
        CertificateValidationError, match="no sslCACertificate was provided"
    ):
        check_certificate_chains(path, "api", ["api.cluster.example.com"])


def test_check_raises_when_ingress_chain_incomplete_without_ca(
    mocker: MockerFixture, tmp_path: Path, leaf_pem: str, intermediate_pem: str
) -> None:
    """Raise when ingress chain is incomplete and no sslCACertificate is set."""
    mocker.patch(
        "enclave.tools.check_certificate_chains.is_self_signed", return_value=False
    )
    path = _write_certs(
        tmp_path,
        sslIngressCertificateFullChain=f"{leaf_pem}\n{intermediate_pem}",
        sslIngressCertificateKey="fake-key",
    )
    with pytest.raises(
        CertificateValidationError, match="no sslCACertificate was provided"
    ):
        check_certificate_chains(path, "ingress", ["apps.cluster.example.com"])


def test_check_skips_absent_cert_field(tmp_path: Path) -> None:
    """Skip gracefully when the configured cert field is absent or empty."""
    path = tmp_path / "certificates.yaml"
    path.write_text("ironicHTTPSCertificate: ''\n", encoding="utf-8")
    check_certificate_chains(str(path), "ironic", [])


def test_check_raises_leaf_only_chain_without_ca(
    mocker: MockerFixture, tmp_path: Path, leaf_pem: str
) -> None:
    """Raise when only a leaf cert is present and no sslCACertificate is set."""
    mocker.patch(
        "enclave.tools.check_certificate_chains.is_self_signed", return_value=False
    )
    path = _write_certs(
        tmp_path,
        sslAPICertificateFullChain=leaf_pem,
        sslAPICertificateKey="fake-key",
    )
    with pytest.raises(
        CertificateValidationError, match="no sslCACertificate was provided"
    ):
        check_certificate_chains(str(path), "api", ["api.cluster.example.com"])


def test_check_passes_leaf_only_chain_when_ca_set(
    mocker: MockerFixture, tmp_path: Path, leaf_pem: str, root_pem: str
) -> None:
    """Pass when a leaf-only chain is completed by sslCACertificate."""
    mocker.patch(
        "enclave.tools.check_certificate_chains.is_self_signed", return_value=False
    )
    mocker.patch(
        "enclave.tools.check_certificate_chains.openssl_verify", return_value=True
    )
    mocker.patch(
        "enclave.tools.check_certificate_chains.cert_validity_window_ok",
        return_value=True,
    )
    mocker.patch(
        "enclave.tools.check_certificate_chains.cert_covers_hostname",
        return_value=True,
    )
    mocker.patch(
        "enclave.tools.check_certificate_chains.cert_key_pair_matches",
        return_value=True,
    )
    path = _write_certs(
        tmp_path,
        sslAPICertificateFullChain=leaf_pem,
        sslCACertificate=root_pem,
        sslAPICertificateKey="fake-key",
    )
    check_certificate_chains(str(path), "api", ["api.cluster.example.com"])


def test_check_raises_when_chain_has_content_but_no_pem_blocks(
    tmp_path: Path,
) -> None:
    """Raise when a chain field is set to text that contains no PEM certificate blocks."""
    path = _write_certs(
        tmp_path,
        sslAPICertificateFullChain="not a certificate",
        sslAPICertificateKey="fake-key",
    )
    with pytest.raises(CertificateValidationError, match="no PEM certificate blocks"):
        check_certificate_chains(str(path), "api", ["api.cluster.example.com"])


def test_check_raises_on_missing_file() -> None:
    """Raise when the config file does not exist."""
    with pytest.raises(CertificateValidationError, match="cannot read"):
        check_certificate_chains("/nonexistent/path/certificates.yaml", "api", [])


def test_check_raises_on_invalid_yaml(tmp_path: Path) -> None:
    """Raise when the config file contains invalid YAML."""
    path = tmp_path / "certificates.yaml"
    path.write_text("key: [\nbad yaml", encoding="utf-8")
    with pytest.raises(CertificateValidationError, match="cannot parse"):
        check_certificate_chains(str(path), "api", [])


def test_check_raises_on_non_mapping_yaml(tmp_path: Path) -> None:
    """Raise when the config file is not a YAML mapping."""
    path = tmp_path / "certificates.yaml"
    path.write_text("- item1\n- item2\n", encoding="utf-8")
    with pytest.raises(CertificateValidationError, match="expected a YAML mapping"):
        check_certificate_chains(str(path), "api", [])


def test_check_passes_via_system_store_when_no_ca_set(
    mocker: MockerFixture,
    tmp_path: Path,
    leaf_pem: str,
    intermediate_pem: str,
    root_pem: str,
) -> None:
    """Pass when no sslCACertificate is set but the system trust store provides one."""
    mock_is_self_signed = mocker.patch(
        "enclave.tools.check_certificate_chains.is_self_signed", return_value=False
    )
    mock_find_system_ca = mocker.patch(
        "enclave.tools.check_certificate_chains.find_system_ca_for_chain",
        return_value=root_pem,
    )
    mock_openssl_verify = mocker.patch(
        "enclave.tools.check_certificate_chains.openssl_verify", return_value=True
    )
    mocker.patch(
        "enclave.tools.check_certificate_chains.cert_validity_window_ok",
        return_value=True,
    )
    mocker.patch(
        "enclave.tools.check_certificate_chains.cert_covers_hostname",
        return_value=True,
    )
    mocker.patch(
        "enclave.tools.check_certificate_chains.cert_key_pair_matches",
        return_value=True,
    )
    full_chain = f"{leaf_pem}\n{intermediate_pem}"
    path = _write_certs(
        tmp_path,
        sslAPICertificateFullChain=full_chain,
        sslAPICertificateKey="fake-key",
    )
    check_certificate_chains(path, "api", ["api.cluster.example.com"])
    mock_is_self_signed.assert_called_once_with(intermediate_pem)
    mock_find_system_ca.assert_called_once_with(full_chain)
    mock_openssl_verify.assert_not_called()


def test_check_raises_when_system_store_also_has_no_match(
    mocker: MockerFixture, tmp_path: Path, leaf_pem: str, intermediate_pem: str
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
        sslAPICertificateFullChain=f"{leaf_pem}\n{intermediate_pem}",
        sslAPICertificateKey="fake-key",
    )
    with pytest.raises(CertificateValidationError, match="no matching CA was found"):
        check_certificate_chains(path, "api", ["api.cluster.example.com"])


def test_real_three_hop_chain_passes(tmp_path: Path) -> None:
    """check_certificate_chains passes for a genuine three-certificate chain.

    Builds a self-signed root CA, an intermediate CA signed by the root (with
    basicConstraints=CA:TRUE), and a leaf cert signed by the intermediate. The
    full chain leaf→intermediate→root is written to sslAPICertificateFullChain.
    No mocks: is_self_signed and openssl_verify run real openssl subprocess calls,
    so this test exercises the complete validation path with genuine certificates.
    """
    root_pem, root_cert_path, root_key_path = generate_ca(tmp_path, "Root CA")
    inter_pem, inter_cert_path, inter_key_path = generate_intermediate_ca(
        tmp_path, "Intermediate CA", root_cert_path, root_key_path
    )
    leaf_pem = generate_signed_leaf(
        tmp_path,
        inter_cert_path,
        inter_key_path,
        "Leaf",
        sans=["api.cluster.example.com"],
    )
    leaf_key_pem = (tmp_path / "leaf.key").read_text(encoding="utf-8")
    path = _write_certs(
        tmp_path,
        sslAPICertificateFullChain=f"{leaf_pem}\n{inter_pem}\n{root_pem}",
        sslAPICertificateKey=leaf_key_pem,
    )
    check_certificate_chains(path, "api", ["api.cluster.example.com"])


def test_real_chain_missing_intermediate_fails(tmp_path: Path) -> None:
    """Fail when the leaf is not directly signed by the provided CA (no mocks).

    The root CA signs the intermediate, not the leaf directly. Presenting the leaf
    alone with sslCACertificate=root causes openssl_verify(root, leaf) to return
    False, which is the path we want to exercise without any mocking.
    """
    root_pem, root_cert_path, root_key_path = generate_ca(tmp_path, "Root CA")
    _, inter_cert_path, inter_key_path = generate_intermediate_ca(
        tmp_path, "Intermediate CA", root_cert_path, root_key_path
    )
    leaf_pem = generate_signed_leaf(tmp_path, inter_cert_path, inter_key_path, "Leaf")
    leaf_key_pem = (tmp_path / "leaf.key").read_text(encoding="utf-8")
    path = _write_certs(
        tmp_path,
        sslAPICertificateFullChain=leaf_pem,
        sslCACertificate=root_pem,
        sslAPICertificateKey=leaf_key_pem,
    )
    with pytest.raises(
        CertificateValidationError, match="does not sign its last certificate"
    ):
        check_certificate_chains(path, "api", ["api.cluster.example.com"])


def test_expired_self_signed_root_chain_fails(tmp_path: Path) -> None:
    """Fail when the chain ends with an expired self-signed root and no CA is provided.

    is_self_signed uses -no_check_time to detect structural self-signing regardless
    of validity dates. This test ensures that the expiry check after that structural
    check still rejects the chain.
    """
    expired_root_pem = generate_expired_self_signed(tmp_path, "Expired Root CA")
    key_pem = (tmp_path / "expired_ss_Expired Root CA.key").read_text(encoding="utf-8")
    path = _write_certs(
        tmp_path,
        sslAPICertificateFullChain=expired_root_pem,
        sslAPICertificateKey=key_pem,
    )
    with pytest.raises(
        CertificateValidationError, match="self-signed root that has expired"
    ):
        check_certificate_chains(path, "api", ["api.cluster.example.com"])


def test_expired_leaf_fails(tmp_path: Path) -> None:
    """Fail when the leaf cert is expired.

    openssl verify rejects expired certs by default, so openssl_verify(ca, expired_leaf)
    returns False even though the CA did issue the cert.
    """
    root_pem, root_cert_path, root_key_path = generate_ca(tmp_path, "Root CA")
    expired_leaf_pem = generate_expired_cert(
        tmp_path, root_cert_path, root_key_path, "Leaf"
    )
    expired_leaf_key_pem = (tmp_path / "expired_Leaf.key").read_text(encoding="utf-8")
    path = _write_certs(
        tmp_path,
        sslAPICertificateFullChain=expired_leaf_pem,
        sslCACertificate=root_pem,
        sslAPICertificateKey=expired_leaf_key_pem,
    )
    with pytest.raises(CertificateValidationError, match="expired or is not yet valid"):
        check_certificate_chains(path, "api", ["api.cluster.example.com"])


def test_expired_intermediate_fails(tmp_path: Path) -> None:
    """Fail when an intermediate CA in the chain is expired.

    The expired intermediate has basicConstraints=CA:TRUE and was issued by the root.
    The leaf is signed by the expired intermediate's key. The chain ends with the
    expired intermediate (non-self-signed), and openssl_verify(root, expired_inter)
    returns False because the cert has expired.
    """
    root_pem, root_cert_path, root_key_path = generate_ca(tmp_path, "Root CA")
    expired_inter_pem, expired_inter_cert_path, expired_inter_key_path = (
        generate_expired_intermediate_ca(
            tmp_path, "Intermediate CA", root_cert_path, root_key_path
        )
    )
    leaf_pem = generate_signed_leaf(
        tmp_path, expired_inter_cert_path, expired_inter_key_path, "Leaf"
    )
    leaf_key_pem = (tmp_path / "leaf.key").read_text(encoding="utf-8")
    path = _write_certs(
        tmp_path,
        sslAPICertificateFullChain=f"{leaf_pem}\n{expired_inter_pem}",
        sslCACertificate=root_pem,
        sslAPICertificateKey=leaf_key_pem,
    )
    with pytest.raises(
        CertificateValidationError, match="does not sign its last certificate"
    ):
        check_certificate_chains(path, "api", ["api.cluster.example.com"])


def test_hostname_matching_exact(tmp_path: Path) -> None:
    """Pass when the leaf cert has an exact DNS SAN matching the requested hostname."""
    root_pem, root_cert_path, root_key_path = generate_ca(tmp_path, "Root CA")
    leaf_pem = generate_signed_leaf(
        tmp_path,
        root_cert_path,
        root_key_path,
        "Leaf",
        sans=["api.cluster.example.com"],
    )
    leaf_key_pem = (tmp_path / "leaf.key").read_text(encoding="utf-8")
    path = _write_certs(
        tmp_path,
        sslAPICertificateFullChain=leaf_pem,
        sslCACertificate=root_pem,
        sslAPICertificateKey=leaf_key_pem,
    )
    check_certificate_chains(path, "api", hostnames=["api.cluster.example.com"])


def test_hostname_matching_wildcard(tmp_path: Path) -> None:
    """Pass when a wildcard SAN in the leaf cert covers the requested hostname."""
    root_pem, root_cert_path, root_key_path = generate_ca(tmp_path, "Root CA")
    leaf_pem = generate_signed_leaf(
        tmp_path,
        root_cert_path,
        root_key_path,
        "Leaf",
        sans=["*.apps.cluster.example.com"],
    )
    leaf_key_pem = (tmp_path / "leaf.key").read_text(encoding="utf-8")
    path = _write_certs(
        tmp_path,
        sslIngressCertificateFullChain=leaf_pem,
        sslCACertificate=root_pem,
        sslIngressCertificateKey=leaf_key_pem,
    )
    check_certificate_chains(
        path, "ingress", hostnames=["foo.apps.cluster.example.com"]
    )


def test_hostname_wildcard_does_not_match_parent(tmp_path: Path) -> None:
    """Fail when the requested hostname is the apex domain of a wildcard SAN."""
    root_pem, root_cert_path, root_key_path = generate_ca(tmp_path, "Root CA")
    leaf_pem = generate_signed_leaf(
        tmp_path,
        root_cert_path,
        root_key_path,
        "Leaf",
        sans=["*.apps.cluster.example.com"],
    )
    leaf_key_pem = (tmp_path / "leaf.key").read_text(encoding="utf-8")
    path = _write_certs(
        tmp_path,
        sslAPICertificateFullChain=leaf_pem,
        sslCACertificate=root_pem,
        sslAPICertificateKey=leaf_key_pem,
    )
    with pytest.raises(CertificateValidationError, match="SAN does not cover"):
        check_certificate_chains(path, "api", hostnames=["apps.cluster.example.com"])


def test_hostname_missing_san_fails(tmp_path: Path) -> None:
    """Fail when the leaf cert has no SAN extension."""
    root_pem, root_cert_path, root_key_path = generate_ca(tmp_path, "Root CA")
    leaf_pem = generate_signed_leaf(tmp_path, root_cert_path, root_key_path, "Leaf")
    leaf_key_pem = (tmp_path / "leaf.key").read_text(encoding="utf-8")
    path = _write_certs(
        tmp_path,
        sslAPICertificateFullChain=leaf_pem,
        sslCACertificate=root_pem,
        sslAPICertificateKey=leaf_key_pem,
    )
    with pytest.raises(CertificateValidationError, match="SAN does not cover"):
        check_certificate_chains(path, "api", hostnames=["api.cluster.example.com"])


def test_hostname_mismatch_fails(tmp_path: Path) -> None:
    """Fail when the leaf cert SAN does not include the requested hostname."""
    root_pem, root_cert_path, root_key_path = generate_ca(tmp_path, "Root CA")
    leaf_pem = generate_signed_leaf(
        tmp_path, root_cert_path, root_key_path, "Leaf", sans=["other.example.com"]
    )
    leaf_key_pem = (tmp_path / "leaf.key").read_text(encoding="utf-8")
    path = _write_certs(
        tmp_path,
        sslAPICertificateFullChain=leaf_pem,
        sslCACertificate=root_pem,
        sslAPICertificateKey=leaf_key_pem,
    )
    with pytest.raises(CertificateValidationError, match="SAN does not cover"):
        check_certificate_chains(path, "api", hostnames=["api.cluster.example.com"])


def test_key_mismatch_fails(tmp_path: Path) -> None:
    """Fail when the provided private key does not match the leaf cert."""
    root_pem, root_cert_path, root_key_path = generate_ca(tmp_path, "Root CA")
    leaf_pem = generate_signed_leaf(
        tmp_path,
        root_cert_path,
        root_key_path,
        "Leaf",
        sans=["api.cluster.example.com"],
    )
    wrong_key_pem = root_key_path.read_text(encoding="utf-8")
    path = _write_certs(
        tmp_path,
        sslAPICertificateFullChain=leaf_pem,
        sslCACertificate=root_pem,
        sslAPICertificateKey=wrong_key_pem,
    )
    with pytest.raises(CertificateValidationError, match="private key does not match"):
        check_certificate_chains(path, "api", hostnames=["api.cluster.example.com"])


def test_expired_chain_is_rejected(tmp_path: Path) -> None:
    """Fail when the leaf cert is expired, regardless of hostname or CA validity."""
    root_pem, root_cert_path, root_key_path = generate_ca(tmp_path, "Root CA")
    expired_leaf_pem = generate_expired_cert(
        tmp_path, root_cert_path, root_key_path, "Leaf"
    )
    expired_leaf_key_pem = (tmp_path / "expired_Leaf.key").read_text(encoding="utf-8")
    path = _write_certs(
        tmp_path,
        sslAPICertificateFullChain=expired_leaf_pem,
        sslCACertificate=root_pem,
        sslAPICertificateKey=expired_leaf_key_pem,
    )
    with pytest.raises(CertificateValidationError, match="expired or is not yet valid"):
        check_certificate_chains(path, "api", hostnames=["api.cluster.example.com"])


def test_ironic_cert_skipped_when_absent(tmp_path: Path) -> None:
    """Pass without error when ironicHTTPSCertificate is absent from the config."""
    path = tmp_path / "certificates.yaml"
    path.write_text("{}\n", encoding="utf-8")
    check_certificate_chains(str(path), "ironic", ["bmc.example.com"])


def test_ironic_cert_passes_when_valid(tmp_path: Path) -> None:
    """Pass when ironicHTTPSCertificate is valid, key matches, and SAN covers hostname."""
    _, ca_cert_path, ca_key_path = generate_ca(tmp_path, "CA")
    leaf_pem = generate_signed_leaf(
        tmp_path, ca_cert_path, ca_key_path, "Leaf", sans=["bmc.example.com"]
    )
    leaf_key_pem = (tmp_path / "leaf.key").read_text(encoding="utf-8")
    path = _write_certs(
        tmp_path,
        ironicHTTPSCertificate=leaf_pem,
        ironicHTTPSKey=leaf_key_pem,
    )
    check_certificate_chains(str(path), "ironic", hostnames=["bmc.example.com"])


def test_ironic_cert_expiry_rejected(tmp_path: Path) -> None:
    """Fail when ironicHTTPSCertificate is expired."""
    _, ca_cert_path, ca_key_path = generate_ca(tmp_path, "CA")
    expired_pem = generate_expired_cert(tmp_path, ca_cert_path, ca_key_path, "Leaf")
    expired_key_pem = (tmp_path / "expired_Leaf.key").read_text(encoding="utf-8")
    path = _write_certs(
        tmp_path,
        ironicHTTPSCertificate=expired_pem,
        ironicHTTPSKey=expired_key_pem,
    )
    with pytest.raises(CertificateValidationError, match="expired or is not yet valid"):
        check_certificate_chains(str(path), "ironic", ["bmc.example.com"])


def test_ironic_cert_key_mismatch_rejected(tmp_path: Path) -> None:
    """Fail when ironicHTTPSKey does not match ironicHTTPSCertificate."""
    ca_pem, ca_cert_path, ca_key_path = generate_ca(tmp_path, "CA")
    leaf_pem = generate_signed_leaf(
        tmp_path, ca_cert_path, ca_key_path, "Leaf", sans=["bmc.example.com"]
    )
    path = _write_certs(
        tmp_path,
        ironicHTTPSCertificate=leaf_pem,
        ironicHTTPSKey=ca_pem,
    )
    with pytest.raises(CertificateValidationError, match="private key does not match"):
        check_certificate_chains(str(path), "ironic", hostnames=["bmc.example.com"])


def test_ironic_cert_san_mismatch_rejected(tmp_path: Path) -> None:
    """Fail when the ironic cert SAN does not cover the requested hostname."""
    _, ca_cert_path, ca_key_path = generate_ca(tmp_path, "CA")
    leaf_pem = generate_signed_leaf(
        tmp_path, ca_cert_path, ca_key_path, "Leaf", sans=["other.example.com"]
    )
    leaf_key_pem = (tmp_path / "leaf.key").read_text(encoding="utf-8")
    path = _write_certs(
        tmp_path,
        ironicHTTPSCertificate=leaf_pem,
        ironicHTTPSKey=leaf_key_pem,
    )
    with pytest.raises(CertificateValidationError, match="SAN does not cover"):
        check_certificate_chains(str(path), "ironic", hostnames=["bmc.example.com"])
