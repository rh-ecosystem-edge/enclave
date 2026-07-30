from pathlib import Path
from typing import cast

import click
import yaml

from enclave.reconcile.cluster_upgrade import (
    ClusterUpgradeError,
    reconcile as cluster_upgrade_reconcile,
)
from enclave.reconcile.ironic_version import (
    IronicVersionError,
    reconcile as ironic_version_reconcile,
)
from enclave.reconcile.operator_versions import reconcile as operator_versions_reconcile
from enclave.utils import (
    LOG_LEVELS,
    KubeconfigGroup,
    configure_logging,
)


def defaults_path(filename: str) -> Path:
    # Installed: site-packages/enclave/reconcile/cli.py → site-packages/enclave/ → enclave/defaults/
    # Editable:  src/enclave/reconcile/cli.py → src/enclave/ (no defaults/) → repo_root/defaults/
    enclave_pkg = Path(__file__).resolve().parent.parent
    path = enclave_pkg / "defaults" / filename
    if not path.exists():
        path = enclave_pkg.parent.parent / "defaults" / filename
    return path


def resolve_platform_version(version: str | None, use_defaults: bool) -> str:
    """Resolve OpenShift version from either direct input or defaults.

    Args:
        version: Direct version string (e.g., "4.20.21")
        use_defaults: If True, load default from platforms.yaml

    Returns:
        Resolved version string

    Raises:
        click.UsageError: If arguments are mutually exclusive or missing
        click.ClickException: If defaults file issues or missing version key
    """
    if use_defaults and version:
        raise click.UsageError("--use-defaults is mutually exclusive with --version")

    if not use_defaults and not version:
        raise click.UsageError("Either --version or --use-defaults must be provided")

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
        if "version" not in default_entry:
            raise click.ClickException(
                "Default OpenShift entry in defaults/platforms.yaml is missing 'version'"
            )
        return str(default_entry["version"])
    return cast("str", version)


@click.group(cls=KubeconfigGroup)
@click.option(
    "--log-level",
    default="INFO",
    type=click.Choice(LOG_LEVELS, case_sensitive=False),
    help="Set the logging level.",
)
def cli(log_level: str) -> None:
    """Reconcile CLI."""
    # Configure logging only if not already configured by the parent enclave CLI
    configure_logging(log_level)


@cli.command(no_args_is_help=True)
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


@cli.command(no_args_is_help=True)
@click.option(
    "--version", "version", default=None, help="OpenShift version to upgrade to"
)
@click.option(
    "--use-defaults",
    is_flag=True,
    default=False,
    help="Load the default version from defaults/platforms.yaml (mutually exclusive with --version)",
)
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
    version: str | None,
    use_defaults: bool,
    dry_run: bool,
    timeout_minutes: int,
    sleep_interval: int,
) -> None:
    resolved_version = resolve_platform_version(version, use_defaults)

    try:
        cluster_upgrade_reconcile(
            resolved_version, dry_run, timeout_minutes, sleep_interval
        )
    except (ClusterUpgradeError, RuntimeError, TimeoutError) as e:
        raise click.ClickException(str(e)) from e


@cli.command(no_args_is_help=True)
@click.option(
    "--version", "version", default=None, help="OpenShift version to get ironic from"
)
@click.option(
    "--use-defaults",
    is_flag=True,
    default=False,
    help="Load the default OpenShift version from defaults/platforms.yaml (mutually exclusive with --version)",
)
@click.option("--dry-run/--no-dry-run", default=False)
@click.option(
    "--timeout-minutes",
    default=10,
    type=click.IntRange(min=1),
    help="Timeout for waiting operations in minutes (default: 10)",
)
@click.option(
    "--sleep-interval",
    default=5,
    type=click.IntRange(min=1),
    help="Sleep interval between polling attempts in seconds (default: 5)",
)
def ironic_version(
    version: str | None,
    use_defaults: bool,
    dry_run: bool,
    timeout_minutes: int,
    sleep_interval: int,
) -> None:
    """Update ironic to the version bundled with the specified OpenShift release."""
    resolved_version = resolve_platform_version(version, use_defaults)

    try:
        ironic_version_reconcile(
            resolved_version, dry_run, timeout_minutes, sleep_interval
        )
    except (IronicVersionError, RuntimeError, TimeoutError) as e:
        raise click.ClickException(str(e)) from e


if __name__ == "__main__":
    cli()
