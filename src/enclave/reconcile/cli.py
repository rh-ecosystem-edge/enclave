"""Click commands for ``enclave reconcile``."""

import re
from pathlib import Path
from typing import NamedTuple, cast

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
    semver_key,
)

PLUGIN_NAME_RE = re.compile(r"^[a-z0-9][a-z0-9-]*$")


class OperatorEntry(NamedTuple):
    """Validated operator definition passed to the operator reconciler."""

    name: str
    version: str
    namespace: str
    csv_names: list[str]
    channel: str | None


def package_data_path(*parts: str) -> Path:
    """Resolve a data file bundled with the enclave package.

    Installed: site-packages/enclave/reconcile/cli.py → site-packages/enclave/<parts>.
    Editable: src/enclave/reconcile/cli.py → src/enclave/ has no data, so fall back
    to repo_root/<parts>.
    """
    enclave_pkg = Path(__file__).resolve().parent.parent
    path = enclave_pkg.joinpath(*parts)
    if not path.exists():
        path = enclave_pkg.parent.parent.joinpath(*parts)
    return path


def defaults_path(filename: str) -> Path:
    """Resolve a file under the bundled defaults/ directory."""
    return package_data_path("defaults", filename)


def load_openshift_versions() -> list[dict[str, object]]:
    """Load the openshift_versions list from defaults/platforms.yaml."""
    defaults_file = defaults_path("platforms.yaml")
    try:
        with defaults_file.open(encoding="utf-8") as fh:
            platforms = yaml.safe_load(fh)
    except FileNotFoundError as exc:
        raise click.ClickException(
            f"{defaults_file} not found; run from the repo root"
        ) from exc
    except yaml.YAMLError as exc:
        raise click.ClickException(f"Failed to parse {defaults_file}: {exc}") from exc

    if not isinstance(platforms, dict):
        raise click.ClickException(
            f"{defaults_file} must be a mapping with an 'openshift_versions' list"
        )
    openshift_versions = platforms.get("openshift_versions")
    if not isinstance(openshift_versions, list) or not openshift_versions:
        raise click.ClickException(
            f"{defaults_file} must define a non-empty 'openshift_versions' list"
        )

    return openshift_versions


def _validate_operator_entries(operators: object, source: str) -> list[OperatorEntry]:
    """Validate every operator entry before any of them is reconciled."""
    if not isinstance(operators, list) or not operators:
        raise click.ClickException(f"{source} defines no operators")

    validated: list[OperatorEntry] = []
    for idx, op in enumerate(operators):
        if not isinstance(op, dict):
            raise click.ClickException(
                f"{source}: operator entry {idx} is not a mapping"
            )
        for field in ("name", "version", "namespace"):
            value = op.get(field)
            if not isinstance(value, str) or not value.strip():
                raise click.ClickException(
                    f"{source}: operator entry {idx} has invalid or missing "
                    f"'{field}': {value!r}"
                )
        channel = op.get("channel")
        if channel is not None and (
            not isinstance(channel, str) or not channel.strip()
        ):
            raise click.ClickException(
                f"{source}: operator entry {idx} has invalid 'channel': {channel!r}"
            )
        csv_names = op.get("csvNames")
        if csv_names is not None and (
            not isinstance(csv_names, list)
            or not all(isinstance(n, str) and n.strip() for n in csv_names)
        ):
            raise click.ClickException(
                f"{source}: operator entry {idx} has invalid 'csvNames': must be "
                "a list of non-blank strings"
            )
        validated.append(
            OperatorEntry(
                op["name"],
                op["version"],
                op["namespace"],
                csv_names or [op["name"]],
                channel,
            )
        )
    return validated


def _reconcile_operator_entries(
    entries: list[OperatorEntry], dry_run: bool, channel_only: bool
) -> None:
    """Reconcile each operator entry in order.

    Raises:
        click.ClickException: If channel_only is set and any entry has no
            channel; no entry is reconciled in that case.
    """
    if channel_only:
        missing = [entry.name for entry in entries if entry.channel is None]
        if missing:
            raise click.ClickException(
                f"--channel-only requires a channel for: {', '.join(missing)}"
            )
    for entry in entries:
        operator_versions_reconcile(
            entry.version,
            entry.namespace,
            entry.csv_names,
            dry_run,
            channel=entry.channel,
            subscription_name=entry.name,
            channel_only=channel_only,
        )


