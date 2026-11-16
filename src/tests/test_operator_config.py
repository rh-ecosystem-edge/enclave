"""Contracts for core operator configuration shared by install, mirror and upgrade."""

import json
import os
import shutil
import subprocess
import sys
from copy import deepcopy
from pathlib import Path
from typing import Any

import pytest
import yaml
from click.testing import CliRunner
from pytest_mock import MockerFixture

from enclave.operator_config import OperatorConfigError, load_operators
from enclave.reconcile.cli import cli, package_data_path
from enclave.tools.cli import cli as tools_cli


@pytest.fixture(scope="module")
def installed_cli(tmp_path_factory: pytest.TempPathFactory) -> Path:
    """Build and install the real wheel, independently of editable source imports."""
    directory = tmp_path_factory.mktemp("installed-operator-cli")
    root = package_data_path("defaults", "operators.yaml").parent.parent
    environment = directory / "venv"
    commands = [
        ["uv", "build", "--wheel", "--out-dir", str(directory), str(root)],
        [
            "uv",
            "venv",
            "--python",
            sys.executable,
            str(environment),
        ],
        [
            "uv",
            "pip",
            "install",
            "--python",
            str(environment / "bin/python"),
            str(directory / "enclave-0.1.0-py3-none-any.whl"),
        ],
    ]
    for command in commands:
        result = subprocess.run(
            command, capture_output=True, text=True, check=False, timeout=120
        )
        assert result.returncode == 0, result.stdout + result.stderr
    result = subprocess.run(
        [
            str(environment / "bin/python"),
            "-I",
            "-c",
            "import enclave.reconcile.cli; print(enclave.reconcile.cli.__file__)",
        ],
        cwd=directory,
        capture_output=True,
        text=True,
        check=False,
        timeout=30,
    )
    assert result.returncode == 0, result.stderr
    assert str(environment / "lib") in result.stdout
    return environment / "bin/enclave"


@pytest.fixture
def offline_cli_env(tmp_path: Path, installed_cli: Path) -> dict[str, str]:
    """Replace only oc, rejecting writes so installed CLI tests cannot mutate a cluster."""
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    fake_oc = fake_bin / "oc"
    fake_oc.write_text(
        '#!/bin/sh\nset -eu\n[ "$1" = get ] || exit 99\n'
        "printf '%s\\n' "
        '\'{"spec":{"channel":"baseline"},"items":['
        '{"metadata":{"name":"test-plan"},"status":{"phase":"RequiresApproval"},'
        '"spec":{"clusterServiceVersionNames":["quay-operator.v3.15.10"]}}'
        "]}'\n",
    )
    fake_oc.chmod(0o755)
    environment = os.environ.copy()
    environment.pop("PYTHONPATH", None)
    environment["PATH"] = os.pathsep.join([
        str(fake_bin),
        str(installed_cli.parent),
        environment["PATH"],
    ])
    environment["KUBECONFIG"] = str(tmp_path / "unused-kubeconfig")
    return environment


@pytest.mark.parametrize(
    "content",
    [
        None,
        "operators: []",
        "operators: [{name: quay-operator, version: 3.15.10, channel: fast-test}]",
    ],
)
def test_installed_operator_config_outside_checkout(
    tmp_path: Path,
    installed_cli: Path,
    offline_cli_env: dict[str, str],
    content: str | None,
) -> None:
    """An outside-checkout installed CLI supports both bundled and selected config."""
    config = tmp_path / "selected.yaml"
    config.write_text(content if content is not None else "operators: []")
    result = subprocess.run(
        [
            str(installed_cli),
            "reconcile",
            "operator-versions",
            "--use-defaults",
            "--dry-run",
            *(["--operators-vars-file", config.name] if content is not None else []),
        ],
        cwd=tmp_path,
        env=offline_cli_env,
        capture_output=True,
        text=True,
        check=False,
        timeout=30,
    )
    assert result.returncode == 0, result.stderr
    overridden = content is not None and "fast-test" in content
    assert (
        "quay-enterprise/quay-operator channel=fast-test" in result.stderr
    ) == overridden
    assert (
        "[UPDATE] quay-enterprise/quay-operator:3.15.10" in result.stderr
    ) == overridden
    assert result.stderr.count("Updating Subscription ") == 15


