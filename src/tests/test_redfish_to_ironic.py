"""Tests for the Redfish settings to ironic boot plugin migration."""

import logging
import stat
from pathlib import Path

import pytest
import yaml
from click.testing import CliRunner
from pytest_mock import MockerFixture

from enclave.cli import cli as enclave_cli
from enclave.migrations.cli import cli
from enclave.migrations.redfish_to_ironic import (
    IRONIC_HEADER,
    MigrationError,
    MigrationResult,
    RoundTripError,
    _guess_mapping_indent,
    _remove_bmc_fields_by_lines,
    _remove_bmc_fields_round_trip,
    _write,
    migrate,
)

REPO_ROOT = Path(__file__).resolve().parents[2]
SCHEMA = REPO_ROOT / "plugins" / "boot" / "ironic" / "schemas" / "config.yaml"

GLOBAL = """\
---
# Enclave configuration
workingDir: /home/enclave   # working dir
pullSecretPath: "{{ workingDir }}/config/pull-secret.json"
pullSecret: {"auths":{"quay.io":{"auth":"fake"}}}

# ============================================================================
# Cluster Hosts Configuration (Agent Hosts)
# ============================================================================
agent_hosts:
  - name: mgmt-ctl01              # Example: mgmt-ctl01
    macAddress: 0c:c4:7a:62:fe:ec
    ipAddress: 192.168.2.24       # Should match rendezvousIP for first node
    redfish: 192.168.1.101        # IPMI/Redfish management IP
    rootDisk: /dev/disk/by-path/pci-0000:00:11.4-ata-1.0
    redfishUser: admin
    redfishPassword: 'fake-bmc-password-a'
    # bmcSystemId: System.Embedded.1   # Optional
  - name: mgmt-ctl02
    macAddress: 0c:c4:7a:62:fe:ed
    redfish: "192.168.1.102:8000"
    redfishUser: admin
    redfishPassword: |
      fake-bmc-password-b
    bmcSystemId: abc-123
    mapInterfaces:
      - name: eno1
        macAddress: 0c:c4:7a:62:fe:ed
    networkConfig:
      interfaces:
        - name: eno1   # nic
  # Add a total of 3 agent hosts

  - name: mgmt-ctl03
    macAddress: 0c:c4:7a:62:fe:ee
    ipAddress: 192.168.2.26
    rootDisk: /dev/vda
    redfish: 192.168.1.103
    redfishUser: admin
    redfishPassword: Multi
      fake-bmc-password-c

# Quay
quayUser: admin
"""

EXPECTED_GLOBAL = """\
---
# Enclave configuration
workingDir: /home/enclave   # working dir
pullSecretPath: "{{ workingDir }}/config/pull-secret.json"
pullSecret: {"auths":{"quay.io":{"auth":"fake"}}}

# ============================================================================
# Cluster Hosts Configuration (Agent Hosts)
# ============================================================================
agent_hosts:
  - name: mgmt-ctl01              # Example: mgmt-ctl01
    macAddress: 0c:c4:7a:62:fe:ec
    ipAddress: 192.168.2.24       # Should match rendezvousIP for first node
    rootDisk: /dev/disk/by-path/pci-0000:00:11.4-ata-1.0
    # bmcSystemId: System.Embedded.1   # Optional
  - name: mgmt-ctl02
    macAddress: 0c:c4:7a:62:fe:ed
    mapInterfaces:
      - name: eno1
        macAddress: 0c:c4:7a:62:fe:ed
    networkConfig:
      interfaces:
        - name: eno1   # nic
  # Add a total of 3 agent hosts

  - name: mgmt-ctl03
    macAddress: 0c:c4:7a:62:fe:ee
    ipAddress: 192.168.2.26
    rootDisk: /dev/vda

# Quay
quayUser: admin
"""

EXPECTED_HOSTS = {
    "mgmt-ctl01": {
        "address": "192.168.1.101",
        "username": "admin",
        "password": "fake-bmc-password-a",
    },
    "mgmt-ctl02": {
        "address": "192.168.1.102:8000",
        "username": "admin",
        "password": "fake-bmc-password-b\n",
        "systemId": "abc-123",
    },
    "mgmt-ctl03": {
        "address": "192.168.1.103",
        "username": "admin",
        "password": "Multi fake-bmc-password-c",
    },
}


