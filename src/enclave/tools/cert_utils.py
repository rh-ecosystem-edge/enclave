"""Shared certificate utilities for PEM parsing and self-signed detection."""

import logging
import re
import subprocess
import tempfile
from pathlib import Path

logger = logging.getLogger(__name__)

_OPENSSL_TIMEOUT_SECONDS = 10
_PEM_BLOCK = re.compile(
    r"-----BEGIN CERTIFICATE-----.*?-----END CERTIFICATE-----",
    re.DOTALL,
)


def openssl_verify(ca_pem: str, cert_pem: str) -> bool:
    """Return True if ca_pem signs cert_pem according to openssl verify.

    Verification is isolated from the system trust store via -no-CAfile/-no-CApath
    so only the explicitly provided CA is consulted. Raises RuntimeError on timeout
    or missing openssl binary.
    """
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
                timeout=_OPENSSL_TIMEOUT_SECONDS,
            )
        except subprocess.TimeoutExpired as exc:
            msg = "openssl verify timed out"
            raise RuntimeError(msg) from exc
        except OSError as exc:
            msg = "openssl not found"
            raise RuntimeError(msg) from exc
    return result.returncode == 0


def pem_blocks(pem_text: str) -> list[str]:
    """Return all PEM certificate blocks found in pem_text, in order."""
    return _PEM_BLOCK.findall(pem_text or "")


def is_self_signed(cert_pem: str) -> bool:
    """Return True if cert_pem is self-signed.

    Two-step check: fast DN equality pre-filter (self-issued), then an actual
    signature verification using openssl verify -check_ss_sig so that a cert
    whose issuer==subject but was signed by a different key returns False.
    Returns False when openssl is unavailable, times out, or returns a non-zero exit code.
    """
    try:
        result = subprocess.run(
            ["openssl", "x509", "-noout", "-issuer", "-subject", "-nameopt", "RFC2253"],
            input=cert_pem,
            capture_output=True,
            text=True,
            check=False,
            timeout=_OPENSSL_TIMEOUT_SECONDS,
        )
    except (subprocess.TimeoutExpired, OSError) as exc:
        logger.warning("openssl unavailable: %s", exc)
        return False
    if result.returncode != 0:
        return False
    issuer_match = re.search(r"^issuer=(.+)$", result.stdout, re.MULTILINE)
    subject_match = re.search(r"^subject=(.+)$", result.stdout, re.MULTILINE)
    if not issuer_match or not subject_match:
        return False
    if issuer_match.group(1).strip() != subject_match.group(1).strip():
        return False
    with tempfile.TemporaryDirectory() as tmpdir:
        cert_path = Path(tmpdir) / "cert.pem"
        cert_path.write_text(cert_pem, encoding="utf-8")
        try:
            verify = subprocess.run(
                [
                    "openssl",
                    "verify",
                    "-no-CAfile",
                    "-no-CApath",
                    "-check_ss_sig",
                    "-CAfile",
                    str(cert_path),
                    str(cert_path),
                ],
                capture_output=True,
                text=True,
                check=False,
                timeout=_OPENSSL_TIMEOUT_SECONDS,
            )
        except (subprocess.TimeoutExpired, OSError) as exc:
            logger.warning("openssl self-signature check unavailable: %s", exc)
            return False
    return verify.returncode == 0
