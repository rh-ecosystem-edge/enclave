from pathlib import Path

import click
import yaml

from enclave.tools.check_certificate_chains import (
    CertificateValidationError,
    main as check_certificate_chains_main,
)
from enclave.tools.node_image_digests import main as collect_node_image_digests_main
from enclave.tools.quay_registry_ca import main as quay_registry_ca_main
from enclave.tools.system_ca import find_system_ca_for_chain
from enclave.utils import KubeconfigGroup


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
        try:
            raw = (
                yaml.safe_load(Path(certificates_config).read_text(encoding="utf-8"))
                or {}
            )
        except OSError as exc:
            raise click.ClickException(
                f"cannot read {certificates_config}: {exc}"
            ) from exc
        except yaml.YAMLError as exc:
            raise click.ClickException(
                f"cannot parse {certificates_config}: {exc}"
            ) from exc
        if not isinstance(raw, dict):
            raise click.ClickException(
                f"{certificates_config}: expected a YAML mapping, "
                f"got {type(raw).__name__}"
            )
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
        try:
            raw = yaml.safe_load(Path(config).read_text(encoding="utf-8")) or {}
        except OSError as exc:
            raise click.ClickException(f"cannot read {config}: {exc}") from exc
        except yaml.YAMLError as exc:
            raise click.ClickException(f"cannot parse {config}: {exc}") from exc
        if not isinstance(raw, dict):
            raise click.ClickException(
                f"{config}: expected a YAML mapping, got {type(raw).__name__}"
            )
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
    ca_pem = find_system_ca_for_chain(resolved_chain)
    if ca_pem is None:
        raise click.ClickException("no matching CA found in system trust store")
    click.echo(ca_pem, nl=False)


check_certificate_chains.no_kubeconfig = True  # type: ignore[attr-defined]
get_root_ca.no_kubeconfig = True  # type: ignore[attr-defined]

if __name__ == "__main__":
    cli()
