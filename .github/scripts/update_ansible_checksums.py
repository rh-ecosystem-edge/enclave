#!/usr/bin/env python3
"""
Update ansible_collections.sha256 with checksums from Ansible Galaxy.

Reads ansible_collections.txt and updates the SHA256 checksums for each
collection by querying the Ansible Galaxy v3 API.
"""

import json
import re
import subprocess
import sys
from pathlib import Path

import yaml


def update_collection_sha256(fqcn: str, version: str, sha256_file: Path) -> None:
    """
    Update the SHA256 checksum for a specific Ansible collection.

    Queries the Ansible Galaxy v3 API for the published sha256 of a specific
    collection version and rewrites its line in ansible_collections.sha256.

    Args:
        fqcn: Fully qualified collection name (namespace.name)
        version: Collection version
        sha256_file: Path to ansible_collections.sha256 file
    """
    # Validate collection name and version to prevent injection
    if not re.match(r"^[a-zA-Z0-9_]+\.[a-zA-Z0-9_]+$", fqcn):
        raise ValueError(f"Invalid collection name '{fqcn}' (expected namespace.name)")
    if not re.match(r"^[a-zA-Z0-9._-]+$", version):
        raise ValueError(f"Invalid version '{version}'")

    namespace, name = fqcn.split(".", 1)

    # Fetch collection metadata from Galaxy API
    url = (
        f"https://galaxy.ansible.com/api/v3/plugin/ansible/content/published/"
        f"collections/index/{namespace}/{name}/versions/{version}/"
    )

    curl_cmd = [
        "curl",
        "-sf",
        "--connect-timeout",
        "10",
        "--max-time",
        "30",
        "--retry",
        "3",
        "--retry-max-time",
        "60",
        url,
    ]

    try:
        result = subprocess.run(curl_cmd, check=True, capture_output=True, text=True)
        metadata = json.loads(result.stdout)
    except subprocess.CalledProcessError as e:
        raise RuntimeError(
            f"Failed to fetch metadata for {fqcn} {version}: {e.stderr}"
        ) from e
    except json.JSONDecodeError as e:
        raise RuntimeError(
            f"Failed to parse Galaxy API response for {fqcn} {version}: {e}"
        ) from e

    artifact = metadata.get("artifact")
    if not artifact:
        raise RuntimeError(f"No artifact metadata found for {fqcn} {version}")

    sha256 = artifact.get("sha256")
    filename = artifact.get("filename")

    if not sha256 or not filename:
        raise RuntimeError(
            f"Missing sha256 or filename in artifact metadata for {fqcn} {version}"
        )

    # Update the sha256 file
    try:
        lines = sha256_file.read_text().splitlines(keepends=True)
    except FileNotFoundError:
        lines = []

    # Remove existing entry for this collection
    prefix = f"*collections/{namespace}-{name}-"
    lines = [line for line in lines if prefix not in line]

    # Add new entry
    lines.append(f"{sha256} *collections/{filename}\n")

    # Write back
    sha256_file.write_text("".join(lines))

    print(f"✅ Updated checksum for {namespace}.{name} {version}")


def main() -> int:
    """Main entry point."""
    collections_file = Path("ansible_collections.txt")
    sha256_file = Path("ansible_collections.sha256")

    if not collections_file.exists():
        print(f"❌ File not found: {collections_file}")
        return 1

    with collections_file.open() as f:
        data = yaml.safe_load(f)

    collections = data.get("collections", [])
    if not collections:
        print("No collections found in ansible_collections.txt")
        return 0

    failed = []

    for collection in collections:
        name = collection["name"]
        version = collection["version"]
        print(f"Updating checksum for {name} {version}...")
        try:
            update_collection_sha256(name, version, sha256_file)
        except Exception as e:
            print(f"❌ Failed to update {name}: {e}")
            failed.append(name)

    if failed:
        print(f"\n❌ Failed to update checksums for: {', '.join(failed)}")
        return 1

    return 0


if __name__ == "__main__":
    sys.exit(main())
