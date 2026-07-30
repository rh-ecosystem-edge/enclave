import contextlib
import json
import logging
import os
import re
import subprocess
import tempfile
import time
from pathlib import Path
from typing import Any

import yaml

from enclave.utils import (
    log_subprocess_output,
    run_oc_command,
)

logger = logging.getLogger(__name__)


class IronicVersionError(Exception):
    """Base exception for ironic version operations."""


class IronicNotFoundError(IronicVersionError):
    """Raised when ironic containers are not found."""

    def __init__(self) -> None:
        super().__init__("Ironic containers not found. Is ironic deployed?")


class IronicImageNotFoundError(IronicVersionError):
    """Raised when the specified ironic image cannot be found."""

    def __init__(self, openshift_version: str) -> None:
        self.openshift_version = openshift_version
        super().__init__(
            f"Cannot extract ironic image from OpenShift release {openshift_version}"
        )


def load_pull_secret_from_config() -> dict[str, Any] | None:
    """Load pull secret from config/global.yaml."""
    config_path = Path("config/global.yaml")
    if not config_path.is_file():
        logger.warning("config/global.yaml not found, skipping authentication")
        return None

    try:
        with config_path.open(encoding="utf-8") as f:
            config: dict[str, Any] = yaml.safe_load(f) or {}
    except (OSError, yaml.YAMLError) as e:
        logger.warning("Failed to read config/global.yaml: %s", e)
        return None

    pull_secret = config.get("pullSecret")
    if pull_secret is None:
        logger.warning("pullSecret not found in config/global.yaml")
        return None

    # Handle different pull secret formats
    result = None
    if isinstance(pull_secret, str):
        # Parse string-encoded JSON
        try:
            result = json.loads(pull_secret)
        except json.JSONDecodeError:
            logger.exception("Invalid JSON in pullSecret field")
    elif isinstance(pull_secret, dict):
        result = pull_secret
    else:
        logger.error("pullSecret field has unexpected type: %s", type(pull_secret))

    return result


def get_current_ironic_image() -> str:
    """Get the currently running ironic container image (systemd quadlet deployment)."""
    # Get all containers and look for ironic
    result = subprocess.run(
        ["sudo", "podman", "ps", "--format", "json"],
        capture_output=True,
        text=True,
        check=False,
    )

    logger.debug("podman ps command returned: returncode=%d", result.returncode)

    if result.returncode != 0:
        logger.error("podman ps command failed: %s", result.stderr)
        raise IronicNotFoundError

    try:
        containers = json.loads(result.stdout)
        logger.debug("Found %d total containers", len(containers))

        # Look for the ironic container specifically
        for container in containers:
            container_names = container.get("Names", [])
            image = container.get("Image", "")  # This is the full registry reference

            # Log all containers for debugging
            logger.debug("Container: names=%s, image=%s", container_names, image)

            # Check if any name is exactly "ironic" or contains "ironic" but not "infra"
            for name in container_names:
                if name == "ironic" or (
                    "ironic" in name.lower() and "infra" not in name.lower()
                ):
                    logger.debug(
                        "Found ironic container '%s' with image: %s", name, image
                    )
                    if image:  # Make sure we have a non-empty image
                        return image

        logger.debug("No ironic container found matching criteria")
        raise IronicNotFoundError
    except (json.JSONDecodeError, KeyError, IndexError) as e:
        logger.exception("Failed to parse container list")
        logger.debug("Raw stdout was: %s", result.stdout)
        raise IronicNotFoundError from e


def _validate_openshift_version(version: str) -> None:
    """Validate OpenShift version format to prevent malformed input."""
    if not re.match(r"^\d+\.\d+\.\d+$", version):
        raise ValueError(f"Invalid OpenShift version format: {version}. Expected format: X.Y.Z")


