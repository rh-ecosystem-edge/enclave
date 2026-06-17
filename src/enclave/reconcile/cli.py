from pathlib import Path
from typing import cast

import click
import yaml

from enclave.reconcile.cluster_upgrade import (
    ClusterUpgradeError,
    reconcile as cluster_upgrade_reconcile,
)
from enclave.reconcile.operator_versions import reconcile as operator_versions_reconcile
from enclave.utils import LOG_LEVELS, HelpGroup, configure_logging


def defaults_path(filename: str) -> Path:
    # Non-editable install: cli.py → site-packages/enclave/reconcile/ → site-packages/enclave/ → site-packages/ → defaults/
    # Editable src layout:  cli.py → src/enclave/reconcile/ → src/enclave/ → src/ → (not found) → repo root → defaults/
    pkg_root = Path(__file__).resolve().parent.parent.parent
    path = pkg_root / "defaults" / filename
    if not path.exists():
        path = pkg_root.parent / "defaults" / filename
    return path


@click.group(cls=HelpGroup)
@click.option(
    "--log-level",
    "-l",
    default="INFO",
    type=click.Choice(LOG_LEVELS, case_sensitive=False),
    help="Set the logging level.",
)
def cli(log_level: str) -> None:
    """Reconcile management cluster state against desired configuration.

    Commands compare the current cluster state with expected values
    and apply changes to bring them in sync.
    """
    # Configure logging only if not already configured by the parent enclave CLI
    configure_logging(log_level)


@cli.command()
@click.option("--name", "-n", help="Operator package name")
@click.option("--version", "-v", help="Operator version")
@click.option("--namespace", "-N", help="Operator namespace")
@click.option(
    "--csv-name",
    "-c",
    "csv_names",
    multiple=True,
    help="CSV name(s); defaults to operator name if omitted",
)
@click.option(
    "--use-defaults",
    "-u",
    is_flag=True,
    default=False,
    help="Load all operators from defaults/operators.yaml (mutually exclusive with --name, --version, --namespace, --csv-name)",
)
@click.option(
    "--dry-run",
    "-d",
    is_flag=True,
    default=False,
    help="Print what would be done without applying any changes.",
)
def operator_versions(
    name: str,
    version: str,
    namespace: str,
    csv_names: tuple[str, ...],
    use_defaults: bool,
    dry_run: bool,
) -> None:
    """Reconcile operator CSV versions on the management cluster."""
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

    defaults_file = defaults_path("operators.yaml")

    try:
        with defaults_file.open(encoding="utf-8") as fh:
            operators = yaml.safe_load(fh)["operators"]
    except FileNotFoundError as exc:
        raise click.ClickException(
            f"{defaults_file} not found; run from the repo root"
        ) from exc
    except (yaml.YAMLError, KeyError) as exc:
        raise click.ClickException(f"Failed to parse {defaults_file}: {exc}") from exc

    for op in operators:
        op_name: str = op["name"]
        op_csv_names: list[str] = op.get("csvNames") or [op_name]
        operator_versions_reconcile(
            op["version"], op["namespace"], op_csv_names, dry_run
        )


@cli.command()
@click.option(
    "--version", "-v", "version", default=None, help="OpenShift version to upgrade to"
)
@click.option(
    "--use-defaults",
    "-u",
    is_flag=True,
    default=False,
    help="Load the default version from defaults/platforms.yaml (mutually exclusive with --version)",
)
@click.option(
    "--dry-run",
    "-d",
    is_flag=True,
    default=False,
    help="Print what would be done without applying any changes.",
)
@click.option(
    "--timeout-minutes",
    "-t",
    default=180,
    type=click.IntRange(min=1),
    help="Timeout for waiting operations in minutes (default: 180 = 3 hours)",
)
@click.option(
    "--sleep-interval",
    "-s",
    default=60,
    type=click.IntRange(min=1),
    help="Sleep interval between polling attempts in seconds (default: 60)",
)
def mgmt_cluster_version(
    version: str | None,
    use_defaults: bool,
    dry_run: bool,
    timeout_minutes: int,
    sleep_interval: int,
) -> None:
    """Reconcile the management cluster's OpenShift version.

    Triggers an upgrade if the cluster is not already at the target
    version, then waits for the rollout to complete.
    """
    if use_defaults and version:
        raise click.UsageError("--use-defaults is mutually exclusive with --version")

    if not use_defaults and not version:
        raise click.UsageError("Either --version or --use-defaults must be provided")

    resolved_version: str
    if use_defaults:
        defaults_file = defaults_path("platforms.yaml")
        try:
            with defaults_file.open(encoding="utf-8") as fh:
                platforms = yaml.safe_load(fh)
        except FileNotFoundError as exc:
            raise click.ClickException(
                f"{defaults_file} not found; run from the repo root"
            ) from exc
        except yaml.YAMLError as exc:
            raise click.ClickException(
                f"Failed to parse {defaults_file}: {exc}"
            ) from exc

        openshift_versions: list[dict[str, object]] = platforms.get(
            "openshift_versions", []
        )
        default_entry = next(
            (v for v in openshift_versions if v.get("default") is True), None
        )
        if default_entry is None:
            raise click.ClickException(
                "No default version found in defaults/platforms.yaml; "
                "set 'default: true' on one entry"
            )
        resolved_version = str(default_entry["version"])
    else:
        resolved_version = cast("str", version)

    try:
        cluster_upgrade_reconcile(
            resolved_version, dry_run, timeout_minutes, sleep_interval
        )
    except (ClusterUpgradeError, RuntimeError, TimeoutError) as e:
        raise click.ClickException(str(e)) from e


if __name__ == "__main__":
    cli()
