"""CLI for environment management commands."""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import click
import yaml

from enclave.environment.check_leftovers import (
    LeftoverCheckError,
    main as check_leftovers_main,
)


@click.group()
def cli() -> None:
    """Enclave environment CLI."""


@cli.command("check-leftovers")
@click.option(
    "--working-dir",
    envvar="WORKING_DIR",
    default=None,
    help="Working directory path to check for leftover content.",
)
def check_leftovers_cmd(working_dir: str | None) -> None:
    """Check for leftover resources from a previous installation.

    Exits 1 if cleanup is needed, 0 if the environment is clean.
    """
    try:
        cleanup_needed = check_leftovers_main(working_dir=working_dir)
    except LeftoverCheckError as exc:
        cmd_str = " ".join(str(a) for a in exc.cmd)
        stdout = exc.stdout.strip()
        stderr = exc.stderr.strip()
        details = "\n".join(
            part
            for part in [
                f"stdout: {stdout}" if stdout else "",
                f"stderr: {stderr}" if stderr else "",
            ]
            if part
        )
        msg = f"Check command failed (exit {exc.returncode}): {cmd_str}"
        raise click.ClickException(f"{msg}\n{details}" if details else msg) from exc
    sys.exit(1 if cleanup_needed else 0)


@cli.command("cleanup")
@click.option(
    "--working-dir",
    envvar="WORKING_DIR",
    default=None,
    help="Working directory to wipe. Defaults to reading workingDir from config/global.yaml.",
)
def cleanup_cmd(working_dir: str | None) -> None:
    """Remove leftover resources from a previous installation."""
    resolved = working_dir or _read_working_dir_from_config()
    if resolved is None:
        raise click.ClickException(
            "Working directory not specified. Use --working-dir, set WORKING_DIR, "
            "or run from the enclave repo root where config/global.yaml is present."
        )
    script_path = Path(__file__).parent / "cleanup.sh"
    env = {**os.environ, "WORKING_DIR": resolved}
    try:
        result = subprocess.run(
            ["bash", str(script_path)], env=env, check=False, timeout=600
        )
    except subprocess.TimeoutExpired as exc:
        raise click.ClickException(
            "Cleanup script timed out after 600 seconds"
        ) from exc
    raise SystemExit(result.returncode)


def _read_working_dir_from_config() -> str | None:
    config_path = Path("config/global.yaml")
    if not config_path.is_file():
        return None
    try:
        with config_path.open(encoding="utf-8") as f:
            config: dict[str, object] = yaml.safe_load(f) or {}
        value = config.get("workingDir")
        return str(value) if value is not None else None
    except (OSError, yaml.YAMLError):
        return None
