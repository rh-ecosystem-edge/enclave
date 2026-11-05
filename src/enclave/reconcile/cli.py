import re
from pathlib import Path
from typing import cast

import click
import yaml

from enclave.reconcile.cluster_upgrade import (
    ClusterUpgradeError,
    InvalidVersionError,
    parse_version,
    reconcile as cluster_upgrade_reconcile,
)
from enclave.reconcile.operator_versions import reconcile as operator_versions_reconcile
from enclave.utils import (
    LOG_LEVELS,
    KubeconfigGroup,
    configure_logging,
    semver_key,
)

OPENSHIFT_VERSION_PATTERN = re.compile(r"[0-9]+\.[0-9]+\.[0-9]+(-[A-Za-z0-9.]+)?")


def _enclave_checkout_root() -> Path | None:
    """Find the checkout whose playbooks and defaults belong to this CLI."""
    package_dir = Path(__file__).resolve().parent
    for start in (Path.cwd(), package_dir):
        for candidate in (start, *start.parents):
            if (candidate / "defaults/platforms.yaml").is_file() and (
                candidate / "playbooks/common/load-vars.yaml"
            ).is_file():
                return candidate
    return None


def defaults_path(filename: str) -> Path:
    """Resolve a defaults file from the active checkout or installed package."""
    checkout = _enclave_checkout_root()
    if checkout is not None:
        return checkout / "defaults" / filename

    # Installed: site-packages/enclave/reconcile/cli.py → site-packages/enclave/ → enclave/defaults/
    # Editable:  src/enclave/reconcile/cli.py → src/enclave/ (no defaults/) → repo_root/defaults/
    enclave_pkg = Path(__file__).resolve().parent.parent
    path = enclave_pkg / "defaults" / filename
    if not path.exists():
        path = enclave_pkg.parent.parent / "defaults" / filename
    return path


def config_path(filename: str) -> Path:
    """Resolve config from the Enclave checkout used by its Ansible playbooks."""
    checkout = _enclave_checkout_root()
    if checkout is not None:
        return checkout / "config" / filename

    enclave_pkg = Path(__file__).resolve().parent.parent
    return enclave_pkg.parent.parent / "config" / filename


def _validate_openshift_versions(
    versions: object, source_file: Path
) -> list[dict[str, object]]:
    """Validate a version list and require exactly one default entry."""
    if not isinstance(versions, list) or not versions:
        raise click.ClickException(
            f"{source_file} must define a non-empty 'openshift_versions' list"
        )
    if not all(isinstance(version, dict) for version in versions):
        raise click.ClickException(
            f"{source_file} 'openshift_versions' entries must be mappings"
        )

    version_entries = cast("list[dict[str, object]]", versions)
    for index, entry in enumerate(version_entries):
        version = entry.get("version")
        if not isinstance(version, str) or not version:
            raise click.ClickException(
                f"{source_file} openshift_versions[{index}] requires a non-empty 'version'"
            )
        if OPENSHIFT_VERSION_PATTERN.fullmatch(version) is None:
            raise click.ClickException(
                f"{source_file} openshift_versions[{index}].version must match "
                "the platforms schema version format"
            )
        try:
            parse_version(version, f"{source_file} openshift_versions[{index}].version")
        except InvalidVersionError as exc:
            raise click.ClickException(str(exc)) from exc
        if "default" in entry and not isinstance(entry["default"], bool):
            raise click.ClickException(
                f"{source_file} version 'default' values must be boolean"
            )

    default_count = sum(entry.get("default") is True for entry in version_entries)
    if default_count != 1:
        raise click.ClickException(
            f"{source_file} must have exactly one version with 'default: true', "
            f"found {default_count}"
        )
    return version_entries


def load_openshift_versions(
    platforms_vars_file: Path | None = None,
) -> list[dict[str, object]]:
    """Load default OpenShift versions and apply an optional config override."""
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
    openshift_versions = _validate_openshift_versions(
        platforms.get("openshift_versions"), defaults_file
    )

    config_file = platforms_vars_file or config_path("platforms.yaml")
    if config_file.exists():
        try:
            with config_file.open(encoding="utf-8") as fh:
                config_platforms = yaml.safe_load(fh)
        except yaml.YAMLError as exc:
            raise click.ClickException(
                f"Failed to parse {config_file} as YAML"
            ) from exc

        if config_platforms is None:
            config_platforms = {}
        if not isinstance(config_platforms, dict):
            raise click.ClickException(f"{config_file} must contain a YAML mapping")
        if "openshift_versions" in config_platforms:
            openshift_versions = _validate_openshift_versions(
                config_platforms["openshift_versions"], config_file
            )

    return openshift_versions


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

    for op in operators:
        op_name: str = op["name"]
        op_csv_names: list[str] = op.get("csvNames") or [op_name]
        operator_versions_reconcile(
            op["version"],
            op["namespace"],
            op_csv_names,
            dry_run,
            channel=op.get("channel"),
            subscription_name=op_name,
            channel_only=channel_only,
        )


@cli.command(no_args_is_help=True)
@click.option(
    "--version", "version", default=None, help="OpenShift version to upgrade to"
)
@click.option(
    "--use-defaults",
    is_flag=True,
    default=False,
    help=(
        "Load the default version from the configured OpenShift list "
        "(mutually exclusive with --version, --latest)"
    ),
)
@click.option(
    "--latest",
    "use_latest",
    is_flag=True,
    default=False,
    help=(
        "Upgrade to the highest semver version in the configured OpenShift list "
        "(mutually exclusive with --version, --use-defaults)"
    ),
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
@click.option(
    "--platforms-vars-file",
    type=click.Path(exists=True, dir_okay=False, path_type=Path),
    default=None,
    help="Load OpenShift version overrides from this platforms config file",
)
def mgmt_cluster_version(
    version: str | None,
    use_defaults: bool,
    use_latest: bool,
    dry_run: bool,
    timeout_minutes: int,
    sleep_interval: int,
    allow_not_recommended: bool,
    platforms_vars_file: Path | None,
) -> None:
    """Reconcile the management cluster to a selected configured version."""
    if sum([version is not None, use_defaults, use_latest]) > 1:
        raise click.UsageError(
            "--version, --use-defaults, and --latest are mutually exclusive"
        )

    if not use_defaults and not use_latest and not version:
        raise click.UsageError(
            "One of --version, --use-defaults, or --latest must be provided"
        )

    openshift_versions = load_openshift_versions(platforms_vars_file)

    if use_defaults:
        default_entry = next(
            (v for v in openshift_versions if v.get("default") is True), None
        )
        if default_entry is None:
            raise click.ClickException(
                "No default version found in the configured OpenShift list; "
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
                f"Version '{resolved_version}' is not in the effective "
                "openshift_versions list. "
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
