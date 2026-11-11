"""Exercise directed network validation and real Ansible gate consumers safely."""

import copy
import importlib.util
import json
import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path
from typing import Any
from unittest.mock import patch

import yaml

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "scripts/validation/network_matrix.py"
SPEC = importlib.util.spec_from_file_location("network_matrix", SCRIPT)
MATRIX = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MATRIX)


def topology(stage: str = "preinstall") -> dict[str, Any]:
    """Build synthetic three-control-plane discovered inventory."""
    hosts = [
        {
            "name": f"master-{index}",
            "macAddress": f"02:00:00:00:00:0{index}",
            "ipAddress": f"192.0.2.{10 + index}",
        }
        for index in range(3)
    ]
    if stage == "preinstall":
        inventory = [
            {
                "requested_hostname": host["name"],
                "inventory": json.dumps({
                    "interfaces": [
                        {
                            "mac_address": host["macAddress"],
                            "ipv4_addresses": [host["ipAddress"] + "/24"],
                        }
                    ]
                }),
            }
            for host in hosts
        ]
    else:
        inventory = [
            {
                "metadata": {"name": host["name"]},
                "status": {
                    "addresses": [{"type": "InternalIP", "address": host["ipAddress"]}]
                },
            }
            for host in hosts
        ]
    return {
        "stage": stage,
        "hosts": hosts,
        "inventory": inventory,
        "machine_network": "192.0.2.0/24",
        "disconnected": True,
        "mirror": "mirror.example",
        "api_vip": "192.0.2.2",
        "ingress_vip": "192.0.2.3",
    }


class FakeProbe:
    """Record directed paths without contacting any infrastructure."""

    def __init__(
        self, access: bool = True, connect: bool | None = True, listening: bool = True
    ) -> None:
        """Choose independent access, response, and listener evidence."""
        self.access = access
        self.connection = connect
        self.listener = listening
        self.calls = []

    def accessible(self, node: dict[str, Any]) -> bool:
        """Require discovered addresses as well as fixture access."""
        return self.access and bool(node["address"])

    def connect(self, source: dict[str, Any], address: str, port: int) -> bool | None:
        """Record source, destination and port for direction assertions."""
        self.calls.append((source["name"], address, port))
        return self.connection

    def listening(self, _nodes: list[dict[str, Any]] | None, _port: int) -> bool:
        """Return independently configured service readiness evidence."""
        return self.listener


