#!/bin/bash
set -euo pipefail

# Upgrade script for Red Hat Sovereign Enclave
# Run sync.sh BEFORE running this script to ensure content is synchronized

global_vars=config/global.yaml

getValue(){
    python -c 'import sys, yaml, json; print(json.dumps(yaml.safe_load(sys.stdin)))' < "$global_vars" \
        | jq -r "$1"
}

step_done(){
    echo -e "\e[38;5;10m Done...\033[0m" | tee -a "${log}"
    date | tee -a "${log}"
}

workingDir=$(getValue .workingDir)
DSTAMP=$(date +%Y%m%d_%H%M%S)
logdir=${workingDir}/logs
log="$logdir/${DSTAMP}-upgrade"

mkdir -p "$(dirname "$log")"
date > "$log"

echo "Running Enclave upgrade migrations .. " | tee -a "${log}"
    ANSIBLE_LOG_PATH="${log}" ansible-playbook playbooks/upgrade.yaml -e fresh=false
step_done
