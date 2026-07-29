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
from cryptography import x509
from cryptography.hazmat.primitives.serialization import Encoding
from cryptography.x509 import UniformResourceIdentifier
from cryptography.x509.oid import AuthorityInformationAccessOID

LE_SERVER = "https://acme-v02.api.letsencrypt.org/directory"
ZS_SERVER = "https://acme.zerossl.com/v2/DV90"

CERT_TYPES: dict[str, dict[str, str]] = {
    "le-rsa": {
        "server": LE_SERVER,
        "key_type": "rsa",
        "key_param": "2048",
        "preferred_chain": "",
    },
    "le-ecdsa-long": {
        "server": LE_SERVER,
        "key_type": "ecdsa",
        "key_param": "secp384r1",
        "preferred_chain": "",
    },
    "le-ecdsa-short": {
        "server": LE_SERVER,
        "key_type": "ecdsa",
        "key_param": "secp384r1",
        "preferred_chain": "ISRG Root X2",
    },
    "zerossl-rsa": {
        "server": ZS_SERVER,
        "key_type": "rsa",
        "key_param": "2048",
        "preferred_chain": "",
    },
    "zerossl-ecdsa": {
        "server": ZS_SERVER,
        "key_type": "ecdsa",
        "key_param": "secp384r1",
        "preferred_chain": "",
    },
}


def write_hetzner_ini(work_dir: Path, token: str) -> Path:
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
        args += ["--eab-kid", eab_kid, "--eab-hmac-key", eab_hmac_key]
    for domain in domains:
        args += ["-d", domain]

    result = subprocess.run(args, capture_output=True, text=True, check=False)
    if result.returncode != 0:
        output = result.stdout + result.stderr
        match = re.search(r"retry after \S+", output, re.IGNORECASE)
        if match:
            raise RuntimeError(f"CA rate limit reached — {match.group()}")
        raise RuntimeError(f"Certificate issuance failed:\n{output}")


def _aia_issuer_url(cert: x509.Certificate) -> str:
    try:
        aia = cert.extensions.get_extension_for_class(x509.AuthorityInformationAccess)
    except x509.ExtensionNotFound as exc:
        raise ValueError(f"Cert has no AIA extension: {cert.subject}") from exc
    for desc in aia.value:
        if (
            desc.access_method == AuthorityInformationAccessOID.CA_ISSUERS
            and isinstance(desc.access_location, UniformResourceIdentifier)
        ):
            return desc.access_location.value
    raise ValueError(f"No CA Issuers URI in AIA of cert: {cert.subject}")


def _fetch_cert(url: str) -> x509.Certificate:
    data = requests.get(url, timeout=15).content
    try:
        return x509.load_der_x509_certificate(data)
    except ValueError:
        return x509.load_pem_x509_certificate(data)


def extract_root_ca(chain_pem_path: Path) -> str:
    """Return the root CA PEM from a certificate chain file.

    First checks whether the chain already contains a self-signed cert (fast path).
    If not, walks the AIA CA Issuers chain from the topmost intermediate, downloading
    each issuer until a self-signed root is found (requires network access).
    """
    pem_text = chain_pem_path.read_text(encoding="utf-8")
    pem_blocks = re.findall(
        r"-----BEGIN CERTIFICATE-----.*?-----END CERTIFICATE-----",
        pem_text,
        re.DOTALL,
    )
    if not pem_blocks:
        raise ValueError(f"No certificates found in {chain_pem_path}")

    for pem in reversed(pem_blocks):
        cert = x509.load_pem_x509_certificate(pem.encode())
        if cert.subject == cert.issuer:
            return pem

    current = x509.load_pem_x509_certificate(pem_blocks[-1].encode())
    for _ in range(5):
        try:
            url = _aia_issuer_url(current)
        except ValueError as exc:
            raise ValueError(
                f"Chain in {chain_pem_path} contains no root CA and {exc}"
            ) from exc
        fetched = _fetch_cert(url)
        if fetched.subject == fetched.issuer:
            return fetched.public_bytes(Encoding.PEM).decode()
        current = fetched

    raise ValueError(f"Could not find root CA after 5 AIA hops from {chain_pem_path}")


def _indent_pem(pem: str) -> str:
    return "\n".join("  " + line for line in pem.strip().splitlines())


def render_ingress_api_yaml(
    fullchain: str, key: str, root_ca: str | None = None
) -> str:
    fields = [
        ("sslAPICertificateKey", key),
        ("sslAPICertificateFullChain", fullchain),
        ("sslIngressCertificateKey", key),
        ("sslIngressCertificateFullChain", fullchain),
    ]
    lines: list[str] = []
    for name, val in fields:
        lines.extend((f"{name}: |", _indent_pem(val)))
    if root_ca is not None:
        lines.extend(("sslCACertificate: |", _indent_pem(root_ca)))
    return "\n".join(lines) + "\n"


def render_ironic_yaml(cert: str, key: str) -> str:
    fields = [
        ("ironicHTTPSCertificate", cert),
        ("ironicHTTPSKey", key),
    ]
    lines: list[str] = []
    for name, val in fields:
        lines.extend((f"{name}: |", _indent_pem(val)))
    return "\n".join(lines) + "\n"


def _get_env(name: str) -> str:
    val = os.environ.get(name, "")
    if not val:
        raise click.UsageError(f"Missing required environment variable: {name}")
    return val


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
    token = _get_env("HETZNER_API_TOKEN")
    email = _get_env("ACME_EMAIL")
    eab_kid = os.environ.get("ZEROSSL_EAB_KID", "")
    eab_hmac_key = os.environ.get("ZEROSSL_EAB_HMAC_KEY", "")
    if cert_type.startswith("zerossl") and not (eab_kid and eab_hmac_key):
        raise click.UsageError(
            "ZEROSSL_EAB_KID and ZEROSSL_EAB_HMAC_KEY are required for zerossl cert types"
        )

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
        except RuntimeError as exc:
            raise click.ClickException(str(exc)) from exc

        live_dir = config_dir / "live" / primary_san
        fullchain = (live_dir / "fullchain.pem").read_text(encoding="utf-8")
        privkey = (live_dir / "privkey.pem").read_text(encoding="utf-8")

        extracted_root: str | None = None
        if root_ca:
            try:
                extracted_root = extract_root_ca(live_dir / "chain.pem")
            except ValueError as exc:
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
    token = _get_env("HETZNER_API_TOKEN")
    email = _get_env("ACME_EMAIL")
    eab_kid = os.environ.get("ZEROSSL_EAB_KID", "")
    eab_hmac_key = os.environ.get("ZEROSSL_EAB_HMAC_KEY", "")
    if cert_type.startswith("zerossl") and not (eab_kid and eab_hmac_key):
        raise click.UsageError(
            "ZEROSSL_EAB_KID and ZEROSSL_EAB_HMAC_KEY are required for zerossl cert types"
        )

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
        except RuntimeError as exc:
            raise click.ClickException(str(exc)) from exc

        live_dir = config_dir / "live" / primary_san
        fullchain = (live_dir / "fullchain.pem").read_text(encoding="utf-8")
        privkey = (live_dir / "privkey.pem").read_text(encoding="utf-8")

        click.echo(render_ironic_yaml(fullchain, privkey), nl=False)
