"""RHEL 10 system trust store CA discovery for certificate chain completion."""

import logging
from pathlib import Path

from enclave.tools.cert_utils import is_self_signed, openssl_verify, pem_blocks

logger = logging.getLogger(__name__)

RHEL_TRUST_STORE = Path("/etc/pki/tls/certs/ca-bundle.crt")


def find_system_ca_for_chain(chain_pem: str) -> str | None:
    """Return the PEM of the first RHEL 10 trust store CA that signs the last cert.

    Raises ValueError when chain_pem contains no PEM certificate blocks.
    Raises RuntimeError when the RHEL trust store cannot be read.
    Returns None when the chain ends with a self-signed root (no CA needed) or
    when no CA in the store verifies the last certificate.
    """
    certs = pem_blocks(chain_pem)
    if not certs:
        raise ValueError("no PEM blocks in chain")
    last_cert = certs[-1]
    if is_self_signed(last_cert):
        logger.debug(
            "find_system_ca_for_chain: chain ends with self-signed root; no CA needed"
        )
        return None
    try:
        bundle_text = RHEL_TRUST_STORE.read_text(encoding="utf-8")
    except OSError as exc:
        logger.warning("find_system_ca_for_chain: trust store unavailable: %s", exc)
        raise RuntimeError(f"system trust store unavailable: {exc}") from exc
    for ca_pem in pem_blocks(bundle_text):
        if openssl_verify(ca_pem, last_cert):
            logger.info(
                "find_system_ca_for_chain: found matching CA in %s", RHEL_TRUST_STORE
            )
            return ca_pem
    logger.warning(
        "find_system_ca_for_chain: no matching CA found in %s", RHEL_TRUST_STORE
    )
    return None
