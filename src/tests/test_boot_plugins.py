"""Tests for boot plugin contract validation and the enclave plugins CLI."""

from collections.abc import Callable
from pathlib import Path

import pytest
from click.testing import CliRunner

from enclave.cli import cli as enclave_cli
from enclave.plugins import boot
from enclave.plugins.boot import validate_boot_plugin
from enclave.plugins.cli import cli

REPO_ROOT = Path(__file__).resolve().parents[2]

DEFAULTS_SCHEMA = """\
"$schema": "http://json-schema.org/draft-07/schema"
type: object
additionalProperties: false
properties:
  driver:
    type: string
    enum: [redfish]
required: [driver]
"""


def _write(path: Path, content: str) -> None:
    """Write content to path, creating its parent directories."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


@pytest.fixture
def plugin_dir(tmp_path: Path) -> Path:
    """A valid boot plugin under <tmp>/plugins/boot/sample."""
    root = tmp_path / "plugins" / "boot" / "sample"
    _write(
        root / "plugin.yaml",
        "name: sample\ntype: boot\ndescription: Sample boot plugin\ndiscoveryTimeout: 900\n",
    )
    _write(root / "defaults.yaml", "driver: redfish\n")
    _write(root / "schemas" / "defaults.yaml", DEFAULTS_SCHEMA)
    _write(root / "schemas" / "config.yaml", DEFAULTS_SCHEMA)
    for hook in ("validate", "setup", "boot", "post-install", "cleanup"):
        _write(
            root / "tasks" / f"{hook}.yaml",
            "- name: Noop\n  ansible.builtin.debug:\n    msg: noop\n",
        )
    return root


def test_valid_plugin(plugin_dir: Path) -> None:
    """Accept a plugin with every hook, defaults and schemas."""
    assert validate_boot_plugin(plugin_dir) == []


def test_plugin_with_lib(plugin_dir: Path) -> None:
    """Accept YAML task files in tasks/lib/."""
    _write(
        plugin_dir / "tasks" / "lib" / "helper.yaml",
        "- name: Noop\n  ansible.builtin.debug:\n    msg: noop\n",
    )
    assert validate_boot_plugin(plugin_dir) == []


def test_minimal_plugin(tmp_path: Path) -> None:
    """Accept a plugin with only a descriptor and tasks/boot.yaml."""
    root = tmp_path / "plugins" / "boot" / "minimal"
    _write(root / "plugin.yaml", "name: minimal\ntype: boot\ndescription: Minimal\n")
    _write(root / "tasks" / "boot.yaml", "[]\n")
    assert validate_boot_plugin(root) == []


@pytest.mark.parametrize(
    "plugin", sorted(p.name for p in (REPO_ROOT / "plugins" / "boot").iterdir())
)
def test_in_repo_boot_plugins(plugin: str) -> None:
    """Accept every boot plugin in the repository."""
    assert validate_boot_plugin(REPO_ROOT / "plugins" / "boot" / plugin) == []


def _rm(path: Path) -> None:
    """Remove the file at path."""
    path.unlink()


BrokenCase = tuple[Callable[[Path], object], str]

BROKEN_CASES: dict[str, BrokenCase] = {
    "missing descriptor": (lambda d: _rm(d / "plugin.yaml"), "missing plugin.yaml"),
    "descriptor not yaml": (
        lambda d: _write(d / "plugin.yaml", "name: [unclosed\n"),
        "plugin.yaml: cannot be read as YAML",
    ),
    "descriptor unknown field": (
        lambda d: _write(
            d / "plugin.yaml",
            "name: sample\ntype: boot\ndescription: x\ndefaults: {}\n",
        ),
        "Additional properties are not allowed",
    ),
    "descriptor missing type": (
        lambda d: _write(d / "plugin.yaml", "name: sample\ndescription: x\n"),
        "'type' is a required property",
    ),
    "descriptor wrong type": (
        lambda d: _write(
            d / "plugin.yaml", "name: sample\ntype: foundation\ndescription: x\n"
        ),
        "plugin.yaml: type: 'boot' was expected",
    ),
    "descriptor missing description": (
        lambda d: _write(d / "plugin.yaml", "name: sample\n"),
        "'description' is a required property",
    ),
    "descriptor timeout too low": (
        lambda d: _write(
            d / "plugin.yaml",
            "name: sample\ntype: boot\ndescription: x\ndiscoveryTimeout: 5\n",
        ),
        "plugin.yaml: discoveryTimeout: 5 is less than the minimum",
    ),
    "name mismatch": (
        lambda d: _write(
            d / "plugin.yaml", "name: other\ntype: boot\ndescription: x\n"
        ),
        "must match the directory name 'sample'",
    ),
    "missing boot hook": (
        lambda d: _rm(d / "tasks" / "boot.yaml"),
        "missing tasks/boot.yaml",
    ),
    "misspelled hook": (
        lambda d: (d / "tasks" / "post-install.yaml").rename(
            d / "tasks" / "post_install.yaml"
        ),
        "tasks/post_install.yaml: not a boot plugin hook",
    ),
    "hook not a task list": (
        lambda d: _write(d / "tasks" / "setup.yaml", "name: not a list\n"),
        "tasks/setup.yaml: must be a YAML list of tasks",
    ),
    "hook with non-mapping task": (
        lambda d: _write(d / "tasks" / "setup.yaml", "- just a string\n"),
        "tasks/setup.yaml: must be a YAML list of tasks",
    ),
    "lib file not yaml": (
        lambda d: _write(d / "tasks" / "lib" / "notes.txt", "notes\n"),
        "tasks/lib/notes.txt: only YAML task files are allowed in tasks/lib/",
    ),
    "lib subdirectory": (
        lambda d: _write(d / "tasks" / "lib" / "sub" / "x.yaml", "[]\n"),
        "tasks/lib/sub: only YAML task files are allowed in tasks/lib/",
    ),
    "lib file not a task list": (
        lambda d: _write(d / "tasks" / "lib" / "helper.yaml", "a: b\n"),
        "tasks/lib/helper.yaml: must be a YAML list of tasks",
    ),
    "other tasks subdirectory": (
        lambda d: (d / "tasks" / "helpers").mkdir(),
        "tasks/helpers: not a boot plugin hook",
    ),
    "hook not yaml": (
        lambda d: _write(d / "tasks" / "setup.yaml", "- [unclosed\n"),
        "setup.yaml: cannot be read as YAML",
    ),
    "unexpected schema file": (
        lambda d: _write(d / "schemas" / "host.yaml", DEFAULTS_SCHEMA),
        "schemas/host.yaml: unexpected entry",
    ),
    "invalid json schema": (
        lambda d: _write(d / "schemas" / "config.yaml", "type: 12\n"),
        "schemas/config.yaml: not a valid JSON Schema",
    ),
    "schema not a mapping": (
        lambda d: _write(d / "schemas" / "config.yaml", "- a\n"),
        "schemas/config.yaml: must be a YAML mapping",
    ),
    "defaults without schema": (
        lambda d: _rm(d / "schemas" / "defaults.yaml"),
        "must both exist or both be absent",
    ),
    "schema without defaults": (
        lambda d: _rm(d / "defaults.yaml"),
        "must both exist or both be absent",
    ),
    "defaults not matching schema": (
        lambda d: _write(d / "defaults.yaml", "driver: ipmi\n"),
        "defaults.yaml: driver: 'ipmi' is not one of ['redfish']",
    ),
    "defaults not yaml": (
        lambda d: _write(d / "defaults.yaml", "driver: [unclosed\n"),
        "defaults.yaml: cannot be read as YAML",
    ),
    "unresolvable reference in defaults schema": (
        lambda d: _write(
            d / "schemas" / "defaults.yaml",
            'properties:\n  driver:\n    "$ref": "#/definitions/missing"\n',
        ),
        "schemas/defaults.yaml: unresolvable reference",
    ),
    "unexpected top-level file": (
        lambda d: _write(d / "README.md", "docs\n"),
        "unexpected entry: README.md",
    ),
    "unexpected top-level directory": (
        lambda d: (d / "test-fixtures").mkdir(),
        "unexpected entry: test-fixtures",
    ),
}


@pytest.mark.parametrize("case", sorted(BROKEN_CASES))
def test_broken_plugin(plugin_dir: Path, case: str) -> None:
    """Report the expected error for each broken plugin."""
    breaker, expected = BROKEN_CASES[case]
    breaker(plugin_dir)
    errors = validate_boot_plugin(plugin_dir)
    assert any(expected in e for e in errors), errors


def test_plugin_outside_plugins_boot(tmp_path: Path) -> None:
    """Reject a plugin directory that is not under plugins/boot/."""
    root = tmp_path / "elsewhere" / "sample"
    _write(root / "plugin.yaml", "name: sample\ntype: boot\ndescription: x\n")
    _write(root / "tasks" / "boot.yaml", "[]\n")
    assert validate_boot_plugin(root) == [
        "the plugin directory must be under plugins/boot/"
    ]


def test_not_a_directory(tmp_path: Path) -> None:
    """Reject a path that is not a directory."""
    errors = validate_boot_plugin(tmp_path / "missing")
    assert len(errors) == 1
    assert "is not a directory" in errors[0]


def test_descriptor_schema_path_prefers_package_data(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Use the schema shipped as package data when it exists."""
    pkg = tmp_path / "site-packages" / "enclave"
    _write(pkg / "schemas" / "boot-plugin.yaml", "type: object\n")
    monkeypatch.setattr(boot, "__file__", str(pkg / "plugins" / "boot.py"))
    assert boot.descriptor_schema_path() == pkg / "schemas" / "boot-plugin.yaml"