def get_ironic_image_from_openshift_release(openshift_version: str) -> str:
    """Extract ironic image from OpenShift release (matches deploy_ironic.yaml logic)."""
    _validate_openshift_version(openshift_version)
    logger.info("Extracting ironic image from OpenShift release %s", openshift_version)

    # Load pull secret for authentication
    pull_secret = load_pull_secret_from_config()

    if pull_secret is None:
        # Try without authentication first
        result = run_oc_command([
            "oc",
            "adm",
            "release",
            "info",
            "--image-for",
            "ironic",
            f"quay.io/openshift-release-dev/ocp-release:{openshift_version}-x86_64",
        ])
    else:
        # Use authentication with temporary file
        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".json", delete=False, encoding="utf-8"
        ) as auth_file:
            try:
                json.dump(pull_secret, auth_file)
                auth_file.flush()

                # Use the auth file with oc command via REGISTRY_AUTH_FILE environment variable
                env = os.environ.copy()
                env["REGISTRY_AUTH_FILE"] = auth_file.name

                try:
                    result = subprocess.run(
                        [
                            "oc",
                            "adm",
                            "release",
                            "info",
                            "--image-for",
                            "ironic",
                            f"quay.io/openshift-release-dev/ocp-release:{openshift_version}-x86_64",
                        ],
                        capture_output=True,
                        text=True,
                        timeout=60,
                        check=False,
                        env=env,
                    )
                except subprocess.TimeoutExpired as exc:
                    cmd_str = f"oc adm release info --image-for ironic quay.io/openshift-release-dev/ocp-release:{openshift_version}-x86_64"
                    raise TimeoutError(
                        f"Command timed out after 60 seconds: {cmd_str}"
                    ) from exc

            finally:
                # Clean up temporary auth file
                with contextlib.suppress(OSError):
                    Path(auth_file.name).unlink()

    if result.returncode != 0:
        header = (
            f"Failed to get ironic image from OpenShift release {openshift_version}"
        )
        if result.stderr:
            log_subprocess_output(f"{header} [stderr]", result.stderr, logging.ERROR)
        if result.stdout:
            log_subprocess_output(f"{header} [stdout]", result.stdout, logging.ERROR)
        raise IronicImageNotFoundError(openshift_version)

    ironic_image = result.stdout.strip()
    logger.info("Extracted ironic image: %s", ironic_image)
    return ironic_image


def validate_ironic_image_exists(image: str) -> bool:
    """Check if the specified ironic image exists and is accessible (systemd quadlet deployment)."""
    logger.debug("Validating ironic image exists: %s", image)

    # Check if image exists locally
    result = subprocess.run(
        ["sudo", "podman", "image", "exists", image],
        capture_output=True,
        text=True,
        check=False,
    )

    if result.returncode == 0:
        logger.debug("Image exists locally: %s", image)
        return True

    # Try to pull the image if it doesn't exist locally
    logger.info("Pulling ironic image: %s", image)

    # Load pull secret for authentication
    pull_secret = load_pull_secret_from_config()

    if pull_secret is None:
        # Try without authentication
        result = subprocess.run(
            ["sudo", "podman", "pull", image],
            capture_output=True,
            text=True,
            check=False,
        )
        if result.returncode == 0:
            logger.info("Successfully pulled image without authentication: %s", image)
            return True

        logger.error(
            "Failed to pull image %s (no authentication available): %s",
            image,
            result.stderr,
        )
        return False

    # Use authentication with temporary file
    with tempfile.NamedTemporaryFile(
        mode="w", suffix=".json", delete=False, encoding="utf-8"
    ) as auth_file:
        try:
            json.dump(pull_secret, auth_file)
            auth_file.flush()

            result = subprocess.run(
                ["sudo", "podman", "pull", "--authfile", auth_file.name, image],
                capture_output=True,
                text=True,
                check=False,
            )

            if result.returncode == 0:
                logger.info("Successfully pulled image with authentication: %s", image)
                return True

            logger.error(
                "Failed to pull image %s with authentication: %s", image, result.stderr
            )
            return False

        finally:
            # Clean up temporary auth file
            with contextlib.suppress(OSError):
                Path(auth_file.name).unlink()


def update_ironic_systemd(new_image: str) -> None:
    """Update ironic systemd quadlet configuration with new image."""
    logger.info("Updating systemd quadlet configuration with image: %s", new_image)

    def update_quadlet_file(quadlet_path: str) -> None:
        """Update a single quadlet file with the new image."""
        # Check if file exists
        result = subprocess.run(
            ["sudo", "test", "-f", quadlet_path],
            capture_output=True,
            text=True,
            check=False,
        )
        if result.returncode != 0:
            logger.warning("Quadlet file not found: %s", quadlet_path)
            return

        # Read current content
        result = subprocess.run(
            ["sudo", "cat", quadlet_path],
            capture_output=True,
            text=True,
            check=False,
        )
        if result.returncode != 0:
            raise IronicVersionError(f"Failed to read {quadlet_path}: {result.stderr}")

        content = result.stdout

        # Update the Image line
        lines = content.splitlines()
        updated_lines = []
        image_updated = False

        for line in lines:
            if line.startswith("Image="):
                updated_lines.append(f"Image={new_image}")
                image_updated = True
                logger.info(
                    "Updated Image line in %s: %s", quadlet_path, f"Image={new_image}"
                )
            else:
                updated_lines.append(line)

        if not image_updated:
            logger.warning("Could not find Image= line in %s", quadlet_path)
            return

        # Write back the updated content using sudo tee
        updated_content = "\n".join(updated_lines) + "\n"

        result = subprocess.run(
            ["sudo", "tee", quadlet_path],
            input=updated_content,
            capture_output=True,
            text=True,
            check=False,
        )
        if result.returncode != 0:
            raise IronicVersionError(f"Failed to write {quadlet_path}: {result.stderr}")

    # Update the ironic API container quadlet
    update_quadlet_file("/etc/containers/systemd/metal3-ironic-api.container")

    # Also update httpd container quadlet (both use same image)
    update_quadlet_file("/etc/containers/systemd/metal3-httpd.container")

    logger.info("Updated systemd quadlet files")


