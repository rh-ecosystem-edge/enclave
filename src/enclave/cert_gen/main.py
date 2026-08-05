"""Real-CA certificate generation for enclave deployments.

Issues TLS certificates from public CAs via certbot + Hetzner DNS-01 and prints
YAML suitable for use as certificates.yaml (ingress-api) or to populate
ironicHTTPSCertificate/ironicHTTPSKey (ironic).

Required environment variables:
  HETZNER_API_TOKEN      Hetzner Cloud API token (used by certbot-dns-hetzner)
  ACME_EMAIL             ACME account registration email

ZeroSSL cert types also require:
  ZEROSSL_EAB_KID        ZeroSSL EAB key ID
  ZEROSSL_EAB_HMAC_KEY   ZeroSSL EAB HMAC key
"""

import os
import re
import subprocess
import tempfile
from pathlib import Path

import click
import requests
import yaml
from cryptography import x509
from cryptography.hazmat.primitives.serialization import Encoding
from cryptography.x509 import UniformResourceIdentifier
from cryptography.x509.oid import AuthorityInformationAccessOID


class CertIssuanceError(Exception):
    """Raised when certbot fails to issue or renew a certificate."""


class RootCaExtractionError(Exception):
    """Raised when the root CA cannot be located in the certificate chain."""


LE_SERVER = "https://acme-v02.api.letsencrypt.org/directory"
ZS_SERVER = "https://acme.zerossl.com/v2/DV90"

# Each entry drives one certbot invocation.  Fields:
#   server          — ACME directory URL
#   key_type        — certbot --key-type value ("rsa" or "ecdsa")
#   key_param       — --rsa-key-size (RSA) or --elliptic-curve (ECDSA)
#   preferred_chain — certbot --preferred-chain; empty string = CA default
CERT_TYPES: dict[str, dict[str, str]] = {
    # Let's Encrypt — RSA 2048
    # Key:   RSA 2048-bit
    # Chain: leaf → R10/R11 intermediate (RSA, signed by ISRG Root X1)
    # Root:  ISRG Root X1 (RSA 4096); trusted by virtually all TLS clients,
    #        including Android < 7.1 and other legacy devices.
    # Notes: broadest compatibility; largest handshake of the five types.
    "le-rsa": {
        "server": LE_SERVER,
        "key_type": "rsa",
        "key_param": "2048",
        "preferred_chain": "",
    },
    # Let's Encrypt — ECDSA P-384, long chain (cross-signed to RSA root)
    # Key:   ECDSA P-384 (secp384r1)
    # Chain: leaf → E5/E6 intermediate (ECDSA, cross-signed by ISRG Root X1)
    # Root:  ISRG Root X1 (RSA 4096)
    # Notes: smaller leaf+intermediate than le-rsa but the RSA root keeps the
    #        chain compatible with clients that do not yet trust ISRG Root X2.
    #        "Long" refers to the cross-signature adding an extra cert vs the
    #        short ECDSA chain; preferred_chain="" lets certbot pick this default.
    "le-ecdsa-long": {
        "server": LE_SERVER,
        "key_type": "ecdsa",
        "key_param": "secp384r1",
        "preferred_chain": "",
    },
    # Let's Encrypt — ECDSA P-384, short chain (native ECDSA root)
    # Key:   ECDSA P-384 (secp384r1)
    # Chain: leaf → E5/E6 intermediate (ECDSA, signed directly by ISRG Root X2)
    # Root:  ISRG Root X2 (ECDSA P-384); present in major trust stores since ~2021
    #        (Chrome, Firefox, Safari, Android 8+, iOS 14+, RHEL 9+).
    # Notes: shortest and fastest handshake of the LE options; requires a modern
    #        trust store.  preferred_chain="ISRG Root X2" instructs certbot to
    #        select this chain instead of the cross-signed default.
    "le-ecdsa-short": {
        "server": LE_SERVER,
        "key_type": "ecdsa",
        "key_param": "secp384r1",
        "preferred_chain": "ISRG Root X2",
    },
    # ZeroSSL — RSA 2048
    # Key:   RSA 2048-bit
    # Chain: leaf → ZeroSSL RSA Domain Secure Site CA → USERTrust RSA CA
    # Root:  USERTrust RSA Certification Authority (RSA 4096, Sectigo/COMODO);
    #        trusted by all major browsers and OS trust stores.
    # Notes: second CA vendor; diversifies CA dependency vs Let's Encrypt.
    #        Requires EAB credentials (ZEROSSL_EAB_KID + ZEROSSL_EAB_HMAC_KEY).
    "zerossl-rsa": {
        "server": ZS_SERVER,
        "key_type": "rsa",
        "key_param": "2048",
        "preferred_chain": "",
    },
    # ZeroSSL — ECDSA P-384
    # Key:   ECDSA P-384 (secp384r1)
    # Chain: leaf → ZeroSSL ECC Domain Secure Site CA → USERTrust ECC CA
    # Root:  USERTrust ECC Certification Authority (ECDSA P-384, Sectigo/COMODO);
    #        trusted by major browsers and OS trust stores (Android 7+, iOS 10+).
    # Notes: smallest ZeroSSL chain; same EAB credential requirement as zerossl-rsa.
    "zerossl-ecdsa": {
        "server": ZS_SERVER,
        "key_type": "ecdsa",
        "key_param": "secp384r1",
        "preferred_chain": "",
    },
}


