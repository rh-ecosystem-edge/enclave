#!/usr/bin/env python3
"""
Inject an installation gate service into an agent-based installer ISO.

This script modifies the ignition config embedded inside the ISO's
/images/ignition.img so that start-cluster-installation.service is
blocked until a signal file is created on the node.

Usage:
    python3 agent-gate-inject.py <agent.iso>

To release the gate on the booted node:
    touch /var/tmp/proceed-with-installation

Requires: Python 3.6+  (no external dependencies)
"""

import argparse
import gzip
import io
import json
import mmap
import struct
import sys

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

ISO_SECTOR_SIZE = 2048
# Primary Volume Descriptor starts at sector 16 (byte 32768)
PVD_OFFSET = 16 * ISO_SECTOR_SIZE

GATE_SCRIPT = """\
#!/bin/bash
echo "=========================================="
echo "  Installation gate is ACTIVE"
echo "  Waiting for release signal..."
echo "  To proceed, run:"
echo "    touch /var/tmp/proceed-with-installation"
echo "=========================================="
while [ ! -f /var/tmp/proceed-with-installation ]; do
  sleep 5
done
echo "Gate released. Proceeding with installation..."
"""

GATE_SERVICE_UNIT = """\
[Unit]
Description=Installation gate - blocks start-cluster-installation
Before=start-cluster-installation.service

[Service]
Type=oneshot
ExecStart=/usr/local/bin/installation-gate.sh
RemainAfterExit=yes
StandardOutput=journal+console

[Install]
RequiredBy=start-cluster-installation.service
"""

GATE_DROPIN = """\
[Unit]
Requires=installation-gate.service
After=installation-gate.service
"""


# ---------------------------------------------------------------------------
# ISO 9660 helpers  (read-only, just enough to locate files)
# ---------------------------------------------------------------------------

def read_iso9660_string(data, offset, length):
    return data[offset:offset + length].decode("ascii", errors="replace").strip()


