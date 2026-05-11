import logging
import sys

import click

from reconcile.cluster_upgrade import (
    ClusterUpgradeError,
    reconcile as cluster_upgrade_reconcile,
)
from reconcile.operator_versions import reconcile as operator_versions_reconcile

LOG_LEVELS = ["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"]


@click.group()
@click.option(
    "--log-level",
    default="INFO",
    type=click.Choice(LOG_LEVELS, case_sensitive=False),
    help="Set the logging level.",
)
def cli(log_level: str) -> None:
    """Reconcile CLI."""
    logging.basicConfig(
        level=getattr(logging, log_level.upper()),
        format="%(asctime)s %(levelname)-8s %(message)s",
        datefmt="%Y-%m-%dT%H:%M:%S",
    )


@cli.command()
@click.option("--operators", default="[]")
@click.option("--dry-run/--no-dry-run", default=False)
def operator_versions(operators: str, dry_run: bool) -> None:
    sys.argv = ["operator_versions.py", operators, str(dry_run)]
    operator_versions_reconcile()


@cli.command()
@click.argument("version")
@click.option("--dry-run/--no-dry-run", default=False)
@click.option(
    "--timeout-minutes",
    default=180,
    type=click.IntRange(min=1),
    help="Timeout for waiting operations in minutes (default: 180 = 3 hours)",
)
@click.option(
    "--sleep-interval",
    default=60,
    type=click.IntRange(min=1),
    help="Sleep interval between polling attempts in seconds (default: 60)",
)
def mgmt_cluster_version(
    version: str, dry_run: bool, timeout_minutes: int, sleep_interval: int
) -> None:
    try:
        cluster_upgrade_reconcile(version, dry_run, timeout_minutes, sleep_interval)
    except (ClusterUpgradeError, RuntimeError, TimeoutError) as e:
        raise click.ClickException(str(e)) from e


if __name__ == "__main__":
    cli()
