import click

from enclave.tools.node_image_digests import main as collect_node_image_digests_main
from enclave.tools.quay_registry_ca import main as quay_registry_ca_main
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
def resolve_quay_registry_ca(hostname: str, oc: str) -> None:
    """Print the CA PEM that trusts the Quay registry route TLS certificate."""
    try:
        quay_registry_ca_main(hostname, oc=oc)
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


if __name__ == "__main__":
    cli()
