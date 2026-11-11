import copy
import importlib.util
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "scripts/validation/network_matrix.py"
SPEC = importlib.util.spec_from_file_location("network_matrix", SCRIPT)
MATRIX = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MATRIX)


def topology(stage="preinstall"):
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
    def __init__(self, access=True, connect=True, listening=True):
        self.access = access
        self.connection = connect
        self.listener = listening
        self.calls = []

    def accessible(self, node):
        return self.access and bool(node["address"])

    def connect(self, source, address, port):
        self.calls.append((source["name"], address, port))
        return self.connection

    def listening(self, nodes, port):
        return self.listener


class NetworkMatrixTest(unittest.TestCase):
    def test_disconnected_preinstall_has_three_node_origin_mirror_paths(self):
        probe = FakeProbe()
        result, status = MATRIX.validate(topology(), probe)
        self.assertEqual(status, 0)
        self.assertEqual(
            probe.calls,
            [(f"master-{index}", "mirror.example", 8443) for index in range(3)],
        )
        self.assertEqual(sum(row["status"] == "PASS" for row in result["results"]), 3)
        self.assertEqual(
            sum(row["status"] == "NOT TESTED" for row in result["results"]), 3
        )

    def test_connected_does_not_require_mirror_or_http(self):
        config = topology()
        config["disconnected"] = False
        probe = FakeProbe(access=False)
        result, status = MATRIX.validate(config, probe)
        self.assertEqual(status, 0)
        self.assertFalse(probe.calls)
        self.assertNotIn("PASS", [row["status"] for row in result["results"]])

    def test_postinstall_matrix_is_role_and_direction_aware(self):
        probe = FakeProbe()
        result, status = MATRIX.validate(topology("postinstall"), probe)
        self.assertEqual(status, 0)
        self.assertEqual(len(probe.calls), 21)
        self.assertEqual(sum(port in (2379, 2380) for _, _, port in probe.calls), 12)
        self.assertFalse(any(port == 8443 for _, _, port in probe.calls))
        for row in result["results"]:
            self.assertEqual(row["status"], "PASS")
            if row["service"] == "api":
                self.assertEqual(row["destination"], "192.0.2.2")
            elif row["service"] == "ingress":
                self.assertEqual(row["destination"], "192.0.2.3")
            else:
                self.assertNotEqual(row["source_address"], row["destination"])

    def test_listener_absence_and_access_are_not_firewall_failures(self):
        for probe in (
            FakeProbe(access=False),
            FakeProbe(connect=None),
            FakeProbe(connect=False, listening=False),
        ):
            with self.subTest(probe=probe):
                result, status = MATRIX.validate(topology(), probe)
                self.assertEqual(status, 2)
                self.assertEqual(
                    len([row for row in result["results"] if "source" in row]), 3
                )
                self.assertNotIn("FAIL", [row["status"] for row in result["results"]])

    def test_failures_collect_entire_matrix(self):
        probe = FakeProbe(connect=False)
        result, status = MATRIX.validate(topology("postinstall"), probe)
        self.assertEqual(status, 1)
        self.assertEqual(len(probe.calls), 21)
        self.assertTrue(all(row["status"] == "FAIL" for row in result["results"]))

    def test_nmstate_mac_mapping_does_not_guess_address(self):
        config = topology()
        for host in config["hosts"]:
            del host["ipAddress"]
            host["mapInterfaces"] = [{"macAddress": host.pop("macAddress")}]
        for discovered in config["inventory"]:
            discovered.pop("requested_hostname")
        self.assertEqual(MATRIX.node_addresses(config)[0]["address"], "192.0.2.10")
        config["inventory"].append(copy.deepcopy(config["inventory"][0]))
        self.assertIsNone(MATRIX.node_addresses(config)[0]["address"])
        result, status = MATRIX.validate(config, FakeProbe())
        self.assertEqual(status, 2)

    def test_ssh_modes_and_commands_are_bounded(self):
        config = topology()
        config["ssh_public_key_path"] = "/nonexistent/public.pub"
        self.assertNotIn("-i", MATRIX.Probe(config).ssh)
        config["ssh_key"] = "/explicit key"
        config["ssh_args"] = "-o IdentitiesOnly=yes"
        probe = MATRIX.Probe(config)
        self.assertIn("/explicit key", probe.ssh)
        source = {"name": "master-0", "address": "192.0.2.10"}
        with patch.object(MATRIX.subprocess, "run") as run:
            run.return_value = subprocess.CompletedProcess([], 0, "", "")
            self.assertTrue(probe.connect(source, "mirror.example", 8443))
            args = run.call_args.args[0]
            self.assertIn("core@192.0.2.10", args)
            self.assertIn("timeout 4 bash", args[-1])
            self.assertIn("/dev/tcp", args[-1])
            self.assertEqual(run.call_args.kwargs["timeout"], 10)
            run.side_effect = subprocess.TimeoutExpired("ssh", 10)
            self.assertIsNone(probe.connect(source, "mirror.example", 8443))

    def test_existing_private_counterpart_only(self):
        with tempfile.TemporaryDirectory() as directory:
            key = Path(directory) / "node key"
            key.touch()
            config = topology()
            config["ssh_public_key_path"] = str(key) + ".pub"
            self.assertIn(str(key), MATRIX.Probe(config).ssh)

    def test_executable_cli_with_controlled_ssh_fixture(self):
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
                env=dict(os.environ, PATH=directory + ":" + os.environ["PATH"]),
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(
                sum(
                    row["status"] == "PASS"
                    for row in json.loads(result.stdout)["results"]
                ),
                3,
            )

    def test_actual_gate_and_postinstall_consumers(self):
        pre = (ROOT / "playbooks/tasks/pre_install_validate_plugins.yaml").read_text()
        self.assertLess(
            pre.index("include_tasks: validate_network_paths.yaml"),
            pre.index("Release installation gate"),
        )
        post = (ROOT / "playbooks/tasks/wait_for_deployment.yaml").read_text()
        self.assertLess(
            post.index("agent wait-for install-complete"),
            post.index("include_tasks: validate_network_paths.yaml"),
        )
        tasks = (ROOT / "playbooks/tasks/validate_network_paths.yaml").read_text()
        self.assertIn("r_network_matrix.rc == 0", tasks)
        self.assertIn("no_log: true", tasks)

    def test_rendezvous_identity_tasks_support_public_only_and_explicit_keys(self):
        import yaml

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
                            "tasks": identity_tasks
                            + [
                                {
                                    "name": "Verify selected identity",
                                    "ansible.builtin.assert": {
                                        "that": "(hostvars['rendezvous'].ansible_ssh_private_key_file | default('')) == "
                                        + json.dumps(expected)
                                    },
                                }
                            ],
                        }
                    ]
                    with tempfile.NamedTemporaryFile(
                        mode="w", suffix=".yaml"
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
                        )
                    self.assertEqual(
                        result.returncode, 0, result.stdout + result.stderr
                    )

    def test_ansible_consumer_keeps_gate_closed_on_incomplete_matrix(self):
        import yaml

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
                    mode="w", suffix=".yaml", dir=ROOT / "playbooks"
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
                        env=dict(os.environ, PATH=directory + ":" + os.environ["PATH"]),
                    )
                self.assertEqual(
                    result.returncode == 0,
                    ssh_status == 0,
                    result.stdout + result.stderr,
                )
                self.assertEqual(gate.exists(), ssh_status == 0)
                if ssh_status:
                    self.assertIn("NOT TESTED", result.stdout)


if __name__ == "__main__":
    unittest.main()
