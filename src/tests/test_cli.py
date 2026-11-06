"""Unit tests for the ``enclave reconcile`` CLI."""

from pathlib import Path

import pytest
import yaml
from click.testing import CliRunner
from pytest_mock import MockerFixture

from enclave.reconcile.cli import cli, defaults_path, package_data_path

_KC = {"KUBECONFIG": "/fake/kubeconfig"}


def test_cli_help() -> None:
    """Top-level help shows the reconcile CLI without tools commands."""
    result = CliRunner().invoke(cli, ["--help"])
    assert result.exit_code == 0
    assert "Reconcile CLI" in result.output
    assert "resolve-quay-registry-ca" not in result.output
    assert "collect-node-image-digests" not in result.output


def test_operator_versions_help() -> None:
    """operator-versions help lists its options."""
    result = CliRunner().invoke(cli, ["operator-versions", "--help"], env=_KC)
    assert result.exit_code == 0
    assert "--name" in result.output
    assert "--version" in result.output
    assert "--namespace" in result.output
    assert "--channel-only" in result.output
    assert "--csv-name" in result.output
    assert "--dry-run" in result.output
    assert "--use-defaults" in result.output
    assert "--operators" not in result.output


def test_mgmt_cluster_version_help() -> None:
    """mgmt-cluster-version help lists its options."""
    result = CliRunner().invoke(cli, ["mgmt-cluster-version", "--help"], env=_KC)
    assert result.exit_code == 0
    assert "--version" in result.output
    assert "--use-defaults" in result.output
    assert "--timeout-minutes" in result.output
    assert "--sleep-interval" in result.output


def test_log_level_option() -> None:
    """A valid --log-level is accepted."""
    result = CliRunner().invoke(cli, ["--log-level", "DEBUG", "--help"])
    assert result.exit_code == 0


def test_invalid_log_level() -> None:
    """An invalid --log-level is rejected."""
    result = CliRunner().invoke(cli, ["--log-level", "INVALID"])
    assert result.exit_code != 0


def test_operator_versions_csv_name_defaults_to_name(mocker: MockerFixture) -> None:
    """The CSV name defaults to the operator name."""
    mock_reconcile = mocker.patch("enclave.reconcile.cli.operator_versions_reconcile")
    dry_run = True
    result = CliRunner().invoke(
        cli,
        [
            "operator-versions",
            "--name",
            "quay-operator",
            "--version",
            "3.15.3",
            "--namespace",
            "quay-enterprise",
            "--dry-run",
        ],
        env=_KC,
    )
    assert result.exit_code == 0
    mock_reconcile.assert_called_once_with(
        "3.15.3", "quay-enterprise", ["quay-operator"], dry_run
    )


def test_operator_versions_multiple_csv_names(mocker: MockerFixture) -> None:
    """Repeated --csv-name options are all passed through."""
    mock_reconcile = mocker.patch("enclave.reconcile.cli.operator_versions_reconcile")
    dry_run = True
    result = CliRunner().invoke(
        cli,
        [
            "operator-versions",
            "--name",
            "metallb-operator",
            "--version",
            "4.20.0",
            "--namespace",
            "metallb-system",
            "--csv-name",
            "metallb-operator",
            "--csv-name",
            "metallb-operator-bundle",
            "--dry-run",
        ],
        env=_KC,
    )
    assert result.exit_code == 0
    mock_reconcile.assert_called_once_with(
        "4.20.0",
        "metallb-system",
        ["metallb-operator", "metallb-operator-bundle"],
        dry_run,
    )


def test_operator_versions_passes_channel_to_reconcile(
    mocker: MockerFixture,
) -> None:
    """Pass explicit Subscription channel and name through the CLI."""
    mock_reconcile = mocker.patch("enclave.reconcile.cli.operator_versions_reconcile")
    dry_run = False
    result = CliRunner().invoke(
        cli,
        [
            "operator-versions",
            "--name",
            "quay-operator",
            "--version",
            "3.15.3",
            "--namespace",
            "quay-enterprise",
            "--channel",
            "stable-3.15",
        ],
        env=_KC,
    )

    assert result.exit_code == 0
    mock_reconcile.assert_called_once_with(
        "3.15.3",
        "quay-enterprise",
        ["quay-operator"],
        dry_run,
        channel="stable-3.15",
        subscription_name="quay-operator",
        channel_only=False,
    )


