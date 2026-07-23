"""Certificate chain completeness and CA consistency checks."""

import logging
from pathlib import Path
from typing import Any

import yaml

from enclave.tools.cert_utils import (
    cert_covers_hostname,
    cert_key_pair_matches,
    cert_validity_window_ok,
    is_self_signed,
    openssl_verify,
    pem_blocks,
)
from enclave.tools.system_ca import find_system_ca_for_chain

logger = logging.getLogger(__name__)

# Maps cert_type argument to its config field name in certificates.yaml.
CERT_TYPE_TO_FIELD: dict[str, str] = {
    "api": "sslAPICertificateFullChain",
    "ingress": "sslIngressCertificateFullChain",
    "ironic": "ironicHTTPSCertificate",
}

# Maps cert_type to the private key field that accompanies the cert in certificates.yaml.
CERT_TYPE_TO_KEY_FIELD: dict[str, str] = {
    "api": "sslAPICertificateKey",
    "ingress": "sslIngressCertificateKey",
    "ironic": "ironicHTTPSKey",
}

# Types that carry a full chain and require completeness + CA consistency checks.
# "ironic" is excluded: ironicHTTPSCertificate is a single cert with no CA counterpart.
CHAIN_CERT_TYPES: frozenset[str] = frozenset({"api", "ingress"})


class CertificateValidationError(Exception):
    """Raised when certificate chain validation fails."""


def _get_config_str(raw: dict[str, Any], field: str) -> str:
    """Return raw[field] as a stripped string, or "" if absent or None.

    Raises CertificateValidationError if the field is present but not a string,
    so a misconfigured YAML value (list, int, bool) never leaks an AttributeError.
    """
    value = raw.get(field)
    if value is None:
        return ""
    if not isinstance(value, str):
        raise CertificateValidationError(
            f"{field}: expected a string value, got {type(value).__name__}"
        )
    return value.strip()


def _load_config(config_path: str) -> dict[str, Any]:
    """Read and parse a YAML config file, raising CertificateValidationError on any failure."""
    try:
        text = Path(config_path).read_text(encoding="utf-8")
    except OSError as exc:
        raise CertificateValidationError(f"cannot read {config_path}: {exc}") from exc
    try:
        raw = yaml.safe_load(text) or {}
    except yaml.YAMLError as exc:
        raise CertificateValidationError(f"cannot parse {config_path}: {exc}") from exc
    if not isinstance(raw, dict):
        raise CertificateValidationError(
            f"{config_path}: expected a YAML mapping, got {type(raw).__name__}"
        )
    return raw


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


def _check_leaf(
    field: str,
    leaf_pem: str,
    hostnames: list[str],
    key_pem: str,
) -> list[str]:
    """Return issue strings for leaf-cert checks: expiry, SANs, and key match."""
    issues: list[str] = []
    if not cert_validity_window_ok(leaf_pem):
        issues.append(f"{field}: leaf certificate has expired or is not yet valid.")
    issues.extend(
        f"{field}: leaf certificate SAN does not cover '{hostname}'."
        for hostname in hostnames
        if not cert_covers_hostname(leaf_pem, hostname)
    )
    if not cert_key_pair_matches(leaf_pem, key_pem):
        issues.append(f"{field}: private key does not match the leaf certificate.")
    return issues


def check_certificate_chains(
    config_path: str,
    cert_type: str,
    hostnames: list[str],
) -> None:
    """Check that a certificate is valid, complete, and consistent.

    cert_type selects which config field to validate:
      "api"     → sslAPICertificateFullChain    (full chain + leaf checks)
      "ingress" → sslIngressCertificateFullChain (full chain + leaf checks)
      "ironic"  → ironicHTTPSCertificate        (leaf checks only)

    For api/ingress: chain completeness is verified against sslCACertificate or the
    system trust store.
    For ironic: no chain check.

    The private key is always read from the config (sslAPICertificateKey,
    sslIngressCertificateKey, or ironicHTTPSKey) and is required whenever the
    corresponding cert field is set.

    Leaf cert expiry is always checked when the cert field is present and non-empty.
    Each hostname in hostnames is checked against the cert's SANs.

    Raises CertificateValidationError on any validation failure.
    """
    try:
        field = CERT_TYPE_TO_FIELD[cert_type]
        key_field = CERT_TYPE_TO_KEY_FIELD[cert_type]
    except KeyError as exc:
        msg = f"unknown certificate type: {cert_type!r}"
        raise CertificateValidationError(msg) from exc

    raw = _load_config(config_path)

    cert_pem: str = _get_config_str(raw, field)
    if not cert_pem:
        logger.debug("%s: no certificate configured; skipping", cert_type)
        return

    if not hostnames:
        msg = f"{cert_type}: at least one hostname is required"
        raise CertificateValidationError(msg)

    key_pem: str = _get_config_str(raw, key_field)
    if not key_pem:
        msg = f"{key_field} is required when {field} is configured"
        raise CertificateValidationError(msg)

    issues: list[str] = []

    if cert_type in CHAIN_CERT_TYPES:
        ca_pem: str = _get_config_str(raw, "sslCACertificate")
        issue = _check_chain(field, cert_pem, ca_pem)
        if issue:
            issues.append(issue)

        certs = pem_blocks(cert_pem)
        if certs:
            issues.extend(_check_leaf(field, certs[0], hostnames, key_pem))
    else:
        issues.extend(_check_leaf(field, cert_pem, hostnames, key_pem))

    if issues:
        raise CertificateValidationError("\n".join(issues))

    logger.info("certificate chain check passed for %s", cert_type)
