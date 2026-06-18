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
                ["openssl", "verify", "-CAfile", str(ca_path), str(cert_path)],
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


def _chain_trust_anchor_pem(chain: list[str]) -> str:
    """Return CA PEM bundle from a fetched TLS chain (all certs except the leaf)."""
    ca_certs = chain[1:]
    if not ca_certs:
        msg = (
            "TLS chain contains only a leaf certificate; cannot determine trust anchor"
        )
        raise RuntimeError(msg)
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
