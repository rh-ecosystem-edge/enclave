import logging
from pathlib import Path
from typing import Any

import click
import yaml

from enclave.tools.cert_utils import pem_blocks
from enclave.tools.check_certificate_chains import (
    CertificateValidationError,
    main as check_certificate_chains_main,
)
from enclave.tools.node_image_digests import main as collect_node_image_digests_main
from enclave.tools.quay_registry_ca import main as quay_registry_ca_main
from enclave.tools.system_ca import find_system_ca_for_chain
from enclave.tools.wait_and_detach_vmedia import main as wait_and_detach_vmedia_main
from enclave.utils import KubeconfigGroup

logger = logging.getLogger(__name__)


def _load_certs_config(config_path: str) -> dict[str, Any]:
    try:
        raw = yaml.safe_load(Path(config_path).read_text(encoding="utf-8")) or {}
    except OSError as exc:
        raise click.ClickException(f"cannot read {config_path}: {exc}") from exc
    except yaml.YAMLError as exc:
        raise click.ClickException(f"cannot parse {config_path}: {exc}") from exc
    if not isinstance(raw, dict):
        raise click.ClickException(
            f"{config_path}: expected a YAML mapping, got {type(raw).__name__}"
        )
    return raw


@click.group(cls=KubeconfigGroup)
def cli() -> None:
    """Enclave tools CLI."""


@cli.command("resolve-quay-registry-ca", no_args_is_help=True)
@click.option("--hostname", required=True, help="Quay registry route hostname.")
@click.option(
    "--oc",
    default="oc",
    show_default=True,
    help="Path to the oc binary.",
)
@click.option(
    "--ca-pem",
    default=None,
    help="PEM-encoded CA certificate to complete an incomplete TLS chain.",
)
@click.option(
    "--certificates-config",
    default=None,
    type=click.Path(exists=True, dir_okay=False),
    help="Path to certificates.yaml; sslCACertificate is used to complete the TLS chain.",
)
def resolve_quay_registry_ca(
    hostname: str, oc: str, ca_pem: str | None, certificates_config: str | None
) -> None:
    """Print the CA PEM that trusts the Quay registry route TLS certificate."""
    if ca_pem is not None and certificates_config is not None:
        raise click.UsageError(
            "--ca-pem and --certificates-config are mutually exclusive"
        )
    resolved_ca_pem = ca_pem or ""
    if certificates_config is not None:
        raw = _load_certs_config(certificates_config)
        ca_raw = raw.get("sslCACertificate")
        resolved_ca_pem = (ca_raw if isinstance(ca_raw, str) else "").strip()
    if ca_pem is not None and not ca_pem.strip():
        raise click.BadParameter("must not be empty", param_hint="--ca-pem")
    try:
        quay_registry_ca_main(hostname, oc=oc, ca_pem=resolved_ca_pem)
    except RuntimeError as exc:
        raise click.ClickException(str(exc)) from exc


@cli.command("collect-node-image-digests", no_args_is_help=True)
@click.option("--node", required=True, help="Node name.")
@click.option(
    "--oc",
    default="oc",
    show_default=True,
    help="Path to the oc binary.",
)
@click.option(
    "--exclude-contains",
    default=None,
    help="JSON array of substrings; matching digest refs are skipped.",
)
@click.option(
    "--raw-output-file",
    default=None,
    help="File path for raw oc debug/crictl output when no digest refs are collected.",
)
def collect_node_image_digests(
    node: str,
    oc: str,
    exclude_contains: str | None,
    raw_output_file: str | None,
) -> None:
    """Collect digest pull specs from images on a node via oc debug + crictl."""
    try:
        collect_node_image_digests_main(
            node,
            oc=oc,
            exclude_contains_raw=exclude_contains,
            raw_output_file=raw_output_file,
        )
    except (TimeoutError, ValueError, TypeError) as exc:
        raise click.ClickException(str(exc)) from exc


@cli.command("check-certificate-chains")
@click.option(
    "--config",
    required=True,
    type=click.Path(exists=True, dir_okay=False),
    help="Path to certificates.yaml.",
)
def check_certificate_chains(config: str) -> None:
    """Check certificate chain completeness and CA consistency."""
    try:
        check_certificate_chains_main(config)
    except CertificateValidationError as exc:
        raise click.ClickException(str(exc)) from exc