def write_hetzner_ini(work_dir: Path, token: str) -> Path:
    """Write a 0600 certbot-dns-hetzner credentials file and return its path."""
    ini_path = work_dir / "hetzner.ini"
    ini_path.write_text(f"dns_hetzner_api_token = {token}\n", encoding="utf-8")
    ini_path.chmod(0o600)
    return ini_path


def issue_cert(
    config_dir: Path,
    server: str,
    key_type: str,
    key_param: str,
    preferred_chain: str,
    domains: list[str],
    *,
    email: str,
    hetzner_ini: Path,
    eab_kid: str,
    eab_hmac_key: str,
) -> None:
    """Run certbot to obtain a certificate via DNS-01 and store it under config_dir."""
    args = [
        "certbot",
        "certonly",
        "--non-interactive",
        "--agree-tos",
        "--email",
        email,
        "--no-eff-email",
        "--authenticator",
        "dns-hetzner",
        "--dns-hetzner-credentials",
        str(hetzner_ini),
        "--server",
        server,
        "--config-dir",
        str(config_dir),
        "--work-dir",
        str(config_dir / "work"),
        "--logs-dir",
        str(config_dir / "logs"),
        "--key-type",
        key_type,
        "--force-renewal",
    ]
    if key_type == "rsa":
        args += ["--rsa-key-size", key_param]
    else:
        args += ["--elliptic-curve", key_param]
    if preferred_chain:
        args += ["--preferred-chain", preferred_chain]
    if eab_kid and eab_hmac_key:
        # Write EAB credentials to a 0600 file and pass --config so they never
        # appear in the process argument list (readable via /proc/<pid>/cmdline).
        eab_ini = config_dir / "eab.ini"
        eab_ini.write_text(
            f"eab-kid = {eab_kid}\neab-hmac-key = {eab_hmac_key}\n", encoding="utf-8"
        )
        eab_ini.chmod(0o600)
        args += ["--config", str(eab_ini)]
    for domain in domains:
        args += ["-d", domain]

    result = subprocess.run(args, capture_output=True, text=True, check=False)
    if result.returncode != 0:
        output = result.stdout + result.stderr
        for secret in (eab_kid, eab_hmac_key):
            if secret:
                output = output.replace(secret, "***REDACTED***")
        match = re.search(r"retry after \S+", output, re.IGNORECASE)
        if match:
            raise CertIssuanceError(f"CA rate limit reached — {match.group()}")
        raise CertIssuanceError(f"Certificate issuance failed:\n{output}")


def _aia_issuer_url(cert: x509.Certificate) -> str:
    """Return the CA Issuers URL from a certificate's AIA extension."""
    try:
        aia = cert.extensions.get_extension_for_class(x509.AuthorityInformationAccess)
    except x509.ExtensionNotFound as exc:
        raise RootCaExtractionError(
            f"Cert has no AIA extension: {cert.subject}"
        ) from exc
    for desc in aia.value:
        if (
            desc.access_method == AuthorityInformationAccessOID.CA_ISSUERS
            and isinstance(desc.access_location, UniformResourceIdentifier)
        ):
            return desc.access_location.value
    raise RootCaExtractionError(f"No CA Issuers URI in AIA of cert: {cert.subject}")


