import os
from pathlib import Path
import subprocess
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "scripts/validation/check_tmp_exec.sh"


class TemporaryExecutionTest(unittest.TestCase):
    def setUp(self):
        self.fixture = tempfile.TemporaryDirectory(prefix="enclave fixture ")
        self.addCleanup(self.fixture.cleanup)
        self.directory = Path(self.fixture.name)
        self.environment = dict(os.environ, TMPDIR=str(self.directory))

    def run_probe(self):
        return subprocess.run(
            ["bash", str(SCRIPT)],
            env=self.environment,
            capture_output=True,
            text=True,
            timeout=10,
        )

    def mock(self, name, body):
        tools = self.directory / "tools"
        tools.mkdir(exist_ok=True)
        command = tools / name
        command.write_text("#!/bin/bash\n" + body)
        command.chmod(0o755)
        self.environment["PATH"] = str(tools) + os.pathsep + os.environ["PATH"]

    def test_native_execution_with_spaces_and_cleanup(self):
        result = self.run_probe()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("PASS temporary native execution", result.stdout)
        self.assertEqual(list(self.directory.iterdir()), [])

    def test_creation_failure(self):
        self.environment["TMPDIR"] = str(self.directory / "absent")
        result = self.run_probe()
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("cannot create", result.stderr)
        self.assertNotIn("PASS", result.stdout)

    def test_copy_failure(self):
        self.mock("cp", "exit 1\n")
        result = self.run_probe()
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("cannot copy", result.stderr)
        self.assertEqual([entry.name for entry in self.directory.iterdir()], ["tools"])

    def test_execution_failure(self):
        self.mock("cp", '/bin/cp "$@"; chmod -x "${@: -1}"\n')
        result = self.run_probe()
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("native execution denied", result.stderr)
        self.assertIn("environment allowed by sudo", result.stderr)
        self.assertEqual([entry.name for entry in self.directory.iterdir()], ["tools"])

    def test_signal_cleanup(self):
        for name, status in (("TERM", 143), ("HUP", 129), ("INT", 130)):
            with self.subTest(signal=name):
                self.mock("cp", f'/bin/cp "$@"; kill -{name} "$PPID"\n')
                result = self.run_probe()
                self.assertEqual(result.returncode, status)
                self.assertEqual(
                    [entry.name for entry in self.directory.iterdir()], ["tools"]
                )

    def test_standalone_setup_consumers_stop_before_installers(self):
        self.mock("cp", "exit 1\n")
        for tool in ("dnf", "curl", "sudo"):
            self.mock(tool, 'echo "UNEXPECTED MUTATION" >&2; exit 95\n')
        for filename in ("setup_env.sh", "setup_ansible.sh"):
            with self.subTest(filename=filename):
                result = subprocess.run(
                    ["bash", str(ROOT / filename)],
                    env=self.environment,
                    capture_output=True,
                    text=True,
                    timeout=10,
                )
                self.assertNotEqual(result.returncode, 0)
                self.assertIn("cannot copy", result.stderr)
                self.assertNotIn("UNEXPECTED MUTATION", result.stderr)

    def test_all_setup_consumers_are_guarded_before_mutation(self):
        for filename, mutation in [
            ("setup_env.sh", "dnf install"),
            ("setup_ansible.sh", "UV_INSTALLER=$(mktemp)"),
            ("bootstrap.sh", "sudo bash -e ./setup_env.sh"),
            ("sync.sh", "sudo bash -e ./setup_env.sh"),
            ("Makefile", "@sudo bash ./setup_env.sh"),
            (
                "ci/deployment/install_enclave.sh",
                'ssh_exec "sudo $LZ_ENCLAVE_DIR/setup_env.sh"',
            ),
        ]:
            with self.subTest(filename=filename):
                content = (ROOT / filename).read_text()
                self.assertLess(
                    content.index("scripts/validation/check_tmp_exec.sh"),
                    content.index(mutation),
                )


if __name__ == "__main__":
    unittest.main()