def _operator_upgrade_dry_run_tasks(root: Path) -> list[dict[str, Any]]:
    """Exercise the actual upgrade argv and cwd with writes disabled at the CLI."""
    tasks = yaml.safe_load((root / "playbooks/upgrade.yaml").read_text())[0]["tasks"]
    selected = [
        task
        for task in tasks
        if task["name"]
        in {
            "Check for optional core operator override",
            "Upgrade operator versions",
        }
    ]
    assert len(selected) == 2
    command = selected[1]["ansible.builtin.command"]
    command["argv"] = "{{ (" + command["argv"].strip()[2:-2] + ") + ['--dry-run'] }}"
    return selected


@pytest.mark.parametrize(
    "kind", ["missing", "directory", "dangling", "yaml", "schema", "non-utf8"]
)
def test_installed_explicit_operator_config_invalid(
    tmp_path: Path,
    installed_cli: Path,
    offline_cli_env: dict[str, str],
    kind: str,
) -> None:
    """An explicit bad path or invalid file fails before any reconciliation."""
    config = tmp_path / "selected.yaml"
    if kind == "directory":
        config.mkdir()
    elif kind == "dangling":
        config.symlink_to(tmp_path / "absent.yaml")
    elif kind == "yaml":
        config.write_text("operators: [")
    elif kind == "schema":
        config.write_text("operators: [{name: quay-operator, seed: null}]")
    elif kind == "non-utf8":
        config.write_bytes(b"operators: \xff")
    result = subprocess.run(
        [
            str(installed_cli),
            "reconcile",
            "operator-versions",
            "--use-defaults",
            "--operators-vars-file",
            str(config),
            "--dry-run",
        ],
        cwd=tmp_path,
        env=offline_cli_env,
        capture_output=True,
        text=True,
        check=False,
        timeout=30,
    )
    assert result.returncode != 0
    assert str(config) in result.stderr
    assert "Updating Subscription" not in result.stderr
    assert "Traceback" not in result.stderr


@pytest.mark.parametrize(
    "arguments",
    [
        [],
        [
            "--name",
            "quay-operator",
            "--version",
            "3.15.10",
            "--namespace",
            "quay-enterprise",
        ],
        *[
            ["--use-defaults", option, "value"]
            for option in (
                "--name",
                "--version",
                "--namespace",
                "--channel",
                "--csv-name",
            )
        ],
    ],
)
def test_explicit_operator_config_option_combinations(
    tmp_path: Path, arguments: list[str]
) -> None:
    """A config file requires defaults mode and excludes every explicit operator option."""
    config = tmp_path / "operators.yaml"
    config.write_text("operators: []")
    result = CliRunner().invoke(
        cli,
        ["operator-versions", "--operators-vars-file", str(config), *arguments],
        env={"KUBECONFIG": "/fake/kubeconfig"},
    )
    assert result.exit_code == 2
    assert (
        "mutually exclusive"
        if "--use-defaults" in arguments
        else "requires --use-defaults"
    ) in result.output


@pytest.fixture
def operator_files(tmp_path: Path) -> tuple[Path, Path, Path]:
    """Create isolated defaults with optional flags and a separate override path."""
    defaults = tmp_path / "defaults.yaml"
    defaults.write_text(
        yaml.safe_dump({
            "operators": [
                {
                    "name": "first",
                    "version": "1.0.0",
                    "channel": "stable",
                    "namespace": "first-ns",
                    "init_version": "1.0.0",
                    "seed": True,
                    "csvNames": ["first-csv"],
                    "global": True,
                    "extraMirrorPackages": ["additional-package"],
                },
                {
                    "name": "second",
                    "version": "2.0.0",
                    "channel": "stable",
                    "namespace": "second-ns",
                    "connected_auto": True,
                },
            ]
        }),
        encoding="utf-8",
    )
    return (
        defaults,
        tmp_path / "operators.yaml",
        package_data_path("schemas", "operators.yaml"),
    )


