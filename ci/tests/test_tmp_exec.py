"""Exercise native temporary execution and the real setup entry points."""

import os
import subprocess
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "scripts/validation/check_tmp_exec.sh"


class TemporaryExecutionTest(unittest.TestCase):
    """Run probes in isolated directories without any setup mutations."""

    def setUp(self) -> None:
        """Create a temporary environment, including a path containing spaces."""
        self.fixture = tempfile.TemporaryDirectory(prefix="enclave fixture ")
        self.addCleanup(self.fixture.cleanup)
        self.directory = Path(self.fixture.name)
        self.environment = dict(os.environ, TMPDIR=str(self.directory))

    def run_probe(self) -> subprocess.CompletedProcess[str]:
        """Execute the actual probe and preserve its failure status."""
        return subprocess.run(
            ["bash", str(SCRIPT)],
            env=self.environment,
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
        )

    def mock(self, name: str, body: str) -> None:
        """Prepend a controlled command fixture to the probe's PATH."""
        tools = self.directory / "tools"
        tools.mkdir(exist_ok=True)
        command = tools / name
        command.write_text("#!/bin/bash\n" + body)
        command.chmod(0o755)
        self.environment["PATH"] = str(tools) + os.pathsep + os.environ["PATH"]

    def test_native_execution_with_spaces_and_cleanup(self) -> None:
        """Execute a trusted native binary and leave no temporary files."""
        result = self.run_probe()
        assert result.returncode == 0, result.stderr
        assert "PASS temporary native execution" in result.stdout
        assert list(self.directory.iterdir()) == []

    def test_creation_failure(self) -> None:
        """Reject an unusable effective temporary directory."""
        self.environment["TMPDIR"] = str(self.directory / "absent")
        result = self.run_probe()
        assert result.returncode != 0
        assert "cannot create" in result.stderr
        assert "PASS" not in result.stdout

    def test_effective_temp_path_preserves_symlinks_and_literal_dollars(self) -> None:
        """Do not expand shell syntax embedded in an effective TMPDIR value."""
        target = self.directory / "literal $HOME and 'quotes'"
        target.mkdir()
        link = self.directory / "temp link"
        link.symlink_to(target, target_is_directory=True)
        self.environment["TMPDIR"] = str(link)
        result = self.run_probe()
        assert result.returncode == 0, result.stderr
        assert str(link) in result.stdout
        assert list(target.iterdir()) == []

    def test_unwritable_temp_path_fails_without_leaving_artifacts(self) -> None:
        """Reject directories whose permissions prevent creation."""
        target = self.directory / "read only"
        target.mkdir(mode=0o500)
        self.addCleanup(target.chmod, 0o700)
        self.environment["TMPDIR"] = str(target)
        result = self.run_probe()
        assert result.returncode != 0
        assert "cannot create" in result.stderr
        assert list(target.iterdir()) == []

    def test_ci_remote_probe_quotes_path_as_one_argument(self) -> None:
        """Pass spaces, quotes and literal dollars through the real CI line."""
        line = next(
            line
            for line in (ROOT / "ci/deployment/install_enclave.sh")
            .read_text()
            .splitlines()
            if line.startswith("printf -v tmp_probe_command")
        )
        path = self.directory / "remote '$HOME location"
        target = path / "scripts/validation"
        target.mkdir(parents=True)
        (target / "check_tmp_exec.sh").write_text("printf 'REMOTE PROBE OK\\n'\n")
        result = subprocess.run(
            ["bash", "-c", line + '\nbash -c "$tmp_probe_command"'],
            env=dict(self.environment, LZ_ENCLAVE_DIR=str(path)),
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
        )
        assert result.returncode == 0, result.stderr
        assert result.stdout == "REMOTE PROBE OK\n"

    def test_copy_failure(self) -> None:
        """Clean up after failing to copy the native executable."""
        self.mock("cp", "exit 1\n")
        result = self.run_probe()
        assert result.returncode != 0
        assert "cannot copy" in result.stderr
        assert [entry.name for entry in self.directory.iterdir()] == ["tools"]

    def test_execution_failure(self) -> None:
        """Fail with policy-preserving remediation when execution is denied."""
        self.mock("cp", '/bin/cp "$@"; chmod -x "${@: -1}"\n')
        result = self.run_probe()
        assert result.returncode != 0
        assert "native execution denied" in result.stderr
        assert "environment allowed by sudo" in result.stderr
        assert [entry.name for entry in self.directory.iterdir()] == ["tools"]

    def test_signal_cleanup(self) -> None:
        """Remove the probe directory after each handled signal."""
        for name, status in (("TERM", 143), ("HUP", 129), ("INT", 130)):
            with self.subTest(signal=name):
                self.mock("cp", f'/bin/cp "$@"; kill -{name} "$PPID"\n')
                result = self.run_probe()
                assert result.returncode == status
                assert [entry.name for entry in self.directory.iterdir()] == ["tools"]

    def test_standalone_setup_consumers_stop_before_installers(self) -> None:
        """Prevent standalone setup mutations when the initial probe fails."""
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
                    check=False,
                )
                assert result.returncode != 0
                assert "cannot copy" in result.stderr
                assert "UNEXPECTED MUTATION" not in result.stderr

    def test_all_setup_consumers_are_guarded_before_mutation(self) -> None:
        """Keep the guard before privileged setup in every actual caller."""
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
                assert content.index(
                    "scripts/validation/check_tmp_exec.sh"
                ) < content.index(mutation)


if __name__ == "__main__":
    unittest.main()