@pytest.fixture
def paths(tmp_path: Path) -> tuple[Path, Path]:
    config = tmp_path / "config"
    config.mkdir()
    global_path = config / "global.yaml"
    global_path.write_text(GLOBAL, encoding="utf-8")
    global_path.chmod(0o640)
    return global_path, config / "plugins" / "boot" / "ironic.yaml"


def _backups(path: Path) -> list[Path]:
    return sorted(path.parent.glob(f"{path.name}.*~"))


def test_migrate_keeps_global_byte_for_byte(paths: tuple[Path, Path]) -> None:
    global_path, ironic_path = paths
    result = migrate(global_path, ironic_path, SCHEMA)

    assert global_path.read_text(encoding="utf-8") == EXPECTED_GLOBAL
    ironic_text = ironic_path.read_text(encoding="utf-8")
    assert ironic_text.startswith(IRONIC_HEADER)
    assert yaml.safe_load(ironic_text) == {"hosts": EXPECTED_HOSTS}
    assert result.hosts == list(EXPECTED_HOSTS)
    assert result.written == [ironic_path, global_path]
    assert result.rewritten == []
    assert result.changed
    assert stat.S_IMODE(ironic_path.stat().st_mode) == 0o600
    assert stat.S_IMODE(global_path.stat().st_mode) == 0o640
    assert [b.read_text(encoding="utf-8") for b in _backups(global_path)] == [GLOBAL]
    assert _backups(ironic_path) == []


def test_migrate_is_idempotent(paths: tuple[Path, Path]) -> None:
    global_path, ironic_path = paths
    migrate(global_path, ironic_path, SCHEMA)
    ironic_text = ironic_path.read_text(encoding="utf-8")

    result = migrate(global_path, ironic_path, SCHEMA)

    assert not result.changed
    assert global_path.read_text(encoding="utf-8") == EXPECTED_GLOBAL
    assert ironic_path.read_text(encoding="utf-8") == ironic_text


def test_migrate_after_interrupted_run(paths: tuple[Path, Path]) -> None:
    global_path, ironic_path = paths
    migrate(global_path, ironic_path, SCHEMA)
    ironic_text = ironic_path.read_text(encoding="utf-8")
    global_path.write_text(GLOBAL, encoding="utf-8")

    result = migrate(global_path, ironic_path, SCHEMA)

    assert result.written == [global_path]
    assert global_path.read_text(encoding="utf-8") == EXPECTED_GLOBAL
    assert ironic_path.read_text(encoding="utf-8") == ironic_text


def test_migrate_merges_into_existing_ironic_config(
    paths: tuple[Path, Path],
) -> None:
    global_path, ironic_path = paths
    ironic_path.parent.mkdir(parents=True)
    existing = (
        "---\n"
        "# my ironic settings\n"
        "driver: redfish   # only redfish\n"
        "hosts:\n"
        "  mgmt-ctl01:   # same values\n"
        "    address: 192.168.1.101\n"
        "    username: admin\n"
        "    password: fake-bmc-password-a\n"
        "  other:\n"
        "    address: 10.0.0.9\n"
        "    username: root\n"
        "    password: x\n"
    )
    ironic_path.write_text(existing, encoding="utf-8")
    ironic_path.chmod(0o600)

    migrate(global_path, ironic_path, SCHEMA)

    text = ironic_path.read_text(encoding="utf-8")
    assert text.startswith(existing)
    assert yaml.safe_load(text) == {
        "driver": "redfish",
        "hosts": {
            "mgmt-ctl01": EXPECTED_HOSTS["mgmt-ctl01"],
            "other": {"address": "10.0.0.9", "username": "root", "password": "x"},
            "mgmt-ctl02": EXPECTED_HOSTS["mgmt-ctl02"],
            "mgmt-ctl03": EXPECTED_HOSTS["mgmt-ctl03"],
        },
    }
    assert [b.read_text(encoding="utf-8") for b in _backups(ironic_path)] == [existing]


def test_migrate_adds_hosts_to_ironic_config_without_hosts(
    paths: tuple[Path, Path],
) -> None:
    global_path, ironic_path = paths
    ironic_path.parent.mkdir(parents=True)
    ironic_path.write_text("# only a comment\ndriver: redfish\n", encoding="utf-8")

    migrate(global_path, ironic_path, SCHEMA)

    text = ironic_path.read_text(encoding="utf-8")
    assert text.startswith("# only a comment\ndriver: redfish\n")
    assert yaml.safe_load(text)["hosts"] == EXPECTED_HOSTS