def test_missing_and_empty_overrides(operator_files: tuple[Path, Path, Path]) -> None:
    """Missing files and an explicit empty list retain all defaults without aliasing."""
    defaults, override, schema = operator_files
    original = defaults.read_text()
    expected = yaml.safe_load(original)
    result = load_operators(defaults, override, schema)
    assert result == expected
    result["operators"][0]["csvNames"].append("changed")
    override.write_text("operators: []\n")
    assert load_operators(defaults, override, schema) == expected
    assert defaults.read_text() == original


def test_partial_fields_and_order(operator_files: tuple[Path, Path, Path]) -> None:
    """Out-of-order partial overrides preserve all other fields and default order."""
    defaults, override, schema = operator_files
    expected = deepcopy(yaml.safe_load(defaults.read_text()))
    expected["operators"][0]["version"] = "1.1.0"
    expected["operators"][1]["channel"] = "fast"
    override.write_text(
        "operators:\n- name: second\n  channel: fast\n- name: first\n  version: 1.1.0\n"
    )
    assert load_operators(defaults, override, schema) == expected


@pytest.mark.parametrize(
    "content",
    [
        "",
        "null",
        "{}",
        "[]",
        "operators: null",
        "operators: {}",
        "operators: [null]",
        "operators: [{}]",
        "operatros: []",
        "operators: [{name: typo, version: 1.2.3}]",
        "operators: [{name: first}, {name: first}]",
        "operators: [{name: first, versoin: 1.2.3}]",
        "operators: [{name: first, version: null}]",
        "operators: [{name: first, version: 42}]",
        "operators: [{name: first, channel: '  '}]",
        "operators: [{name: first, seed: 'true'}]",
        "operators: [{name: first, csvNames: [null]}]",
        "operators: [{name: first, csvNames: []}]",
        "operators: [{name: first, extraMirrorPackages: ['']}]",
        "operators: [{name: first, global: 1}]",
        "operators: [{name: first, init_version: false}]",
        "operators: [{name: first, namespace: ''}]",
        "operators: [{name: first, source: null}]",
        "operators: [{name: first, connected_auto: null}]",
        "operators: [{name: first, csvMirror: 'false'}]",
        "operators: [{name: first, name: second}]",
        "operators: [{1: first}]",
        "operators: [{name: first, <<: {name: second}}]",
        "operators: []\noperators: []",
        "operators: [",
    ],
)
def test_invalid_overrides_fail(
    operator_files: tuple[Path, Path, Path],
    content: str,
) -> None:
    """Malformed overrides never silently fall back to defaults."""
    defaults, override, schema = operator_files
    override.write_text(content)
    with pytest.raises(OperatorConfigError):
        load_operators(defaults, override, schema)


def test_validate_effective_dependencies(
    operator_files: tuple[Path, Path, Path],
) -> None:
    """Partial dependencies may be inherited, but must exist after merging."""
    defaults, override, schema = operator_files
    override.write_text("operators: [{name: first, csvMirror: true}]")
    assert load_operators(defaults, override, schema)["operators"][0]["csvMirror"]
    override.write_text("operators: [{name: second, csvMirror: true}]")
    with pytest.raises(OperatorConfigError):
        load_operators(defaults, override, schema)


def test_yaml_alias_values_are_supported(
    operator_files: tuple[Path, Path, Path],
) -> None:
    """Aliases may reuse values without introducing duplicate keys or names."""
    defaults, override, schema = operator_files
    override.write_text(
        "operators:\n- name: first\n  version: &version 1.2.3\n"
        "  init_version: *version\n",
    )
    operator = load_operators(defaults, override, schema)["operators"][0]
    assert operator["version"] == "1.2.3"
    assert operator["init_version"] == "1.2.3"


