"""Tests for quay_registry_ca: TLS chain resolution and CA trust anchor selection."""

from subprocess import CompletedProcess, TimeoutExpired

import pytest
from pytest_mock import MockerFixture

from enclave.tools.cert_utils import pem_blocks
from enclave.tools.quay_registry_ca import (
    chain_trust_anchor_pem,
    fetch_tls_chain_pem,
    get_router_ca_pem,
    main,
    resolve_registry_ca_pem,
)


def test_fetch_tls_chain_pem_timeout_raises_runtime_error(
    mocker: MockerFixture,
) -> None:
    """Raise RuntimeError when openssl s_client times out."""
    mocker.patch(
        "enclave.tools.quay_registry_ca.subprocess.run",
        side_effect=TimeoutExpired(cmd=["openssl", "s_client"], timeout=60),
    )
    with pytest.raises(RuntimeError, match="openssl s_client timed out connecting to"):
        fetch_tls_chain_pem("registry.example.com")


def test_fetch_tls_chain_pem_missing_openssl_raises_runtime_error(
    mocker: MockerFixture,
) -> None:
    """Raise RuntimeError when openssl binary is not found."""
    mocker.patch(
        "enclave.tools.quay_registry_ca.subprocess.run",
        side_effect=FileNotFoundError(2, "No such file or directory", "openssl"),
    )
    with pytest.raises(RuntimeError, match="openssl not found"):
        fetch_tls_chain_pem("registry.example.com")


def test_get_router_ca_pem_invalid_base64_raises_runtime_error(
    mocker: MockerFixture,
) -> None:
    """Raise RuntimeError when the router-ca secret contains invalid base64."""
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
    """Raise RuntimeError when oc binary is not found."""
    mocker.patch(
        "enclave.tools.quay_registry_ca.subprocess.run",
        side_effect=FileNotFoundError(2, "No such file or directory", "/bin/oc"),
    )
    with pytest.raises(RuntimeError, match="/bin/oc not found"):
        get_router_ca_pem(oc="/bin/oc")


def test_chain_trust_anchor_pem_leaf_only_raises(leaf_pem: str) -> None:
    """Raise RuntimeError when only a leaf certificate is present."""
    with pytest.raises(RuntimeError, match="only a leaf certificate"):
        chain_trust_anchor_pem([leaf_pem])


def test_chain_trust_anchor_pem_with_self_signed_last_cert(
    mocker: MockerFixture,
    leaf_pem: str,
    root_pem: str,
) -> None:
    """Return the self-signed root when the chain ends with one."""
    mocker.patch("enclave.tools.quay_registry_ca.is_self_signed", return_value=True)
    chain = pem_blocks(f"{leaf_pem}\n{root_pem}\n")
    assert chain_trust_anchor_pem(chain) == f"{root_pem}\n"


def test_chain_trust_anchor_pem_returns_all_non_leaf_certs(
    mocker: MockerFixture,
    leaf_pem: str,
    intermediate_pem: str,
    root_pem: str,
) -> None:
    """Return all certs except the leaf (intermediate + root)."""
    mocker.patch("enclave.tools.quay_registry_ca.is_self_signed", return_value=True)
    chain = pem_blocks(f"{leaf_pem}\n{intermediate_pem}\n{root_pem}\n")
    assert chain_trust_anchor_pem(chain) == f"{intermediate_pem}\n{root_pem}\n"


def test_chain_trust_anchor_pem_appends_ca_pem_when_chain_incomplete(
    mocker: MockerFixture,
    leaf_pem: str,
    intermediate_pem: str,
    root_pem: str,
) -> None:
    """Append ca_pem to the anchor when it verifies the last chain cert."""
    mocker.patch("enclave.tools.quay_registry_ca.is_self_signed", return_value=False)
    mocker.patch("enclave.tools.quay_registry_ca.openssl_verify", return_value=True)
    chain = pem_blocks(f"{leaf_pem}\n{intermediate_pem}\n")
    assert (
        chain_trust_anchor_pem(chain, ca_pem=root_pem)
        == f"{intermediate_pem}\n{root_pem}\n"
    )


def test_chain_trust_anchor_pem_returns_incomplete_chain_without_ca_pem(
    mocker: MockerFixture,
    leaf_pem: str,
    intermediate_pem: str,
) -> None:
    """Return the non-leaf portion of an incomplete chain when no ca_pem or system CA."""
    mocker.patch("enclave.tools.quay_registry_ca.is_self_signed", return_value=False)
    mocker.patch(
        "enclave.tools.quay_registry_ca.find_system_ca_for_chain", return_value=None
    )
    chain = pem_blocks(f"{leaf_pem}\n{intermediate_pem}\n")
    assert chain_trust_anchor_pem(chain) == f"{intermediate_pem}\n"


def test_chain_trust_anchor_pem_completes_chain_from_system_trust_store(
    mocker: MockerFixture,
    leaf_pem: str,
    intermediate_pem: str,
    root_pem: str,
) -> None:
    """Complete the chain with a CA from the system trust store when no ca_pem is given."""
    mocker.patch("enclave.tools.quay_registry_ca.is_self_signed", return_value=False)
    mocker.patch(
        "enclave.tools.quay_registry_ca.find_system_ca_for_chain",
        return_value=root_pem,
    )
    chain = pem_blocks(f"{leaf_pem}\n{intermediate_pem}\n")
    assert chain_trust_anchor_pem(chain) == f"{intermediate_pem}\n{root_pem}\n"