def find_file_in_directory(iso_data, dir_offset, dir_length, target_name):
    """Walk an ISO 9660 directory and return (offset, size) of target_name."""
    pos = 0
    while pos < dir_length:
        record_len = iso_data[dir_offset + pos]
        if record_len == 0:
            # skip to next sector boundary
            next_sector = ((pos // ISO_SECTOR_SIZE) + 1) * ISO_SECTOR_SIZE
            if next_sector >= dir_length:
                break
            pos = next_sector
            continue

        extent_loc = struct.unpack_from("<I", iso_data, dir_offset + pos + 2)[0]
        data_len = struct.unpack_from("<I", iso_data, dir_offset + pos + 10)[0]
        name_len = iso_data[dir_offset + pos + 32]
        name_raw = iso_data[dir_offset + pos + 33: dir_offset + pos + 33 + name_len]

        # Strip version number (;1)
        name = name_raw.decode("ascii", errors="replace").split(";")[0].strip(".")

        if name.upper() == target_name.upper():
            return extent_loc * ISO_SECTOR_SIZE, data_len

        pos += record_len

    return None, None


def locate_iso_file(iso_data, path):
    """Locate a file in the ISO by path (e.g. '/images/ignition.img').
    Returns (byte_offset, byte_length)."""
    parts = [p for p in path.strip("/").split("/") if p]

    # Read root directory from PVD
    root_entry_offset = PVD_OFFSET + 156
    root_extent = struct.unpack_from("<I", iso_data, root_entry_offset + 2)[0]
    root_size = struct.unpack_from("<I", iso_data, root_entry_offset + 10)[0]

    cur_offset = root_extent * ISO_SECTOR_SIZE
    cur_size = root_size

    for part in parts:
        offset, size = find_file_in_directory(iso_data, cur_offset, cur_size, part)
        if offset is None:
            raise FileNotFoundError(f"Cannot find '{part}' in ISO path '{path}'")
        cur_offset = offset
        cur_size = size

    return cur_offset, cur_size


# ---------------------------------------------------------------------------
# CPIO helpers  (newc / SVR4 format, which is what RHCOS uses)
# ---------------------------------------------------------------------------

def cpio_newc_header(name_bytes, data_len, mode=0o100644):
    """Build a CPIO 'newc' header."""
    name_with_nul = name_bytes + b"\x00"
    hdr = b"070701"
    hdr += b"%08X" % 0          # ino
    hdr += b"%08X" % mode       # mode
    hdr += b"%08X" % 0          # uid
    hdr += b"%08X" % 0          # gid
    hdr += b"%08X" % 1          # nlink
    hdr += b"%08X" % 0          # mtime
    hdr += b"%08X" % data_len   # filesize
    hdr += b"%08X" % 0          # devmajor
    hdr += b"%08X" % 0          # devminor
    hdr += b"%08X" % 0          # rdevmajor
    hdr += b"%08X" % 0          # rdevminor
    hdr += b"%08X" % len(name_with_nul)  # namesize
    hdr += b"%08X" % 0          # check
    hdr += name_with_nul

    # Pad header+name to 4-byte boundary
    total = len(hdr)
    pad = (4 - (total % 4)) % 4
    hdr += b"\x00" * pad
    return hdr


def build_cpio_archive(entries):
    """Build a newc CPIO archive from [(path, content_bytes, mode), ...]."""
    buf = io.BytesIO()
    for path, content, mode in entries:
        name_bytes = path.encode("ascii")
        hdr = cpio_newc_header(name_bytes, len(content), mode)
        buf.write(hdr)
        buf.write(content)
        # Pad content to 4-byte boundary
        pad = (4 - (len(content) % 4)) % 4
        buf.write(b"\x00" * pad)

    # TRAILER
    trailer_name = b"TRAILER!!!\x00"
    trailer_hdr = cpio_newc_header(b"TRAILER!!!", 0, 0)
    buf.write(trailer_hdr)

    # Pad entire archive to 4-byte boundary
    total = buf.tell()
    pad = (4 - (total % 4)) % 4
    buf.write(b"\x00" * pad)
    return buf.getvalue()


def parse_cpio_archive(data):
    """Parse a newc CPIO archive. Returns [(path, content, mode), ...]."""
    entries = []
    pos = 0
    while pos < len(data):
        if pos + 110 > len(data):
            break
        magic = data[pos:pos + 6]
        if magic != b"070701":
            break

        mode = int(data[pos + 14:pos + 22], 16)
        filesize = int(data[pos + 54:pos + 62], 16)
        namesize = int(data[pos + 94:pos + 102], 16)

        name_start = pos + 110
        name_end = name_start + namesize - 1  # exclude trailing NUL
        name = data[name_start:name_end].decode("ascii", errors="replace")

        # Align after header + name
        header_plus_name = 110 + namesize
        data_start = name_start + namesize
        data_start += (4 - (header_plus_name % 4)) % 4

        content = data[data_start:data_start + filesize]

        if name == "TRAILER!!!":
            break

        entries.append((name, content, mode))

        # Align after data
        pos = data_start + filesize
        pos += (4 - (pos % 4)) % 4

    return entries


# ---------------------------------------------------------------------------
# Ignition manipulation
# ---------------------------------------------------------------------------

def add_gate_to_ignition(ign_json):
    """Add the gate service, script, and drop-in to the ignition config."""
    ign = json.loads(ign_json)

    # Ensure storage.files exists
    storage = ign.setdefault("storage", {})
    files = storage.setdefault("files", [])

    # Add the gate script
    import base64
    script_b64 = base64.b64encode(GATE_SCRIPT.encode()).decode()
    files.append({
        "path": "/usr/local/bin/installation-gate.sh",
        "mode": 0o755,
        "overwrite": True,
        "contents": {
            "source": f"data:text/plain;base64,{script_b64}"
        }
    })

    # Ensure systemd.units exists
    systemd = ign.setdefault("systemd", {})
    units = systemd.setdefault("units", [])

    # Add the gate service unit
    units.append({
        "name": "installation-gate.service",
        "enabled": True,
        "contents": GATE_SERVICE_UNIT
    })

    # Add a drop-in to start-cluster-installation.service
    # Check if it already exists in the list
    found = False
    for unit in units:
        if unit.get("name") == "start-cluster-installation.service":
            dropins = unit.setdefault("dropins", [])
            dropins.append({
                "name": "10-wait-for-gate.conf",
                "contents": GATE_DROPIN
            })
            found = True
            break

    if not found:
        units.append({
            "name": "start-cluster-installation.service",
            "dropins": [{
                "name": "10-wait-for-gate.conf",
                "contents": GATE_DROPIN
            }]
        })

    return json.dumps(ign, indent=2).encode()


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(
        description="Inject installation gate service into agent ISO"
    )
    parser.add_argument("iso", help="Path to the agent ISO file (modified in-place)")
    parser.add_argument("--dry-run", action="store_true",
                        help="Print modified ignition JSON to stdout without writing to ISO")
    args = parser.parse_args()

    # Memory-map the ISO to avoid loading the entire file into RAM
    print(f"Reading ISO: {args.iso}")
    iso_file = open(args.iso, "r+b")
    iso_data = mmap.mmap(iso_file.fileno(), 0)

    # --- Locate ignition embed area ---
    # First try to read /coreos/igninfo.json for the embed area metadata
    try:
        igninfo_offset, igninfo_size = locate_iso_file(iso_data, "/coreos/igninfo.json")
        igninfo = json.loads(bytes(iso_data[igninfo_offset:igninfo_offset + igninfo_size]))
        ign_file_path = igninfo.get("file", "/images/ignition.img")
        embed_offset_in_file = igninfo.get("offset", 0)
        embed_length = igninfo.get("length", 0)
        print(f"Found igninfo.json: file={ign_file_path}, offset={embed_offset_in_file}, length={embed_length}")
    except FileNotFoundError:
        # Fallback for older RHCOS versions
        ign_file_path = "/images/ignition.img"
        embed_offset_in_file = 0
        embed_length = 0
        print("No igninfo.json found, using default ignition.img location")

    # Locate the ignition image file in the ISO
    ign_img_offset, ign_img_size = locate_iso_file(iso_data, ign_file_path)
    print(f"Located {ign_file_path} at ISO offset {ign_img_offset}, size {ign_img_size}")

    if embed_length == 0:
        embed_length = ign_img_size
    abs_embed_offset = ign_img_offset + embed_offset_in_file

    # --- Extract the gzip-compressed CPIO from the embed area ---
    embed_data = bytes(iso_data[abs_embed_offset:abs_embed_offset + embed_length])

    # Find the actual gzip data (skip any leading zeros)
    gz_start = None
    for i in range(len(embed_data) - 1):
        if embed_data[i] == 0x1f and embed_data[i + 1] == 0x8b:
            gz_start = i
            break

    if gz_start is None:
        print("ERROR: No gzip data found in ignition embed area.", file=sys.stderr)
        print("The embed area may be empty (no ignition config embedded yet).", file=sys.stderr)
        sys.exit(1)

    # Decompress
    try:
        decompressed = gzip.decompress(embed_data[gz_start:])
    except Exception as e:
        print(f"ERROR: Failed to decompress ignition data: {e}", file=sys.stderr)
        sys.exit(1)

    # --- Parse CPIO archive ---
    entries = parse_cpio_archive(decompressed)
    print(f"CPIO archive contains {len(entries)} file(s):")
    for path, content, mode in entries:
        print(f"  {path} ({len(content)} bytes, mode {oct(mode)})")

    # --- Find and modify config.ign ---
    ign_idx = None
    for i, (path, content, mode) in enumerate(entries):
        if path == "config.ign":
            ign_idx = i
            break

    if ign_idx is None:
        print("ERROR: config.ign not found in CPIO archive.", file=sys.stderr)
        sys.exit(1)

    original_ign = entries[ign_idx][1]
    print(f"\nOriginal ignition config: {len(original_ign)} bytes")

    modified_ign = add_gate_to_ignition(original_ign)
    print(f"Modified ignition config: {len(modified_ign)} bytes")

    if args.dry_run:
        print("\n--- Modified ignition config ---")
        print(modified_ign.decode())
        print("--- Dry run: ISO not modified ---")
        iso_data.close()
        iso_file.close()
        return

    # Replace config.ign in the entries
    entries[ign_idx] = ("config.ign", modified_ign, entries[ign_idx][2])

    # --- Rebuild gzip-compressed CPIO ---
    new_cpio = build_cpio_archive(entries)
    compressed_buf = io.BytesIO()
    with gzip.GzipFile(fileobj=compressed_buf, mode="wb") as gz:
        gz.write(new_cpio)
    new_compressed = compressed_buf.getvalue()

    # Pad to 4-byte boundary
    pad = (4 - (len(new_compressed) % 4)) % 4
    new_compressed += b"\x00" * pad

    print(f"New compressed CPIO: {len(new_compressed)} bytes (embed area: {embed_length} bytes)")

    if len(new_compressed) > embed_length:
        print("ERROR: Modified ignition data exceeds embed area size!", file=sys.stderr)
        print(f"  Need: {len(new_compressed)} bytes", file=sys.stderr)
        print(f"  Have: {embed_length} bytes", file=sys.stderr)
        print("The ignition config is too large to fit in the ISO's embed area.", file=sys.stderr)
        sys.exit(1)

    # --- Write new data into the ISO ---
    # Zero out the embed area and write the new compressed data
    patched = b"\x00" * embed_length
    patched = new_compressed + patched[len(new_compressed):]
    iso_data[abs_embed_offset:abs_embed_offset + embed_length] = patched
    iso_data.flush()
    iso_data.close()
    iso_file.close()

    print(f"\nISO modified successfully: {args.iso}")
    print()
    print("After booting the ISO, the installation will be paused.")
    print("To release the gate and start installation, SSH into the node and run:")
    print("  touch /var/tmp/proceed-with-installation")


if __name__ == "__main__":
    main()