def _fetch_cert(url: str) -> x509.Certificate:
    """Download a DER or PEM certificate from url and return it parsed."""
    if not url.startswith(("http://", "https://")):
        raise RootCaExtractionError(f"Unsupported AIA issuer URL scheme: {url}")
    try:
        with requests.get(url, timeout=15, stream=True) as resp:
            resp.raise_for_status()
            data = resp.raw.read(65536, decode_content=True)
    except requests.RequestException as exc:
        raise RootCaExtractionError(f"Failed to fetch AIA issuer {url}: {exc}") from exc
    try:
        return x509.load_der_x509_certificate(data)
    except ValueError:
        try:
            return x509.load_pem_x509_certificate(data)
        except ValueError as exc:
            raise RootCaExtractionError(f"Cannot parse cert from {url}") from exc


def extract_root_ca(chain_pem_path: Path) -> str:
    """Return the root CA PEM by scanning the chain file or walking AIA issuer URLs."""
    pem_text = chain_pem_path.read_text(encoding="utf-8")
    pem_blocks = re.findall(
        r"-----BEGIN CERTIFICATE-----.*?-----END CERTIFICATE-----",
        pem_text,
        re.DOTALL,
    )
    if not pem_blocks:
        raise RootCaExtractionError(f"No certificates found in {chain_pem_path}")

    for pem in reversed(pem_blocks):
        cert = x509.load_pem_x509_certificate(pem.encode())
        if cert.subject == cert.issuer:
            return pem

    current = x509.load_pem_x509_certificate(pem_blocks[-1].encode())
    for _ in range(5):
        try:
            url = _aia_issuer_url(current)
        except RootCaExtractionError as exc:
            raise RootCaExtractionError(
                f"Chain in {chain_pem_path} contains no root CA and {exc}"
            ) from exc
        fetched = _fetch_cert(url)
        if fetched.subject == fetched.issuer:
            return fetched.public_bytes(Encoding.PEM).decode()
        current = fetched

    raise RootCaExtractionError(
        f"Could not find root CA after 5 AIA hops from {chain_pem_path}"
    )


class _BlockLiteralDumper(yaml.Dumper):
    pass


def _literal_str(dumper: yaml.Dumper, data: str) -> yaml.ScalarNode:
    r"""PyYAML representer that renders multiline strings as YAML literal block scalars.

    PyYAML calls registered representers once per Python value being serialized,
    including both mapping keys and values.  This function is registered for the
    ``str`` type on ``_BlockLiteralDumper`` via ``add_representer``.

    When ``data`` contains a newline the YAML ``|`` (literal block) style is
    requested, which preserves every embedded newline exactly — the reader gets
    back the string character-for-character.  PEM blobs always contain newlines,
    so they reliably take this path and appear as indented blocks in the output:

        sslAPICertificateKey: |
          <base64 key data>
          ...

    When ``data`` has no newline (e.g. YAML mapping key names such as
    ``sslAPICertificateKey``) ``style=None`` lets PyYAML choose the default plain
    scalar style, which keeps key names on the same line as their ``:``.

    PyYAML automatically selects the chomp indicator based on whether ``data``
    ends with ``\\n``: ``|`` (clip — keep one trailing newline) when it does,
    ``|-`` (strip — drop it) when it does not.  Real PEM files always end with
    ``\\n``, so the output consistently uses ``|`` rather than ``|-``.
    """
    style = "|" if "\n" in data else None
    return dumper.represent_scalar("tag:yaml.org,2002:str", data, style=style)


_BlockLiteralDumper.add_representer(str, _literal_str)


def render_ingress_api_yaml(
    fullchain: str, key: str, root_ca: str | None = None
) -> str:
    """Render the sslAPI*/sslIngress* (and optionally sslCACertificate) YAML block."""
    data: dict[str, str] = {
        "sslAPICertificateKey": key,
        "sslAPICertificateFullChain": fullchain,
        "sslIngressCertificateKey": key,
        "sslIngressCertificateFullChain": fullchain,
    }
    if root_ca is not None:
        data["sslCACertificate"] = root_ca
    return yaml.dump(data, Dumper=_BlockLiteralDumper, sort_keys=False)


def render_ironic_yaml(cert: str, key: str) -> str:
    """Render the ironicHTTPSCertificate and ironicHTTPSKey YAML block."""
    return yaml.dump(
        {"ironicHTTPSCertificate": cert, "ironicHTTPSKey": key},
        Dumper=_BlockLiteralDumper,
        sort_keys=False,
    )


def _get_env(name: str) -> str:
    """Return the value of environment variable name, raising UsageError if unset."""
    val = os.environ.get(name, "")
    if not val:
        raise click.UsageError(f"Missing required environment variable: {name}")
    return val