def test_operator_versions_passes_channel_only_to_reconcile(
    mocker: MockerFixture,
) -> None:
    """Pass channel-only mode through the CLI."""
    mock_reconcile = mocker.patch("enclave.reconcile.cli.operator_versions_reconcile")
    dry_run = False
    result = CliRunner().invoke(
        cli,
        [
            "operator-versions",
            "--name",
            "quay-operator",
            "--version",
            "3.15.3",
            "--namespace",
            "quay-enterprise",
            "--channel",
            "stable-3.15",
            "--channel-only",
        ],
        env=_KC,
    )

    assert result.exit_code == 0, result.output
    mock_reconcile.assert_called_once_with(
        "3.15.3",
        "quay-enterprise",
        ["quay-operator"],
        dry_run,
        channel="stable-3.15",
        subscription_name="quay-operator",
        channel_only=True,
    )


def test_operator_versions_channel_only_requires_channel() -> None:
    """--channel-only without --channel is rejected."""
    result = CliRunner().invoke(
        cli,
        [
            "operator-versions",
            "--name",
            "quay-operator",
            "--version",
            "3.15.3",
            "--namespace",
            "quay-enterprise",
            "--channel-only",
        ],
        env=_KC,
    )
    assert result.exit_code != 0
    assert "--channel-only requires --channel" in result.output


def test_use_defaults_calls_reconcile_per_operator(mocker: MockerFixture) -> None:
    """Pass each default operator's channel and Subscription name to reconcile."""
    mock_reconcile = mocker.patch("enclave.reconcile.cli.operator_versions_reconcile")
    defaults_file = defaults_path("operators.yaml")
    with defaults_file.open(encoding="utf-8") as fh:
        operators = yaml.safe_load(fh)["operators"]
    dry_run = True
    result = CliRunner().invoke(
        cli, ["operator-versions", "--use-defaults", "--dry-run"], env=_KC
    )
    assert result.exit_code == 0, result.output
    assert mock_reconcile.call_count == len(operators)
    quay_op = next(op for op in operators if op["name"] == "quay-operator")
    mock_reconcile.assert_any_call(
        quay_op["version"],
        quay_op["namespace"],
        quay_op.get("csvNames") or [quay_op["name"]],
        dry_run,
        channel=quay_op["channel"],
        subscription_name=quay_op["name"],
        channel_only=False,
    )


def test_use_defaults_passes_channel_only(mocker: MockerFixture) -> None:
    """--use-defaults forwards --channel-only."""
    mock_reconcile = mocker.patch("enclave.reconcile.cli.operator_versions_reconcile")
    result = CliRunner().invoke(
        cli,
        ["operator-versions", "--use-defaults", "--channel-only"],
        env=_KC,
    )

    assert result.exit_code == 0, result.output
    assert mock_reconcile.call_count > 0
    assert all(call.kwargs["channel_only"] for call in mock_reconcile.call_args_list)


def test_use_defaults_mutual_exclusive_name() -> None:
    """--use-defaults and --name are mutually exclusive."""
    result = CliRunner().invoke(
        cli, ["operator-versions", "--use-defaults", "--name", "foo"], env=_KC
    )
    assert result.exit_code != 0
    assert "mutually exclusive" in result.output


def test_use_defaults_mutual_exclusive_version() -> None:
    """--use-defaults and --version are mutually exclusive."""
    result = CliRunner().invoke(
        cli, ["operator-versions", "--use-defaults", "--version", "1.0.0"], env=_KC
    )
    assert result.exit_code != 0
    assert "mutually exclusive" in result.output


def test_use_defaults_mutual_exclusive_csv_name() -> None:
    """--use-defaults and --csv-name are mutually exclusive."""
    result = CliRunner().invoke(
        cli, ["operator-versions", "--use-defaults", "--csv-name", "foo"], env=_KC
    )
    assert result.exit_code != 0
    assert "mutually exclusive" in result.output


def test_operator_versions_missing_required_without_defaults() -> None:
    """Without --use-defaults the explicit options are required."""
    result = CliRunner().invoke(cli, ["operator-versions", "--name", "foo"], env=_KC)
    assert result.exit_code != 0
    assert "Missing option" in result.output


