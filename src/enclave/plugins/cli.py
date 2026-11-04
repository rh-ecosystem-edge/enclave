import logging
from pathlib import Path

import click

from enclave.plugins.boot import validate_boot_plugin

logger = logging.getLogger(__name__)


@click.group()
def cli() -> None:
    """Enclave plugins CLI."""


@cli.command("validate", no_args_is_help=True)
@click.option(
    "--type",
    "plugin_type",
    required=True,
    type=click.Choice(["boot"]),
    help="Plugin type whose contract the plugin directories must honour.",
)
@click.argument(
    "plugin_dirs",
    nargs=-1,
    required=True,
    type=click.Path(path_type=Path),
)
def validate(plugin_type: str, plugin_dirs: tuple[Path, ...]) -> None:
    """Validate plugin directories against the plugin contract of their type."""
    invalid = []
    for plugin_dir in plugin_dirs:
        errors = validate_boot_plugin(plugin_dir)
        if errors:
            invalid.append(plugin_dir)
            for error in errors:
                logger.error("%s: %s", plugin_dir, error)
        else:
            logger.info("%s: valid %s plugin", plugin_dir, plugin_type)
    if invalid:
        raise click.ClickException(
            f"{len(invalid)} of {len(plugin_dirs)} {plugin_type} plugin(s) "
            "failed validation"
        )