def test_chain_trust_anchor_pem_raises_when_ca_pem_does_not_verify(
    mocker: MockerFixture,
    leaf_pem: str,
    intermediate_pem: str,
    root_pem: str,
) -> None:
    """Raise RuntimeError when ca_pem does not verify the last chain cert."""
    mocker.patch("enclave.tools.quay_registry_ca.is_self_signed", return_value=False)
    mocker.patch("enclave.tools.quay_registry_ca.openssl_verify", return_value=False)
    chain = pem_blocks(f"{leaf_pem}\n{intermediate_pem}\n")
    with pytest.raises(RuntimeError, match="does not verify the last certificate"):
        chain_trust_anchor_pem(chain, ca_pem=root_pem)


def test_resolve_registry_ca_pem_uses_router_ca_when_it_verifies(
    mocker: MockerFixture,
    router_ca_pem: str,
    leaf_pem: str,
    root_pem: str,
) -> None:
    """Use the router CA when it verifies the chain."""
    mocker.patch(
        "enclave.tools.quay_registry_ca.get_router_ca_pem", return_value=router_ca_pem
    )
    mocker.patch(
        "enclave.tools.quay_registry_ca.fetch_tls_chain_pem",
        return_value=f"{leaf_pem}\n{root_pem}\n",
    )
    mocker.patch("enclave.tools.quay_registry_ca.openssl_verify", return_value=True)
    assert (
        resolve_registry_ca_pem("registry.example.com") == f"{router_ca_pem.strip()}\n"
    )


def test_resolve_registry_ca_pem_falls_back_to_chain_ca_bundle(
    mocker: MockerFixture,
    router_ca_pem: str,
    leaf_pem: str,
    intermediate_pem: str,
    root_pem: str,
) -> None:
    """Fall back to chain CA bundle when router CA does not verify."""
    mocker.patch(
        "enclave.tools.quay_registry_ca.get_router_ca_pem", return_value=router_ca_pem
    )
    mocker.patch(
        "enclave.tools.quay_registry_ca.fetch_tls_chain_pem",
        return_value=f"{leaf_pem}\n{intermediate_pem}\n{root_pem}\n",
    )
    mocker.patch("enclave.tools.quay_registry_ca.openssl_verify", return_value=False)
    mocker.patch("enclave.tools.quay_registry_ca.is_self_signed", return_value=True)
    assert resolve_registry_ca_pem("registry.example.com") == (
        f"{intermediate_pem}\n{root_pem}\n"
    )


def test_resolve_registry_ca_pem_empty_chain_raises(
    mocker: MockerFixture,
    router_ca_pem: str,
) -> None:
    """Raise RuntimeError when the fetched chain is empty."""
    mocker.patch(
        "enclave.tools.quay_registry_ca.get_router_ca_pem", return_value=router_ca_pem
    )
    mocker.patch("enclave.tools.quay_registry_ca.fetch_tls_chain_pem", return_value="")
    with pytest.raises(RuntimeError, match="unable to fetch TLS certificate chain"):
        resolve_registry_ca_pem("registry.example.com")


def test_resolve_registry_ca_pem_leaf_only_chain_raises(
    mocker: MockerFixture,
    router_ca_pem: str,
    leaf_pem: str,
) -> None:
    """Raise RuntimeError when the chain has only a leaf certificate."""
    mocker.patch(
        "enclave.tools.quay_registry_ca.get_router_ca_pem", return_value=router_ca_pem
    )
    mocker.patch(
        "enclave.tools.quay_registry_ca.fetch_tls_chain_pem",
        return_value=f"{leaf_pem}\n",
    )
    mocker.patch("enclave.tools.quay_registry_ca.openssl_verify", return_value=False)
    with pytest.raises(RuntimeError, match="only a leaf certificate"):
        resolve_registry_ca_pem("registry.example.com")


def test_resolve_registry_ca_pem_completes_chain_with_ca_pem(
    mocker: MockerFixture,
    router_ca_pem: str,
    leaf_pem: str,
    intermediate_pem: str,
    root_pem: str,
) -> None:
    """Append ca_pem to the anchor when it completes the chain."""
    mocker.patch(
        "enclave.tools.quay_registry_ca.get_router_ca_pem", return_value=router_ca_pem
    )
    mocker.patch(
        "enclave.tools.quay_registry_ca.fetch_tls_chain_pem",
        return_value=f"{leaf_pem}\n{intermediate_pem}\n",
    )
    # First call: router-ca does not verify the leaf → fall back to chain.
    # Second call: provided ca_pem verifies the last chain cert → append it.
    mocker.patch(
        "enclave.tools.quay_registry_ca.openssl_verify", side_effect=[False, True]
    )
    mocker.patch("enclave.tools.quay_registry_ca.is_self_signed", return_value=False)
    result = resolve_registry_ca_pem("registry.example.com", ca_pem=root_pem)
    assert result == f"{intermediate_pem}\n{root_pem}\n"


def test_main_writes_resolved_ca_to_stdout(
    mocker: MockerFixture,
    router_ca_pem: str,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Write the CA PEM returned by resolve_registry_ca_pem to stdout."""
    mocker.patch(
        "enclave.tools.quay_registry_ca.resolve_registry_ca_pem",
        return_value=f"{router_ca_pem}\n",
    )
    main("registry.example.com")
    assert capsys.readouterr().out == f"{router_ca_pem}\n"