def _load_plugin_operators(plugin_name: str) -> list[OperatorEntry]:
    """Load and validate the operators of plugins/<plugin_name>/plugin.yaml."""
    if not PLUGIN_NAME_RE.fullmatch(plugin_name):
        raise click.UsageError(
            f"Invalid plugin name {plugin_name!r}: must match {PLUGIN_NAME_RE.pattern}"
        )

    plugin_file = package_data_path("plugins", plugin_name, "plugin.yaml")
    try:
        with plugin_file.open(encoding="utf-8") as fh:
            descriptor = yaml.safe_load(fh)
    except FileNotFoundError as exc:
        raise click.ClickException(f"Plugin {plugin_name!r} not found") from exc
    except yaml.YAMLError as exc:
        raise click.ClickException(f"Failed to parse {plugin_file}") from exc

    if not isinstance(descriptor, dict):
        raise click.ClickException(f"{plugin_file} must be a mapping")
    if descriptor.get("installOperators") is False:
        raise click.ClickException(
            f"Plugin {plugin_name!r} has installOperators set to false"
        )

    return _validate_operator_entries(
        descriptor.get("operators"), f"Plugin {plugin_name!r}"
    )


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
@click.option("--channel", help="Subscription channel to reconcile")
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
    help=(
        "Load all operators from defaults/operators.yaml (mutually exclusive "
        "with --name, --version, --namespace, --channel, --csv-name)"
    ),
)
@click.option("--dry-run/--no-dry-run", default=False)
@click.option(
    "--channel-only",
    is_flag=True,
    default=False,
    help=(
        "Update Subscription channels and wait for target InstallPlans without "
        "approving them"
    ),
)
def operator_versions(
    name: str,
    version: str,
    namespace: str,
    channel: str | None,
    csv_names: tuple[str, ...],
    use_defaults: bool,
    dry_run: bool,
    channel_only: bool,
) -> None:
    """Reconcile operator versions using explicit options or bundled defaults."""
    if use_defaults and any([name, version, namespace, channel, csv_names]):
        raise click.UsageError(
            "--use-defaults is mutually exclusive with --name, --version, "
            "--namespace, --channel, --csv-name"
        )

    if not use_defaults:
        missing = [
            f"--{f}"
            for f, v in [("name", name), ("version", version), ("namespace", namespace)]
            if not v
        ]
        if missing:
            raise click.UsageError(f"Missing option(s): {', '.join(missing)}")
        if channel is None:
            if channel_only:
                raise click.UsageError("--channel-only requires --channel")
            operator_versions_reconcile(
                version, namespace, list(csv_names) or [name], dry_run
            )
        else:
            operator_versions_reconcile(
                version,
                namespace,
                list(csv_names) or [name],
                dry_run,
                channel=channel,
                subscription_name=name,
                channel_only=channel_only,
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

    _reconcile_operator_entries(
        _validate_operator_entries(operators, str(defaults_file)),
        dry_run,
        channel_only,
    )


@cli.command(no_args_is_help=True)
@click.option(
    "--plugin",
    "plugins",
    multiple=True,
    required=True,
    help="Plugin name under plugins/; repeat to reconcile several plugins",
)
@click.option("--dry-run/--no-dry-run", default=False)
@click.option(
    "--channel-only",
    is_flag=True,
    default=False,
    help=(
        "Update Subscription channels and wait for target InstallPlans without "
        "approving them"
    ),
)
def plugin_operator_versions(
    plugins: tuple[str, ...], dry_run: bool, channel_only: bool
) -> None:
    """Reconcile operator versions defined in plugins/<name>/plugin.yaml."""
    entries = [entry for plugin in plugins for entry in _load_plugin_operators(plugin)]
    _reconcile_operator_entries(entries, dry_run, channel_only)


@cli.command(no_args_is_help=True)
@click.option(
    "--version", "version", default=None, help="OpenShift version to upgrade to"
)
@click.option(
    "--use-defaults",
    is_flag=True,
    default=False,
    help="Load the default version from defaults/platforms.yaml (mutually exclusive with --version, --latest)",
)
@click.option(
    "--latest",
    "use_latest",
    is_flag=True,
    default=False,
    help="Upgrade to the highest semver version in defaults/platforms.yaml (mutually exclusive with --version, --use-defaults)",
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
@click.option(
    "--allow-not-recommended",
    is_flag=True,
    default=False,
    help="Allow upgrade to not-recommended versions",
)
def mgmt_cluster_version(
    version: str | None,
    use_defaults: bool,
    use_latest: bool,
    dry_run: bool,
    timeout_minutes: int,
    sleep_interval: int,
    allow_not_recommended: bool,
) -> None:
    """Upgrade the management cluster to an explicit, default, or latest version."""
    if sum([version is not None, use_defaults, use_latest]) > 1:
        raise click.UsageError(
            "--version, --use-defaults, and --latest are mutually exclusive"
        )

    if not use_defaults and not use_latest and not version:
        raise click.UsageError(
            "One of --version, --use-defaults, or --latest must be provided"
        )

    openshift_versions = load_openshift_versions()

    if use_defaults:
        default_entry = next(
            (v for v in openshift_versions if v.get("default") is True), None
        )
        if default_entry is None:
            raise click.ClickException(
                "No default version found in defaults/platforms.yaml; "
                "set 'default: true' on one entry"
            )
        resolved_version: str = str(default_entry["version"])
    elif use_latest:
        resolved_version = str(
            max(openshift_versions, key=lambda v: semver_key(str(v["version"])))[
                "version"
            ]
        )
    else:
        resolved_version = cast("str", version)
        allowed_versions = {str(v["version"]) for v in openshift_versions}
        if resolved_version not in allowed_versions:
            raise click.UsageError(
                f"Version '{resolved_version}' is not in defaults/platforms.yaml. "
                f"Allowed: {', '.join(sorted(allowed_versions))}"
            )

    try:
        cluster_upgrade_reconcile(
            resolved_version,
            dry_run,
            timeout_minutes,
            sleep_interval,
            allow_not_recommended,
        )
    except (ClusterUpgradeError, RuntimeError, TimeoutError) as e:
        raise click.ClickException(str(e)) from e


if __name__ == "__main__":
    cli()