def test_plugin_upgrade_ignores_invalid_core_config(
    operator_files: tuple[Path, Path, Path],
    mocker: MockerFixture,
) -> None:
    """Plugin reconciliation remains independent of optional core overrides."""
    _defaults, override, _schema = operator_files
    override.write_text("operators: null")
    mocker.patch("enclave.reconcile.cli.config_path", return_value=override)
    reconcile = mocker.patch("enclave.reconcile.cli.operator_versions_reconcile")
    plugin = yaml.safe_load(
        package_data_path("plugins", "lvms", "plugin.yaml").read_text(),
    )["operators"][0]
    result = CliRunner().invoke(
        cli,
        ["plugin-operator-versions", "--plugin", "lvms", "--dry-run"],
        env={"KUBECONFIG": "/fake/kubeconfig"},
    )
    assert result.exit_code == 0, result.output
    dry_run = True
    reconcile.assert_called_once_with(
        plugin["version"],
        plugin["namespace"],
        plugin.get("csvNames", [plugin["name"]]),
        dry_run,
        channel=plugin["channel"],
        subscription_name=plugin["name"],
    )


def _assert_upgrade_matches(
    defaults: Path,
    override: Path,
    effective: list[dict[str, Any]],
    mocker: MockerFixture,
) -> None:
    """Assert every ordered upgrade input matches previously resolved consumer data."""
    mocker.patch("enclave.reconcile.cli.defaults_path", return_value=defaults)
    mocker.patch("enclave.reconcile.cli.config_path", return_value=override)
    reconcile = mocker.patch("enclave.reconcile.cli.operator_versions_reconcile")
    result = CliRunner().invoke(
        cli,
        [
            "operator-versions",
            "--use-defaults",
            "--dry-run",
            *(["--operators-vars-file", str(override)] if override.exists() else []),
        ],
        env={"KUBECONFIG": "/fake/kubeconfig"},
    )
    assert result.exit_code == 0, result.output
    assert reconcile.call_count == len(effective)
    for call, operator in zip(reconcile.call_args_list, effective, strict=True):
        assert call.args == (
            operator["version"],
            operator["namespace"],
            operator.get("csvNames", [operator["name"]]),
            True,
        )
        assert call.kwargs == {
            "channel": operator["channel"],
            "subscription_name": operator["name"],
        }


def test_tools_and_upgrade_parity(
    operator_files: tuple[Path, Path, Path],
    mocker: MockerFixture,
) -> None:
    """The install/mirror resolver CLI and upgrade consume identical effective data."""
    defaults, override, schema = operator_files
    override.write_text("operators: [{name: first, version: 1.1.0, channel: fast}]")
    result = CliRunner().invoke(
        tools_cli,
        [
            "resolve-core-operators",
            "--defaults-file",
            str(defaults),
            "--config-file",
            str(override),
            "--schema-file",
            str(schema),
        ],
    )
    assert result.exit_code == 0, result.output
    _assert_upgrade_matches(
        defaults, override, yaml.safe_load(result.output)["operators"], mocker
    )


def test_upgrade_rejects_invalid_before_reconcile(
    operator_files: tuple[Path, Path, Path],
    mocker: MockerFixture,
) -> None:
    """A later invalid override prevents any reconciliation side effects."""
    defaults, override, _schema = operator_files
    override.write_text(
        "operators: [{name: first, version: 1.1.0}, {name: second, seed: null}]"
    )
    mocker.patch("enclave.reconcile.cli.defaults_path", return_value=defaults)
    mocker.patch("enclave.reconcile.cli.config_path", return_value=override)
    reconcile = mocker.patch("enclave.reconcile.cli.operator_versions_reconcile")
    result = CliRunner().invoke(
        cli,
        ["operator-versions", "--use-defaults"],
        env={"KUBECONFIG": "/fake/kubeconfig"},
    )
    assert result.exit_code != 0
    reconcile.assert_not_called()