def test_descriptor_schema_path_falls_back_to_repo() -> None:
    """Use the repository schema when there is no package data."""
    assert boot.descriptor_schema_path() == REPO_ROOT / "schemas" / "boot-plugin.yaml"


def test_cli_validate_valid(plugin_dir: Path) -> None:
    """Exit 0 for a valid plugin."""
    result = CliRunner().invoke(cli, ["validate", "--type", "boot", str(plugin_dir)])
    assert result.exit_code == 0, result.output


def test_cli_validate_invalid(
    plugin_dir: Path, caplog: pytest.LogCaptureFixture
) -> None:
    """Exit 1 and log the errors when a plugin is invalid."""
    _rm(plugin_dir / "tasks" / "boot.yaml")
    result = CliRunner().invoke(
        cli,
        [
            "validate",
            "--type",
            "boot",
            str(plugin_dir),
            str(REPO_ROOT / "plugins" / "boot" / "ironic"),
        ],
    )
    assert result.exit_code == 1
    assert "1 of 2 boot plugin(s) failed validation" in result.output
    assert "missing tasks/boot.yaml" in caplog.text


def test_cli_validate_requires_type(plugin_dir: Path) -> None:
    """Require --type."""
    result = CliRunner().invoke(cli, ["validate", str(plugin_dir)])
    assert result.exit_code == 2
    assert "--type" in result.output


def test_enclave_cli_has_plugins_group() -> None:
    """Expose the plugins group in the enclave CLI."""
    result = CliRunner().invoke(enclave_cli, ["plugins", "--help"])
    assert result.exit_code == 0
    assert "validate" in result.output
