"""Tests for the add_enabled_plugin helper and the `enclave tools add-enabled-plugin` command."""

from pathlib import Path

import pytest
from click.testing import CliRunner
from pytest_mock import MockerFixture

from enclave.tools.add_enabled_plugin import (
    AddEnabledPluginError,
    add_enabled_plugin,
)
from enclave.tools.cli import cli

_CONFIG_WITH_COMMENTED_KEY = """\
# Global enclave config
storage_plugin: lvms

# Plugins to deploy during the pipeline (default: only the storage plugin above)
# enabled_plugins:
#   - lvms
#   - example
quayHostname: "quay.example.com"  # keep quoted
"""

_CONFIG_WITH_LIST = """\
storage_plugin: lvms
enabled_plugins:
  - lvms  # storage
  - clair-import
"""


def test_add_creates_key_when_absent(tmp_path: Path) -> None:
    """A commented-out key is treated as absent; the key is created."""
    config = tmp_path / "global.yaml"
    config.write_text(_CONFIG_WITH_COMMENTED_KEY, encoding="utf-8")

    assert add_enabled_plugin(config, "clair-import") is True

    text = config.read_text(encoding="utf-8")
    # Comments and the quoted value are preserved.
    assert "# Global enclave config" in text
    assert "#   - example" in text
    assert 'quayHostname: "quay.example.com"  # keep quoted' in text
    assert "enabled_plugins:\n  - clair-import\n" in text


def test_add_appends_to_existing_list(tmp_path: Path) -> None:
    """Appending to an existing list keeps earlier entries and their comments."""
    config = tmp_path / "global.yaml"
    config.write_text(_CONFIG_WITH_LIST, encoding="utf-8")

    assert add_enabled_plugin(config, "rhbk") is True

    text = config.read_text(encoding="utf-8")
    assert "- lvms  # storage" in text
    assert "- clair-import" in text
    assert "- rhbk" in text


def test_add_is_idempotent(tmp_path: Path) -> None:
    """A plugin already present is a no-op and leaves the file untouched."""
    config = tmp_path / "global.yaml"
    config.write_text(_CONFIG_WITH_LIST, encoding="utf-8")
    before = config.read_text(encoding="utf-8")

    assert add_enabled_plugin(config, "clair-import") is False
    assert config.read_text(encoding="utf-8") == before


def test_add_to_empty_file(tmp_path: Path) -> None:
    """An empty config file gets the key created."""
    config = tmp_path / "global.yaml"
    config.write_text("", encoding="utf-8")

    assert add_enabled_plugin(config, "clair-import") is True
    assert "enabled_plugins:\n  - clair-import\n" in config.read_text(encoding="utf-8")


def test_missing_file_raises(tmp_path: Path) -> None:
    """A missing config path raises AddEnabledPluginError."""
    config = tmp_path / "does-not-exist.yaml"
    with pytest.raises(AddEnabledPluginError, match="cannot read"):
        add_enabled_plugin(config, "clair-import")


def test_non_mapping_raises(tmp_path: Path) -> None:
    """A top-level non-mapping document raises AddEnabledPluginError."""
    config = tmp_path / "global.yaml"
    config.write_text("- just\n- a\n- list\n", encoding="utf-8")
    with pytest.raises(AddEnabledPluginError, match="expected a YAML mapping"):
        add_enabled_plugin(config, "clair-import")


def test_enabled_plugins_not_a_list_raises(tmp_path: Path) -> None:
    """A scalar enabled_plugins value raises AddEnabledPluginError."""
    config = tmp_path / "global.yaml"
    config.write_text("enabled_plugins: lvms\n", encoding="utf-8")
    with pytest.raises(AddEnabledPluginError, match="must be a list"):
        add_enabled_plugin(config, "clair-import")


def test_invalid_yaml_raises_without_leaking_content(tmp_path: Path) -> None:
    """A parse error reports the location only, never the file content."""
    config = tmp_path / "global.yaml"
    config.write_text(
        'secret_token: "do-not-leak-me"\nenabled_plugins: [unterminated\n',
        encoding="utf-8",
    )
    with pytest.raises(AddEnabledPluginError) as excinfo:
        add_enabled_plugin(config, "clair-import")
    message = str(excinfo.value)
    assert "invalid YAML" in message
    assert "do-not-leak-me" not in message


def test_add_follows_symlink_target(tmp_path: Path) -> None:
    """Editing through a symlink updates the target and keeps the link."""
    target = tmp_path / "shared" / "global.yaml"
    target.parent.mkdir()
    target.write_text(_CONFIG_WITH_LIST, encoding="utf-8")
    link = tmp_path / "global.yaml"
    link.symlink_to(target)

    assert add_enabled_plugin(link, "rhbk") is True

    assert "- rhbk" in target.read_text(encoding="utf-8")
    assert link.is_symlink()
    assert link.resolve() == target.resolve()


def test_write_failure_raises_and_preserves_original(
    tmp_path: Path, mocker: MockerFixture
) -> None:
    """A write error is wrapped and leaves the original config intact."""
    config = tmp_path / "global.yaml"
    config.write_text(_CONFIG_WITH_LIST, encoding="utf-8")
    mocker.patch("ruamel.yaml.YAML.dump", side_effect=OSError("disk full"))
    with pytest.raises(AddEnabledPluginError, match="cannot write"):
        add_enabled_plugin(config, "rhbk")
    # The original file is untouched (not truncated) and no temp files remain.
    assert config.read_text(encoding="utf-8") == _CONFIG_WITH_LIST
    assert list(tmp_path.iterdir()) == [config]


def test_cli_adds_plugin(tmp_path: Path) -> None:
    """The command adds the plugin and reports an update."""
    config = tmp_path / "global.yaml"
    config.write_text(_CONFIG_WITH_COMMENTED_KEY, encoding="utf-8")

    result = CliRunner().invoke(
        cli,
        ["add-enabled-plugin", "--config", str(config), "--plugin", "clair-import"],
    )
    assert result.exit_code == 0
    assert "[UPDATE]" in result.output
    assert "clair-import" in config.read_text(encoding="utf-8")


def test_cli_idempotent_reports_ok(tmp_path: Path) -> None:
    """Running twice reports [OK] on the second run."""
    config = tmp_path / "global.yaml"
    config.write_text(_CONFIG_WITH_LIST, encoding="utf-8")

    result = CliRunner().invoke(
        cli,
        ["add-enabled-plugin", "--config", str(config), "--plugin", "clair-import"],
    )
    assert result.exit_code == 0
    assert "[OK]" in result.output


def test_cli_rejects_invalid_plugin_name(tmp_path: Path) -> None:
    """An invalid plugin name is rejected before touching the file."""
    config = tmp_path / "global.yaml"
    config.write_text(_CONFIG_WITH_LIST, encoding="utf-8")

    result = CliRunner().invoke(
        cli,
        ["add-enabled-plugin", "--config", str(config), "--plugin", "Bad_Name"],
    )
    assert result.exit_code != 0
    assert "--plugin" in result.output


def test_cli_missing_config_errors(tmp_path: Path) -> None:
    """A non-existent --config path is rejected by Click's path check."""
    result = CliRunner().invoke(
        cli,
        [
            "add-enabled-plugin",
            "--config",
            str(tmp_path / "nope.yaml"),
            "--plugin",
            "clair-import",
        ],
    )
    assert result.exit_code != 0
