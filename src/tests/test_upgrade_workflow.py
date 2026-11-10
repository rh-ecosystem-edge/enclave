"""Offline regression checks for the upgrade playbook's reconciliation tasks."""

from pathlib import Path
from typing import Any

import pytest
import yaml


@pytest.fixture
def upgrade_play() -> dict[str, Any]:
    """Load the real playbook without executing infrastructure tasks."""
    playbook = Path(__file__).resolve().parents[2] / "playbooks" / "upgrade.yaml"
    return yaml.safe_load(playbook.read_text(encoding="utf-8"))[0]


def test_upgrade_reconciliation_order(upgrade_play: dict[str, Any]) -> None:
    """Run the normal operator command once, after Metal3 and before management."""
    tasks = upgrade_play["tasks"]
    assert [task["name"] for task in tasks] == [
        "Load configuration",
        "Run migrations",
        "Run metal3 upgrade tasks",
        "Upgrade operator versions",
        "Print operator versions upgrade output",
        "Build management cluster upgrade arguments",
        "Upgrade management cluster OpenShift version",
        "Print management cluster upgrade output",
    ]
    commands = [
        task["ansible.builtin.command"]
        for task in tasks
        if "ansible.builtin.command" in task
    ]
    assert commands == [
        {"argv": ["enclave", "reconcile", "operator-versions", "--use-defaults"]},
        {"argv": "{{ mgmt_cluster_upgrade_argv }}"},
    ]


def test_upgrade_flags_and_arguments(upgrade_play: dict[str, Any]) -> None:
    """Preserve independent default-true gates and management upgrade arguments."""
    tasks = upgrade_play["tasks"]
    for task in tasks[3:5]:
        assert task["when"] == "upgrade_operators | default(true) | bool"
    for task in tasks[5:]:
        assert task["when"] == "upgrade_mgmt_cluster | default(true) | bool"
    assert tasks[5]["ansible.builtin.set_fact"]["mgmt_cluster_upgrade_argv"] == (
        "{{\n  ['enclave', 'reconcile', 'mgmt-cluster-version', '--use-defaults']\n"
        "  + (['--allow-not-recommended'] if (allow_not_recommended_upgrades | "
        "default(false) | bool) else [])\n}}"
    )


def test_upgrade_failure_is_not_ignored(upgrade_play: dict[str, Any]) -> None:
    """Keep reconciliation synchronous and let command failures stop the play."""
    for scope in [upgrade_play, *upgrade_play["tasks"]]:
        assert not {
            "ignore_errors",
            "ignore_unreachable",
            "failed_when",
            "async",
            "poll",
            "rescue",
            "block",
        }.intersection(scope)


def test_upgrade_output_and_environment(upgrade_play: dict[str, Any]) -> None:
    """Retain result registrations, output tasks and inherited kubeconfig."""
    tasks = upgrade_play["tasks"]
    assert tasks[3]["register"] == "r_operator_versions_upgrade"
    assert tasks[4]["ansible.builtin.debug"] == {
        "var": "r_operator_versions_upgrade.stderr_lines",
    }
    assert tasks[6]["register"] == "r_mgmt_cluster_upgrade"
    assert tasks[7]["ansible.builtin.debug"] == {
        "var": "r_mgmt_cluster_upgrade.stderr_lines",
    }
    assert "environment" not in upgrade_play
    assert all("environment" not in task for task in tasks[3:])
