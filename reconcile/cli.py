import logging
import sys
from pathlib import Path
from typing import cast

import click
import yaml

from reconcile.cluster_upgrade import (
    ClusterUpgradeError,
    reconcile as cluster_upgrade_reconcile,
)
from reconcile.operator_versions import reconcile as operator_versions_reconcile
from reconcile.quay_registry_ca import reconcile as quay_registry_ca_reconcile

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


@cli.command("resolve-quay-registry-ca")
@click.option("--hostname", required=True, help="Quay registry route hostname.")
@click.option(
    "--oc",
    default="oc",
    show_default=True,
    help="Path to the oc binary.",
)
def resolve_quay_registry_ca(hostname: str, oc: str) -> None:
    """Print the CA PEM that trusts the Quay registry route TLS certificate."""
    try:
        quay_registry_ca_reconcile(hostname, oc=oc)
    except RuntimeError as exc:
        raise click.ClickException(str(exc)) from exc


def _reconcile_operators_from_list(operators: list[dict], dry_run: bool) -> None:
    """Reconcile operator versions from a list of operator definitions.

    Args:
        operators: List of operator definitions, each with 'name', 'version', 'namespace',
            and optionally 'csvNames'.
        dry_run: If True, only print what would be done without making changes.
    """
    for op in operators:
        csv_names_list: list[str] = op.get("csvNames") or [op["name"]]
        operator_versions_reconcile(
            op["version"], op["namespace"], csv_names_list, dry_run
        )


def _load_plugin_operators(plugin_name: str) -> list[dict]:
    """Load operator definitions from a plugin descriptor.

    Args:
        plugin_name: Name of the plugin (must be a simple name without path separators).

    Returns:
        List of operator definitions from the plugin's plugin.yaml file.

    Raises:
        click.ClickException: If plugin_name contains invalid characters, plugin not found,
            YAML parsing fails, installOperators is false, or no operators defined.
    """
    # Validate plugin_name to prevent path traversal attacks
    if (
        not plugin_name
        or "/" in plugin_name
        or "\\" in plugin_name
        or ".." in plugin_name
    ):
        raise click.ClickException(
            f"Invalid plugin name: {plugin_name!r}. Plugin name must be a simple name without path separators or '..'."
        )

    plugin_path = (
        Path(__file__).resolve().parent.parent / "plugins" / plugin_name / "plugin.yaml"
    )
    try:
        with plugin_path.open(encoding="utf-8") as fh:
            plugin_data = yaml.safe_load(fh)
    except FileNotFoundError as exc:
        raise click.ClickException(
            f"{plugin_path} not found; check plugin name or run from repo root"
        ) from exc
    except yaml.YAMLError:
        raise click.ClickException(
            f"Failed to parse {plugin_path}: invalid YAML syntax"
        ) from None

    if plugin_data.get("installOperators") is False:
        raise click.ClickException(
            f"Plugin {plugin_name} has installOperators set to false"
        )

    operators = plugin_data.get("operators", [])
    if not operators:
        raise click.ClickException(
            f"Plugin {plugin_name} has no operators defined in {plugin_path}"
        )
    return operators


def _load_defaults_operators() -> list[dict]:
    """Load operator definitions from defaults/operators.yaml.

    Returns:
        List of operator definitions from defaults/operators.yaml.

    Raises:
        click.ClickException: If file not found, YAML parsing fails, or 'operators' key missing.
    """
    defaults_path = (
        Path(__file__).resolve().parent.parent / "defaults" / "operators.yaml"
    )
    try:
        with defaults_path.open(encoding="utf-8") as fh:
            return yaml.safe_load(fh)["operators"]
    except FileNotFoundError as exc:
        raise click.ClickException(
            f"{defaults_path} not found; run from the repo root"
        ) from exc
    except yaml.YAMLError:
        raise click.ClickException(
            f"Failed to parse {defaults_path}: invalid YAML syntax"
        ) from None
    except KeyError as exc:
        raise click.ClickException(
            f"Failed to parse {defaults_path}: missing 'operators' key"
        ) from exc


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
    help="Load all operators from defaults/operators.yaml (mutually exclusive with --name, --version, --namespace, --csv-name, --plugin)",
)
@click.option(
    "--plugin",
    help="Load operators from a plugin descriptor in plugins/<name>/plugin.yaml (mutually exclusive with --name, --version, --namespace, --csv-name, --use-defaults)",
)
@click.option("--dry-run/--no-dry-run", default=False)
def operator_versions(
    name: str,
    version: str,
    namespace: str,
    csv_names: tuple[str, ...],
    use_defaults: bool,
    plugin: str,
    dry_run: bool,
) -> None:
    if use_defaults and any([name, version, namespace, csv_names, plugin]):
        raise click.UsageError(
            "--use-defaults is mutually exclusive with --name, --version, --namespace, --csv-name, --plugin"
        )

    if plugin and any([name, version, namespace, csv_names]):
        raise click.UsageError(
            "--plugin is mutually exclusive with --name, --version, --namespace, --csv-name"
        )

    if plugin:
        operators = _load_plugin_operators(plugin)
        _reconcile_operators_from_list(operators, dry_run)
        return

    if use_defaults:
        operators = _load_defaults_operators()
        _reconcile_operators_from_list(operators, dry_run)
        return

    missing = [
        f"--{f}"
        for f, v in [("name", name), ("version", version), ("namespace", namespace)]
        if not v
    ]
    if missing:
        raise click.UsageError(f"Missing option(s): {', '.join(missing)}")
    operator_versions_reconcile(version, namespace, list(csv_names) or [name], dry_run)


@cli.command()
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
    if use_defaults and version:
        raise click.UsageError("--use-defaults is mutually exclusive with --version")

    if not use_defaults and not version:
        raise click.UsageError("Either --version or --use-defaults must be provided")

    resolved_version: str
    if use_defaults:
        defaults_path = (
            Path(__file__).resolve().parent.parent / "defaults" / "platforms.yaml"
        )
        try:
            with defaults_path.open(encoding="utf-8") as fh:
                platforms = yaml.safe_load(fh)
        except FileNotFoundError as exc:
            raise click.ClickException(
                f"{defaults_path} not found; run from the repo root"
            ) from exc
        except yaml.YAMLError:
            raise click.ClickException(
                f"Failed to parse {defaults_path}: invalid YAML syntax"
            ) from None

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
