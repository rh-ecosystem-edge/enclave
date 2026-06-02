import click

from tools.quay_registry_ca import main as quay_registry_ca_main


@click.group()
def cli() -> None:
    """Enclave tools CLI."""


@cli.command("resolve-quay-registry-ca")
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


if __name__ == "__main__":
    cli()
