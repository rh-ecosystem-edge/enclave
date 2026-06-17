import click

from enclave.reconcile.cli import cli as reconcile_cli
from enclave.tools.cli import cli as tools_cli
from enclave.utils import LOG_LEVELS, HelpGroup, configure_logging


@click.group(cls=HelpGroup)
@click.option(
    "--log-level",
    "-l",
    default="INFO",
    type=click.Choice(LOG_LEVELS, case_sensitive=False),
    help="Set the logging level.",
)
def cli(log_level: str) -> None:
    """Management CLI for Red Hat Sovereign Enclave (RHSE).

    Provides subcommands for reconciling cluster state and running
    operational tools against the management cluster.
    """
    configure_logging(log_level)


# Add existing command groups as subcommands
cli.add_command(reconcile_cli, name="reconcile")
cli.add_command(tools_cli, name="tools")


if __name__ == "__main__":
    cli()
