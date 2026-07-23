Name:           enclave
Version:        %{enclave_version}
Release:        1%{?dist}
Summary:        Red Hat Sovereign Enclave — deployment platform for OpenShift on bare metal
License:        Apache-2.0
URL:            https://github.com/rh-ecosystem-edge/enclave
BuildArch:      noarch

Source0:        enclave-%{enclave_version}.tar.gz

Requires:       bind-utils
Requires:       curl
Requires:       git-core
Requires:       httpd
Requires:       ipcalc
Requires:       jq
Requires:       lsof
Requires:       make
Requires:       nmstate
Requires:       openssl
Requires:       podman
Requires:       python3
Requires:       rsync
Requires:       skopeo
Requires:       tar
Requires:       unzip

%description
Red Hat Sovereign Enclave (RHSE) is an optionally disconnected infrastructure
platform that delivers a cloud-like experience based on OpenShift. It provisions
and maintains OpenShift clusters on bare metal hardware, supports a local
management plane (ACM, Quay), and controls software ingress into air-gapped
environments.

This package installs the enclave distribution to /opt/enclave with playbooks,
scripts, schemas, plugins, and the Python CLI source. After installing, run
'make setup' from /opt/enclave to bootstrap the Python and Ansible environment.

%prep
%setup -q -n enclave-%{enclave_version}

%build
%dnl Nothing to build — interpreted code only (Python, Ansible, Bash, Jinja2)

%install
mkdir -p %{buildroot}/opt/enclave
cp -a . %{buildroot}/opt/enclave

# Remove dev-only files that must not ship
rm -rf %{buildroot}/opt/enclave/.github
rm -rf %{buildroot}/opt/enclave/.githooks
rm -rf %{buildroot}/opt/enclave/.claude
rm -rf %{buildroot}/opt/enclave/.ruff_cache
rm -rf %{buildroot}/opt/enclave/.pytest_cache
rm -rf %{buildroot}/opt/enclave/scripts
rm -rf %{buildroot}/opt/enclave/src/tests
rm -rf %{buildroot}/opt/enclave/test-fixtures
rm -rf %{buildroot}/opt/enclave/hack
rm -rf %{buildroot}/opt/enclave/out
rm -rf %{buildroot}/opt/enclave/artifacts
rm -rf %{buildroot}/opt/enclave/docs/superpowers
rm -f  %{buildroot}/opt/enclave/.coderabbit.yaml
rm -f  %{buildroot}/opt/enclave/.ansible-lint
rm -f  %{buildroot}/opt/enclave/.yamllint.yml
rm -f  %{buildroot}/opt/enclave/.gitignore
rm -f  %{buildroot}/opt/enclave/.python-version
rm -f  %{buildroot}/opt/enclave/.coverage
rm -f  %{buildroot}/opt/enclave/Makefile.ci
rm -f  %{buildroot}/opt/enclave/CLAUDE.md
rm -f  %{buildroot}/opt/enclave/AGENTS.md
rm -f  %{buildroot}/opt/enclave/CONTRIBUTING.md
find %{buildroot}/opt/enclave/plugins -type d -name test-fixtures -exec rm -rf {} + 2>/dev/null || :

%post
for f in /opt/enclave/config/*.example.yaml; do
    [ -f "$f" ] || continue
    target="${f%.example.yaml}.yaml"
    [ -f "$target" ] || cp "$f" "$target"
done
for f in /opt/enclave/config/plugins/*.example.yaml; do
    [ -f "$f" ] || continue
    target="${f%.example.yaml}.yaml"
    [ -f "$target" ] || cp "$f" "$target"
done

%postun
if [ $1 -eq 0 ]; then
    rm -f /opt/enclave/config/global.yaml
    rm -f /opt/enclave/config/certificates.yaml
    rm -f /opt/enclave/config/cloud_infra.yaml
    rm -f /opt/enclave/config/plugins/lvms.yaml
    rm -f /opt/enclave/config/plugins/odf.yaml
    rm -f /opt/enclave/config/plugins/osac.yaml
    rm -f /opt/enclave/config/plugins/rhbk.yaml
    rm -f /opt/enclave/config/plugins/vast-csi.yaml
    rm -rf /opt/enclave/.local
    rm -rf /opt/enclave/.cache
    rm -rf /opt/enclave/collections
fi

%files
/opt/enclave

%changelog
* Tue Jul 07 2026 Ricardo Piccoli <rpiccoli@redhat.com> - 0.1.0-1
- Initial RPM packaging with Mock-based build system