def _load_credentials(cert_type: str) -> tuple[str, str, str, str]:
    """Return (token, email, eab_kid, eab_hmac_key) for the given cert type."""
    token = _get_env("HETZNER_API_TOKEN")
    email = _get_env("ACME_EMAIL")
    eab_kid = os.environ.get("ZEROSSL_EAB_KID", "")
    eab_hmac_key = os.environ.get("ZEROSSL_EAB_HMAC_KEY", "")
    if cert_type.startswith("zerossl") and not (eab_kid and eab_hmac_key):
        raise click.UsageError(
            "ZEROSSL_EAB_KID and ZEROSSL_EAB_HMAC_KEY are required "
            "for zerossl cert types"
        )
    return token, email, eab_kid, eab_hmac_key


@click.group()
def cli() -> None:
    """Issue real CA TLS certificates for enclave deployments."""


@cli.command("ingress-api")
@click.option(
    "--san", "sans", multiple=True, required=True, help="Subject Alternative Name"
)
@click.option(
    "--type",
    "cert_type",
    required=True,
    type=click.Choice(list(CERT_TYPES)),
    help="Certificate type",
)
@click.option(
    "--root-ca", is_flag=True, default=False, help="Include root CA in output"
)
def cmd_ingress_api(sans: tuple[str, ...], cert_type: str, root_ca: bool) -> None:
    """Issue a multi-SAN cert and print certificates.yaml fields to stdout."""
    token, email, eab_kid, eab_hmac_key = _load_credentials(cert_type)

    cfg = CERT_TYPES[cert_type]
    primary_san = sans[0]

    with tempfile.TemporaryDirectory() as tmp:
        work_dir = Path(tmp)
        hetzner_ini = write_hetzner_ini(work_dir, token)
        config_dir = work_dir / "certbot"
        config_dir.mkdir()
        try:
            issue_cert(
                config_dir,
                cfg["server"],
                cfg["key_type"],
                cfg["key_param"],
                cfg["preferred_chain"],
                list(sans),
                email=email,
                hetzner_ini=hetzner_ini,
                eab_kid=eab_kid,
                eab_hmac_key=eab_hmac_key,
            )
        except CertIssuanceError as exc:
            raise click.ClickException(str(exc)) from exc

        live_dir = config_dir / "live" / primary_san
        fullchain = (live_dir / "fullchain.pem").read_text(encoding="utf-8")
        privkey = (live_dir / "privkey.pem").read_text(encoding="utf-8")

        extracted_root: str | None = None
        if root_ca:
            try:
                extracted_root = extract_root_ca(live_dir / "chain.pem")
            except RootCaExtractionError as exc:
                raise click.ClickException(str(exc)) from exc

        click.echo(
            render_ingress_api_yaml(fullchain, privkey, extracted_root), nl=False
        )


@cli.command("ironic")
@click.option(
    "--san", "sans", multiple=True, required=True, help="Subject Alternative Name"
)
@click.option(
    "--type",
    "cert_type",
    required=True,
    type=click.Choice(list(CERT_TYPES)),
    help="Certificate type",
)
def cmd_ironic(sans: tuple[str, ...], cert_type: str) -> None:
    """Issue a cert and print ironicHTTPS* YAML fields to stdout."""
    token, email, eab_kid, eab_hmac_key = _load_credentials(cert_type)

    cfg = CERT_TYPES[cert_type]
    primary_san = sans[0]

    with tempfile.TemporaryDirectory() as tmp:
        work_dir = Path(tmp)
        hetzner_ini = write_hetzner_ini(work_dir, token)
        config_dir = work_dir / "certbot"
        config_dir.mkdir()
        try:
            issue_cert(
                config_dir,
                cfg["server"],
                cfg["key_type"],
                cfg["key_param"],
                cfg["preferred_chain"],
                list(sans),
                email=email,
                hetzner_ini=hetzner_ini,
                eab_kid=eab_kid,
                eab_hmac_key=eab_hmac_key,
            )
        except CertIssuanceError as exc:
            raise click.ClickException(str(exc)) from exc

        live_dir = config_dir / "live" / primary_san
        fullchain = (live_dir / "fullchain.pem").read_text(encoding="utf-8")
        privkey = (live_dir / "privkey.pem").read_text(encoding="utf-8")

        click.echo(render_ironic_yaml(fullchain, privkey), nl=False)
