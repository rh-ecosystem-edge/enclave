from pathlib import Path

import click

from enclave.migrations.redfish_to_ironic import MigrationError, migrate


@click.group()
def cli() -> None:
    """Enclave configuration migrations CLI."""


@cli.command("redfish-to-ironic", no_args_is_help=True)
@click.option(
    "--global-config",
    required=True,
    type=click.Path(exists=True, dir_okay=False, path_type=Path),
    help="Path to config/global.yaml.",
)
@click.option(
    "--ironic-config",
    required=True,
    type=click.Path(dir_okay=False, path_type=Path),
    help="Path to config/plugins/boot/ironic.yaml (created if missing).",
)
@click.option(
    "--ironic-schema",
    required=True,
    type=click.Path(exists=True, dir_okay=False, path_type=Path),
    help="Path to plugins/boot/ironic/schemas/config.yaml.",
)
def redfish_to_ironic(
    global_config: Path, ironic_config: Path, ironic_schema: Path
) -> None:
    """Move the Redfish settings of agent_hosts to the ironic boot plugin config.

    Prints "changed" when a file was written, "unchanged" otherwise.
    """
    try:
        result = migrate(global_config, ironic_config, ironic_schema)
    except MigrationError as exc:
        raise click.ClickException(str(exc)) from exc
    click.echo("changed" if result.changed else "unchanged")
