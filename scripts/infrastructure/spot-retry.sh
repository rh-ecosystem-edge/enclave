#!/usr/bin/env bash
# Re-run e2e jobs whose spot runner was reclaimed by AWS mid-job.
#
# Invoked by .github/workflows/e2e-spot-retry.yml when an e2e run completes with failure.
# Lists the run's failed jobs and, for each, maps its runner name (which IS the EC2 instance
# id on the AWS self-hosted fleet, e.g. i-0abc...) to an AWS spot-reclaim signal:
#   - ec2 DescribeInstances StateTransitionReason == "Server.SpotInstanceTermination"
#   - fallback (instance aged out of DescribeInstances ~1h): a CloudTrail BidEvictedEvent.
# If ANY failed job was spot-killed, re-runs the run's failed jobs (GitHub re-runs only the
# failed ones, so a passing sibling is untouched). A genuine test failure carries no spot
# signal, so nothing is re-run — that is what keeps this quiet.
#
# Requires: gh (GH_TOKEN with actions:write) and aws (read-only role via OIDC).
# Env: RUN_ID, REPO (owner/name).
set -euo pipefail

RUN_ID="${RUN_ID:?RUN_ID required}"
REPO="${REPO:?REPO required}"
readonly SPOT_REASON="Server.SpotInstanceTermination"

echo "::group::Failed jobs for run ${RUN_ID}"
runners=()
while IFS= read -r line; do
  [ -n "$line" ] && runners+=("$line")
done < <(
  gh api --paginate "/repos/${REPO}/actions/runs/${RUN_ID}/jobs" \
    --jq '.jobs[] | select(.conclusion=="failure") | .runner_name // empty'
)
printf '%s\n' "${runners[@]:-(none)}"
echo "::endgroup::"

if [ "${#runners[@]}" -eq 0 ]; then
  echo "No failed jobs with a runner; nothing to retry."
  exit 0
fi

spot_killed=false
for name in "${runners[@]}"; do
  # Fleet runner name == EC2 instance id. Skip anything else (GitHub-hosted jobs, or a job
  # that failed before ever acquiring a runner).
  case "$name" in
    i-*) ;;
    *)
      echo "skip non-instance runner: ${name}"
      continue
      ;;
  esac

  reason="$(aws ec2 describe-instances --instance-ids "$name" \
    --query 'Reservations[].Instances[].StateTransitionReason' \
    --output text 2>/dev/null || true)"

  if [ -z "$reason" ]; then
    # Instance no longer visible in DescribeInstances (~1h retention). Fall back to CloudTrail.
    if aws cloudtrail lookup-events \
      --lookup-attributes "AttributeKey=EventName,AttributeValue=BidEvictedEvent" \
      --query 'Events[].CloudTrailEvent' --output text 2>/dev/null |
      grep -qF "$name"; then
      reason="${SPOT_REASON} (via CloudTrail BidEvictedEvent)"
    fi
  fi

  echo "runner ${name}: ${reason:-(unknown)}"
  case "$reason" in
    *"${SPOT_REASON}"*) spot_killed=true ;;
  esac
done

if [ "$spot_killed" = true ]; then
  echo "Spot reclaim detected — re-running failed jobs for run ${RUN_ID}."
  gh run rerun --failed "$RUN_ID" -R "$REPO"
else
  echo "No spot reclaim among failed jobs — not retrying (treating as a genuine failure)."
fi
