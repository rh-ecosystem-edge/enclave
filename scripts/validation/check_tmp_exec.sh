#!/bin/bash
set -euo pipefail

probe_dir=''
trap '[[ -z "$probe_dir" ]] || rm -rf -- "$probe_dir"' EXIT
trap 'exit 129' HUP
trap 'exit 130' INT
trap 'exit 143' TERM

fail() {
    printf 'FAIL temporary execution (%s): %s\n' "${1}" "${TMPDIR:-/tmp}" >&2
    printf '%s\n' 'Select an administrator-approved writable, executable TMPDIR and retry. Privileged setup uses the environment allowed by sudo; a user TMPDIR may not carry through. Do not change noexec policies.' >&2
    exit 1
}

probe_dir=$(mktemp -d) || fail 'cannot create directory'
cp -- /usr/bin/true "$probe_dir/probe" || fail 'cannot copy trusted native executable'
"$probe_dir/probe" || fail 'native execution denied or failed'
printf 'PASS temporary native execution: %s (uid %s)\n' "${TMPDIR:-/tmp}" "$(id -u)"