def test_mgmt_cluster_version_with_version(mocker: MockerFixture) -> None:
    """An explicit --version is passed to the upgrade."""
    mock_reconcile = mocker.patch("enclave.reconcile.cli.cluster_upgrade_reconcile")
    dry_run = True
    allow_not_recommended = False
    result = CliRunner().invoke(
        cli, ["mgmt-cluster-version", "--version", "4.20.21", "--dry-run"], env=_KC
    )
    assert result.exit_code == 0, result.output
    mock_reconcile.assert_called_once_with(
        "4.20.21", dry_run, 180, 60, allow_not_recommended
    )


def test_mgmt_cluster_version_unknown_version_rejected(mocker: MockerFixture) -> None:
    """A version not in the bundled defaults is rejected."""
    mock_reconcile = mocker.patch("enclave.reconcile.cli.cluster_upgrade_reconcile")
    result = CliRunner().invoke(
        cli, ["mgmt-cluster-version", "--version", "4.99.0", "--dry-run"], env=_KC
    )
    assert result.exit_code != 0
    assert "Version '4.99.0'" in result.output
    assert "not in defaults/platforms.yaml" in result.output
    assert "4.20.21" in result.output
    assert "4.20.32" in result.output
    mock_reconcile.assert_not_called()


def test_mgmt_cluster_version_unknown_version_rejected_even_with_allow_not_recommended(
    mocker: MockerFixture,
) -> None:
    """Versions not in platforms.yaml are rejected even with --allow-not-recommended."""
    mock_reconcile = mocker.patch("enclave.reconcile.cli.cluster_upgrade_reconcile")
    result = CliRunner().invoke(
        cli,
        [
            "mgmt-cluster-version",
            "--version",
            "4.99.0",
            "--allow-not-recommended",
            "--dry-run",
        ],
        env=_KC,
    )
    assert result.exit_code != 0
    assert "Version '4.99.0'" in result.output
    assert "not in defaults/platforms.yaml" in result.output
    mock_reconcile.assert_not_called()


def test_mgmt_cluster_version_use_defaults(mocker: MockerFixture) -> None:
    """--use-defaults upgrades to the default version."""
    mock_reconcile = mocker.patch("enclave.reconcile.cli.cluster_upgrade_reconcile")
    dry_run = True
    allow_not_recommended = False
    result = CliRunner().invoke(
        cli, ["mgmt-cluster-version", "--use-defaults", "--dry-run"], env=_KC
    )
    assert result.exit_code == 0, result.output
    mock_reconcile.assert_called_once_with(
        "4.20.32", dry_run, 180, 60, allow_not_recommended
    )


def test_mgmt_cluster_version_use_defaults_mutual_exclusive_version() -> None:
    """--use-defaults and --version are mutually exclusive."""
    result = CliRunner().invoke(
        cli, ["mgmt-cluster-version", "--use-defaults", "--version", "4.20.8"], env=_KC
    )
    assert result.exit_code != 0
    assert "mutually exclusive" in result.output


def test_mgmt_cluster_version_latest(mocker: MockerFixture) -> None:
    """--latest upgrades to the newest bundled version."""
    mock_reconcile = mocker.patch("enclave.reconcile.cli.cluster_upgrade_reconcile")
    dry_run = True
    allow_not_recommended = False
    result = CliRunner().invoke(
        cli, ["mgmt-cluster-version", "--latest", "--dry-run"], env=_KC
    )
    assert result.exit_code == 0, result.output
    mock_reconcile.assert_called_once_with(
        "4.20.32", dry_run, 180, 60, allow_not_recommended
    )


def test_mgmt_cluster_version_latest_mutual_exclusive_version() -> None:
    """--latest and --version are mutually exclusive."""
    result = CliRunner().invoke(
        cli, ["mgmt-cluster-version", "--latest", "--version", "4.20.21"], env=_KC
    )
    assert result.exit_code != 0
    assert "mutually exclusive" in result.output


def test_mgmt_cluster_version_latest_mutual_exclusive_use_defaults() -> None:
    """--latest and --use-defaults are mutually exclusive."""
    result = CliRunner().invoke(
        cli, ["mgmt-cluster-version", "--latest", "--use-defaults"], env=_KC
    )
    assert result.exit_code != 0
    assert "mutually exclusive" in result.output


