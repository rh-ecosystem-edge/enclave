#!/bin/bash
# Re-attach Ceph loopback OSD devices on boot
#
# This script is called by the ceph-loopback.service systemd unit
# to re-attach loopback files and activate LVM before Ceph OSDs start.
#
# Environment:
#   LOOP_DIR - Directory containing OSD image files (default: /var/lib/ceph-loops)

LOOP_DIR="${LOOP_DIR:-/var/lib/ceph-loops}"

for img in "$LOOP_DIR"/osd-*.img; do
    [ -f "$img" ] || continue
    if ! losetup -j "$img" | grep -q .; then
        dev=$(losetup --find --show "$img")
        echo "Attached $img -> $dev"
    fi
done

# Activate Ceph VGs on the loop devices
for vg in $(vgs --noheadings -o vg_name 2>/dev/null | grep -o 'ceph-vg[0-9]*'); do
    vgchange -ay "$vg" 2>/dev/null && echo "Activated VG $vg"
done
