import logging
import sys

import click

from reconcile.cli import cli as reconcile_cli
from tools.cli import cli as tools_cli

LOG_LEVELS = ["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"]


@click.group()
@click.option(
    "--log-level",
    default="INFO",
    type=click.Choice(LOG_LEVELS, case_sensitive=False),
    help="Set the logging level.",
)
def cli(log_level: str) -> None:
    """Enclave CLI."""
    logging.basicConfig(
        level=getattr(logging, log_level.upper()),
        format="%(asctime)s %(levelname)-8s %(message)s",
        datefmt="%Y-%m-%dT%H:%M:%S",
        stream=sys.stdout,
    )


# Add existing command groups as subcommands
cli.add_command(reconcile_cli, name="reconcile")
cli.add_command(tools_cli, name="tools")


if __name__ == "__main__":
    cli()
