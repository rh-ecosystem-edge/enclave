"""Certificate chain completeness and CA consistency checks."""

import logging
import sys
from pathlib import Path

import yaml

from enclave.tools.cert_utils import is_self_signed, openssl_verify, pem_blocks
from enclave.tools.system_ca import find_system_ca_for_chain

logger = logging.getLogger(__name__)

_CHAIN_FIELDS = ("sslAPICertificateFullChain", "sslIngressCertificateFullChain")


class CertificateValidationError(Exception):
    """Raised when certificate chain validation fails."""


def _check_self_signed_root(field: str, cert_pem: str, ca_pem: str) -> str | None:
    """Return an issue string when the chain ends with a self-signed root, else None."""
    if not openssl_verify(cert_pem, cert_pem):
        return f"{field}: chain ends with a self-signed root that has expired."
    if ca_pem and not openssl_verify(ca_pem, cert_pem):
        return (
            f"{field}: chain ends with a self-signed root but sslCACertificate "
            f"does not verify it — they may belong to different CA hierarchies."
        )
    return None


def _check_chain(field: str, chain_pem: str, ca_pem: str) -> str | None:
    """Return an issue string if the chain has a completeness or consistency problem, else None."""
    certs = pem_blocks(chain_pem)
    if not certs:
        return f"{field}: configured but contains no PEM certificate blocks — check the value"
    if is_self_signed(certs[-1]):
        return _check_self_signed_root(field, certs[-1], ca_pem)
    if not ca_pem:
        try:
            system_ca = find_system_ca_for_chain(chain_pem)
        except RuntimeError:
            system_ca = None
        if system_ca:
            logger.info("%s: using CA found in system trust store", field)
            return None
        return (
            f"{field}: chain ends with a non-self-signed certificate, "
            f"no sslCACertificate was provided, and no matching CA was found "
            f"in the system trust store (/etc/pki/tls/certs/ca-bundle.crt)."
        )
    if openssl_verify(ca_pem, certs[-1]):
        logger.info("%s: sslCACertificate correctly completes the chain", field)
        return None
    return (
        f"{field}: chain is incomplete and sslCACertificate does not "
        f"sign its last certificate — the CA will not complete the chain."
    )


def check_certificate_chains(config_path: str) -> None:
    """Check that certificate chains are complete and the CA is consistent.

    Raises CertificateValidationError when a chain ends with a non-self-signed
    certificate and either sslCACertificate is not set, or the provided CA does not
    sign the last certificate in the chain. Also raises when a complete chain (ending
    with a self-signed root) is inconsistent with the provided sslCACertificate.
    """
    try:
        text = Path(config_path).read_text(encoding="utf-8")
    except OSError as exc:
        msg = f"cannot read {config_path}: {exc}"
        raise CertificateValidationError(msg) from exc

    try:
        raw = yaml.safe_load(text) or {}
    except yaml.YAMLError as exc:
        msg = f"cannot parse {config_path}: {exc}"
        raise CertificateValidationError(msg) from exc

    if not isinstance(raw, dict):
        msg = f"{config_path}: expected a YAML mapping, got {type(raw).__name__}"
        raise CertificateValidationError(msg)

    ca_pem: str = raw.get("sslCACertificate") or ""
    issues: list[str] = []

    for field in _CHAIN_FIELDS:
        chain_pem: str = raw.get(field) or ""
        if not chain_pem:
            continue
        issue = _check_chain(field, chain_pem, ca_pem)
        if issue:
            issues.append(issue)

    if issues:
        raise CertificateValidationError("\n".join(issues))

    logger.debug("certificate chain check passed")


def main(config_path: str) -> None:
    check_certificate_chains(config_path)
    sys.stdout.write("Certificate chain check passed.\n")
