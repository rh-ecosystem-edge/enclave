import logging
import sys
from pathlib import Path

import click
import yaml

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
        stream=sys.stdout,
    )


@cli.command()
@click.option("--name", help="Operator package name")
@click.option("--version", help="Operator version")
@click.option("--namespace", help="Operator namespace")
@click.option(
    "--csv-name",
    "csv_names",
    multiple=True,
    help="CSV name(s); defaults to operator name if omitted",
)
@click.option(
    "--use-defaults",
    is_flag=True,
    default=False,
    help="Load all operators from defaults/operators.yaml (mutually exclusive with --name, --version, --namespace, --csv-name)",
)
@click.option("--dry-run/--no-dry-run", default=False)
def operator_versions(
    name: str,
    version: str,
    namespace: str,
    csv_names: tuple[str, ...],
    use_defaults: bool,
    dry_run: bool,
) -> None:
    if use_defaults and any([name, version, namespace, csv_names]):
        raise click.UsageError(
            "--use-defaults is mutually exclusive with --name, --version, --namespace, --csv-name"
        )

    if not use_defaults:
        missing = [
            f"--{f}"
            for f, v in [("name", name), ("version", version), ("namespace", namespace)]
            if not v
        ]
        if missing:
            raise click.UsageError(f"Missing option(s): {', '.join(missing)}")
        operator_versions_reconcile(
            version, namespace, list(csv_names) or [name], dry_run
        )
        return

    defaults_path = (
        Path(__file__).resolve().parent.parent / "defaults" / "operators.yaml"
    )
    try:
        with defaults_path.open(encoding="utf-8") as fh:
            operators = yaml.safe_load(fh)["operators"]
    except FileNotFoundError as exc:
        raise click.ClickException(
            f"{defaults_path} not found; run from the repo root"
        ) from exc
    except (yaml.YAMLError, KeyError) as exc:
        raise click.ClickException(f"Failed to parse {defaults_path}: {exc}") from exc

    for op in operators:
        op_name: str = op["name"]
        op_csv_names: list[str] = op.get("csvNames") or [op_name]
        operator_versions_reconcile(
            op["version"], op["namespace"], op_csv_names, dry_run
        )


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