def test_mgmt_cluster_version_allow_not_recommended(mocker: MockerFixture) -> None:
    """--allow-not-recommended is passed to the upgrade."""
    mock_reconcile = mocker.patch("enclave.reconcile.cli.cluster_upgrade_reconcile")
    dry_run = True
    allow_not_recommended = True
    result = CliRunner().invoke(
        cli,
        [
            "mgmt-cluster-version",
            "--version",
            "4.20.21",
            "--allow-not-recommended",
            "--dry-run",
        ],
        env=_KC,
    )
    assert result.exit_code == 0, result.output
    mock_reconcile.assert_called_once_with(
        "4.20.21", dry_run, 180, 60, allow_not_recommended
    )


def test_mgmt_cluster_version_no_args_shows_help() -> None:
    """mgmt-cluster-version with no arguments shows help."""
    result = CliRunner().invoke(cli, ["mgmt-cluster-version"])
    assert result.exit_code == 2
    assert "--version" in result.output
    assert "--use-defaults" in result.output


def test_reconcile_no_args_shows_help() -> None:
    """The reconcile group with no arguments shows help."""
    result = CliRunner().invoke(cli, [])
    assert result.exit_code == 2
    assert "Reconcile CLI" in result.output


def test_kubeconfig_missing_fails(mocker: MockerFixture) -> None:
    """A missing kubeconfig makes a cluster command fail."""
    mocker.patch("enclave.utils.Path.exists", return_value=False)
    result = CliRunner().invoke(
        cli, ["mgmt-cluster-version", "--version", "4.14"], env={"KUBECONFIG": ""}
    )
    assert result.exit_code != 0
    assert "KUBECONFIG" in result.output


_REPO_PLUGINS = Path(__file__).resolve().parents[2] / "plugins"

_PLUGIN_OPERATOR = {
    "name": "op-a",
    "version": "1.2.3",
    "channel": "stable-1.2",
    "namespace": "ns-a",
}


@pytest.fixture
def plugins_root(tmp_path: Path, mocker: MockerFixture) -> Path:
    """Resolve package data under tmp_path so tests control plugin descriptors."""
    mocker.patch(
        "enclave.reconcile.cli.package_data_path",
        side_effect=tmp_path.joinpath,
    )
    return tmp_path


def _write_plugin(root: Path, name: str, descriptor: object) -> None:
    """Write a plugin.yaml descriptor under root/plugins/<name>/."""
    plugin_dir = root / "plugins" / name
    plugin_dir.mkdir(parents=True)
    (plugin_dir / "plugin.yaml").write_text(
        yaml.safe_dump(descriptor), encoding="utf-8"
    )


def test_plugin_operator_versions_help() -> None:
    """Help lists the plugin, dry-run and channel-only options."""
    result = CliRunner().invoke(cli, ["plugin-operator-versions", "--help"], env=_KC)
    assert result.exit_code == 0
    assert "--plugin" in result.output
    assert "--dry-run" in result.output
    assert "--channel-only" in result.output


def test_plugin_operator_versions_requires_plugin() -> None:
    """Omitting --plugin is a usage error."""
    result = CliRunner().invoke(cli, ["plugin-operator-versions", "--dry-run"], env=_KC)
    assert result.exit_code == 2
    assert "--plugin" in result.output


def test_plugin_operator_versions_passes_channel_and_subscription(
    mocker: MockerFixture, plugins_root: Path
) -> None:
    """Pass version, namespace, csvNames, channel and Subscription name to reconcile."""
    mock_reconcile = mocker.patch("enclave.reconcile.cli.operator_versions_reconcile")
    _write_plugin(
        plugins_root,
        "plug",
        {"name": "plug", "operators": [{**_PLUGIN_OPERATOR, "csvNames": ["csv-a"]}]},
    )
    dry_run = True
    result = CliRunner().invoke(
        cli, ["plugin-operator-versions", "--plugin", "plug", "--dry-run"], env=_KC
    )
    assert result.exit_code == 0, result.output
    mock_reconcile.assert_called_once_with(
        "1.2.3",
        "ns-a",
        ["csv-a"],
        dry_run,
        channel="stable-1.2",
        subscription_name="op-a",
        channel_only=False,
    )


