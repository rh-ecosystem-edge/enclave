#!/usr/bin/env python3
"""
Parse OpenSnitch journald JSON logs and produce a normalized YAML egress allow list.

Usage:
    analyze_egress.py <log_file> [<log_file> ...]

    log_file  Path(s) to journald JSON log file(s) (journalctl -u opensnitchd -o json)

Output: Complete YAML document to stdout (docs/EGRESS_ALLOWLIST.yaml format).

Log format (MESSAGE field from opensnitchd journald entries):
    [action] src=<ip>:<port> dst=<ip>:<port> proto=<tcp|udp> ... host=<fqdn>

RFC1918 / loopback destinations are excluded from the output.
"""

import gzip
import ipaddress
import json
import re
import sys
from typing import Dict, List, Optional, Set, Tuple

# RFC1918 and special-use networks to exclude from the allow list
EXCLUDED_NETWORKS = [
    ipaddress.ip_network("10.0.0.0/8"),
    ipaddress.ip_network("172.16.0.0/12"),
    ipaddress.ip_network("192.168.0.0/16"),
    ipaddress.ip_network("127.0.0.0/8"),
    ipaddress.ip_network("169.254.0.0/16"),
    ipaddress.ip_network("::1/128"),
    ipaddress.ip_network("fe80::/10"),
    ipaddress.ip_network("100.64.0.0/10"),
]

# Patterns to extract connection fields from the opensnitchd MESSAGE text.
# OpenSnitch logs connections in two possible formats:
#   1. Old format: [allow] src=1.2.3.4:PORT dst=5.6.7.8:PORT proto=tcp ... host=example.com
#   2. RFC5424 format (with syslog logger): DST="1.2.3.4" DSTHOST="example.com" DPT="443" PROTO="tcp"
# Fields may appear in any order; host/DSTHOST is optional (absent for IP-only destinations).

# Old format patterns
FIELD_PATTERNS_OLD = {
    "dst":   re.compile(r"\bdst[=:](\S+)"),
    "proto": re.compile(r"\bproto[=:](\w+)"),
    "host":  re.compile(r"\bhost[=:](\S+)"),
}

# RFC5424 format patterns
FIELD_PATTERNS_RFC5424 = {
    "dst":     re.compile(r'\bDST="([^"]+)"'),
    "proto":   re.compile(r'\bPROTO="(\w+)"'),
    "host":    re.compile(r'\bDSTHOST="([^"]+)"'),
    "port":    re.compile(r'\bDPT="(\d+)"'),
    "path":    re.compile(r'\bPATH="([^"]+)"'),
    "cmdline": re.compile(r'\bCMDLINE="([^"]*)"'),
}


def classify_caller(path: str, cmdline: str) -> str:
    """Classify caller into a category based on binary path and command line."""
    import os
    basename = os.path.basename(path)

    # Direct binary classifications
    if basename == "clairctl":
        return "clair"
    if basename == "oc-mirror" or "/oc-mirror" in path:
        return "oc-mirror"
    if basename == "oc":
        return "oc"
    if basename in ("podman", "skopeo"):
        return "podman"
    if basename == "openshift-install":
        return "openshift-install"

    # Python calls — classify by cmdline keywords
    if basename.startswith("python"):
        if "pip" in cmdline or "pip3" in cmdline:
            return "pip"
        if "dnf" in cmdline:
            return "dnf"
        if "ansible-galaxy" in cmdline:
            return "ansible"
        if "ansible-tmp-" in cmdline or "/ansible/tmp/" in cmdline:
            return "ansible"
        # Generic python caller without specific markers
        return "other"

    # Fallback
    return "other"


def is_excluded(ip_str: str) -> bool:
    try:
        addr = ipaddress.ip_address(ip_str)
    except ValueError:
        # Unparseable destination — exclude to avoid leaking garbage into the allow list.
        return True
    return any(addr in net for net in EXCLUDED_NETWORKS)


def parse_dst(dst_field: str) -> Optional[Tuple[str, str]]:
    """Split 'ip:port' or '[ipv6]:port' into (ip, port). Returns None on failure."""
    if dst_field.startswith("["):
        # IPv6 bracket notation: [::1]:443
        m = re.match(r"\[(.+)\]:(\d+)$", dst_field)
        if m:
            return m.group(1), m.group(2)
        return None
    parts = dst_field.rsplit(":", 1)
    if len(parts) == 2:
        return parts[0], parts[1]
    return None


def parse_message(message: str) -> Optional[Dict]:
    """Extract connection fields from a single opensnitchd log MESSAGE. Returns None to skip."""
    # Try RFC5424 format first (newer, with syslog logger)
    if 'DST="' in message:
        return parse_message_rfc5424(message)
    # Fall back to old format
    if "dst" in message:
        return parse_message_old(message)
    return None


