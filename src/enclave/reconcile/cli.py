from pathlib import Path
from typing import cast

import click
import yaml

from enclave.reconcile.cluster_upgrade import (
    ClusterUpgradeError,
    reconcile as cluster_upgrade_reconcile,
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


def plugin_descriptor_path(plugin_name: str) -> Path:
    """Resolve the plugin.yaml path for a plugin name.

    Plugins are not packaged into the built wheel, so this only resolves
    against a repo checkout: src/enclave/reconcile/cli.py → src/enclave/ →
    repo_root/plugins/<plugin_name>/plugin.yaml.

    Raises:
        click.ClickException: If plugin_name is empty, contains path
            separators or '..', or the resolved descriptor path escapes
            the plugins directory.
    """
    # Normalize Unicode and reject traversal/separator patterns
    plugin_name = plugin_name.strip()
    if (
        not plugin_name
        or "/" in plugin_name
        or "\\" in plugin_name
        or ".." in plugin_name
    ):
        raise click.ClickException(
            f"Invalid plugin name: {plugin_name!r}. Plugin name must be a "
            "simple name without path separators or '..'."
        )

    enclave_pkg = Path(__file__).resolve().parent.parent
    plugins_root = (enclave_pkg.parent.parent / "plugins").resolve()
    descriptor_path = (plugins_root / plugin_name / "plugin.yaml").resolve()

    # Ensure the resolved descriptor is actually under the plugins root
    try:
        descriptor_path.relative_to(plugins_root)
    except ValueError as exc:
        raise click.ClickException(
            f"Invalid plugin name: {plugin_name!r} escapes the plugins directory"
        ) from exc

    return descriptor_path


def _reconcile_operators_from_list(
    operators: list[dict[str, object]], dry_run: bool
) -> None:
    """Call operator_versions_reconcile for each operator in a list.

    Each operator dict must have 'name', 'version', 'namespace', and
    optionally 'csvNames' (defaults to [name] when absent).

    Raises:
        click.ClickException: If any operator entry is missing required
            fields or has invalid field types.
    """
    for idx, op in enumerate(operators):
        # Validate operator entry structure
        if not isinstance(op, dict):
            raise click.ClickException(f"Operator entry {idx} is not a mapping: {op!r}")

        # Validate required string fields
        for field in ("name", "version", "namespace"):
            value = op.get(field)
            if not isinstance(value, str) or not value.strip():
                raise click.ClickException(
                    f"Operator entry {idx} has invalid or missing '{field}': {value!r}"
                )

        # Validate optional csvNames field
        csv_names_raw = op.get("csvNames")
        if csv_names_raw is not None and (
            not isinstance(csv_names_raw, list)
            or not all(isinstance(name, str) for name in csv_names_raw)
        ):
            raise click.ClickException(
                f"Operator entry {idx} has invalid 'csvNames': must be a list of strings"
            )

        op_name = cast("str", op["name"])
        op_csv_names = cast("list[str] | None", csv_names_raw) or [op_name]
        operator_versions_reconcile(
            cast("str", op["version"]),
            cast("str", op["namespace"]),
            op_csv_names,
            dry_run,
        )


def _load_defaults_operators() -> list[dict[str, object]]:
    """Load the operators list from defaults/operators.yaml.

    Raises:
        click.ClickException: If the file is missing, not valid YAML, or
            does not contain an 'operators' list.
    """
    defaults_file = defaults_path("operators.yaml")
    try:
        with defaults_file.open(encoding="utf-8") as fh:
            data = yaml.safe_load(fh)
    except FileNotFoundError as exc:
        raise click.ClickException(
            f"{defaults_file} not found; run from the repo root"
        ) from exc
    except yaml.YAMLError as exc:
        raise click.ClickException(f"Failed to parse {defaults_file}") from exc

    if not isinstance(data, dict) or not isinstance(data.get("operators"), list):
        raise click.ClickException(
            f"{defaults_file} does not contain an 'operators' list"
        )
    return cast("list[dict[str, object]]", data["operators"])


def _load_plugin_operators(plugin_name: str) -> list[dict[str, object]]:
    """Load the operators list from a plugin's plugin.yaml descriptor.

    Raises:
        click.ClickException: If the plugin name is invalid, the plugin
            descriptor is missing or not valid YAML, the plugin has
            installOperators set to false, or it defines no operators.
    """
    plugin_file = plugin_descriptor_path(plugin_name)
    try:
        with plugin_file.open(encoding="utf-8") as fh:
            data = yaml.safe_load(fh)
    except FileNotFoundError as exc:
        raise click.ClickException(
            f"Plugin {plugin_name!r} not found; check plugin name or run "
            "from the repo root"
        ) from exc
    except yaml.YAMLError as exc:
        raise click.ClickException(f"Failed to parse {plugin_file}") from exc

    if not isinstance(data, dict):
        raise click.ClickException(f"{plugin_file} is empty or not a mapping")

    if data.get("installOperators") is False:
        raise click.ClickException(
            f"Plugin {plugin_name!r} has installOperators set to false"
        )

    operators = data.get("operators")
    if not isinstance(operators, list) or not operators:
        raise click.ClickException(
            f"Plugin {plugin_name!r} has no operators defined in {plugin_file}"
        )
    return cast("list[dict[str, object]]", operators)


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
    help="Load all operators from defaults/operators.yaml (mutually exclusive with --name, --version, --namespace, --csv-name, --plugin)",
)
@click.option(
    "--plugin",
    help="Load operators from plugins/<name>/plugin.yaml (mutually exclusive with --name, --version, --namespace, --csv-name, --use-defaults)",
)
@click.option("--dry-run/--no-dry-run", default=False)
def operator_versions(
    name: str,
    version: str,
    namespace: str,
    csv_names: tuple[str, ...],
    use_defaults: bool,
    plugin: str | None,
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
        _reconcile_operators_from_list(_load_plugin_operators(plugin), dry_run)
        return

    if use_defaults:
        _reconcile_operators_from_list(_load_defaults_operators(), dry_run)
        return

    missing = [
        f"--{f}"
        for f, v in [("name", name), ("version", version), ("namespace", namespace)]
        if not v
    ]
    if missing:
        raise click.UsageError(f"Missing option(s): {', '.join(missing)}")
    operator_versions_reconcile(version, namespace, list(csv_names) or [name], dry_run)


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
        resolved_version: str = str(default_entry["version"])
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