def test_list_boolean_and_name_only_overrides(
    operator_files: tuple[Path, Path, Path],
) -> None:
    """Explicit lists and false replace fields; a name-only entry changes nothing."""
    defaults, override, schema = operator_files
    expected = yaml.safe_load(defaults.read_text())
    expected["operators"][0].update({
        "csvNames": ["new-csv"],
        "seed": False,
        "extraMirrorPackages": [],
    })
    override.write_text(
        "operators: [{name: first, csvNames: [new-csv], seed: false, extraMirrorPackages: []}, {name: second}]",
    )
    assert load_operators(defaults, override, schema) == expected


@pytest.mark.parametrize(
    "content",
    [
        "operators: []",
        "operators: [{name: first}]",
        (
            "operators: [{name: first, version: 1.0.0, channel: stable, namespace: ns}, "
            "{name: first, version: 1.0.0, channel: stable, namespace: ns}]"
        ),
    ],
)
def test_invalid_defaults_fail(
    operator_files: tuple[Path, Path, Path],
    content: str,
) -> None:
    """Even without overrides, incomplete or ambiguous defaults cannot be consumed."""
    defaults, override, schema = operator_files
    defaults.write_text(content)
    with pytest.raises(OperatorConfigError):
        load_operators(defaults, override, schema)


def test_unreadable_override_fails(operator_files: tuple[Path, Path, Path]) -> None:
    """A present non-file path is not treated as a missing optional override."""
    defaults, override, schema = operator_files
    override.mkdir()
    with pytest.raises(OperatorConfigError, match="Cannot load"):
        load_operators(defaults, override, schema)


def test_non_utf8_override_fails(operator_files: tuple[Path, Path, Path]) -> None:
    """Non-UTF-8 content produces a configuration error rather than defaults."""
    defaults, override, schema = operator_files
    override.write_bytes(b"operators: \xff")
    with pytest.raises(OperatorConfigError, match="Cannot load"):
        load_operators(defaults, override, schema)


def test_dangling_override_symlink_fails(
    operator_files: tuple[Path, Path, Path],
) -> None:
    """A configured path that links to a missing file must not fall back silently."""
    defaults, override, schema = operator_files
    override.symlink_to(override.parent / "missing.yaml")
    with pytest.raises(OperatorConfigError, match="Cannot load"):
        load_operators(defaults, override, schema)


