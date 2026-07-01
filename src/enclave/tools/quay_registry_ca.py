"""Resolve the CA PEM used to trust the Quay registry route for OSUS and image pulls."""

import base64
import binascii
import logging
import subprocess
import sys

from enclave.tools.cert_utils import is_self_signed, openssl_verify, pem_blocks
from enclave.tools.system_ca import find_system_ca_for_chain

logger = logging.getLogger(__name__)

_TLS_CONNECT_TIMEOUT_SECONDS = 60
_OC_COMMAND_TIMEOUT_SECONDS = 30


def get_router_ca_pem(*, oc: str = "oc") -> str:
    """Return the PEM-encoded router CA from the openshift-ingress-operator secret.

    Returns an empty string when the secret is absent or empty.
    Raises RuntimeError on oc timeout, missing binary, or malformed secret data.
    """
    try:
        result = subprocess.run(
            [
                oc,
                "get",
                "secret",
                "router-ca",
                "-n",
                "openshift-ingress-operator",
                "-o",
                "jsonpath={.data.tls\\.crt}",
            ],
            capture_output=True,
            text=True,
            check=False,
            timeout=_OC_COMMAND_TIMEOUT_SECONDS,
        )
    except subprocess.TimeoutExpired as exc:
        msg = "oc get secret router-ca timed out"
        raise RuntimeError(msg) from exc
    except OSError as exc:
        msg = f"{oc} not found"
        raise RuntimeError(msg) from exc
    if result.returncode != 0 or not result.stdout.strip():
        return ""
    try:
        return base64.b64decode(result.stdout).decode("utf-8")
    except (binascii.Error, UnicodeDecodeError) as exc:
        msg = f"invalid router-ca secret: {exc}"
        raise RuntimeError(msg) from exc


def fetch_tls_chain_pem(hostname: str, *, port: int = 443) -> str:
    """Return all PEM blocks from the TLS certificate chain presented by hostname:port.

    Uses openssl s_client -showcerts. Returns an empty string when no PEM blocks are
    found. Raises RuntimeError on connection timeout or missing openssl binary.
    """
    try:
        result = subprocess.run(
            [
                "openssl",
                "s_client",
                "-connect",
                f"{hostname}:{port}",
                "-servername",
                hostname,
                "-showcerts",
            ],
            input="",
            capture_output=True,
            text=True,
            check=False,
            timeout=_TLS_CONNECT_TIMEOUT_SECONDS,
        )
    except subprocess.TimeoutExpired as exc:
        msg = f"openssl s_client timed out connecting to {hostname}:{port}"
        raise RuntimeError(msg) from exc
    except OSError as exc:
        msg = "openssl not found"
        raise RuntimeError(msg) from exc
    return "\n".join(pem_blocks(result.stdout + result.stderr))


def chain_trust_anchor_pem(chain: list[str], ca_pem: str = "") -> str:
    """Return the CA bundle PEM that should be trusted to verify the TLS chain.

    Skips the leaf (chain[0]) and returns all remaining CA certificates. If the last
    certificate is not self-signed and ca_pem is provided, verifies that ca_pem signs
    it and appends ca_pem to complete the chain. Raises RuntimeError when the chain
    contains only a leaf or when ca_pem fails to verify the last certificate.
    """
    ca_certs = chain[1:]
    if not ca_certs:
        msg = (
            "TLS chain contains only a leaf certificate; cannot determine trust anchor"
        )
        raise RuntimeError(msg)

    if is_self_signed(ca_certs[-1]):
        logger.debug("TLS chain contains self-signed root CA")
        return "\n".join(ca_certs) + "\n"

    if ca_pem:
        if not openssl_verify(ca_pem, ca_certs[-1]):
            msg = "provided --ca-pem does not verify the last certificate in the TLS chain"
            raise RuntimeError(msg)
        logger.info("Completing certificate chain with provided CA")
        return "\n".join([*ca_certs, ca_pem.strip()]) + "\n"

    system_ca = find_system_ca_for_chain("\n".join(ca_certs))
    if system_ca:
        logger.info("Completing certificate chain with CA from system trust store")
        return "\n".join([*ca_certs, system_ca.strip()]) + "\n"

    logger.warning(
        "TLS chain is incomplete (last certificate is not self-signed) and no CA "
        "provided. UpdateService may fail to verify the registry certificate."
    )
    return "\n".join(ca_certs) + "\n"


def resolve_registry_ca_pem(hostname: str, *, oc: str = "oc", ca_pem: str = "") -> str:
    """Return PEM for the CA that should trust the registry route certificate."""
    router_ca = get_router_ca_pem(oc=oc).strip()
    chain = pem_blocks(fetch_tls_chain_pem(hostname))
    if not chain:
        msg = f"unable to fetch TLS certificate chain for {hostname}"
        raise RuntimeError(msg)

    leaf = chain[0]
    if router_ca and openssl_verify(router_ca, leaf):
        logger.debug("using router-ca to trust %s", hostname)
        return f"{router_ca}\n"

    logger.debug("using TLS chain CA bundle to trust %s", hostname)
    return chain_trust_anchor_pem(chain, ca_pem=ca_pem)


def main(hostname: str, *, oc: str = "oc", ca_pem: str = "") -> None:
    """Print the CA PEM that should be trusted to verify the Quay registry route."""
    sys.stdout.write(resolve_registry_ca_pem(hostname, oc=oc, ca_pem=ca_pem))
