from subprocess import CompletedProcess

import pytest

from reconcile.tests.fixtures import OcResultFactory


@pytest.fixture
def oc_result() -> OcResultFactory:
    def _make(
        stdout: str = "", returncode: int = 0, stderr: str = ""
    ) -> CompletedProcess[str]:
        result: CompletedProcess[str] = CompletedProcess(args=[], returncode=returncode)
        result.stdout = stdout
        result.stderr = stderr
        return result

    return _make
