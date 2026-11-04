import click

from enclave.boot.cli import cli as boot_cli
from enclave.migrations.cli import cli as migrations_cli
from enclave.plugins.cli import cli as plugins_cli
from enclave.reconcile.cli import cli as reconcile_cli
from enclave.tools.cli import cli as tools_cli
from enclave.utils import LOG_FORMATS, LOG_LEVELS, configure_logging


@click.group()
@click.option(
    "--log-level",
    default="INFO",
    type=click.Choice(LOG_LEVELS, case_sensitive=False),
    help="Set the logging level.",
)
@click.option(
    "--log-format",
    default="full",
    type=click.Choice(LOG_FORMATS, case_sensitive=False),
    help=(
        "Log output format. "
        "'full' prints timestamp, level, and message "
        "(e.g. '2026-07-21T12:00:00 INFO     done'). "
        "'plain' prints the message only (e.g. 'done')."
    ),
)
def cli(log_level: str, log_format: str) -> None:
    """Enclave CLI."""
    configure_logging(log_level, log_format)


# Add existing command groups as subcommands
cli.add_command(reconcile_cli, name="reconcile")
cli.add_command(tools_cli, name="tools")
cli.add_command(plugins_cli, name="plugins")
cli.add_command(migrations_cli, name="migrations")
cli.add_command(boot_cli, name="boot")


if __name__ == "__main__":
    cli()
