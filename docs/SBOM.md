# Software bill of materials

The build workflow generates a CycloneDX 1.6 JSON SBOM from the checked-out
repository metadata, then includes it as `enclave.cdx.json` in the Enclave release
tarball. The SBOM records the release version and source commit.

The inventory includes:

- Enclave's locked Python runtime dependency graph. Development-only Python
  dependencies are omitted.
- Pinned Ansible Galaxy collections and their recorded SHA-256 checksums.
- Supported OpenShift release versions, with the configured default marked.
- Core operators from `defaults/operators.yaml` and optional plugin operators,
  including channels, initial CSV versions, and operator packages selected from
  catalogs at deployment.
- Plugin Helm charts, explicit container image references, and helper images called
  by playbooks, including image defaults used by playbooks.
- OpenShift catalog images, control binaries, the mirror registry, and the
  `clairctl` utility.
- Payload components that playbooks resolve from OpenShift releases, such as
  Ironic, Bare Metal Operator, `openshift-install`, and the RHCOS live ISO.

The default OpenShift release, default operators, and default storage plugin are
marked in CycloneDX component properties. Optional plugin components are marked as
optional. Image tags are recorded as configured; the SBOM does not resolve mutable
tags to registry digests. OpenShift payload components are identified by the
release from which playbooks resolve them because their individual versions are
not pinned in repository files.

This is a repository and defaults inventory. A site's configuration can select a
different OpenShift version, enable different plugins, or override image tags, so
the SBOM does not claim to be a deployment-specific inventory of pulled image
digests.

## Get the SBOM

The SBOM is inside the tarball published to Quay. Pull the release and extract the
SBOM file:

```bash
oras pull quay.io/edge-infrastructure/enclave:<release-tag>
tar -xzf enclave.tar.gz ./enclave.cdx.json
```

Pull request builds upload the tarball, which contains the same SBOM, as a
short-lived GitHub Actions artifact.

## Generate locally

With the project dependencies installed, generate an SBOM from the current
repository contents:

```bash
python scripts/generate_sbom.py \
  --version <release-tag> \
  --revision "$(git rev-parse HEAD)" \
  --output enclave.cdx.json
```
