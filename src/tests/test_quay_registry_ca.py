from pathlib import Path
from subprocess import CompletedProcess, TimeoutExpired

import pytest
from pytest_mock import MockerFixture

from enclave.tools.quay_registry_ca import (
    _chain_trust_anchor_pem,
    _find_root_ca_in_system_store,
    _get_cert_issuer,
    _get_cert_subject,
    _openssl_verify,
    fetch_tls_chain_pem,
    get_router_ca_pem,
    pem_blocks,
    resolve_registry_ca_pem,
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
_ROUTER_CA = """-----BEGIN CERTIFICATE-----
router-ca
-----END CERTIFICATE-----"""


def test_fetch_tls_chain_pem_timeout_raises_runtime_error(
    mocker: MockerFixture,
) -> None:
    mocker.patch(
        "enclave.tools.quay_registry_ca.subprocess.run",
        side_effect=TimeoutExpired(cmd=["openssl", "s_client"], timeout=60),
    )
    with pytest.raises(RuntimeError, match="openssl s_client timed out connecting to"):
        fetch_tls_chain_pem("registry.example.com")


def test_get_router_ca_pem_invalid_base64_raises_runtime_error(
    mocker: MockerFixture,
) -> None:
    mocker.patch(
        "enclave.tools.quay_registry_ca.subprocess.run",
        return_value=CompletedProcess(
            args=["oc"], returncode=0, stdout="not-valid-base64!!!"
        ),
    )
    with pytest.raises(RuntimeError, match="invalid router-ca secret"):
        get_router_ca_pem()


def test_get_router_ca_pem_missing_oc_raises_runtime_error(
    mocker: MockerFixture,
) -> None:
    mocker.patch(
        "enclave.tools.quay_registry_ca.subprocess.run",
        side_effect=FileNotFoundError(2, "No such file or directory", "/bin/oc"),
    )
    with pytest.raises(RuntimeError, match="/bin/oc not found"):
        get_router_ca_pem(oc="/bin/oc")


def test_fetch_tls_chain_pem_missing_openssl_raises_runtime_error(
    mocker: MockerFixture,
) -> None:
    mocker.patch(
        "enclave.tools.quay_registry_ca.subprocess.run",
        side_effect=FileNotFoundError(2, "No such file or directory", "openssl"),
    )
    with pytest.raises(RuntimeError, match="openssl not found"):
        fetch_tls_chain_pem("registry.example.com")


def test_openssl_verify_missing_openssl_raises_runtime_error(
    mocker: MockerFixture,
) -> None:
    mocker.patch(
        "enclave.tools.quay_registry_ca.subprocess.run",
        side_effect=FileNotFoundError(2, "No such file or directory", "openssl"),
    )
    with pytest.raises(RuntimeError, match="openssl not found"):
        _openssl_verify(_ROUTER_CA, _LEAF)


def test_openssl_verify_isolates_from_system_trust_store(
    mocker: MockerFixture,
) -> None:
    mock_run = mocker.patch(
        "enclave.tools.quay_registry_ca.subprocess.run",
        return_value=CompletedProcess(
            args=["openssl"], returncode=0, stdout="", stderr=""
        ),
    )
    _openssl_verify(_ROUTER_CA, _LEAF)
    cmd = mock_run.call_args[0][0]
    assert "-no-CAfile" in cmd
    assert "-no-CApath" in cmd


def test_pem_blocks_splits_multiple_certificates() -> None:
    chain = f"{_LEAF}\n{_ROOT}\n"
    assert pem_blocks(chain) == [_LEAF, _ROOT]


def test_resolve_registry_ca_pem_uses_router_ca_when_it_verifies(
    mocker: MockerFixture,
) -> None:
    mocker.patch(
        "enclave.tools.quay_registry_ca.get_router_ca_pem", return_value=_ROUTER_CA
    )
    mocker.patch(
        "enclave.tools.quay_registry_ca.fetch_tls_chain_pem",
        return_value=f"{_LEAF}\n{_ROOT}\n",
    )
    mocker.patch("enclave.tools.quay_registry_ca._openssl_verify", return_value=True)
    assert resolve_registry_ca_pem("registry.example.com") == f"{_ROUTER_CA.strip()}\n"


def test_chain_trust_anchor_pem_returns_all_non_leaf_certs() -> None:
    chain = pem_blocks(f"{_LEAF}\n{_INTERMEDIATE}\n{_ROOT}\n")
    assert _chain_trust_anchor_pem(chain) == f"{_INTERMEDIATE}\n{_ROOT}\n"


def test_chain_trust_anchor_pem_leaf_only_raises() -> None:
    with pytest.raises(RuntimeError, match="only a leaf certificate"):
        _chain_trust_anchor_pem([_LEAF])


def test_resolve_registry_ca_pem_falls_back_to_chain_ca_bundle(
    mocker: MockerFixture,
) -> None:
    mocker.patch(
        "enclave.tools.quay_registry_ca.get_router_ca_pem", return_value=_ROUTER_CA
    )
    mocker.patch(
        "enclave.tools.quay_registry_ca.fetch_tls_chain_pem",
        return_value=f"{_LEAF}\n{_INTERMEDIATE}\n{_ROOT}\n",
    )
    mocker.patch("enclave.tools.quay_registry_ca._openssl_verify", return_value=False)
    assert resolve_registry_ca_pem("registry.example.com") == (
        f"{_INTERMEDIATE}\n{_ROOT}\n"
    )


def test_resolve_registry_ca_pem_empty_chain_raises(
    mocker: MockerFixture,
) -> None:
    mocker.patch(
        "enclave.tools.quay_registry_ca.get_router_ca_pem", return_value=_ROUTER_CA
    )
    mocker.patch("enclave.tools.quay_registry_ca.fetch_tls_chain_pem", return_value="")
    with pytest.raises(RuntimeError, match="unable to fetch TLS certificate chain"):
        resolve_registry_ca_pem("registry.example.com")


def test_resolve_registry_ca_pem_leaf_only_chain_raises(
    mocker: MockerFixture,
) -> None:
    mocker.patch(
        "enclave.tools.quay_registry_ca.get_router_ca_pem", return_value=_ROUTER_CA
    )
    mocker.patch(
        "enclave.tools.quay_registry_ca.fetch_tls_chain_pem",
        return_value=f"{_LEAF}\n",
    )
    mocker.patch("enclave.tools.quay_registry_ca._openssl_verify", return_value=False)
    with pytest.raises(RuntimeError, match="only a leaf certificate"):
        resolve_registry_ca_pem("registry.example.com")


def test_get_cert_subject_extracts_subject_dn(mocker: MockerFixture) -> None:
    mocker.patch(
        "enclave.tools.quay_registry_ca.subprocess.run",
        return_value=CompletedProcess(
            args=["openssl"],
            returncode=0,
            stdout="subject=CN=Test CA,O=Test Org,C=US\n",
            stderr="",
        ),
    )
    assert _get_cert_subject(_ROOT) == "CN=Test CA,O=Test Org,C=US"


def test_get_cert_issuer_extracts_issuer_dn(mocker: MockerFixture) -> None:
    mocker.patch(
        "enclave.tools.quay_registry_ca.subprocess.run",
        return_value=CompletedProcess(
            args=["openssl"],
            returncode=0,
            stdout="issuer=CN=Root CA,O=Test Org,C=US\n",
            stderr="",
        ),
    )
    assert _get_cert_issuer(_INTERMEDIATE) == "CN=Root CA,O=Test Org,C=US"


def test_find_root_ca_in_system_store_finds_matching_cert(
    mocker: MockerFixture, tmp_path: Path
) -> None:
    # Create a mock CA bundle file
    ca_bundle = tmp_path / "ca-bundle.crt"
    ca_bundle.write_text(f"{_ROOT}\n{_INTERMEDIATE}\n", encoding="utf-8")

    # Mock Path.exists and read_text
    mocker.patch("enclave.tools.quay_registry_ca.Path.exists", return_value=True)
    mocker.patch(
        "enclave.tools.quay_registry_ca.Path.read_text",
        return_value=ca_bundle.read_text(),
    )

    # Mock _get_cert_subject to return matching DN
    mocker.patch(
        "enclave.tools.quay_registry_ca._get_cert_subject",
        side_effect=["CN=Root CA,O=Test,C=US", "CN=Other,O=Test,C=US"],
    )

    result = _find_root_ca_in_system_store("CN=Root CA,O=Test,C=US")
    assert result == _ROOT


def test_find_root_ca_in_system_store_returns_none_when_not_found(
    mocker: MockerFixture,
) -> None:
    mocker.patch("enclave.tools.quay_registry_ca.Path.exists", return_value=True)
    mocker.patch(
        "enclave.tools.quay_registry_ca.Path.read_text", return_value=f"{_ROOT}\n"
    )
    mocker.patch(
        "enclave.tools.quay_registry_ca._get_cert_subject",
        return_value="CN=Different,O=Test,C=US",
    )

    result = _find_root_ca_in_system_store("CN=Not Found,O=Test,C=US")
    assert result is None


def test_chain_trust_anchor_pem_with_self_signed_root(mocker: MockerFixture) -> None:
    # Mock issuer and subject to be the same (self-signed)
    mocker.patch(
        "enclave.tools.quay_registry_ca._get_cert_issuer",
        return_value="CN=Root CA,O=Test,C=US",
    )
    mocker.patch(
        "enclave.tools.quay_registry_ca._get_cert_subject",
        return_value="CN=Root CA,O=Test,C=US",
    )

    chain = pem_blocks(f"{_LEAF}\n{_ROOT}\n")
    result = _chain_trust_anchor_pem(chain)
    assert result == f"{_ROOT}\n"


def test_chain_trust_anchor_pem_completes_chain_from_system_store(
    mocker: MockerFixture,
) -> None:
    # Intermediate is not self-signed
    mocker.patch(
        "enclave.tools.quay_registry_ca._get_cert_issuer",
        return_value="CN=Root CA,O=Test,C=US",
    )
    mocker.patch(
        "enclave.tools.quay_registry_ca._get_cert_subject",
        return_value="CN=Intermediate CA,O=Test,C=US",
    )
    # Mock finding the root CA
    mocker.patch(
        "enclave.tools.quay_registry_ca._find_root_ca_in_system_store",
        return_value=_ROOT,
    )

    chain = pem_blocks(f"{_LEAF}\n{_INTERMEDIATE}\n")
    result = _chain_trust_anchor_pem(chain)
    assert result == f"{_INTERMEDIATE}\n{_ROOT}\n"


def test_chain_trust_anchor_pem_incomplete_chain_without_root_in_system(
    mocker: MockerFixture,
) -> None:
    # Intermediate is not self-signed
    mocker.patch(
        "enclave.tools.quay_registry_ca._get_cert_issuer",
        return_value="CN=Root CA,O=Test,C=US",
    )
    mocker.patch(
        "enclave.tools.quay_registry_ca._get_cert_subject",
        return_value="CN=Intermediate CA,O=Test,C=US",
    )
    # Root CA not found in system store
    mocker.patch(
        "enclave.tools.quay_registry_ca._find_root_ca_in_system_store",
        return_value=None,
    )

    chain = pem_blocks(f"{_LEAF}\n{_INTERMEDIATE}\n")
    result = _chain_trust_anchor_pem(chain)
    # Should return intermediate only, with a warning logged
    assert result == f"{_INTERMEDIATE}\n"
