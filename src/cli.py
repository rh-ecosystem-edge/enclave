import click

from reconcile.cli import cli as reconcile_cli
from tools.cli import cli as tools_cli
from utils import LOG_LEVELS, configure_logging


@click.group()
@click.option(
    "--log-level",
    default="INFO",
    type=click.Choice(LOG_LEVELS, case_sensitive=False),
    help="Set the logging level.",
)
def cli(log_level: str) -> None:
    """Enclave CLI."""
    configure_logging(log_level)


# Add existing command groups as subcommands
cli.add_command(reconcile_cli, name="reconcile")
cli.add_command(tools_cli, name="tools")


if __name__ == "__main__":
    cli()
