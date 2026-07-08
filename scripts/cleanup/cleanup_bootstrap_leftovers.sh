#!/usr/bin/env bash
# Pre-bootstrap cleanup: removes Metal3 and Quay resources from a previous installation.
#
# Designed to be idempotent — commands that fail because a resource no longer exists are ignored.
# WORKING_DIR must be set in the environment (handled by the enclave environment cleanup command).

set -u

_ts() { date +'%Y-%m-%dT%H:%M:%S'; }
info()    { echo "$(_ts) INFO     $1"; }
warning() { echo "$(_ts) WARNING  $1"; }
error()   { echo "$(_ts) ERROR    $1"; }

workingDir="${WORKING_DIR:?WORKING_DIR must be set}"

info "Starting pre-bootstrap cleanup (working dir: ${workingDir})"

# Stop and disable metal3 systemd services
info "Stopping metal3 systemd services..."
sudo systemctl stop metal3-bmo.service metal3-ironic-api.service metal3-httpd.service metal3-ironic-pod.service
sudo systemctl reset-failed metal3-bmo.service metal3-ironic-api.service metal3-httpd.service metal3-ironic-pod.service
sudo systemctl disable metal3-bmo.service metal3-ironic-api.service metal3-httpd.service metal3-ironic-pod.service
info "Metal3 services stopped and disabled"

# Remove quadlet unit files and reload systemd
info "Removing metal3 quadlet unit files..."
sudo rm -f /etc/containers/systemd/metal3-bmo.container
sudo rm -f /etc/containers/systemd/metal3-ironic-api.container
sudo rm -f /etc/containers/systemd/metal3-httpd.container
sudo rm -f /etc/containers/systemd/metal3-ironic.pod
sudo systemctl daemon-reload
info "Quadlet files removed"

# Remove root podman resources
info "Removing root podman resources..."
sudo podman pod rm -f metal3-ironic
sudo podman rm -f ironic httpd baremetal-operator
sudo podman volume rm -f metal3-ironic-conf metal3-ironic-data metal3-ironic-shared
sudo podman secret rm -i metal3-ironic-htpasswd metal3-ironic-password metal3-kubeconfig metal3-ironic-username metal3-ca-bundle
info "Root podman resources removed"

# Remove user podman resources
info "Removing user podman resources..."
podman pod rm -f metal3-ironic
podman rm -f baremetal-operator
podman volume rm -f metal3-ironic-conf metal3-ironic-data metal3-ironic-shared quay-storage
podman secret rm --ignore metal3-ironic-htpasswd metal3-ironic-username metal3-ca-bundle metal3-ironic-password
info "User podman resources removed"

# Remove Quay registry
info "Removing Quay registry..."
if [ -x "${workingDir}/bin/mirror-registry" ]; then
    "${workingDir}/bin/mirror-registry" uninstall --quayRoot "${workingDir}/data" --autoApprove -v
fi
podman pod rm -f quay-pod
podman secret rm -i pgdb_pass redis_pass
info "Quay registry removed"

info "Wiping kubeconfig symlink ~/.config/enclave/kubeconfig..."
rm -f ~/.config/enclave/kubeconfig
info "kubeconfig symlink wiped"

# Remove discovery cron job (legacy: previously installed by older deployments)
info "Removing discovery cron job..."
crontab -l 2>/dev/null | grep -v "07-configure-discovery" | crontab -
info "Discovery cron job removed"
#
# Wipe working directory contents
info "Wiping working directory ${workingDir}..."
resolvedWorkingDir="$(readlink -f -- "${workingDir}")"
if [ -z "${resolvedWorkingDir}" ] || [ ! -d "${resolvedWorkingDir}" ]; then
    if [ -e "${resolvedWorkingDir}" ]; then
        error "Invalid WORKING_DIR (not a directory): ${workingDir}"
        exit 1
    fi
    warning "Working directory does not exist, skipping wipe: ${workingDir}"
    info "Pre-bootstrap cleanup complete"
    exit 0
fi

case "${resolvedWorkingDir}" in
    /|/etc|/usr|/bin|/sbin|/lib|/lib64|/boot|/proc|/sys|/dev|/var|/home|/root|/tmp)
        error "Refusing to wipe critical system path: ${resolvedWorkingDir}"
        exit 1
        ;;
esac
shopt -s dotglob nullglob
rm -fr -- "${resolvedWorkingDir:?}/"*
shopt -u dotglob
info "Working directory wiped"


info "Pre-bootstrap cleanup complete"