def test_plugin_operator_versions_csv_names_default_to_name(
    mocker: MockerFixture, plugins_root: Path
) -> None:
    """Use the operator name as CSV name when csvNames is absent."""
    mock_reconcile = mocker.patch("enclave.reconcile.cli.operator_versions_reconcile")
    _write_plugin(
        plugins_root, "plug", {"name": "plug", "operators": [_PLUGIN_OPERATOR]}
    )
    result = CliRunner().invoke(
        cli, ["plugin-operator-versions", "--plugin", "plug"], env=_KC
    )
    assert result.exit_code == 0, result.output
    assert mock_reconcile.call_args.args[2] == ["op-a"]


def test_plugin_operator_versions_channel_only(
    mocker: MockerFixture, plugins_root: Path
) -> None:
    """Forward --channel-only to reconcile."""
    mock_reconcile = mocker.patch("enclave.reconcile.cli.operator_versions_reconcile")
    _write_plugin(
        plugins_root, "plug", {"name": "plug", "operators": [_PLUGIN_OPERATOR]}
    )
    result = CliRunner().invoke(
        cli,
        ["plugin-operator-versions", "--plugin", "plug", "--channel-only"],
        env=_KC,
    )
    assert result.exit_code == 0, result.output
    assert mock_reconcile.call_args.kwargs["channel_only"] is True


def test_plugin_operator_versions_multiple_plugins_and_operators(
    mocker: MockerFixture, plugins_root: Path
) -> None:
    """Reconcile every operator of every --plugin in the given order."""
    mock_reconcile = mocker.patch("enclave.reconcile.cli.operator_versions_reconcile")
    _write_plugin(
        plugins_root,
        "plug-a",
        {
            "name": "plug-a",
            "installOperators": True,
            "operators": [_PLUGIN_OPERATOR, {**_PLUGIN_OPERATOR, "name": "op-b"}],
        },
    )
    _write_plugin(
        plugins_root,
        "plug-b",
        {"name": "plug-b", "operators": [{**_PLUGIN_OPERATOR, "name": "op-c"}]},
    )
    result = CliRunner().invoke(
        cli,
        ["plugin-operator-versions", "--plugin", "plug-a", "--plugin", "plug-b"],
        env=_KC,
    )
    assert result.exit_code == 0, result.output
    assert [c.kwargs["subscription_name"] for c in mock_reconcile.call_args_list] == [
        "op-a",
        "op-b",
        "op-c",
    ]


@pytest.mark.parametrize("name", ["../plug", "a/b", "Plug", "-plug", ""])
def test_plugin_operator_versions_rejects_invalid_name(
    mocker: MockerFixture, name: str
) -> None:
    """Reject plugin names that do not match the plugin name pattern."""
    mock_reconcile = mocker.patch("enclave.reconcile.cli.operator_versions_reconcile")
    result = CliRunner().invoke(
        cli, ["plugin-operator-versions", "--plugin", name], env=_KC
    )
    assert result.exit_code == 2
    assert "Invalid plugin name" in result.output
    mock_reconcile.assert_not_called()


@pytest.mark.usefixtures("plugins_root")
def test_plugin_operator_versions_unknown_plugin() -> None:
    """Fail when the plugin descriptor does not exist."""
    result = CliRunner().invoke(
        cli, ["plugin-operator-versions", "--plugin", "missing"], env=_KC
    )
    assert result.exit_code == 1
    assert "Plugin 'missing' not found" in result.output


@pytest.mark.parametrize(
    ("descriptor", "message"),
    [
        (
            {
                "name": "plug",
                "installOperators": False,
                "operators": [_PLUGIN_OPERATOR],
            },
            "installOperators set to false",
        ),
        ({"name": "plug"}, "defines no operators"),
        ({"name": "plug", "operators": []}, "defines no operators"),
        (["not", "a", "mapping"], "must be a mapping"),
        ({"name": "plug", "operators": ["op-a"]}, "is not a mapping"),
        (
            {"name": "plug", "operators": [{**_PLUGIN_OPERATOR, "namespace": ""}]},
            "invalid or missing 'namespace'",
        ),
        (
            {"name": "plug", "operators": [{**_PLUGIN_OPERATOR, "channel": 1}]},
            "invalid 'channel'",
        ),
        (
            {"name": "plug", "operators": [{**_PLUGIN_OPERATOR, "channel": "  "}]},
            "invalid 'channel'",
        ),
        (
            {"name": "plug", "operators": [{**_PLUGIN_OPERATOR, "csvNames": "csv"}]},
            "invalid 'csvNames'",
        ),
    ],
)
def test_plugin_operator_versions_rejects_invalid_descriptor(
    mocker: MockerFixture, plugins_root: Path, descriptor: object, message: str
) -> None:
    """Fail without reconciling when the descriptor or an operator entry is invalid."""
    mock_reconcile = mocker.patch("enclave.reconcile.cli.operator_versions_reconcile")
    _write_plugin(plugins_root, "plug", descriptor)
    result = CliRunner().invoke(
        cli, ["plugin-operator-versions", "--plugin", "plug"], env=_KC
    )
    assert result.exit_code == 1
    assert message in result.output
    mock_reconcile.assert_not_called()