class NetworkMatrixTest(unittest.TestCase):
    """Verify stage semantics, safe identities, and fail-closed consumer ordering."""

    def test_disconnected_preinstall_has_three_node_origin_mirror_paths(self) -> None:
        """Test all configured node-to-mirror installation paths."""
        probe = FakeProbe()
        result, status = MATRIX.validate(topology(), probe)
        assert status == 0
        assert probe.calls == [
            (f"master-{index}", "mirror.example", 8443) for index in range(3)
        ]
        assert sum(row["status"] == "PASS" for row in result["results"]) == 3
        assert sum(row["status"] == "NOT TESTED" for row in result["results"]) == 3

    def test_connected_does_not_require_mirror_or_http(self) -> None:
        """Do not require optional services or claim unperformed paths passed."""
        config = topology()
        config["disconnected"] = False
        probe = FakeProbe(access=False)
        result, status = MATRIX.validate(config, probe)
        assert status == 0
        assert not probe.calls
        assert "PASS" not in [row["status"] for row in result["results"]]

    def test_postinstall_matrix_is_role_and_direction_aware(self) -> None:
        """Cover every applicable VIP and directed etcd peer connection."""
        probe = FakeProbe()
        result, status = MATRIX.validate(topology("postinstall"), probe)
        assert status == 0
        assert len(probe.calls) == 21
        assert sum(port in {2379, 2380} for _, _, port in probe.calls) == 12
        assert not any(port == 8443 for _, _, port in probe.calls)
        for row in result["results"]:
            assert row["status"] == "PASS"
            if row["service"] == "api":
                assert row["destination"] == "192.0.2.2"
            elif row["service"] == "ingress":
                assert row["destination"] == "192.0.2.3"
            else:
                assert row["source_address"] != row["destination"]

    def test_listener_absence_and_access_are_not_firewall_failures(self) -> None:
        """Keep missing access and unproven readiness distinct from path failure."""
        for probe in (
            FakeProbe(access=False),
            FakeProbe(connect=None),
            FakeProbe(connect=False, listening=False),
        ):
            with self.subTest(probe=probe):
                result, status = MATRIX.validate(topology(), probe)
                assert status == 2
                assert len([row for row in result["results"] if "source" in row]) == 3
                assert "FAIL" not in [row["status"] for row in result["results"]]

    def test_failures_collect_entire_matrix(self) -> None:
        """Collect every path instead of stopping at the first failure."""
        probe = FakeProbe(connect=False)
        result, status = MATRIX.validate(topology("postinstall"), probe)
        assert status == 1
        assert len(probe.calls) == 21
        assert all(row["status"] == "FAIL" for row in result["results"])

    def test_nmstate_mac_mapping_does_not_guess_address(self) -> None:
        """Match existing nmstate MACs but reject ambiguous discovered inventory."""
        config = topology()
        for host in config["hosts"]:
            del host["ipAddress"]
            host["mapInterfaces"] = [{"macAddress": host.pop("macAddress")}]
        for discovered in config["inventory"]:
            discovered.pop("requested_hostname")
        assert MATRIX.node_addresses(config)[0]["address"] == "192.0.2.10"
        config["inventory"].append(copy.deepcopy(config["inventory"][0]))
        assert MATRIX.node_addresses(config)[0]["address"] is None
        _result, status = MATRIX.validate(config, FakeProbe())
        assert status == 2

    def test_inventory_network_and_broadcast_addresses_are_unusable(self) -> None:
        """Do not treat reserved subnet addresses as usable node origins."""
        for reserved in ("192.0.2.0", "192.0.2.255"):
            config = topology()
            config["hosts"][0]["ipAddress"] = reserved
            config["inventory"][0]["inventory"] = json.dumps({
                "interfaces": [
                    {
                        "mac_address": config["hosts"][0]["macAddress"],
                        "ipv4_addresses": [reserved + "/24"],
                    }
                ]
            })
            assert MATRIX.node_addresses(config)[0]["address"] is None
            _result, status = MATRIX.validate(config, FakeProbe())
            assert status == 2

    def test_ssh_modes_and_commands_are_bounded(self) -> None:
        """Honor controller identity options and bound both TCP and SSH commands."""
        config = topology()
        config["ssh_public_key_path"] = "/nonexistent/public.pub"
        assert "-i" not in MATRIX.Probe(config).ssh
        config["ssh_key"] = "/explicit key"
        config["ssh_args"] = "-o IdentitiesOnly=yes"
        probe = MATRIX.Probe(config)
        assert "/explicit key" in probe.ssh
        source = {"name": "master-0", "address": "192.0.2.10"}
        with patch.object(MATRIX.subprocess, "run") as run:
            run.return_value = subprocess.CompletedProcess([], 0, "", "")
            assert probe.connect(source, "mirror.example", 8443)
            args = run.call_args.args[0]
            assert "core@192.0.2.10" in args
            assert "timeout 4 bash" in args[-1]
            assert "/dev/tcp" in args[-1]
            assert run.call_args.kwargs["timeout"] == 10
            run.side_effect = subprocess.TimeoutExpired("ssh", 10)
            assert probe.connect(source, "mirror.example", 8443) is None

    def test_existing_private_counterpart_only(self) -> None:
        """Use a conventional key only when its private counterpart exists."""
        with tempfile.TemporaryDirectory() as directory:
            key = Path(directory) / "node key"
            key.touch()
            config = topology()
            config["ssh_public_key_path"] = str(key) + ".pub"
            assert str(key) in MATRIX.Probe(config).ssh

    def test_executable_cli_with_controlled_ssh_fixture(self) -> None:
        """Run the actual JSON CLI with a harmless synthetic SSH executable."""
        with tempfile.TemporaryDirectory() as directory:
            ssh = Path(directory) / "ssh"
            ssh.write_text("#!/bin/sh\nexit 0\n")
            ssh.chmod(0o755)
            result = subprocess.run(
                ["python3", str(SCRIPT)],
                input=json.dumps(topology()),
                text=True,
                capture_output=True,
                timeout=10,
                check=False,
                env=dict(os.environ, PATH=directory + ":" + os.environ["PATH"]),
            )
            assert result.returncode == 0, result.stderr
            assert (
                sum(
                    row["status"] == "PASS"
                    for row in json.loads(result.stdout)["results"]
                )
                == 3
            )

    def test_actual_gate_and_postinstall_consumers(self) -> None:
        """Keep pre-install checks before the gate and post-install checks after wait."""
        pre = (ROOT / "playbooks/tasks/pre_install_validate_plugins.yaml").read_text()
        assert pre.index("include_tasks: validate_network_paths.yaml") < pre.index(
            "Release installation gate"
        )
        post = (ROOT / "playbooks/tasks/wait_for_deployment.yaml").read_text()
        assert post.index("agent wait-for install-complete") < post.index(
            "include_tasks: validate_network_paths.yaml"
        )
        tasks = (ROOT / "playbooks/tasks/validate_network_paths.yaml").read_text()
        assert "r_network_matrix.rc == 0" in tasks
        assert "no_log: true" in tasks

    def test_rendezvous_identity_tasks_support_public_only_and_explicit_keys(
        self,
    ) -> None:
        """Execute real identity selection tasks under supported SSH modes."""
        with tempfile.TemporaryDirectory() as directory:
            private = Path(directory) / "node identity"
            private.touch()
            public_only = Path(directory) / "public-key-without-suffix"
            public_only.touch()
            identity_tasks = yaml.safe_load(
                (ROOT / "playbooks/tasks/pre_install_validate_plugins.yaml").read_text()
            )[:3]
            for mode, expected in (
                ("agent", ""),
                ("counterpart", str(private)),
                ("explicit", str(private)),
            ):
                with self.subTest(mode=mode):
                    variables = {
                        "rendezvousIP": "192.0.2.10",
                        "sshPubPath": str(public_only),
                    }
                    if mode == "counterpart":
                        variables["sshPubPath"] = str(private) + ".pub"
                    if mode == "explicit":
                        variables["ansible_ssh_private_key_file"] = str(private)
                    playbook = [
                        {
                            "name": "Controlled SSH identity harness",
                            "hosts": "localhost",
                            "gather_facts": False,
                            "vars": variables,
                            "tasks": [
                                *identity_tasks,
                                {
                                    "name": "Verify selected identity",
                                    "ansible.builtin.assert": {
                                        "that": "(hostvars['rendezvous'].ansible_ssh_private_key_file | default('')) == "
                                        + json.dumps(expected)
                                    },
                                },
                            ],
                        }
                    ]
                    with tempfile.NamedTemporaryFile(
                        encoding="utf-8", mode="w", suffix=".yaml"
                    ) as harness:
                        yaml.safe_dump(playbook, harness)
                        harness.flush()
                        result = subprocess.run(
                            [
                                shutil.which("ansible-playbook"),
                                "-i",
                                "localhost,",
                                "-c",
                                "local",
                                harness.name,
                            ],
                            text=True,
                            capture_output=True,
                            timeout=45,
                            check=False,
                        )
                    assert result.returncode == 0, result.stdout + result.stderr

    def test_ansible_consumer_keeps_gate_closed_on_incomplete_matrix(self) -> None:
        """Execute the dynamic include and release a fixture gate only on success."""
        for ssh_status in (0, 255):
            with (
                self.subTest(ssh_status=ssh_status),
                tempfile.TemporaryDirectory() as directory,
            ):
                fixture = topology()
                ssh = Path(directory) / "ssh"
                ssh.write_text(f"#!/bin/sh\nexit {ssh_status}\n")
                ssh.chmod(0o755)
                gate = Path(directory) / "gate"
                variables = {
                    "network_validation_stage": "preinstall",
                    "agent_hosts": fixture["hosts"],
                    "discovered_hosts": fixture["inventory"],
                    "machineNetwork": fixture["machine_network"],
                    "disconnected": True,
                    "quayHostname": fixture["mirror"],
                    "apiVIP": fixture["api_vip"],
                    "ingressVIP": fixture["ingress_vip"],
                }
                playbook = [
                    {
                        "name": "Controlled network gate harness",
                        "hosts": "localhost",
                        "gather_facts": False,
                        "vars": variables,
                        "tasks": [
                            {
                                "name": "Run real network include",
                                "ansible.builtin.include_tasks": str(
                                    ROOT / "playbooks/tasks/validate_network_paths.yaml"
                                ),
                            },
                            {
                                "name": "Simulate gate release",
                                "ansible.builtin.copy": {
                                    "dest": str(gate),
                                    "content": "released",
                                    "mode": "0600",
                                },
                            },
                        ],
                    }
                ]
                with tempfile.NamedTemporaryFile(
                    encoding="utf-8", mode="w", suffix=".yaml", dir=ROOT / "playbooks"
                ) as harness:
                    yaml.safe_dump(playbook, harness)
                    harness.flush()
                    result = subprocess.run(
                        [
                            shutil.which("ansible-playbook"),
                            "-i",
                            "localhost,",
                            "-c",
                            "local",
                            harness.name,
                        ],
                        text=True,
                        capture_output=True,
                        timeout=60,
                        check=False,
                        env=dict(os.environ, PATH=directory + ":" + os.environ["PATH"]),
                    )
                assert (result.returncode == 0) == (ssh_status == 0), (
                    result.stdout + result.stderr
                )
                assert gate.exists() == (ssh_status == 0)
                if ssh_status:
                    assert "NOT TESTED" in result.stdout

    def test_invalid_topology_never_vacuously_passes(self) -> None:
        """Emit NOT TESTED JSON, never PASS, for missing or duplicate hosts."""
        for hosts in ([], [topology()["hosts"][0]] * 3):
            config = topology()
            config["hosts"] = hosts
            result = subprocess.run(
                ["python3", str(SCRIPT)],
                input=json.dumps(config),
                text=True,
                capture_output=True,
                timeout=10,
                check=False,
            )
            assert result.returncode == 2
            assert json.loads(result.stdout)["results"][0]["status"] == "NOT TESTED"


if __name__ == "__main__":
    unittest.main()
