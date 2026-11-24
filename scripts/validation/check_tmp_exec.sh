#!/bin/bash
set -euo pipefail

probe_dir=''
trap '[[ -z "$probe_dir" ]] || /usr/bin/rm -rf -- "$probe_dir"' EXIT
trap 'exit 129' HUP
trap 'exit 130' INT
trap 'exit 143' TERM

fail() {
    printf 'FAIL temporary execution (%s): %s\n' "${1}" "${TMPDIR:-/tmp}" >&2
    printf '%s\n' 'Select an administrator-approved writable, executable TMPDIR and retry. Privileged setup uses the environment allowed by sudo; a user TMPDIR may not carry through. Do not change noexec policies.' >&2
    exit 1
}

require_trusted_directory() {
    local metadata owner mode
    metadata=$(/usr/bin/stat -c '%u %f' -- "$1") || fail 'cannot inspect privileged TMPDIR ancestry'
    owner=${metadata%% *}
    mode=${metadata#* }
    [[ "$owner" == 0 && "$mode" =~ ^[[:xdigit:]]+$ ]] || fail 'untrusted privileged TMPDIR ancestry'
    mode=$((16#$mode))
    (( (mode & 0170000) == 0040000 )) || fail 'privileged TMPDIR ancestry must not contain symlinks'
    if (( mode & 0022 )); then
        [[ "$2" == /tmp ]] && (( mode & 01000 )) || fail 'writable privileged TMPDIR ancestry'
    fi
}

check_privileged_tmpdir() (
    local remaining component checked='' CDPATH=''
    [[ "$1" == /* && "$1" != *//* && "$1" != *[[:cntrl:]]* ]] || fail 'privileged TMPDIR must be an absolute directory without control characters'
    [[ "$1" == / || "$1" != */ ]] || fail 'privileged TMPDIR must not have a trailing slash'
    require_trusted_directory / /
    cd -P -- /
    remaining=${1#/}
    while [[ -n "$remaining" ]]; do
        component=${remaining%%/*}
        [[ "$component" != . && "$component" != .. ]] || fail 'privileged TMPDIR must not contain dot components'
        checked+="/$component"
        require_trusted_directory "$component" "$checked"
        cd -P -- "$component" || fail 'cannot enter privileged TMPDIR ancestry'
        if [[ "$remaining" == */* ]]; then
            remaining=${remaining#*/}
        else
            remaining=''
        fi
    done
)

if (( EUID == 0 )); then
    check_privileged_tmpdir "${TMPDIR:-/tmp}"
fi

probe_dir=$(/usr/bin/mktemp -d) || fail 'cannot create directory'
/usr/bin/cp -- /usr/bin/true "$probe_dir/probe" || fail 'cannot copy trusted native executable'
"$probe_dir/probe" || fail 'native execution denied or failed'
printf 'PASS temporary native execution: %s (uid %s)\n' "${TMPDIR:-/tmp}" "$(id -u)"