def test_plugin_operator_versions_validates_all_plugins_before_reconciling(
    mocker: MockerFixture, plugins_root: Path
) -> None:
    """Reconcile nothing when any of the given plugins is invalid."""
    mock_reconcile = mocker.patch("enclave.reconcile.cli.operator_versions_reconcile")
    _write_plugin(
        plugins_root, "good", {"name": "good", "operators": [_PLUGIN_OPERATOR]}
    )
    _write_plugin(plugins_root, "bad", {"name": "bad", "operators": []})
    result = CliRunner().invoke(
        cli,
        ["plugin-operator-versions", "--plugin", "good", "--plugin", "bad"],
        env=_KC,
    )
    assert result.exit_code == 1
    mock_reconcile.assert_not_called()


def test_plugin_operator_versions_bad_yaml(plugins_root: Path) -> None:
    """Fail with a parse error on malformed YAML."""
    plugin_dir = plugins_root / "plugins" / "plug"
    plugin_dir.mkdir(parents=True)
    (plugin_dir / "plugin.yaml").write_text("operators: [", encoding="utf-8")
    result = CliRunner().invoke(
        cli, ["plugin-operator-versions", "--plugin", "plug"], env=_KC
    )
    assert result.exit_code == 1
    assert "Failed to parse" in result.output


def test_repo_plugins_with_mgmt_operators_load(mocker: MockerFixture) -> None:
    """Every repo plugin that installs operators on mgmt must pass validation."""
    mock_reconcile = mocker.patch("enclave.reconcile.cli.operator_versions_reconcile")
    names = []
    for descriptor_file in sorted(_REPO_PLUGINS.glob("*/plugin.yaml")):
        descriptor = yaml.safe_load(descriptor_file.read_text(encoding="utf-8"))
        if descriptor.get("operators") and descriptor.get("installOperators", True):
            names.append(descriptor_file.parent.name)
    assert "lvms" in names

    args = ["plugin-operator-versions", "--dry-run"]
    for name in names:
        args += ["--plugin", name]
    result = CliRunner().invoke(cli, args, env=_KC)
    assert result.exit_code == 0, result.output
    assert mock_reconcile.call_count >= len(names)


def test_package_data_path_prefers_installed_location(
    tmp_path: Path, mocker: MockerFixture
) -> None:
    """Resolve data from the installed package when it exists there."""
    installed = tmp_path / "site-packages" / "enclave"
    (installed / "reconcile").mkdir(parents=True)
    (installed / "plugins" / "lvms").mkdir(parents=True)
    (installed / "plugins" / "lvms" / "plugin.yaml").touch()
    mocker.patch(
        "enclave.reconcile.cli.__file__", str(installed / "reconcile" / "cli.py")
    )
    assert package_data_path("plugins", "lvms", "plugin.yaml") == (
        installed / "plugins" / "lvms" / "plugin.yaml"
    )


def test_plugin_operator_versions_channel_only_requires_channel_on_all_entries(
    mocker: MockerFixture, plugins_root: Path
) -> None:
    """Reconcile nothing with --channel-only when any entry has no channel."""
    mock_reconcile = mocker.patch("enclave.reconcile.cli.operator_versions_reconcile")
    no_channel = {k: v for k, v in _PLUGIN_OPERATOR.items() if k != "channel"}
    _write_plugin(
        plugins_root,
        "plug",
        {
            "name": "plug",
            "operators": [_PLUGIN_OPERATOR, {**no_channel, "name": "op-b"}],
        },
    )
    result = CliRunner().invoke(
        cli,
        ["plugin-operator-versions", "--plugin", "plug", "--channel-only"],
        env=_KC,
    )
    assert result.exit_code == 1
    assert "--channel-only requires a channel for: op-b" in result.output
    mock_reconcile.assert_not_called()
