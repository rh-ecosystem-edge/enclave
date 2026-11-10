"""Offline regression checks for operator channel preparation orchestration."""

from pathlib import Path
from typing import Any

import yaml

REPO_ROOT = Path(__file__).resolve().parents[2]
PREPARATION_FILE = "tasks/prepare_operator_channels.yaml"
OPERATOR_GATE = "upgrade_operators | default(true) | bool"
CLUSTER_GATE = "upgrade_mgmt_cluster | default(true) | bool"


def upgrade_tasks() -> list[dict[str, Any]]:
    """Read upgrade tasks without executing Ansible or accessing a cluster."""
    playbook = REPO_ROOT / "playbooks/upgrade.yaml"
    tasks: list[dict[str, Any]] = yaml.safe_load(playbook.read_text())[0]["tasks"]
    return tasks


def test_sync_and_day2_do_not_prepare_channels() -> None:
    """Content sync and untagged Day 2 must not stage Subscription channels."""
    for relative_path in ("sync.sh", "playbooks/06-day2.yaml"):
        content = (REPO_ROOT / relative_path).read_text()
        assert "prepare_operator_channels" not in content
        assert "prepare-operator-channels" not in content
        assert "--channel-only" not in content


def test_upgrade_is_the_only_preparation_caller() -> None:
    """Keep a single preparation include, outside Day 2 and migrations."""
    callers = [
        path.relative_to(REPO_ROOT).as_posix()
        for path in (REPO_ROOT / "playbooks").rglob("*.yaml")
        for line in path.read_text().splitlines()
        if PREPARATION_FILE in line
    ]
    assert callers == ["playbooks/upgrade.yaml"]


def test_upgrade_task_order() -> None:
    """Stage channels after Metal3 but before any cluster reconciliation."""
    assert [task["name"] for task in upgrade_tasks()] == [
        "Load configuration",
        "Run migrations",
        "Run metal3 upgrade tasks",
        "Prepare operator subscription channels",
        "Build management cluster upgrade arguments",
        "Upgrade management cluster OpenShift version",
        "Print management cluster upgrade output",
        "Upgrade operator versions",
        "Print operator versions upgrade output",
    ]


def test_preparation_inherits_kubeconfig_and_operator_gate() -> None:
    """Use the same default-true boolean gate as operator reconciliation."""
    tasks = {task["name"]: task for task in upgrade_tasks()}
    preparation = tasks["Prepare operator subscription channels"]
    assert preparation["ansible.builtin.include_tasks"] == {
        "file": PREPARATION_FILE,
        "apply": {
            "environment": {
                "KUBECONFIG": "{{ workingDir }}/ocp-cluster/auth/kubeconfig",
            },
        },
    }
    assert preparation["when"] == OPERATOR_GATE
    for name in ("Upgrade operator versions", "Print operator versions upgrade output"):
        assert tasks[name]["when"] == OPERATOR_GATE
    for name in (
        "Build management cluster upgrade arguments",
        "Upgrade management cluster OpenShift version",
        "Print management cluster upgrade output",
    ):
        assert tasks[name]["when"] == CLUSTER_GATE


def test_preparation_keeps_channel_only_command() -> None:
    """Reuse the existing non-approving helper without changing wait behavior."""
    helper = REPO_ROOT / "playbooks" / PREPARATION_FILE
    tasks = yaml.safe_load(helper.read_text())
    assert len(tasks) == 1
    assert tasks[0]["ansible.builtin.command"]["argv"] == [
        "enclave",
        "reconcile",
        "operator-versions",
        "--use-defaults",
        "--channel-only",
    ]
    assert tasks[0]["changed_when"] == (
        "'[UPDATE]' in r_operator_channel_preparation.stderr"
    )
