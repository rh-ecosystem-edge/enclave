#!/bin/bash
set -euo pipefail

report() {
    printf -- '- name: "ntp_%s"\n  status: "%s"\n  description: "%s"\n' "$1" "$2" "$3"
}

sources=$(cat)
if ! jq -e '(. == null) or (type == "array" and all(.[]; type == "string" and test("\\A[A-Za-z0-9][A-Za-z0-9.:-]*\\z")))' <<< "$sources" >/dev/null; then
    report config FAIL 'Expected server hostnames or IP addresses, without options or whitespace'
    exit 1
fi
mapfile -t servers < <(jq -r '. // [] | .[]' <<< "$sources")
if [[ ${#servers[@]} -eq 0 ]]; then
    report config SKIP 'No additional sources configured; no fallback source queried'
    exit 0
fi
if ! command -v chronyd >/dev/null; then
    report prerequisite 'NOT TESTED' 'Install chrony via normal setup, then rerun validation'
    exit 2
fi
failed=0
for server in "${servers[@]}"; do
    if timeout 15s chronyd -Q -t 10 -u "$(id -un)" "server $server iburst" >/dev/null 2>&1; then
        report "$server" PASS 'Usable NTP response from Landing Zone; not a node-path test'
    else
        report "$server" FAIL 'No usable NTP response in bounded Landing Zone query; check DNS, UDP/123 and source synchronization'
        failed=1
    fi
done
exit "$failed"
