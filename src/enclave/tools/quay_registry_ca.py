"""Resolve the CA PEM used to trust the Quay registry route for OSUS and image pulls."""

from __future__ import annotations

import base64
import binascii
import logging
import re
import subprocess
import sys
import tempfile
from pathlib import Path

logger = logging.getLogger(__name__)

_TLS_CONNECT_TIMEOUT_SECONDS = 60
_OC_COMMAND_TIMEOUT_SECONDS = 30
_OPENSSL_VERIFY_TIMEOUT_SECONDS = 10
_PEM_BLOCK = re.compile(
    r"-----BEGIN CERTIFICATE-----.*?-----END CERTIFICATE-----",
    re.DOTALL,
)


def pem_blocks(pem_text: str) -> list[str]:
    return _PEM_BLOCK.findall(pem_text or "")


def get_router_ca_pem(*, oc: str = "oc") -> str:
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


def _openssl_verify(ca_pem: str, cert_pem: str) -> bool:
    with tempfile.TemporaryDirectory() as tmpdir:
        work = Path(tmpdir)
        ca_path = work / "ca.pem"
        cert_path = work / "cert.pem"
        ca_path.write_text(ca_pem, encoding="utf-8")
        cert_path.write_text(cert_pem, encoding="utf-8")
        try:
            result = subprocess.run(
                [
                    "openssl",
                    "verify",
                    "-no-CAfile",
                    "-no-CApath",
                    "-CAfile",
                    str(ca_path),
                    str(cert_path),
                ],
                capture_output=True,
                text=True,
                check=False,
                timeout=_OPENSSL_VERIFY_TIMEOUT_SECONDS,
            )
        except subprocess.TimeoutExpired as exc:
            msg = "openssl verify timed out"
            raise RuntimeError(msg) from exc
        except OSError as exc:
            msg = "openssl not found"
            raise RuntimeError(msg) from exc
    return result.returncode == 0


def _get_cert_issuer(cert_pem: str) -> str:
    """Extract the issuer DN from a certificate in RFC2253 format."""
    try:
        result = subprocess.run(
            ["openssl", "x509", "-noout", "-issuer", "-nameopt", "RFC2253"],
            input=cert_pem,
            capture_output=True,
            text=True,
            check=False,
            timeout=_OPENSSL_VERIFY_TIMEOUT_SECONDS,
        )
    except (subprocess.TimeoutExpired, OSError):
        return ""

    if result.returncode != 0:
        return ""

    # Output format: "issuer=CN=Root CA,O=Org,C=US"
    match = re.match(r"issuer=(.+)", result.stdout.strip())
    return match.group(1) if match else ""


def _get_cert_subject(cert_pem: str) -> str:
    """Extract the subject DN from a certificate in RFC2253 format."""
    try:
        result = subprocess.run(
            ["openssl", "x509", "-noout", "-subject", "-nameopt", "RFC2253"],
            input=cert_pem,
            capture_output=True,
            text=True,
            check=False,
            timeout=_OPENSSL_VERIFY_TIMEOUT_SECONDS,
        )
    except (subprocess.TimeoutExpired, OSError):
        return ""

    if result.returncode != 0:
        return ""

    # Output format: "subject=CN=Intermediate CA,O=Org,C=US"
    match = re.match(r"subject=(.+)", result.stdout.strip())
    return match.group(1) if match else ""


def _find_root_ca_in_system_store(issuer_dn: str) -> str | None:
    """
    Find the root CA certificate in system CA stores matching the given issuer DN.

    Args:
        issuer_dn: The issuer DN to search for (RFC2253 format)

    Returns:
        PEM content of the root CA, or None if not found
    """
    # Common system CA paths (RHEL/Fedora, Debian/Ubuntu)
    ca_paths = [
        Path("/etc/pki/tls/certs/ca-bundle.crt"),
        Path("/etc/ssl/certs/ca-certificates.crt"),
    ]

    for bundle_path in ca_paths:
        if not bundle_path.exists():
            continue

        try:
            bundle_content = bundle_path.read_text(encoding="utf-8")
            # System CA bundles contain multiple certs
            for cert_pem in pem_blocks(bundle_content):
                subject = _get_cert_subject(cert_pem)
                if subject and subject == issuer_dn:
                    logger.debug("Found root CA in %s", bundle_path)
                    return cert_pem
        except (OSError, UnicodeDecodeError) as exc:
            logger.debug("Error reading %s: %s", bundle_path, exc)
            continue

    return None


def _chain_trust_anchor_pem(chain: list[str]) -> str:
    """
    Return complete CA PEM bundle from a fetched TLS chain.

    Includes all intermediate CAs from the chain, plus attempts to find
    and append the root CA from the system trust store if the chain is incomplete.

    Args:
        chain: List of PEM certificates from TLS handshake [leaf, intermediate(s)...]

    Returns:
        Complete CA bundle with intermediates + root CA (if found)
    """
    ca_certs = chain[1:]
    if not ca_certs:
        msg = (
            "TLS chain contains only a leaf certificate; cannot determine trust anchor"
        )
        raise RuntimeError(msg)

    # Check if the last cert in the chain is self-signed (i.e., a root CA)
    last_cert = ca_certs[-1]
    issuer = _get_cert_issuer(last_cert)
    subject = _get_cert_subject(last_cert)

    if issuer and subject and issuer == subject:
        # Chain already contains a self-signed root CA
        logger.debug("TLS chain contains self-signed root CA")
        return "\n".join(ca_certs) + "\n"

    # Chain is incomplete - try to find the root CA from system store
    if not issuer:
        logger.warning("Could not determine issuer from last intermediate CA")
        return "\n".join(ca_certs) + "\n"

    logger.debug("Searching for root CA with subject: %s", issuer)
    root_ca = _find_root_ca_in_system_store(issuer)

    if root_ca:
        logger.info("Completed certificate chain with root CA from system trust store")
        return "\n".join([*ca_certs, root_ca]) + "\n"

    logger.warning(
        "Root CA not found in system trust store for issuer: %s. "
        "UpdateService may fail to verify the registry certificate in disconnected mode.",
        issuer,
    )
    return "\n".join(ca_certs) + "\n"


def resolve_registry_ca_pem(hostname: str, *, oc: str = "oc") -> str:
    """Return PEM for the CA that should trust the registry route certificate."""
    router_ca = get_router_ca_pem(oc=oc).strip()
    chain = pem_blocks(fetch_tls_chain_pem(hostname))
    if not chain:
        msg = f"unable to fetch TLS certificate chain for {hostname}"
        raise RuntimeError(msg)

    leaf = chain[0]
    if router_ca and _openssl_verify(router_ca, leaf):
        logger.debug("using router-ca to trust %s", hostname)
        return f"{router_ca}\n"

    logger.debug("using TLS chain CA bundle to trust %s", hostname)
    return _chain_trust_anchor_pem(chain)


def main(hostname: str, *, oc: str = "oc") -> None:
    sys.stdout.write(resolve_registry_ca_pem(hostname, oc=oc))
