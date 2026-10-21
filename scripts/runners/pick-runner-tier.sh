#!/bin/bash
# Pick the runner tier for enclave-large e2e: prefer an idle runner in the
# enclave-ci-baremetal org group, else fall back to the elastic AWS fleet
# (enclave-ci). Best-effort cost optimization with AWS as the guaranteed floor:
# ANY failure (no token, API error, group missing, no idle runner) routes to
# AWS, and the script ALWAYS exits 0 with a valid choice.
#
# Called by .github/workflows/e2e-deployment.yml (pick-tier job). See
# docs/baremetal-routing.md in the enclave-ci-infra repo for the full design.
#
# Environment:
#   GH_TOKEN         Token with "Organization -> Self-hosted runners: Read".
#                    The default Actions GITHUB_TOKEN lacks this scope; the
#                    workflow mints one from a dedicated read-only GitHub App.
#                    Missing/empty -> fall back to AWS. (gh reads GH_TOKEN.)
#   GH_ORG           Org login (e.g. rh-ecosystem-edge). Missing -> AWS.
#   BAREMETAL_GROUP  Org runner group to prefer (default: enclave-ci-baremetal).
#   BAREMETAL_LABEL  Required runner label (default: enclave-large-baremetal).
#   GITHUB_OUTPUT    When set (GitHub Actions), group/labels are appended there;
#                    otherwise they are printed to stdout.
#
# Standalone debugging (outside the workflow):
#   GH_TOKEN="$(gh auth token)" GH_ORG=rh-ecosystem-edge \
#     ./scripts/runners/pick-runner-tier.sh
#
# NOTE: `set -e` is intentionally omitted — a failed API call must fall through
# to the AWS default, not abort the script.
set -uo pipefail

BAREMETAL_GROUP="${BAREMETAL_GROUP:-enclave-ci-baremetal}"
BAREMETAL_LABEL="${BAREMETAL_LABEL:-enclave-large-baremetal}"

# group + labels contract (see docs/baremetal-routing.md). The baremetal path
# uses a label the AWS scale-up matcher does NOT match, so routing to baremetal
# never trips the (group-blind) AWS autoscaler.
AWS_GROUP="enclave-ci"
AWS_LABELS='["self-hosted", "enclave-large-aws"]'
BAREMETAL_LABELS='["self-hosted", "enclave-large-baremetal"]'

# emit GROUP LABELS -> write the chosen pair to $GITHUB_OUTPUT (or stdout).
emit() {
  local group="$1" labels="$2"
  echo "pick-tier: routing to group=${group} labels=${labels}" >&2
  if [[ -n "${GITHUB_OUTPUT:-}" ]]; then
    {
      echo "group=${group}"
      echo "labels=${labels}"
    } >>"${GITHUB_OUTPUT}"
  else
    echo "group=${group}"
    echo "labels=${labels}"
  fi
}

# count_idle ORG GROUP LABEL -> prints the number of online, non-busy runners in
# GROUP carrying LABEL. Returns non-zero on any failure (caller falls back).
# `gh api --paginate` emits one JSON object per page; `jq -s` slurps them into an
# array so the result is a single correct total, not a per-page value. jq --arg
# is used for safe interpolation (no shell values spliced into the filter). With
# `set -o pipefail`, a gh or jq failure fails the pipeline and returns non-zero.
count_idle() {
  local org="$1" group="$2" label="$3" gid

  gid="$(gh api --paginate "/orgs/${org}/actions/runner-groups" \
    | jq -r -s --arg g "${group}" '[.[].runner_groups[] | select(.name==$g)][0].id // empty')" || return 1
  [[ -n "${gid}" ]] || return 1

  gh api --paginate "/orgs/${org}/actions/runner-groups/${gid}/runners" \
    | jq -s --arg l "${label}" '[.[].runners[]
        | select(.status=="online" and .busy==false)
        | select(any(.labels[]; .name==$l))]
       | length' || return 1
}

main() {
  local org="${GH_ORG:-}" idle

  if [[ -z "${GH_TOKEN:-}" || -z "${org}" ]]; then
    echo "pick-tier: GH_TOKEN or GH_ORG unset; defaulting to AWS" >&2
    emit "${AWS_GROUP}" "${AWS_LABELS}"
    return 0
  fi

  if ! idle="$(count_idle "${org}" "${BAREMETAL_GROUP}" "${BAREMETAL_LABEL}")" || [[ -z "${idle}" ]]; then
    echo "pick-tier: baremetal query failed (group missing or API error); defaulting to AWS" >&2
    emit "${AWS_GROUP}" "${AWS_LABELS}"
    return 0
  fi

  echo "pick-tier: ${BAREMETAL_GROUP} has ${idle} idle runner(s) with ${BAREMETAL_LABEL}" >&2
  if [[ "${idle}" =~ ^[0-9]+$ && "${idle}" -gt 0 ]]; then
    emit "${BAREMETAL_GROUP}" "${BAREMETAL_LABELS}"
  else
    emit "${AWS_GROUP}" "${AWS_LABELS}"
  fi
  return 0
}

main
exit 0