def test_migrate_conflict_changes_nothing(paths: tuple[Path, Path]) -> None:
    global_path, ironic_path = paths
    ironic_path.parent.mkdir(parents=True)
    existing = "hosts:\n  mgmt-ctl02:\n    address: 1.1.1.1\n    username: a\n    password: b\n"
    ironic_path.write_text(existing, encoding="utf-8")

    with pytest.raises(MigrationError, match=r"mgmt-ctl02 that differ"):
        migrate(global_path, ironic_path, SCHEMA)

    assert global_path.read_text(encoding="utf-8") == GLOBAL
    assert ironic_path.read_text(encoding="utf-8") == existing
    assert _backups(global_path) == []


def test_migrate_invalid_result_changes_nothing(paths: tuple[Path, Path]) -> None:
    global_path, ironic_path = paths
    global_path.write_text(
        "agent_hosts:\n  - name: m0\n    redfish: 10.0.0.1\n    redfishUser: admin\n",
        encoding="utf-8",
    )

    with pytest.raises(MigrationError, match=r"hosts/m0: 'password' is a required"):
        migrate(global_path, ironic_path, SCHEMA)

    assert not ironic_path.exists()


def test_migrate_without_redfish_settings(paths: tuple[Path, Path]) -> None:
    global_path, ironic_path = paths
    global_path.write_text(EXPECTED_GLOBAL, encoding="utf-8")

    result = migrate(global_path, ironic_path, SCHEMA)

    assert not result.changed
    assert not ironic_path.exists()
    assert global_path.read_text(encoding="utf-8") == EXPECTED_GLOBAL


@pytest.mark.parametrize(
    ("global_text", "message"),
    [
        ("- a\n", "expected a YAML mapping"),
        ("a: [unclosed\n", "cannot be parsed as YAML"),
        ("agent_hosts:\n  - redfish: 1.1.1.1\n", "has no name"),
    ],
)
def test_migrate_invalid_global(
    paths: tuple[Path, Path], global_text: str, message: str
) -> None:
    global_path, ironic_path = paths
    global_path.write_text(global_text, encoding="utf-8")
    with pytest.raises(MigrationError, match=message):
        migrate(global_path, ironic_path, SCHEMA)


@pytest.mark.parametrize(
    ("ironic_text", "message"),
    [("- a\n", "expected a YAML mapping"), ("hosts: [a]\n", "hosts must be")],
)
def test_migrate_invalid_ironic_config(
    paths: tuple[Path, Path], ironic_text: str, message: str
) -> None:
    global_path, ironic_path = paths
    ironic_path.parent.mkdir(parents=True)
    ironic_path.write_text(ironic_text, encoding="utf-8")
    with pytest.raises(MigrationError, match=message):
        migrate(global_path, ironic_path, SCHEMA)


def test_migrate_missing_global(tmp_path: Path) -> None:
    with pytest.raises(MigrationError, match="cannot read"):
        migrate(tmp_path / "missing.yaml", tmp_path / "ironic.yaml", SCHEMA)


def test_migrate_unreadable_schema(paths: tuple[Path, Path], tmp_path: Path) -> None:
    global_path, ironic_path = paths
    with pytest.raises(MigrationError, match="cannot read"):
        migrate(global_path, ironic_path, tmp_path / "missing-schema.yaml")


ROUND_TRIP_GLOBAL = """\
---
agent_hosts:
  - redfish: 10.0.0.1
    name: m0
    redfishUser: admin
    redfishPassword: fake-bmc-password-a
    networkConfig:
      interfaces:
        - name: eno1   # nic
    bmcSystemId: abc
    # kept after the last key
  # kept between hosts
  - name: m1
    macAddress: aa:00:00:00:00:01
"""


