#!/usr/bin/env python3
"""Generate a CycloneDX SBOM from Enclave repository metadata."""

from __future__ import annotations

import argparse
import json
import re
import tomllib
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from urllib.parse import quote, urlencode, urlsplit

import yaml
from packaging.markers import Marker


REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
PROPERTY_PREFIX = "com.redhat.enclave:"
CONTAINER_REF_PATTERN = re.compile(
    r"(?<![A-Za-z0-9._-])"
    r"(?:quay\.io|registry\.redhat\.io|registry\.connect\.redhat\.com|"
    r"registry\.access\.redhat\.com|docker\.io|ghcr\.io)/"
    r"[A-Za-z0-9._/-]+:[A-Za-z0-9_][A-Za-z0-9_.+-]*"
    r"(?:@sha256:[a-fA-F0-9]{64})?"
)


def normalize_package_name(name: str) -> str:
    """Normalize a Python package name according to PEP 503."""
    return re.sub(r"[-_.]+", "-", name).lower()


def package_url(name: str, version: str) -> str:
    """Return a Package URL for a PyPI package."""
    normalized_name = normalize_package_name(name)
    encoded_name = quote(normalized_name, safe="-._~")
    encoded_version = quote(version, safe=".-_~+")
    return f"pkg:pypi/{encoded_name}@{encoded_version}"


def generic_package_url(
    namespace: str,
    name: str,
    version: str | None = None,
    qualifiers: dict[str, str] | None = None,
) -> str:
    """Return a generic Package URL for a non-PyPI component."""
    package_path = "/".join(
        quote(part, safe="-._~") for part in (*namespace.split("/"), name)
    )
    package_ref = f"pkg:generic/{package_path}"
    if version:
        package_ref += f"@{quote(version, safe='.-_~+')}"
    if qualifiers:
        package_ref += f"?{urlencode(sorted(qualifiers.items()))}"
    return package_ref


def docker_package_url(image_ref: str) -> tuple[str, str, str]:
    """Return a Docker Package URL, image name, and tag for an image reference."""
    image_name, separator, digest = image_ref.partition("@")
    image_parts = image_name.split("/")
    first_part = image_parts[0]
    has_registry = len(image_parts) > 1 and (
        "." in first_part or ":" in first_part or first_part == "localhost"
    )
    registry = first_part if has_registry else "docker.io"
    repository_parts = image_parts[1:] if has_registry else image_parts
    last_part = repository_parts[-1]
    if ":" in last_part:
        repository_parts[-1], tag = last_part.rsplit(":", maxsplit=1)
    else:
        tag = "latest"
    if registry == "docker.io" and len(repository_parts) == 1:
        repository_parts.insert(0, "library")

    repository = "/".join(repository_parts)
    image_path = "/".join(quote(part, safe="-._~") for part in repository_parts)
    purl = f"pkg:docker/{image_path}@{quote(tag, safe='.-_~+')}"
    qualifiers = {"repository_url": f"https://{registry}"}
    if separator and digest:
        qualifiers["digest"] = digest
    purl += f"?{urlencode(sorted(qualifiers.items()))}"
    return purl, f"{registry}/{repository}", tag


