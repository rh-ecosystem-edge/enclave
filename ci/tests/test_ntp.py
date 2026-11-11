"""Test configured NTP queries using an isolated, non-network command fixture."""

import json
import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "scripts/validation/check_ntp.sh"


class NtpTest(unittest.TestCase):
    """Verify query isolation, read-only options, and explicit failure semantics."""

    def setUp(self) -> None:
        """Make only fixture commands accessible to the shell under test."""
        self.fixture = tempfile.TemporaryDirectory()
        self.addCleanup(self.fixture.cleanup)
        self.directory = Path(self.fixture.name)
        self.log = self.directory / "calls"
        for tool in ("bash", "cat", "jq", "id", "timeout"):
            (self.directory / tool).symlink_to(shutil.which(tool))
        self.environment = dict(
            os.environ, PATH=str(self.directory), CALLS=str(self.log)
        )
        command = self.directory / "chronyd"
        command.write_text(
            '#!/bin/bash\nprintf "%s\\n" "$@" >> "$CALLS"\n'
            '[[ "${@: -1}" != *bad.example* ]]\n'
        )
        command.chmod(0o755)

    def query(
        self, sources: list[str | int] | str | None
    ) -> subprocess.CompletedProcess[str]:
        """Pass JSON configuration through the actual shell helper."""
        return subprocess.run(
            ["bash", str(SCRIPT)],
            input=json.dumps(sources),
            env=self.environment,
            capture_output=True,
            text=True,
            timeout=20,
            check=False,
        )

    def test_each_source_is_isolated_and_read_only(self) -> None:
        """Query each requested source alone without default configuration."""
        result = self.query(["private.example", "192.0.2.1", "2001:db8::1"])
        assert result.returncode == 0, result.stderr
        arguments = self.log.read_text().splitlines()
        for index, server in enumerate(["private.example", "192.0.2.1", "2001:db8::1"]):
            assert arguments[index * 6 : index * 6 + 6] == [
                "-Q",
                "-t",
                "10",
                "-u",
                subprocess.check_output(["id", "-un"], text=True).strip(),
                f"server {server} iburst",
            ]
        assert "-q" not in arguments
        assert result.stdout.count('status: "PASS"') == 3

    def test_empty_and_unconfigured_never_query(self) -> None:
        """Skip an absent or empty configured list without a public fallback."""
        for sources in ([], None):
            result = self.query(sources)
            assert result.returncode == 0
            assert "SKIP" in result.stdout
            assert not self.log.exists()

    def test_failure_does_not_hide_later_sources(self) -> None:
        """Report all configured sources even when an earlier one fails."""
        result = self.query(["bad.example", "private.example"])
        assert result.returncode == 1
        assert 'name: "ntp_bad.example"\n  status: "FAIL"' in result.stdout
        assert 'name: "ntp_private.example"\n  status: "PASS"' in result.stdout

    def test_config_injection_rejected_before_any_query(self) -> None:
        """Reject chrony directives, options, whitespace, and invalid types."""
        for sources in (
            ["good.example\nmakestep 1 1"],
            ["good.example iburst"],
            ["-q"],
            ["good.example\n"],
            [12],
            "public.example",
        ):
            with self.subTest(sources=sources):
                result = self.query(sources)
                assert result.returncode == 1
                assert not self.log.exists()

    def test_missing_tool_is_not_pass(self) -> None:
        """Report a missing standalone prerequisite as NOT TESTED."""
        (self.directory / "chronyd").unlink()
        result = self.query(["private.example"])
        assert result.returncode == 2
        assert "NOT TESTED" in result.stdout
        assert "PASS" not in result.stdout

    def test_empty_or_streamed_json_is_not_valid_configuration(self) -> None:
        """Reject malformed input before any configured source is contacted."""
        for raw in ("", '[]\n["private.example"]', '["private.example"]\nnull'):
            result = subprocess.run(
                ["bash", str(SCRIPT)],
                input=raw,
                env=self.environment,
                capture_output=True,
                text=True,
                timeout=10,
                check=False,
            )
            assert result.returncode == 1
            assert 'status: "FAIL"' in result.stdout
            assert not self.log.exists()

    def test_outer_timeout_is_bounded_and_not_a_pass(self) -> None:
        """Treat the process deadline as failure, not successful observation."""
        timeout = self.directory / "timeout"
        timeout.unlink()
        timeout.write_text('#!/bin/bash\n[[ "$1" == 15s ]] || exit 99\nexit 124\n')
        timeout.chmod(0o755)
        result = self.query(["private.example"])
        assert result.returncode == 1
        assert 'status: "FAIL"' in result.stdout
        assert not self.log.exists()

    def test_consumer_and_package_prerequisite(self) -> None:
        """Wire validation to the configured list and normal package setup."""
        assert (
            "scripts/validation/check_ntp.sh" in (ROOT / "validations.sh").read_text()
        )
        assert "    chrony \\" in (ROOT / "setup_env.sh").read_text()


if __name__ == "__main__":
    unittest.main()
