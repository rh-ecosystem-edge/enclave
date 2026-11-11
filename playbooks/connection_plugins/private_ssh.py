"""Use the standard SSH transport without verbose connection diagnostics."""

from collections.abc import Generator
from contextlib import contextmanager

from ansible import constants
from ansible.plugins.connection import ssh
from ansible.utils.display import Display

DOCUMENTATION = """
    name: private_ssh
    short_description: Standard SSH with protected transport diagnostics
    description:
        - Uses the builtin SSH transport without changing its options or trust policy.
        - Sensitive tasks must also set C(no_log=true) to protect their results.
    extends_documentation_fragment:
        - connection_pipelining
    notes:
        - All builtin SSH options are inherited at runtime. See C(ansible-doc -t connection ssh).
"""
DOCUMENTATION += "    options:" + ssh.DOCUMENTATION.split("    options:", 1)[1]


@contextmanager
def private_display() -> Generator[None, None, None]:
    """Suppress worker-local SSH traces, including a separately configured log."""
    display = Display()
    verbosity, log_verbosity = display.verbosity, display.log_verbosity
    debug = constants.DEFAULT_DEBUG
    warning = display.warning

    def transport_warning(*_args: object, **_kwargs: object) -> None:
        warning("Private SSH transport warning suppressed.")

    try:
        display.verbosity = 0
        display.log_verbosity = 0
        constants.DEFAULT_DEBUG = False
        display.warning = transport_warning
        yield
    finally:
        display.verbosity, display.log_verbosity = verbosity, log_verbosity
        constants.DEFAULT_DEBUG = debug
        display.warning = warning


class Connection(ssh.Connection):
    """Keep SSH options and trust behavior unchanged, protecting its diagnostics."""

    transport = "private_ssh"
    private_diagnostics = staticmethod(private_display)

    def exec_command(
        self, cmd: str, in_data: bytes | None = None, sudoable: bool = True
    ) -> tuple[int, bytes, bytes]:
        """Execute with standard SSH semantics inside the private display scope."""
        with private_display():
            return super().exec_command(cmd, in_data, sudoable)

    def put_file(self, in_path: str, out_path: str) -> tuple[int, bytes, bytes]:
        """Protect upload command diagnostics."""
        with private_display():
            return super().put_file(in_path, out_path)

    def fetch_file(self, in_path: str, out_path: str) -> tuple[int, bytes, bytes]:
        """Protect download command diagnostics."""
        with private_display():
            return super().fetch_file(in_path, out_path)

    def reset(self) -> None:
        """Protect control connection diagnostics."""
        with private_display():
            super().reset()