def read_yaml(file_path: Path) -> dict[str, Any]:
    """Read a YAML mapping from a repository file."""
    data = yaml.safe_load(file_path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        msg = f"Expected a YAML mapping in {file_path}"
        raise ValueError(msg)
    return data


def component_properties(**properties: str) -> list[dict[str, str]]:
    """Convert Enclave property values to CycloneDX properties."""
    return [
        {"name": f"{PROPERTY_PREFIX}{name}", "value": value}
        for name, value in sorted(properties.items())
    ]


def add_component(
    components: dict[str, dict[str, Any]], component: dict[str, Any]
) -> str:
    """Add a component, merging properties for identical package references."""
    reference = component["bom-ref"]
    existing = components.get(reference)
    if existing is None:
        components[reference] = component
        return reference

    if component.get("scope") == "required":
        existing["scope"] = "required"
    properties = existing.setdefault("properties", [])
    for property_item in component.get("properties", []):
        existing_property = next(
            (
                item
                for item in properties
                if item["name"] == property_item["name"]
            ),
            None,
        )
        if existing_property is None:
            properties.append(property_item)
        elif property_item["name"] == f"{PROPERTY_PREFIX}default":
            if property_item["value"] == "true":
                existing_property["value"] = "true"
        elif property_item["value"] not in existing_property["value"].split("; "):
            existing_property["value"] += f"; {property_item['value']}"
    return reference


def docker_component(
    image_ref: str,
    source: str,
    scope: str,
    default: bool,
    plugin_name: str | None = None,
) -> dict[str, Any]:
    """Build a CycloneDX component for a configured container image."""
    purl, image_name, tag = docker_package_url(image_ref)
    properties = {
        "default": str(default).lower(),
        "image-ref": image_ref,
        "source": source,
        "tag-is-digest-pinned": str("@sha256:" in image_ref).lower(),
    }
    if plugin_name:
        properties["plugin"] = plugin_name
    return {
        "type": "container",
        "bom-ref": purl,
        "name": image_name,
        "version": tag,
        "purl": purl,
        "scope": scope,
        "properties": component_properties(**properties),
    }


def dependency_names(dependencies: list[dict[str, Any]]) -> set[str]:
    """Return dependency names whose markers match the current environment."""
    names: set[str] = set()
    for dependency in dependencies:
        marker = dependency.get("marker")
        if marker and not Marker(marker).evaluate():
            continue
        name = dependency.get("name")
        if name:
            names.add(normalize_package_name(name))
    return names


def runtime_packages(
    lock: dict[str, Any],
) -> tuple[
    dict[str, dict[str, Any]],
    dict[str, set[str]],
    set[str],
]:
    """Find the locked Python packages reachable from Enclave runtime dependencies."""
    packages: dict[str, dict[str, Any]] = {}
    for package in lock["package"]:
        name = normalize_package_name(package["name"])
        if name in packages and packages[name]["version"] != package["version"]:
            msg = f"Multiple locked versions found for Python package {name}"
            raise ValueError(msg)
        packages[name] = package

    project = packages.get("enclave")
    if project is None:
        msg = "uv.lock does not contain the Enclave project"
        raise ValueError(msg)

    graph: dict[str, set[str]] = {}
    direct_dependencies = dependency_names(project.get("dependencies", []))
    pending = list(direct_dependencies)
    included: set[str] = set()
    while pending:
        name = pending.pop()
        if name in included:
            continue
        package = packages.get(name)
        if package is None:
            msg = f"Runtime dependency {name} is missing from uv.lock"
            raise ValueError(msg)
        included.add(name)
        dependencies = dependency_names(package.get("dependencies", []))
        graph[name] = dependencies
        pending.extend(dependencies - included)

    return {name: packages[name] for name in included}, graph, direct_dependencies


def python_component(name: str, package: dict[str, Any]) -> dict[str, Any]:
    """Build a CycloneDX component from a uv.lock package entry."""
    component_ref = package_url(name, package["version"])
    component: dict[str, Any] = {
        "type": "library",
        "bom-ref": component_ref,
        "name": name,
        "version": package["version"],
        "purl": component_ref,
        "scope": "required",
    }
    sdist = package.get("sdist", {})
    digest = sdist.get("hash", "")
    if digest.startswith("sha256:"):
        component["hashes"] = [{"alg": "SHA-256", "content": digest[7:]}]
    return component


def collection_components(
    requirements_path: Path,
    checksums_path: Path,
) -> list[dict[str, Any]]:
    """Return components for the pinned Ansible Galaxy collection requirements."""
    requirements = yaml.safe_load(requirements_path.read_text(encoding="utf-8"))
    collections = requirements.get("collections", [])
    checksums: dict[str, str] = {}
    for line in checksums_path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        digest, filename = line.split(maxsplit=1)
        checksums[Path(filename.removeprefix("*")).name] = digest

    components: list[dict[str, Any]] = []
    for collection in collections:
        name = collection["name"]
        version = str(collection["version"])
        filename = f"{name.replace('.', '-')}-{version}.tar.gz"
        digest = checksums.get(filename)
        if digest is None:
            msg = f"No checksum is recorded for Ansible collection {name} {version}"
            raise ValueError(msg)
        component_ref = (
            f"pkg:generic/ansible-collection-{quote(name, safe='.-_~')}"
            f"@{quote(version, safe='.-_~+')}"
        )
        components.append(
            {
                "type": "library",
                "bom-ref": component_ref,
                "name": name,
                "version": version,
                "purl": component_ref,
                "scope": "required",
                "hashes": [{"alg": "SHA-256", "content": digest}],
                "properties": component_properties(
                    ecosystem="Ansible Galaxy",
                    source="ansible_collections.txt; ansible_collections.sha256",
                ),
            }
        )
    return components


def version_from_url(url: str) -> str:
    """Return the version directory from a versioned artifact URL."""
    path_parts = [part for part in urlsplit(url).path.split("/") if part]
    if len(path_parts) < 2:
        msg = f"Cannot determine component version from URL: {url}"
        raise ValueError(msg)
    return path_parts[-2]


def resolve_plugin_version(
    version: Any, plugin_directory: Path
) -> tuple[str | None, str | None]:
    """Resolve a simple plugin version template from that plugin's defaults."""
    if not isinstance(version, str):
        return str(version), None
    match = re.fullmatch(r"\s*{{\s*([A-Za-z_][A-Za-z0-9_]*)\s*}}\s*", version)
    if match is None:
        return version, None
    defaults_path = plugin_directory / "defaults.yaml"
    if not defaults_path.is_file():
        return None, version
    default_value = read_yaml(defaults_path).get(match.group(1))
    if default_value is None:
        return None, version
    return str(default_value), version


def deployment_components() -> tuple[
    list[dict[str, Any]], set[str], dict[str, set[str]]
]:
    """Inventory versioned components declared or selected by Enclave playbooks."""
    components: dict[str, dict[str, Any]] = {}
    root_dependencies: set[str] = set()
    dependency_graph: dict[str, set[str]] = {}

    def record(component: dict[str, Any], *, root: bool = True) -> str:
        reference = add_component(components, component)
        if root:
            root_dependencies.add(reference)
        return reference

    platforms_path = REPOSITORY_ROOT / "defaults/platforms.yaml"
    platforms = read_yaml(platforms_path).get("openshift_versions", [])
    release_payload_names: set[str] = set()
    release_payload_sources: dict[str, set[str]] = {}
    image_pattern = re.compile(r"--image-for(?:\s*=\s*|\s+)([A-Za-z0-9_-]+)")
    command_pattern = re.compile(r"--command=([A-Za-z0-9_-]+)")
    for playbook_path in sorted((REPOSITORY_ROOT / "playbooks").rglob("*.yaml")):
        playbook_text = playbook_path.read_text(encoding="utf-8")
        relative_path = playbook_path.relative_to(REPOSITORY_ROOT).as_posix()
        for payload_name in image_pattern.findall(playbook_text):
            release_payload_names.add(payload_name)
            release_payload_sources.setdefault(payload_name, set()).add(relative_path)
        for payload_name in command_pattern.findall(playbook_text):
            release_payload_names.add(payload_name)
            release_payload_sources.setdefault(payload_name, set()).add(relative_path)

    for platform in platforms:
        version = str(platform["version"])
        is_default = bool(platform.get("default", False))
        scope = "required" if is_default else "optional"
        release_ref = record(
            docker_component(
                f"quay.io/openshift-release-dev/ocp-release:{version}-x86_64",
                "defaults/platforms.yaml",
                scope,
                is_default,
            )
            | {
                "name": "OpenShift release",
                "version": version,
                "properties": component_properties(
                    default=str(is_default).lower(),
                    image_ref=(
                        "quay.io/openshift-release-dev/ocp-release:"
                        f"{version}-x86_64"
                    ),
                    source="defaults/platforms.yaml",
                    platform="OpenShift",
                ),
            }
        )
        payload_refs: set[str] = set()
        for payload_name in sorted(release_payload_names):
            payload_type = (
                "application" if payload_name == "openshift-install" else "container"
            )
            payload_ref = generic_package_url(
                "openshift-payload",
                payload_name,
                qualifiers={"openshift_release": version},
            )
            payload_sources = "; ".join(
                sorted(release_payload_sources[payload_name])
            )
            record(
                {
                    "type": payload_type,
                    "bom-ref": payload_ref,
                    "name": payload_name,
                    "purl": payload_ref,
                    "scope": scope,
                    "properties": component_properties(
                        default=str(is_default).lower(),
                        resolved_from=release_ref,
                        resolution="Resolved from the OpenShift release at deployment",
                        source=payload_sources,
                    ),
                },
                root=False,
            )
            payload_refs.add(payload_ref)
            if payload_name == "machine-os-images":
                rhcos_ref = generic_package_url(
                    "operating-system",
                    "rhcos-live-iso",
                    qualifiers={"openshift_release": version},
                )
                record(
                    {
                        "type": "operating-system",
                        "bom-ref": rhcos_ref,
                        "name": "Red Hat Enterprise Linux CoreOS live ISO",
                        "purl": rhcos_ref,
                        "scope": scope,
                        "properties": component_properties(
                            default=str(is_default).lower(),
                            resolved_from=payload_ref,
                            resolution=(
                                "Extracted from machine-os-images for the selected "
                                "OpenShift release"
                            ),
                            source=(
                                "playbooks/tasks/download_content_single_version.yaml"
                            ),
                        ),
                    },
                    root=False,
                )
                dependency_graph.setdefault(payload_ref, set()).add(rhcos_ref)
        dependency_graph[release_ref] = payload_refs

    defaults_operators_path = REPOSITORY_ROOT / "defaults/operators.yaml"
    defaults_operators = read_yaml(defaults_operators_path).get("operators", [])
    for operator in defaults_operators:
        name = str(operator["name"])
        version = str(operator["version"])
        reference = generic_package_url("openshift-operator", name, version)
        operator_ref = record(
            {
                "type": "library",
                "bom-ref": reference,
                "name": name,
                "version": version,
                "purl": reference,
                "scope": "required",
                "properties": component_properties(
                    channel=str(operator.get("channel", "")),
                    catalog="redhat-operators",
                    default="true",
                    init_version=str(operator.get("init_version", "")),
                    namespace=str(operator.get("namespace", "")),
                    csv_names="; ".join(operator.get("csvNames", [])),
                    source="defaults/operators.yaml",
                ),
            }
        )
        for package_name in operator.get("extraMirrorPackages", []):
            package_name = str(package_name)
            package_ref = generic_package_url(
                "openshift-operator-package", package_name
            )
            record(
                {
                    "type": "library",
                    "bom-ref": package_ref,
                    "name": package_name,
                    "purl": package_ref,
                    "scope": "required",
                    "properties": component_properties(
                        default="true",
                        resolution=(
                            "Version selected from the operator catalog at deployment"
                        ),
                        source="defaults/operators.yaml",
                    ),
                },
                root=False,
            )
            dependency_graph.setdefault(operator_ref, set()).add(package_ref)

    deployment_defaults_path = REPOSITORY_ROOT / "defaults/deployment.yaml"
    deployment_defaults = read_yaml(deployment_defaults_path)
    default_plugins = {str(deployment_defaults.get("storage_plugin", ""))}
    for plugin_path in sorted((REPOSITORY_ROOT / "plugins").glob("*/plugin.yaml")):
        plugin_directory = plugin_path.parent
        plugin_data = read_yaml(plugin_path)
        plugin_name = str(plugin_data.get("name", plugin_directory.name))
        operator_catalog = str(plugin_data.get("catalog", "redhat-operators"))
        plugin_is_default = plugin_name in default_plugins
        plugin_scope = "required" if plugin_is_default else "optional"
        plugin_source = plugin_path.relative_to(REPOSITORY_ROOT).as_posix()

        for operator in plugin_data.get("operators", []):
            name = str(operator["name"])
            version = str(operator["version"])
            reference = generic_package_url("openshift-operator", name, version)
            operator_ref = record(
                {
                    "type": "library",
                    "bom-ref": reference,
                    "name": name,
                    "version": version,
                    "purl": reference,
                    "scope": plugin_scope,
                    "properties": component_properties(
                        channel=str(operator.get("channel", "")),
                    catalog=operator_catalog,
                        default=str(plugin_is_default).lower(),
                        init_version=str(operator.get("init_version", "")),
                        namespace=str(operator.get("namespace", "")),
                        csv_names="; ".join(operator.get("csvNames", [])),
                        plugin=plugin_name,
                        source=plugin_source,
                    ),
                }
            )
            for package_name in operator.get("extraMirrorPackages", []):
                package_name = str(package_name)
                package_ref = generic_package_url(
                    "openshift-operator-package", package_name
                )
                record(
                    {
                        "type": "library",
                        "bom-ref": package_ref,
                        "name": package_name,
                        "purl": package_ref,
                        "scope": plugin_scope,
                        "properties": component_properties(
                            default=str(plugin_is_default).lower(),
                            catalog=operator_catalog,
                            plugin=plugin_name,
                            resolution=(
                                "Version selected from the operator catalog "
                                "at deployment"
                            ),
                            source=plugin_source,
                        ),
                    },
                    root=False,
                )
                dependency_graph.setdefault(operator_ref, set()).add(package_ref)

        for chart in plugin_data.get("helm", []):
            chart_ref = str(chart.get("chart", chart.get("release", "unknown")))
            chart_name = chart_ref.rstrip("/").rsplit("/", maxsplit=1)[-1]
            chart_version, version_template = resolve_plugin_version(
                chart.get("version"), plugin_directory
            )
            reference = generic_package_url("helm-chart", chart_name, chart_version)
            chart_properties = {
                "chart": chart_ref,
                "default": str(plugin_is_default).lower(),
                "plugin": plugin_name,
                "release": str(chart.get("release", "")),
                "source": plugin_source,
            }
            if chart.get("repo"):
                chart_properties["repository"] = str(chart["repo"])
            if version_template:
                chart_properties["version-template"] = version_template
            record(
                {
                    "type": "application",
                    "bom-ref": reference,
                    "name": chart_name,
                    "version": chart_version,
                    "purl": reference,
                    "scope": plugin_scope,
                    "properties": component_properties(**chart_properties),
                }
            )

        image_refs: list[tuple[Any, str]] = [
            (image_ref, plugin_source)
            for image_ref in plugin_data.get("additionalImages", [])
        ]
        for defaults_path in (plugin_directory / "defaults.yaml",):
            if not defaults_path.is_file():
                continue
            plugin_defaults = read_yaml(defaults_path)
            defaults_source = defaults_path.relative_to(REPOSITORY_ROOT).as_posix()
            for key, value in plugin_defaults.items():
                if (
                    isinstance(value, str)
                    and (
                        key.lower().endswith("image")
                        or key.lower().endswith("_image")
                    )
                    and (":" in value or "/" in value)
                ):
                    image_refs.append((value, defaults_source))
        for image_ref, image_source in image_refs:
            if not isinstance(image_ref, str) or "{{" in image_ref:
                continue
            record(
                docker_component(
                    image_ref,
                    image_source,
                    plugin_scope,
                    plugin_is_default,
                    plugin_name,
                )
            )

    catalogs_path = REPOSITORY_ROOT / "defaults/catalogs.yaml"
    catalogs = read_yaml(catalogs_path)
    for name, image in catalogs.items():
        if not name.endswith("_catalog") or not isinstance(image, str):
            continue
        version_key = f"{name}_version"
        version = catalogs.get(version_key)
        if version is None:
            continue
        image_ref = f"{image}:{version}"
        record(
            docker_component(
                image_ref,
                "defaults/catalogs.yaml",
                "required",
                True,
            )
        )

    mirror_registry_defaults_path = REPOSITORY_ROOT / "defaults/mirror_registry.yaml"
    mirror_registry_defaults = read_yaml(mirror_registry_defaults_path)
    for key, value in mirror_registry_defaults.items():
        if isinstance(value, str) and key.lower().endswith("image"):
            record(
                docker_component(
                    value,
                    "defaults/mirror_registry.yaml",
                    "required",
                    True,
                )
            )

    control_binaries_path = REPOSITORY_ROOT / "defaults/control_binaries.yaml"
    control_binaries = read_yaml(control_binaries_path).get("control_binaries", {})
    for name, binary in control_binaries.items():
        version = version_from_url(str(binary["url"]))
        reference = generic_package_url(
            "control-binary", name.replace("_", "-"), version
        )
        checksum = str(binary.get("checksum", ""))
        component: dict[str, Any] = {
            "type": "application",
            "bom-ref": reference,
            "name": name.replace("_", "-"),
            "version": version,
            "purl": reference,
            "scope": "required",
            "properties": component_properties(
                default="true",
                source="defaults/control_binaries.yaml",
                url=str(binary["url"]),
            ),
            "externalReferences": [
                {"type": "distribution", "url": str(binary["url"])}
            ],
        }
        if checksum.startswith("sha256:"):
            component["hashes"] = [
                {"alg": "SHA-256", "content": checksum.removeprefix("sha256:")}
            ]
        record(component)

    clair_defaults_path = REPOSITORY_ROOT / "plugins/clair-import/defaults.yaml"
    clair_defaults = read_yaml(clair_defaults_path)
    clair_url = str(clair_defaults["clairImportClairctlUrl"])
    clair_version = version_from_url(clair_url)
    clair_ref = generic_package_url("control-binary", "clairctl", clair_version)
    clair_checksum = str(clair_defaults["clairImportClairctlChecksum"])
    clair_component: dict[str, Any] = {
        "type": "application",
        "bom-ref": clair_ref,
        "name": "clairctl",
        "version": clair_version,
        "purl": clair_ref,
        "scope": "optional",
        "properties": component_properties(
            default="false",
            plugin="clair-import",
            source="plugins/clair-import/defaults.yaml",
            url=clair_url,
        ),
        "externalReferences": [{"type": "distribution", "url": clair_url}],
    }
    if clair_checksum.startswith("sha256:"):
        clair_component["hashes"] = [
            {"alg": "SHA-256", "content": clair_checksum.removeprefix("sha256:")}
        ]
    record(clair_component)

    osac_defaults_path = REPOSITORY_ROOT / "plugins/osac/defaults.yaml"
    osac_defaults = read_yaml(osac_defaults_path)
    kafka_version = str(osac_defaults["osac_kafka_version"])
    kafka_ref = generic_package_url("application", "apache-kafka", kafka_version)
    record(
        {
            "type": "application",
            "bom-ref": kafka_ref,
            "name": "Apache Kafka",
            "version": kafka_version,
            "purl": kafka_ref,
            "scope": "optional",
            "properties": component_properties(
                default="false",
                metadata_version=str(
                    osac_defaults.get("osac_kafka_metadata_version", "")
                ),
                plugin="osac",
                source="plugins/osac/defaults.yaml",
            ),
        }
    )

    for source_root in ("playbooks", "operators", "plugins"):
        root_path = REPOSITORY_ROOT / source_root
        for source_path in sorted(root_path.rglob("*")):
            if (
                not source_path.is_file()
                or source_path.suffix not in {".yaml", ".yml", ".j2"}
                or "test-fixtures" in source_path.parts
                or "schemas" in source_path.parts
            ):
                continue
            relative_path = source_path.relative_to(REPOSITORY_ROOT).as_posix()
            plugin_name = (
                source_path.relative_to(REPOSITORY_ROOT).parts[1]
                if source_root == "plugins"
                else None
            )
            plugin_is_default = plugin_name in default_plugins
            for image_ref in CONTAINER_REF_PATTERN.findall(
                source_path.read_text(encoding="utf-8")
            ):
                record(
                    docker_component(
                        image_ref,
                        relative_path,
                        "required"
                        if plugin_name is None or plugin_is_default
                        else "optional",
                        plugin_name is None or plugin_is_default,
                        plugin_name,
                    )
                )

    return list(components.values()), root_dependencies, dependency_graph


def generate_sbom(
    version: str,
    revision: str | None,
    lock_path: Path,
    requirements_path: Path,
    checksums_path: Path,
) -> dict[str, Any]:
    """Build a CycloneDX 1.6 SBOM from the repository's pinned metadata."""
    lock = tomllib.loads(lock_path.read_text(encoding="utf-8"))
    packages, python_graph, direct_dependencies = runtime_packages(lock)
    app_ref = f"pkg:generic/enclave@{quote(version, safe='.-_~+')}"
    app_properties = component_properties(
        source="https://github.com/rh-ecosystem-edge/enclave"
    )
    if revision:
        app_properties.append(
            {
                "name": f"{PROPERTY_PREFIX}source-revision",
                "value": revision,
            }
        )
    app_component = {
        "type": "application",
        "bom-ref": app_ref,
        "name": "enclave",
        "version": version,
        "purl": app_ref,
        "properties": app_properties,
        "externalReferences": [
            {
                "type": "vcs",
                "url": "https://github.com/rh-ecosystem-edge/enclave",
            }
        ],
    }
    python_components = [
        python_component(name, package) for name, package in sorted(packages.items())
    ]
    ansible_components = collection_components(requirements_path, checksums_path)
    deployment_software, deployment_refs, deployment_graph = deployment_components()
    components = python_components + ansible_components + deployment_software

    dependency_graph: dict[str, set[str]] = dict(deployment_graph)
    for name, children in python_graph.items():
        dependency_graph[package_url(name, packages[name]["version"])] = {
            package_url(child, packages[child]["version"]) for child in children
        }
    for component in ansible_components:
        dependency_graph.setdefault(component["bom-ref"], set())
    for component in deployment_software:
        dependency_graph.setdefault(component["bom-ref"], set())

    direct_refs = {
        package_url(name, packages[name]["version"])
        for name in direct_dependencies
    }
    direct_refs.update(component["bom-ref"] for component in ansible_components)
    direct_refs.update(deployment_refs)
    dependencies: list[dict[str, Any]] = [
        {"ref": app_ref, "dependsOn": sorted(direct_refs)}
    ]
    dependencies.extend(
        {"ref": reference, "dependsOn": sorted(children)}
        for reference, children in sorted(dependency_graph.items())
    )

    return {
        "bomFormat": "CycloneDX",
        "specVersion": "1.6",
        "serialNumber": f"urn:uuid:{uuid.uuid4()}",
        "version": 1,
        "metadata": {
            "timestamp": datetime.now(UTC).isoformat(timespec="seconds").replace(
                "+00:00", "Z"
            ),
            "component": app_component,
        },
        "components": components,
        "dependencies": dependencies,
    }


def parse_args() -> argparse.Namespace:
    """Parse command-line arguments."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--version", required=True, help="release version or tag")
    parser.add_argument("--revision", help="source repository commit")
    parser.add_argument("--output", type=Path, required=True, help="SBOM output path")
    parser.add_argument(
        "--lock",
        type=Path,
        default=REPOSITORY_ROOT / "uv.lock",
        help="uv lock file",
    )
    parser.add_argument(
        "--collections",
        type=Path,
        default=REPOSITORY_ROOT / "ansible_collections.txt",
        help="Ansible collection requirements",
    )
    parser.add_argument(
        "--checksums",
        type=Path,
        default=REPOSITORY_ROOT / "ansible_collections.sha256",
        help="Ansible collection checksums",
    )
    return parser.parse_args()


def main() -> None:
    """Write the SBOM document as formatted JSON."""
    args = parse_args()
    sbom = generate_sbom(
        args.version,
        args.revision,
        args.lock,
        args.collections,
        args.checksums,
    )
    args.output.write_text(json.dumps(sbom, indent=2) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