@cli.command("get-root-ca")
@click.option(
    "--config",
    default=None,
    type=click.Path(exists=True, dir_okay=False),
    help="Path to certificates.yaml; sslAPICertificateFullChain or sslIngressCertificateFullChain is used.",
)
@click.option(
    "--chain-pem",
    default=None,
    help="Inline PEM of the certificate fullchain to look up.",
)
def get_root_ca(config: str | None, chain_pem: str | None) -> None:
    """Print the root CA PEM from the RHEL 10 system trust store that signs the given chain."""
    if config is not None and chain_pem is not None:
        raise click.UsageError("--config and --chain-pem are mutually exclusive")
    if config is None and chain_pem is None:
        raise click.UsageError("one of --config or --chain-pem is required")
    resolved_chain = chain_pem or ""
    if config is not None:
        raw = _load_certs_config(config)
        api_chain = raw.get("sslAPICertificateFullChain")
        ingress_chain = raw.get("sslIngressCertificateFullChain")
        resolved_chain = (
            (api_chain if isinstance(api_chain, str) else None)
            or (ingress_chain if isinstance(ingress_chain, str) else None)
            or ""
        )
        if not resolved_chain:
            raise click.ClickException(
                f"{config}: sslAPICertificateFullChain and sslIngressCertificateFullChain are both unset or empty"
            )
    try:
        ca_pem = find_system_ca_for_chain(resolved_chain)
    except (ValueError, RuntimeError) as exc:
        raise click.ClickException(str(exc)) from exc
    if ca_pem is None:
        raise click.ClickException("no matching CA found in system trust store")
    click.echo(ca_pem, nl=False)


@cli.command("check-root-ca")
@click.option(
    "--config",
    required=True,
    type=click.Path(exists=True, dir_okay=False),
    help="Path to certificates.yaml.",
)
def check_root_ca(config: str) -> None:
    """Check that a root CA is available for the configured certificate chains."""
    raw = _load_certs_config(config)
    api_chain = raw.get("sslAPICertificateFullChain")
    ingress_chain = raw.get("sslIngressCertificateFullChain")
    chain_pem = (
        (api_chain if isinstance(api_chain, str) else None)
        or (ingress_chain if isinstance(ingress_chain, str) else None)
        or ""
    )
    if not chain_pem:
        logger.info("No TLS certificate chains configured; skipping root CA check.")
        return
    ca_raw = raw.get("sslCACertificate")
    ca_pem = (ca_raw if isinstance(ca_raw, str) else "").strip()
    if ca_pem:
        if not pem_blocks(ca_pem):
            raise click.ClickException(
                "sslCACertificate contains no valid PEM certificate blocks"
            )
        return
    try:
        ca_result = find_system_ca_for_chain(chain_pem)
    except (ValueError, RuntimeError) as exc:
        raise click.ClickException(str(exc)) from exc
    if ca_result is None:
        raise click.ClickException(
            "sslCACertificate is not set and no matching CA was found in the "
            "system trust store (/etc/pki/tls/certs/ca-bundle.crt). "
            "Set sslCACertificate in certificates.yaml."
        )


@cli.command("wait-and-detach-vmedia", no_args_is_help=True)
@click.option(
    "--host-name",
    required=True,
    help="Ironic node name (matches Assisted Service requested_hostname).",
)
@click.option(
    "--assisted-service-url",
    required=True,
    help="Assisted Service base URL (e.g. http://<rendezvousIP>:8090/api/assisted-install/v2).",
)
@click.option(
    "--assisted-cluster-id", required=True, help="Assisted Service cluster UUID."
)
@click.option(
    "--assisted-auth-token",
    envvar="ASSISTED_AUTH_TOKEN",
    required=True,
    help="Assisted Service auth token (also accepted via ASSISTED_AUTH_TOKEN env var).",
)
@click.option(
    "--ironic-base-url",
    default="http://localhost:6385",
    show_default=True,
    help="Ironic API base URL.",
)
@click.option(
    "--ironic-api-version",
    default="1.89",
    show_default=True,
    help="X-OpenStack-Ironic-API-Version header value.",
)
@click.option("--ironic-user", required=True, help="Ironic HTTP basic auth username.")
@click.option(
    "--ironic-password",
    envvar="IRONIC_PASSWORD",
    required=True,
    help="Ironic HTTP basic auth password (also accepted via IRONIC_PASSWORD env var).",
)
@click.option(
    "--poll-interval",
    default=1,
    show_default=True,
    type=click.IntRange(min=1),
    help="Seconds between Assisted Service polls.",
)
def wait_and_detach_vmedia(
    host_name: str,
    assisted_service_url: str,
    assisted_cluster_id: str,
    assisted_auth_token: str,
    ironic_base_url: str,
    ironic_api_version: str,
    ironic_user: str,
    ironic_password: str,
    poll_interval: int,
) -> None:
    """Poll Assisted Service until host disk write completes, then detach vmedia via Ironic."""
    try:
        wait_and_detach_vmedia_main(
            host_name,
            assisted_service_url,
            assisted_cluster_id,
            assisted_auth_token,
            ironic_base_url,
            ironic_api_version,
            ironic_user,
            ironic_password,
            poll_interval=poll_interval,
        )
    except RuntimeError as exc:
        raise click.ClickException(str(exc)) from exc


check_certificate_chains.no_kubeconfig = True  # type: ignore[attr-defined]
get_root_ca.no_kubeconfig = True  # type: ignore[attr-defined]
check_root_ca.no_kubeconfig = True  # type: ignore[attr-defined]
wait_and_detach_vmedia.no_kubeconfig = True  # type: ignore[attr-defined]

if __name__ == "__main__":
    cli()
