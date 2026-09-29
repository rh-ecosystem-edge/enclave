#!/usr/bin/env bash
# Re-run e2e jobs whose spot runner was reclaimed by AWS mid-job.
#
# Invoked by .github/workflows/e2e-spot-retry.yml when an e2e run completes with failure.
# Lists the run's failed jobs and, for each, maps its runner name (which IS the EC2 instance
# id on the AWS self-hosted fleet, e.g. i-0abc...) to an AWS spot-reclaim signal:
#   - ec2 DescribeInstances StateTransitionReason == "Server.SpotInstanceTermination"
#   - fallback (instance aged out of DescribeInstances ~1h): a CloudTrail BidEvictedEvent.
# Only the jobs with a confirmed spot signal are re-run, and each is re-run individually
# (never `gh run rerun --failed`, which would also re-run a genuinely-failed sibling job in
# the same run). A genuine test failure carries no spot signal, so it is left red — that is
# what keeps this quiet.
#
# Requires: gh (GH_TOKEN with actions:write) and aws (read-only role via OIDC).
# Env: RUN_ID, REPO (owner/name).
set -euo pipefail

RUN_ID="${RUN_ID:?RUN_ID required}"
REPO="${REPO:?REPO required}"
readonly SPOT_REASON="Server.SpotInstanceTermination"

# List failed jobs as `<job_id>\t<runner_name>` rows. Capture into a variable and let the
# assignment's exit status (set -e + the `if !`) catch a gh/API failure — otherwise a failed
# lookup would look like "no failed jobs" and silently skip a real spot reclaim.
echo "::group::Failed jobs for run ${RUN_ID}"
if ! failed_jobs="$(
  gh api --paginate "/repos/${REPO}/actions/runs/${RUN_ID}/jobs" \
    --jq '.jobs[] | select(.conclusion=="failure") | [.id, (.runner_name // "")] | @tsv'
)"; then
  echo "ERROR: could not list jobs for run ${RUN_ID}; cannot determine spot reclaim." >&2
  exit 1
fi
echo "${failed_jobs:-(none)}"
echo "::endgroup::"

# Job ids whose runner was confirmed spot-reclaimed.
spot_job_ids=()
while IFS=$'\t' read -r job_id runner; do
  [ -n "$job_id" ] || continue

  # Fleet runner name == EC2 instance id. Skip anything else (GitHub-hosted jobs, or a job
  # that failed before ever acquiring a runner).
  case "$runner" in
    i-*) ;;
    *)
      echo "job ${job_id}: skip non-instance runner: ${runner:-(none)}"
      continue
      ;;
  esac

  reason="$(aws ec2 describe-instances --instance-ids "$runner" \
    --query 'Reservations[].Instances[].StateTransitionReason' \
    --output text 2>/dev/null || true)"

  if [ -z "$reason" ]; then
    # Instance no longer visible in DescribeInstances (~1h retention). Fall back to CloudTrail.
    if aws cloudtrail lookup-events \
      --lookup-attributes "AttributeKey=EventName,AttributeValue=BidEvictedEvent" \
      --query 'Events[].CloudTrailEvent' --output text 2>/dev/null |
      grep -qF "$runner"; then
      reason="${SPOT_REASON} (via CloudTrail BidEvictedEvent)"
    fi
  fi

  echo "job ${job_id} runner ${runner}: ${reason:-(unknown)}"
  case "$reason" in
    *"${SPOT_REASON}"*) spot_job_ids+=("$job_id") ;;
  esac
done <<< "$failed_jobs"

if [ "${#spot_job_ids[@]}" -eq 0 ]; then
  echo "No spot reclaim among failed jobs — not retrying (treating as a genuine failure)."
  exit 0
fi

echo "Spot reclaim confirmed for job(s): ${spot_job_ids[*]} — re-running them individually."
for job_id in "${spot_job_ids[@]}"; do
  # Re-run just this job (and its dependents), not the whole run's failed set. GitHub rejects a
  # second re-run while the run is already re-running, so if that happens the remaining spot
  # jobs are picked up on the next attempt (they stay failed and still carry the spot signal),
  # bounded by the workflow's run_attempt cap.
  if gh run rerun --job "$job_id" -R "$REPO"; then
    echo "re-ran job ${job_id}"
  else
    echo "could not re-run job ${job_id} now (run may already be re-running); \
it will be retried on the next attempt if still failed."
  fi
done