def test_migrate_moves_item_indicator_to_first_remaining_key(
    paths: tuple[Path, Path], caplog: pytest.LogCaptureFixture
) -> None:
    global_path, ironic_path = paths
    global_path.write_text(ROUND_TRIP_GLOBAL, encoding="utf-8")

    result = migrate(global_path, ironic_path, SCHEMA)

    assert result.rewritten == []
    assert global_path.read_text(encoding="utf-8") == (
        "---\n"
        "agent_hosts:\n"
        "  - name: m0\n"
        "    networkConfig:\n"
        "      interfaces:\n"
        "        - name: eno1   # nic\n"
        "    # kept after the last key\n"
        "  # kept between hosts\n"
        "  - name: m1\n"
        "    macAddress: aa:00:00:00:00:01\n"
    )
    assert "formatting changed" not in caplog.text


@pytest.mark.parametrize(
    "global_text",
    [
        "agent_hosts:\n  - redfish: 1.1.1.1\n    redfishUser: a\n    redfishPassword: b\n",
        (
            "agent_hosts:\n  - redfish: 1.1.1.1\n    redfishUser: a\n    redfishPassword: b\n"
            "  - name: m0\n"
        ),
    ],
)
def test_line_removal_refuses_entries_it_cannot_restart(global_text: str) -> None:
    with pytest.raises(RoundTripError):
        _remove_bmc_fields_by_lines(global_text)


def test_migrate_falls_back_to_round_trip(
    paths: tuple[Path, Path], caplog: pytest.LogCaptureFixture, mocker: MockerFixture
) -> None:
    global_path, ironic_path = paths
    global_path.write_text(ROUND_TRIP_GLOBAL, encoding="utf-8")
    mocker.patch(
        "enclave.migrations.redfish_to_ironic._remove_bmc_fields_by_lines",
        side_effect=RoundTripError("forced"),
    )

    with caplog.at_level(logging.INFO):
        result = migrate(global_path, ironic_path, SCHEMA)

    text = global_path.read_text(encoding="utf-8")
    assert result.rewritten == []
    assert "# kept after the last key" in text
    assert "# kept between hosts" in text
    assert "# nic" in text
    assert yaml.safe_load(text) == {
        "agent_hosts": [
            {"name": "m0", "networkConfig": {"interfaces": [{"name": "eno1"}]}},
            {"name": "m1", "macAddress": "aa:00:00:00:00:01"},
        ]
    }
    assert "formatting changed beyond the migrated settings" in caplog.text
    assert "Changes to" not in caplog.text
    assert "fake-bmc-password-a" not in caplog.text


def test_round_trip_refuses_comments_after_first_key() -> None:
    with pytest.raises(RoundTripError, match="first key"):
        _remove_bmc_fields_round_trip(
            "agent_hosts:\n  - redfish: 1.1.1.1\n    # after\n    name: m0\n"
        )


def test_migrate_falls_back_to_rewrite(
    paths: tuple[Path, Path], caplog: pytest.LogCaptureFixture, mocker: MockerFixture
) -> None:
    global_path, ironic_path = paths
    original = (
        "# a comment that is lost\n"
        "agent_hosts:\n"
        "  - redfish: 10.0.0.1\n"
        "    # comment after the first key\n"
        "    name: m0\n"
        "    redfishUser: admin\n"
        "    redfishPassword: fake-bmc-password-a\n"
    )
    global_path.write_text(original, encoding="utf-8")
    mocker.patch(
        "enclave.migrations.redfish_to_ironic._remove_bmc_fields_by_lines",
        return_value="not: the expected data\n",
    )

    with caplog.at_level(logging.INFO):
        result = migrate(global_path, ironic_path, SCHEMA)

    assert result.rewritten == [global_path]
    text = global_path.read_text(encoding="utf-8")
    assert "#" not in text
    assert yaml.safe_load(text) == {"agent_hosts": [{"name": "m0"}]}
    assert [b.read_text(encoding="utf-8") for b in _backups(global_path)] == [original]
    assert "rewriting it from its data" in caplog.text
    assert "Changes to" not in caplog.text
    assert "fake-bmc-password-a" not in caplog.text


def test_migrate_logs_removed_fields_without_values(
    paths: tuple[Path, Path], caplog: pytest.LogCaptureFixture
) -> None:
    global_path, ironic_path = paths
    with caplog.at_level(logging.INFO):
        migrate(global_path, ironic_path, SCHEMA)
    assert (
        "removed the lines of redfish, redfishUser, redfishPassword, bmcSystemId "
        "from agent_hosts entry mgmt-ctl02"
    ) in caplog.text
    for value in (
        "fake-bmc-password-a",
        "fake-bmc-password-b",
        "fake-bmc-password-c",
        "192.168.1.101",
        "fake",
    ):
        assert value not in caplog.text


