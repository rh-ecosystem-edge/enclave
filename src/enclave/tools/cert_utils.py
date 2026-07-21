"""Shared certificate utilities for PEM parsing and self-signed detection."""

import logging
import re
import subprocess
import tempfile
from datetime import UTC, datetime
from pathlib import Path

logger = logging.getLogger(__name__)

OPENSSL_TIMEOUT_SECONDS = 10
PEM_BLOCK_RE = re.compile(
    r"-----BEGIN CERTIFICATE-----.*?-----END CERTIFICATE-----",
    re.DOTALL,
)

# OpenSSL always outputs English month abbreviations in notBefore/notAfter regardless
# of the system locale. Python's %b in strptime follows LC_TIME, so it would reject
# "Jan" on a French or German system. We map month names explicitly instead.
OPENSSL_MONTHS = {
    "Jan": 1,
    "Feb": 2,
    "Mar": 3,
    "Apr": 4,
    "May": 5,
    "Jun": 6,
    "Jul": 7,
    "Aug": 8,
    "Sep": 9,
    "Oct": 10,
    "Nov": 11,
    "Dec": 12,
}


def _parse_openssl_date(date_str: str) -> datetime:
    """Parse an OpenSSL notBefore/notAfter string into a UTC-aware datetime."""
    parts = date_str.strip().split()
    if len(parts) < 4:  # noqa: PLR2004 — month, day, HH:MM:SS, year
        raise ValueError(f"unexpected OpenSSL date string: {date_str!r}")
    month = OPENSSL_MONTHS.get(parts[0])
    if month is None:
        raise ValueError(f"unknown month abbreviation {parts[0]!r} in: {date_str!r}")
    normalized = f"{month:02d} {parts[1]} {parts[2]} {parts[3]}"
    return datetime.strptime(normalized, "%m %d %H:%M:%S %Y").replace(tzinfo=UTC)


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
                timeout=OPENSSL_TIMEOUT_SECONDS,
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
    return PEM_BLOCK_RE.findall(pem_text or "")


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
            timeout=OPENSSL_TIMEOUT_SECONDS,
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
                    "-no_check_time",
                    "-check_ss_sig",
                    "-CAfile",
                    str(cert_path),
                    str(cert_path),
                ],
                capture_output=True,
                text=True,
                check=False,
                timeout=OPENSSL_TIMEOUT_SECONDS,
            )
        except (subprocess.TimeoutExpired, OSError) as exc:
            logger.warning("openssl self-signature check unavailable: %s", exc)
            return False
    return verify.returncode == 0


def cert_key_pair_matches(cert_pem: str, key_pem: str) -> bool:
    """Return True if key_pem is the private key for cert_pem.

    Extracts the public key from each side via stdin and compares them.
    Neither the cert nor the key is written to disk.
    Returns False on any openssl error, mismatch, or missing binary.
    """
    try:
        # Extract public key from the certificate via stdin.
        cert_pub = subprocess.run(
            ["openssl", "x509", "-noout", "-pubkey"],
            input=cert_pem,
            capture_output=True,
            text=True,
            check=False,
            timeout=OPENSSL_TIMEOUT_SECONDS,
        )
        # Extract public key from the private key via stdin.
        key_pub = subprocess.run(
            ["openssl", "pkey", "-pubout"],
            input=key_pem,
            capture_output=True,
            text=True,
            check=False,
            timeout=OPENSSL_TIMEOUT_SECONDS,
        )
    except (subprocess.TimeoutExpired, OSError) as exc:
        logger.warning("openssl unavailable: %s", exc)
        return False
    if cert_pub.returncode != 0 or key_pub.returncode != 0:
        return False
    return cert_pub.stdout.strip() == key_pub.stdout.strip()


def cert_validity_window_ok(cert_pem: str) -> bool:
    """Return True if the current UTC time is within the cert's notBefore..notAfter window.

    Returns False when the cert is not yet valid, has expired, or cannot be parsed.
    """
    try:
        result = subprocess.run(
            ["openssl", "x509", "-noout", "-startdate", "-enddate"],
            input=cert_pem,
            capture_output=True,
            text=True,
            check=False,
            timeout=OPENSSL_TIMEOUT_SECONDS,
        )
    except (subprocess.TimeoutExpired, OSError) as exc:
        logger.warning("openssl unavailable: %s", exc)
        return False
    if result.returncode != 0:
        return False
    not_before_match = re.search(r"^notBefore=(.+)$", result.stdout, re.MULTILINE)
    not_after_match = re.search(r"^notAfter=(.+)$", result.stdout, re.MULTILINE)
    if not not_before_match or not not_after_match:
        return False
    try:
        not_before = _parse_openssl_date(not_before_match.group(1))
        not_after = _parse_openssl_date(not_after_match.group(1))
    except ValueError:
        logger.warning(
            "cert_validity_window_ok: could not parse date strings %r / %r",
            not_before_match.group(1).strip(),
            not_after_match.group(1).strip(),
        )
        return False
    now = datetime.now(UTC)
    return not_before <= now <= not_after


def cert_covers_hostname(cert_pem: str, hostname: str) -> bool:
    """Return True if any SAN in cert_pem covers hostname.

    Supports exact DNS names (case-insensitive) and wildcards (*.example.com matches
    foo.example.com but not example.com or foo.bar.example.com). Returns False when
    no SAN extension is present, or when no SAN matches.
    """
    try:
        result = subprocess.run(
            ["openssl", "x509", "-noout", "-ext", "subjectAltName"],
            input=cert_pem,
            capture_output=True,
            text=True,
            check=False,
            timeout=OPENSSL_TIMEOUT_SECONDS,
        )
    except (subprocess.TimeoutExpired, OSError) as exc:
        logger.warning("openssl unavailable: %s", exc)
        return False
    if result.returncode != 0 or not result.stdout.strip():
        return False
    # Parse "DNS:foo.example.com, DNS:*.example.com, IP Address:1.2.3.4" lines.
    dns_names = re.findall(r"DNS:([^\s,]+)", result.stdout)
    hostname_lower = hostname.lower()
    for san in dns_names:
        san_lower = san.lower()
        if san_lower.startswith("*."):
            # Wildcard: exactly one label to the left, no glob metacharacter interpretation.
            # *.example.com covers foo.example.com but not example.com or foo.bar.example.com.
            san_suffix = san_lower[1:]  # ".example.com"
            if hostname_lower.endswith(san_suffix) and hostname.count(".") == san.count(
                "."
            ):
                return True
        elif san_lower == hostname_lower:
            return True
    return False
