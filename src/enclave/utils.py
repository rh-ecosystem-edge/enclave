import logging
import os
import re
import subprocess
import sys
import time
from pathlib import Path

import click

logger = logging.getLogger(__name__)

LOG_LEVELS = ["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"]


def configure_logging(log_level: str) -> None:
    """Configure logging with the specified level if not already configured.

    This helper is shared across all CLI entry points to avoid duplication.
    Logging is only configured if the root logger has no handlers yet, so parent
    CLIs can set up logging once and subcommands will skip reconfiguration.
    """
    if not logging.getLogger().handlers:
        logging.basicConfig(
            level=getattr(logging, log_level.upper()),
            format="%(asctime)s %(levelname)-8s %(message)s",
            datefmt="%Y-%m-%dT%H:%M:%S",
            stream=sys.stderr,
        )


class KubeconfigNotFoundError(RuntimeError):
    pass


def setup_kubeconfig() -> None:
    """Ensure KUBECONFIG is set before running cluster commands.

    If KUBECONFIG is already set, nothing happens. Otherwise tries
    ~/.config/enclave/kubeconfig (symlinked by the deploy playbook).
    Raises KubeconfigNotFoundError with a helpful message if neither is available.
    """
    if os.environ.get("KUBECONFIG"):
        return

    fallback = Path.home() / ".config" / "enclave" / "kubeconfig"
    if fallback.exists():
        os.environ["KUBECONFIG"] = str(fallback)
        logger.debug("KUBECONFIG not set; using %s", fallback)
        return

    raise KubeconfigNotFoundError(
        "KUBECONFIG is not set and ~/.config/enclave/kubeconfig does not exist.\n"
        "Set KUBECONFIG to your kubeconfig file, for example:\n"
        "  export KUBECONFIG=<enclave-workingDir>/ocp-cluster/auth/kubeconfig"
    )


class KubeconfigGroup(click.Group):
    def parse_args(self, ctx: click.Context, args: list[str]) -> list[str]:
        remaining = super().parse_args(ctx, args)
        # ctx.args holds the subcommand's own args (empty when no subcommand was given, or
        # when a bare subcommand name was given with no further args). Only check kubeconfig
        # when there are actual subcommand args — otherwise Click will show help and kubeconfig
        # is irrelevant. Also skip during shell-completion parsing and for --help.
        if not ctx.resilient_parsing and ctx.args:
            help_flags = set(ctx.help_option_names or ["--help", "-h"])
            if not any(f in ctx.args for f in help_flags):
                try:
                    setup_kubeconfig()
                except KubeconfigNotFoundError as exc:
                    raise click.ClickException(str(exc)) from exc
        return remaining


def parse_jsonpath_value(raw: str) -> str:
    """Strip surrounding whitespace and quotes from a JSONPath output value."""
    return raw.strip().strip("'\"")


def semver_key(v_string: str) -> tuple[tuple[int, ...], tuple[int, str, int]]:
    """Return a sort key for semantic version strings so release versions rank above pre-releases.

    A version without a suffix (e.g. '4.20.0') sorts higher than one with a suffix
    (e.g. '4.20.0-rc1'), matching the convention that release > pre-release.
    Suffixes are split into a label and a number (e.g. 'rc10' -> ('rc', 10)) so that
    numeric ordering is used instead of lexicographic ('rc9' < 'rc10').
    """
    parts = v_string.split("-", 1)
    main_version = tuple(map(int, parts[0].split(".")))

    if len(parts) == 1:
        return (main_version, (2**31, "", 0))
    m = re.match(r"^([a-zA-Z]*)(\d*)$", parts[1])
    label = m.group(1) if m else parts[1]
    num = int(m.group(2)) if m and m.group(2) else 0
    return (main_version, (0, label, num))