@pytest.mark.parametrize(
    "global_text",
    [
        # Unchanged secret right next to the removed fields.
        (
            "agent_hosts:\n"
            "  - name: m0\n"
            "    redfish: 10.0.0.1\n"
            "    redfishUser: admin\n"
            "    redfishPassword: fake-secret-one\n"
            'pullSecret: {"auths":{"quay.io":{"auth":"fake-secret-two"}}}\n'
        ),
        # Block scalar password with a blank line in it.
        (
            "agent_hosts:\n"
            "  - name: m0\n"
            "    redfish: 10.0.0.1\n"
            "    redfishUser: admin\n"
            "    redfishPassword: |\n"
            "      fake-secret-one\n"
            "\n"
            "      fake-secret-two\n"
            "    rootDisk: /dev/vda\n"
        ),
    ],
)
def test_migrate_exact_edit_never_logs_secrets(
    paths: tuple[Path, Path], caplog: pytest.LogCaptureFixture, global_text: str
) -> None:
    global_path, ironic_path = paths
    global_path.write_text(global_text, encoding="utf-8")

    with caplog.at_level(logging.DEBUG):
        result = migrate(global_path, ironic_path, SCHEMA)

    assert result.rewritten == []
    assert "formatting changed" not in caplog.text
    assert "removed the lines of redfish" in caplog.text
    assert "fake-secret" not in caplog.text


def test_migrate_never_logs_flow_style_passwords_or_other_secrets(
    paths: tuple[Path, Path], caplog: pytest.LogCaptureFixture
) -> None:
    global_path, ironic_path = paths
    global_path.write_text(
        'pullSecret: {"auths":{"quay.io":{"auth":"fake-pull-secret"}}}\n'
        "agent_hosts:\n"
        "  - {name: m0, redfish: 10.0.0.1, redfishUser: admin, "
        "redfishPassword: fake-flow-password}\n",
        encoding="utf-8",
    )

    with caplog.at_level(logging.INFO):
        result = migrate(global_path, ironic_path, SCHEMA)

    assert yaml.safe_load(global_path.read_text(encoding="utf-8"))["agent_hosts"] == [
        {"name": "m0"}
    ]
    assert result.rewritten == []
    assert f"compare it with the backup {result.backups[-1]}" in caplog.text
    assert "fake-flow-password" not in caplog.text
    assert "fake-pull-secret" not in caplog.text


def test_guess_mapping_indent() -> None:
    assert _guess_mapping_indent("a:\n    b: 1\n") == 4
    assert _guess_mapping_indent("a: 1\n- b\n") == 2


def test_write_cleans_up_on_failure(tmp_path: Path, mocker: MockerFixture) -> None:
    target = tmp_path / "file.yaml"
    mocker.patch.object(Path, "replace", side_effect=OSError("boom"))
    with pytest.raises(OSError, match="boom"):
        _write(target, "a: 1\n", MigrationResult())
    assert list(tmp_path.iterdir()) == []


def test_cli_changed_and_unchanged(paths: tuple[Path, Path]) -> None:
    global_path, ironic_path = paths
    args = [
        "redfish-to-ironic",
        "--global-config",
        str(global_path),
        "--ironic-config",
        str(ironic_path),
        "--ironic-schema",
        str(SCHEMA),
    ]
    first = CliRunner().invoke(cli, args)
    assert first.exit_code == 0, first.output
    assert first.stdout == "changed\n"
    second = CliRunner().invoke(cli, args)
    assert second.stdout == "unchanged\n"


def test_cli_error(paths: tuple[Path, Path]) -> None:
    global_path, ironic_path = paths
    global_path.write_text("- a\n", encoding="utf-8")
    result = CliRunner().invoke(
        enclave_cli,
        [
            "migrations",
            "redfish-to-ironic",
            "--global-config",
            str(global_path),
            "--ironic-config",
            str(ironic_path),
            "--ironic-schema",
            str(SCHEMA),
        ],
    )
    assert result.exit_code == 1
    assert "expected a YAML mapping" in result.output