def parse_message_rfc5424(message: str) -> Optional[Dict]:
    """Parse RFC5424 syslog format: DST="ip" DSTHOST="host" DPT="port" PROTO="proto" PATH="..." CMDLINE="..." """
    fields = {}
    for name, pat in FIELD_PATTERNS_RFC5424.items():
        m = pat.search(message)
        if m:
            fields[name] = m.group(1)

    if "dst" not in fields or "port" not in fields:
        return None

    dst_ip = fields["dst"]
    if is_excluded(dst_ip):
        return None

    proto = fields.get("proto", "tcp").lower()
    host = fields.get("host", "")

    # Skip entries with no resolvable hostname
    if not host or host == "-":
        return None

    try:
        port = int(fields["port"])
    except ValueError:
        return None

    # Classify caller from PATH and CMDLINE
    path = fields.get("path", "")
    cmdline = fields.get("cmdline", "")
    category = classify_caller(path, cmdline) if path else "other"

    return {"dns": host, "protocol": proto, "port": port, "category": category}


def parse_message_old(message: str) -> Optional[Dict]:
    """Parse old format: src=... dst=ip:port proto=... host=..."""
    fields = {}
    for name, pat in FIELD_PATTERNS_OLD.items():
        m = pat.search(message)
        if m:
            fields[name] = m.group(1).strip(",")

    if "dst" not in fields:
        return None

    parsed = parse_dst(fields["dst"])
    if not parsed:
        return None
    dst_ip, dst_port = parsed

    if is_excluded(dst_ip):
        return None

    proto = fields.get("proto", "tcp").lower()
    host = fields.get("host", "")

    # Skip entries with no resolvable hostname
    if not host or host == "-":
        return None

    try:
        port = int(dst_port)
    except ValueError:
        return None

    # Old format doesn't have PATH/CMDLINE — category is unknown
    return {"dns": host, "protocol": proto, "port": port, "category": "other"}


def parse_log_file(path: str) -> List[Dict]:
    """Read a journald JSON or plain text log file and return deduplicated connection entries with sources."""
    destinations: Dict[Tuple[str, int, str], Set[str]] = {}

    opener = gzip.open if path.endswith(".gz") else open
    with opener(path, "rt", encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue

            message = ""
            try:
                record = json.loads(line)
                message = record.get("MESSAGE", "")
                if isinstance(message, list):
                    # Journald encodes non-printable chars (ANSI codes) as byte arrays
                    message = bytes(message).decode("utf-8", errors="replace")
                if not isinstance(message, str):
                    continue
            except json.JSONDecodeError:
                # Not JSON — treat the line itself as a raw log message
                message = line

            entry = parse_message(message)
            if not entry:
                continue

            key = (entry["dns"], entry["port"], entry["protocol"])
            if key not in destinations:
                destinations[key] = set()
            destinations[key].add(entry["category"])

    # Convert to list format
    entries = [
        {"dns": dns, "port": port, "protocol": proto, "source": sorted(sources)}
        for (dns, port, proto), sources in destinations.items()
    ]

    return sorted(entries, key=lambda e: (e["dns"], e["protocol"], e["port"]))


def parse_log_files(paths: List[str]) -> List[Dict]:
    """Parse multiple log files and return merged, deduplicated entries with combined sources."""
    destinations: Dict[Tuple[str, int, str], Set[str]] = {}

    for path in paths:
        for entry in parse_log_file(path):
            key = (entry["dns"], entry["port"], entry["protocol"])
            if key not in destinations:
                destinations[key] = set()
            destinations[key].update(entry["source"])

    # Convert to list format
    entries = [
        {"dns": dns, "port": port, "protocol": proto, "source": sorted(sources)}
        for (dns, port, proto), sources in destinations.items()
    ]

    return sorted(entries, key=lambda e: (e["dns"], e["protocol"], e["port"]))


def render_yaml(entries: List[Dict]) -> str:
    """Render entries as a complete YAML document, flat list sorted by destination."""
    import yaml

    header = """\
# Egress Connectivity Allow List
#
# This list documents the external network destinations contacted during a full
# Enclave Lab deployment, based on OpenSnitch traffic captures from CI runs.
# Auto-generated by scripts/egress/analyze_egress.py — do not edit manually.
#
# Each entry shows:
#   dns:      Destination hostname
#   port:     Destination port
#   protocol: tcp or udp
#   source:   Which tools initiated connections (ansible, pip, podman, etc.)
#
# IMPORTANT: This list serves as a reference baseline, not a fixed specification.
# Actual destinations may vary due to:
#   - CDN/mirror selection based on geography and load balancing
#   - Package repository infrastructure changes
#   - Upstream dependency updates
#
# Use this as a starting point for firewall rules, but monitor and adjust based
# on your specific deployment environment and requirements.
"""

    # Custom dumper for proper list indentation (2 spaces before dash)
    class IndentedDumper(yaml.Dumper):
        def increase_indent(self, flow=False, indentless=False):
            return super(IndentedDumper, self).increase_indent(flow, False)

    document = {"egress": entries}
    yaml_output = yaml.dump(document, Dumper=IndentedDumper, default_flow_style=False, sort_keys=False)
    return (header + yaml_output).rstrip('\n') + '\n'


def main() -> None:
    if len(sys.argv) < 2:
        print(f"Usage: {sys.argv[0]} <log_file> [<log_file> ...]", file=sys.stderr)
        sys.exit(1)

    log_files = sys.argv[1:]
    entries = parse_log_files(log_files)
    sys.stdout.write(render_yaml(entries))


if __name__ == "__main__":
    main()
