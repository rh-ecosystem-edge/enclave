import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "scripts/validation/check_ntp.sh"


class NtpTest(unittest.TestCase):
    def setUp(self):
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

    def query(self, sources):
        return subprocess.run(
            ["bash", str(SCRIPT)],
            input=json.dumps(sources),
            env=self.environment,
            capture_output=True,
            text=True,
            timeout=20,
        )

    def test_each_source_is_isolated_and_read_only(self):
        result = self.query(["private.example", "192.0.2.1", "2001:db8::1"])
        self.assertEqual(result.returncode, 0, result.stderr)
        arguments = self.log.read_text().splitlines()
        for index, server in enumerate(["private.example", "192.0.2.1", "2001:db8::1"]):
            self.assertEqual(
                arguments[index * 6 : index * 6 + 6],
                [
                    "-Q",
                    "-t",
                    "10",
                    "-u",
                    subprocess.check_output(["id", "-un"], text=True).strip(),
                    f"server {server} iburst",
                ],
            )
        self.assertNotIn("-q", arguments)
        self.assertEqual(result.stdout.count('status: "PASS"'), 3)

    def test_empty_and_unconfigured_never_query(self):
        for sources in ([], None):
            result = self.query(sources)
            self.assertEqual(result.returncode, 0)
            self.assertIn("SKIP", result.stdout)
            self.assertFalse(self.log.exists())

    def test_failure_does_not_hide_later_sources(self):
        result = self.query(["bad.example", "private.example"])
        self.assertEqual(result.returncode, 1)
        self.assertIn('name: "ntp_bad.example"\n  status: "FAIL"', result.stdout)
        self.assertIn('name: "ntp_private.example"\n  status: "PASS"', result.stdout)

    def test_config_injection_rejected_before_any_query(self):
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
                self.assertEqual(result.returncode, 1)
                self.assertFalse(self.log.exists())

    def test_missing_tool_is_not_pass(self):
        (self.directory / "chronyd").unlink()
        result = self.query(["private.example"])
        self.assertEqual(result.returncode, 2)
        self.assertIn("NOT TESTED", result.stdout)
        self.assertNotIn("PASS", result.stdout)

    def test_outer_timeout_is_bounded_and_not_a_pass(self):
        timeout = self.directory / "timeout"
        timeout.unlink()
        timeout.write_text('#!/bin/bash\n[[ "$1" == 15s ]] || exit 99\nexit 124\n')
        timeout.chmod(0o755)
        result = self.query(["private.example"])
        self.assertEqual(result.returncode, 1)
        self.assertIn('status: "FAIL"', result.stdout)
        self.assertFalse(self.log.exists())

    def test_consumer_and_package_prerequisite(self):
        self.assertIn(
            "scripts/validation/check_ntp.sh", (ROOT / "validations.sh").read_text()
        )
        self.assertIn("    chrony \\", (ROOT / "setup_env.sh").read_text())


if __name__ == "__main__":
    unittest.main()
