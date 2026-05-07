import click
import logging
import sys

from operator_versions import reconcile as operator_versions_reconcile
from cluster_upgrade import ClusterUpgradeError, reconcile as cluster_upgrade_reconcile


@click.group()
@click.option(
    "--log-level",
    default="INFO",
    type=click.Choice(["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"]),
    help="Log level for all commands (default: INFO)",
)
def cli(log_level: str):
    """Reconcile CLI for cluster operations."""
    logging.basicConfig(
        level=getattr(logging, log_level),
        format="%(asctime)s %(levelname)-8s %(message)s",
        datefmt="%Y-%m-%dT%H:%M:%S",
    )


@cli.command()
@click.option("--operators", default="[]")
@click.option("--dry-run/--no-dry-run", default=False)
def operator_versions(operators, dry_run):
    # rebuild sys.argv for the standalone script
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
def mgmt_cluster_version(version, dry_run, timeout_minutes, sleep_interval) -> None:
    try:
        cluster_upgrade_reconcile(version, dry_run, timeout_minutes, sleep_interval)
    except (ClusterUpgradeError, RuntimeError, TimeoutError) as e:
        raise click.ClickException(str(e)) from e


if __name__ == "__main__":
    cli()
