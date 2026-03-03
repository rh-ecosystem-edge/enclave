#!/bin/bash
# Fix libvirt for remote SSH access (virt-manager)
#
# Modern libvirt uses modular daemons (virtqemud, virtnetworkd, etc.)
# but remote tools expect the legacy socket path.

set -euo pipefail

if [ "$EUID" -ne 0 ]; then
    echo "ERROR: Run as root (sudo)"
    exit 1
fi

echo "Fixing libvirt for remote access..."
echo ""

# Check which libvirt daemons are running
echo "Current libvirt daemon status:"
systemctl status libvirtd 2>/dev/null || echo "  libvirtd: not running (using modular daemons)"
systemctl status virtqemud 2>/dev/null || echo "  virtqemud: not running"
systemctl status virtnetworkd 2>/dev/null || echo "  virtnetworkd: not running"

echo ""

# Check which sockets exist
echo "Current libvirt sockets:"
ls -la /var/run/libvirt/*.sock 2>/dev/null || echo "No sockets in /var/run/libvirt/"

echo ""

# Solution: Enable and start virtqemud-sock-ro (read-only socket for remote access)
echo "Enabling virtqemud sockets for remote access..."
systemctl enable --now virtqemud.socket
systemctl enable --now virtqemud-ro.socket
systemctl enable --now virtqemud-admin.socket

# Also enable virtnetworkd for network management
systemctl enable --now virtnetworkd.socket
systemctl enable --now virtnetworkd-ro.socket
systemctl enable --now virtnetworkd-admin.socket

echo ""
echo "Verifying sockets..."
sleep 2

ls -la /var/run/libvirt/virtqemud-sock* 2>/dev/null || echo "virtqemud sockets not found"
ls -la /var/run/libvirt/libvirt-sock* 2>/dev/null || echo "libvirt-sock not found"

echo ""
echo "Daemon status:"
systemctl is-active virtqemud.socket && echo "✓ virtqemud.socket active" || echo "✗ virtqemud.socket not active"
systemctl is-active virtnetworkd.socket && echo "✓ virtnetworkd.socket active" || echo "✗ virtnetworkd.socket not active"

echo ""
echo "✓ Done!"
echo ""
echo "Test remote connection with:"
echo "  virsh -c qemu+ssh://root@HOSTNAME/system list --all"
echo ""
echo "Or update virt-manager connection URI to use modular daemons:"
echo "  qemu+ssh://root@HOSTNAME/system?socket=/var/run/libvirt/virtqemud-sock"
echo ""