def restart_ironic_systemd() -> None:
    """Restart ironic systemd services."""
    logger.info("Restarting ironic systemd services")

    # Reload systemd daemon to pick up changes
    result = subprocess.run(
        ["sudo", "systemctl", "daemon-reload"],
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        logger.warning("systemctl daemon-reload failed: %s", result.stderr)

    # Stop and start services in correct order
    services = [
        "metal3-ironic-api.service",
        "metal3-httpd.service",
        "metal3-ironic-pod.service",
    ]

    # Stop services
    for service in services:
        logger.info("Stopping %s", service)
        subprocess.run(
            ["sudo", "systemctl", "stop", service],
            capture_output=True,
            text=True,
            check=False,
        )

    # Start services
    for service in reversed(services):  # Start in reverse order
        logger.info("Starting %s", service)
        result = subprocess.run(
            ["sudo", "systemctl", "start", service],
            capture_output=True,
            text=True,
            check=False,
        )
        if result.returncode != 0:
            logger.error("Failed to start %s: %s", service, result.stderr)


def wait_for_ironic_ready(timeout_minutes: int = 10, sleep_interval: int = 5) -> None:
    """Wait for ironic API to be ready after restart."""
    logger.info("Waiting for ironic to be ready...")
    deadline = time.time() + (timeout_minutes * 60)

    while time.time() < deadline:
        try:
            # Check if ironic API is responding (matches deploy_ironic.yaml:167-178)
            result = subprocess.run(
                [
                    "curl",
                    "-s",
                    "-o",
                    "/dev/null",
                    "-w",
                    "%{http_code}",
                    "http://localhost:6385/v1/",
                ],
                capture_output=True,
                text=True,
                check=False,
                timeout=5,
            )

            if result.stdout.strip() in {"200", "401"}:  # 401 is OK (auth required)
                logger.info("Ironic API is responding")
                return

        except subprocess.SubprocessError as e:
            logger.debug("Ironic health check failed: %s", e)

        logger.debug("Ironic not ready yet, waiting %d seconds...", sleep_interval)
        time.sleep(sleep_interval)

    raise TimeoutError(f"Ironic did not become ready within {timeout_minutes} minutes")


def restart_ironic_with_new_image(new_image: str) -> None:
    """Update ironic systemd quadlet deployment to use the new image."""
    update_ironic_systemd(new_image)
    restart_ironic_systemd()


def reconcile(
    openshift_version: str,
    dry_run: bool,
    timeout_minutes: int = 10,
    sleep_interval: int = 5,
) -> None:
    """Reconcile ironic to the version matching the specified OpenShift version.

    Args:
        openshift_version: OpenShift version to extract ironic from (e.g., "4.20.21")
        dry_run: If True, only show what would be done
        timeout_minutes: Max time to wait for ironic to be ready
        sleep_interval: Polling interval for readiness checks
    """
    logger.info(
        "Reconciling ironic version for OpenShift %s (dry_run=%s)",
        openshift_version,
        dry_run,
    )

    # Extract ironic image from the OpenShift release
    target_image = get_ironic_image_from_openshift_release(openshift_version)

    try:
        current_image = get_current_ironic_image()
        logger.info("Current ironic image: %s", current_image)

        if current_image == target_image:
            logger.info(
                "✅ Ironic is already using the target image from OpenShift %s",
                openshift_version,
            )
            logger.info("No action needed - versions match")
            if dry_run:
                logger.info(
                    "DRY RUN: No changes would be made (versions already match)"
                )
            return
        logger.info(
            "Ironic needs to be updated from %s to %s", current_image, target_image
        )
        current_image_found = True

    except IronicNotFoundError:
        logger.info("Ironic not currently running, deployment needed")
        current_image_found = False

    if dry_run:
        if current_image_found:
            logger.info(
                "DRY RUN: Would update ironic systemd quadlets to image %s",
                target_image,
            )
        else:
            logger.info(
                "DRY RUN: Would deploy ironic systemd quadlets with image %s",
                target_image,
            )
        return

    # Validate the target image is accessible
    if not validate_ironic_image_exists(target_image):
        raise IronicImageNotFoundError(openshift_version)

    # Update ironic deployment
    restart_ironic_with_new_image(target_image)

    # Wait for ironic to be ready
    wait_for_ironic_ready(timeout_minutes, sleep_interval)

    logger.info(
        "Successfully updated ironic to version from OpenShift %s", openshift_version
    )
