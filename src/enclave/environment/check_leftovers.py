"""Check for leftover resources from a previous installation."""

from __future__ import annotations

import logging
import re
import subprocess
from pathlib import Path

logger = logging.getLogger(__name__)

_METAL3_QUAY = re.compile(r"metal3|quay")
_METAL3_CONTAINERS = re.compile(r"metal3|ironic|httpd|baremetal-operator")


class LeftoverCheckError(Exception):
    """Raised when a leftover-check command exits with a non-zero status."""

    def __init__(
        self, returncode: int, cmd: list[str], stdout: str, stderr: str
    ) -> None:
        super().__init__(f"Command failed (exit {returncode}): {' '.join(cmd)}")
        self.returncode = returncode
        self.cmd = cmd
        self.stdout = stdout
        self.stderr = stderr


def _run(cmd: list[str]) -> list[str]:
    """Run a command and return its non-empty output lines, raising on failure."""
    result = subprocess.run(cmd, capture_output=True, text=True, check=False)
    if result.returncode != 0:
        logger.info(
            "Command failed (exit %d): %s\nstdout: %s\nstderr: %s",
            result.returncode,
            " ".join(cmd),
            (result.stdout or "").strip(),
            (result.stderr or "").strip(),
        )
        raise LeftoverCheckError(
            result.returncode, cmd, result.stdout or "", result.stderr or ""
        )
    return [line for line in result.stdout.splitlines() if line.strip()]


def check_systemd() -> bool:
    """Return True if any metal3 systemd unit files are present on the host."""
    try:
        lines = _run([
            "systemctl",
            "list-unit-files",
            "metal3-*.service",
            "--no-legend",
        ])
    except LeftoverCheckError as exc:
        if not exc.stdout.strip() and not exc.stderr.strip():
            logger.info(
                "systemctl exited %d with no output — treating as no metal3 units",
                exc.returncode,
            )
            return False
        raise

    if lines:
        logger.warning("Metal3 systemd units detected: %s", lines)
        return True
    logger.info("No metal3 systemd units found")

    return False


def check_podman(*, sudo: bool) -> bool:
    """Return True if any metal3/quay-related podman pods, containers, or volumes exist.

    Pass ``sudo=True`` to inspect the root podman context, ``sudo=False`` for the
    current user context.
    """
    prefix = ["sudo"] if sudo else []
    label = "Root" if sudo else "User"
    found = False

    pods = _run([*prefix, "podman", "pod", "ls", "--format", "{{.Name}}"])
    matching_pods = [n for n in pods if _METAL3_QUAY.search(n)]
    if matching_pods:
        logger.warning(
            "%s podman pods with metal3/quay detected: %s", label, matching_pods
        )
        found = True
    else:
        logger.info("%s podman pods: %d total, none matching", label, len(pods))

    containers = _run([*prefix, "podman", "ps", "-a", "--format", "{{.Names}}"])
    matching_containers = [n for n in containers if _METAL3_CONTAINERS.search(n)]
    if matching_containers:
        logger.warning(
            "%s podman containers with metal3 components detected: %s",
            label,
            matching_containers,
        )
        found = True
    else:
        logger.info(
            "%s podman containers: %d total, none matching", label, len(containers)
        )

    volumes = _run([*prefix, "podman", "volume", "ls", "--format", "{{.Name}}"])
    matching_volumes = [n for n in volumes if _METAL3_QUAY.search(n)]
    if matching_volumes:
        logger.warning(
            "%s podman volumes with metal3/quay detected: %s", label, matching_volumes
        )
        found = True
    else:
        logger.info("%s podman volumes: %d total, none matching", label, len(volumes))

    return found


def check_working_dir(working_dir: str | None) -> bool:
    """Return True if the bootstrap working directory exists and contains unexpected files.

    The ``logs/`` subdirectory is excluded from the check because bootstrap.sh
    creates it on every run, even for a simple check.
    """
    if not working_dir:
        return False
    path = Path(working_dir)
    if not path.is_dir():
        logger.info("Working directory %s does not exist", working_dir)
        return False

    # logs/ is created every time bootstrap.sh is run, even for a check, its
    # existence is not a sign of a complete bootstrap.sh run, so we will
    # return false even if it exists.
    items = [child for child in path.iterdir() if child.name != "logs"]
    if items:
        logger.warning(
            "Working directory %s is non-empty (%d items): %s",
            working_dir,
            len(items),
            [str(i.name) for i in items[:10]],
        )
        return True

    logger.info("Working directory %s is empty or contains only logs/", working_dir)
    return False


def main(working_dir: str | None = None) -> bool:
    """Return True if cleanup is needed, False if the environment is clean."""
    checks = [
        check_systemd(),
        check_podman(sudo=True),
        check_podman(sudo=False),
        check_working_dir(working_dir),
    ]
    cleanup_needed = any(checks)
    if cleanup_needed:
        logger.info("Cleanup is needed")
    else:
        logger.info("Environment is clean, no cleanup needed")

    return cleanup_needed