def log_subprocess_output(
    header: str, output: str, level: int = logging.WARNING
) -> None:
    """Log multi-line subprocess output with a header on the first line.

    Blank lines are skipped. The first non-blank line is logged as
    '<header>: <line>'; subsequent lines are indented with two spaces.
    Does nothing if output is empty or whitespace-only.
    """
    if not output or not output.strip():
        return
    lines = [line for line in output.splitlines() if line.strip()]
    if not lines:
        return
    logger.log(level, "%s: %s", header, lines[0])
    for line in lines[1:]:
        logger.log(level, "  %s", line)


def run_oc_command(args: list[str]) -> subprocess.CompletedProcess[str]:
    """Run an oc command with a 60-second timeout and return its CompletedProcess.

    stdout and stderr are captured as text. The process is never killed on a
    non-zero exit code (check=False) — callers inspect returncode themselves.
    Raises TimeoutError if the command does not finish within 60 seconds.
    """
    try:
        return subprocess.run(
            args,
            capture_output=True,
            text=True,
            timeout=60,
            check=False,
        )
    except subprocess.TimeoutExpired as exc:
        cmd_str = " ".join(args)
        raise TimeoutError(f"Command timed out after 60 seconds: {cmd_str}") from exc


def wait_for_resource_status(
    kind: str,
    name: str,
    status_field: str,
    desired_state: str,
    *,
    namespace: str | None = None,
    timeout_minutes: int = 180,
    sleep_interval: int = 60,
) -> None:
    """Poll a Kubernetes resource's status field until it reaches the desired state.

    Builds an oc get command for the given kind/name, optionally scoped to a
    namespace, and polls status.<status_field> every sleep_interval seconds.
    Per-call oc timeouts (60 s) are retried as long as the global deadline has
    not been exceeded. A non-zero oc exit code is logged as a warning but does
    not abort polling. Raises TimeoutError if the desired state is not observed
    within timeout_minutes.
    """
    logger.debug(
        "Waiting for %s/%s to reach status.%s=%s (timeout: %d min, interval: %d s)",
        kind,
        name,
        status_field,
        desired_state,
        timeout_minutes,
        sleep_interval,
    )

    timeout = time.time() + (timeout_minutes * 60)
    oc_args = ["oc", "get", kind, name]
    if namespace is not None:
        oc_args += ["-n", namespace]
    oc_args += ["-o", f"jsonpath={{.status.{status_field}}}"]

    while True:
        try:
            result = run_oc_command(oc_args)
        except TimeoutError as e:
            logger.warning(
                "oc get %s/%s timed out after 60 seconds, retrying", kind, name
            )
            if time.time() >= timeout:
                raise TimeoutError(
                    f"{kind}/{name} polling exceeded global timeout of {timeout_minutes} minutes"
                ) from e
            time.sleep(sleep_interval)
            continue

        if result.returncode != 0:
            log_subprocess_output(
                f"oc get {kind}/{name} failed (exit {result.returncode})",
                result.stderr or "",
                logging.WARNING,
            )
            current_time = time.time()
            if current_time >= timeout:
                raise TimeoutError(
                    f"{kind}/{name} did not reach status.{status_field}={desired_state}"
                    f" within {timeout_minutes} minutes"
                    f" (last observed: oc command failure exit {result.returncode})"
                )
            time.sleep(sleep_interval)
            continue

        current_state = parse_jsonpath_value(result.stdout or "")
        if current_state == desired_state:
            logger.info(
                "%s/%s has reached status.%s=%s.",
                kind,
                name,
                status_field,
                desired_state,
            )
            return

        current_time = time.time()
        if current_time >= timeout:
            raise TimeoutError(
                f"{kind}/{name} did not reach status.{status_field}={desired_state}"
                f" within {timeout_minutes} minutes"
                f" (last observed: {current_state!r})"
            )

        minutes_to_limit = round((timeout - current_time) / 60)
        logger.debug(
            "Current status %r != %r. %d minutes remaining.",
            current_state,
            desired_state,
            minutes_to_limit,
        )

        time.sleep(sleep_interval)