@pytest.mark.parametrize(
    ("content", "valid"),
    [
        (None, True),
        ("operators: []", True),
        (
            "operators: [{name: quay-operator, version: 3.15.10}, {name: multicluster-engine, channel: fast-test}]",
            True,
        ),
        ("operators: [{name: lvms-operator, version: 9.9.9}]", False),
        ("operators: [{name: quay-operator, seed: null}]", False),
    ],
)
@pytest.mark.parametrize("check_mode", [False, True])
def test_ansible_install_and_mirror_consumers(
    tmp_path: Path,
    mocker: MockerFixture,
    content: str | None,
    valid: bool,
    check_mode: bool,
    offline_cli_env: dict[str, str],
) -> None:
    """Exercise load-vars, seed selection, plugin collection and the real mirror template."""
    root = package_data_path("defaults", "operators.yaml").parent.parent
    shutil.copytree(root / "defaults", tmp_path / "defaults")
    shutil.copytree(root / "playbooks/common", tmp_path / "playbooks/common")
    shutil.copytree(root / "schemas", tmp_path / "schemas")
    plugin_dir = tmp_path / "plugins/lvms"
    plugin_dir.mkdir(parents=True)
    shutil.copy(root / "plugins/lvms/plugin.yaml", plugin_dir / "plugin.yaml")
    config_dir = tmp_path / "config"
    config_dir.mkdir()
    for filename in ("global.yaml", "certificates.yaml", "cloud_infra.yaml"):
        (config_dir / filename).write_text("{}")
    override = config_dir / "operators.yaml"
    if content is not None:
        override.write_text(content)
    output = tmp_path / "effective.json"
    mirror = tmp_path / "imageset.yaml"
    playbook = tmp_path / "playbooks/consumer.yaml"
    playbook.write_text(
        yaml.safe_dump([
            {
                "name": "Core operator consumer contract",
                "hosts": "localhost",
                "connection": "local",
                "gather_facts": False,
                "tasks": [
                    {
                        "name": "Load installation variables",
                        "ansible.builtin.include_tasks": "common/load-vars.yaml",
                    },
                    {
                        "name": "Collect plugin operators",
                        "ansible.builtin.include_tasks": str(
                            root / "playbooks/tasks/collect_core_plugin_operators.yaml"
                        ),
                    },
                    *_operator_upgrade_dry_run_tasks(root),
                    {
                        "name": "Combine catalogs as the mirror playbook does",
                        "ansible.builtin.set_fact": {
                            "catalog_operators": "{{ {'core': {'operators': operators, 'catalog': 'redhat'}} | combine(_core_plugin_operators) }}",
                        },
                    },
                    {
                        "name": "Render real mirror template",
                        "ansible.builtin.template": {
                            "src": str(
                                root / "templates/imagesetconfiguration.yaml.j2"
                            ),
                            "dest": str(mirror),
                            "mode": "0600",
                        },
                    },
                    {
                        "name": "Write installation and plugin inputs",
                        "ansible.builtin.copy": {
                            "content": "{{ {'operators': operators, 'seed': operators | selectattr('seed', 'defined') | selectattr('seed') | list, 'plugins': _core_plugin_operators, 'upgrade': r_operator_versions_upgrade} | to_json }}",
                            "dest": str(output),
                            "mode": "0600",
                        },
                    },
                ],
            }
        ])
    )
    result = subprocess.run(
        ["ansible-playbook", str(playbook), *(["--check"] if check_mode else [])],
        check=False,
        capture_output=True,
        text=True,
        env=offline_cli_env,
        cwd=tmp_path.parent,
        timeout=120,
    )
    if not valid:
        assert result.returncode != 0, result.stdout + result.stderr
        assert not output.exists()
        assert not mirror.exists()
        return
    assert result.returncode == 0, result.stdout + result.stderr
    if check_mode:
        return
    effective = json.loads(output.read_text())
    assert effective["upgrade"]["cmd"] == [
        "enclave",
        "reconcile",
        "operator-versions",
        "--use-defaults",
        *(["--operators-vars-file", str(override)] if content is not None else []),
        "--dry-run",
    ]
    upgrade_log = effective["upgrade"]["stderr"]
    expected = load_operators(
        tmp_path / "defaults/operators.yaml",
        override,
        tmp_path / "schemas/operators.yaml",
    )
    assert effective["operators"] == expected["operators"]
    for operator in expected["operators"]:
        assert (
            f"{operator['namespace']}/{operator['name']} channel={operator['channel']}"
            in upgrade_log
        )
    assert ("[UPDATE] quay-enterprise/quay-operator:3.15.10" in upgrade_log) == (
        content is not None and "3.15.10" in content
    )
    _assert_upgrade_matches(
        tmp_path / "defaults/operators.yaml", override, effective["operators"], mocker
    )
    assert [operator["name"] for operator in effective["seed"]] == [
        "multicluster-engine",
        "advanced-cluster-management",
    ]
    plugin_operators = yaml.safe_load((plugin_dir / "plugin.yaml").read_text())[
        "operators"
    ]
    assert effective["plugins"]["lvms"]["operators"] == plugin_operators
    catalogs = yaml.safe_load(mirror.read_text())["mirror"]["operators"]
    packages = catalogs[0]["packages"]
    for operator in expected["operators"]:
        package = next(
            package for package in packages if package["name"] == operator["name"]
        )
        assert package["defaultChannel"] == operator["channel"]
        assert package["channels"] == [
            {
                "name": operator["channel"],
                "minVersion": operator.get("init_version", operator["version"]),
                "maxVersion": operator["version"],
            }
        ]
    assert catalogs[1]["packages"][0]["name"] == plugin_operators[0]["name"]
    assert (
        catalogs[1]["packages"][0]["channels"][0]["maxVersion"]
        == plugin_operators[0]["version"]
    )
