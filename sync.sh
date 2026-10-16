#!/bin/bash -e
set -o pipefail
set -e

# Config file paths — single source of truth is load-vars.yaml inside playbooks.
# These are only used for shell-level checks and getValue before Ansible runs.
global_vars=config/global.yaml
certs_vars=config/certificates.yaml

getValue(){
    python -c 'import sys, yaml, json; print(json.dumps(yaml.safe_load(sys.stdin)))' < $global_vars \
        | jq -r $1
}

step_done(){
    echo -e "\e[38;5;10m Done...\033[0m" | tee -a ${log}
    date | tee -a ${log}
}

workingDir=$(getValue .workingDir)
lck=~/.lck-rh-lz
DSTAMP=$(date +%Y%m%d_%H%M%S)
logdir=${workingDir}/logs
log="$logdir/${DSTAMP}"

_cleanup(){
    rm -fr "${lck}"
}

# Fail fast on an invalid/expired kubeconfig, before the long mirror step.
check_cluster_session(){
    local kubeconfig="${workingDir}/ocp-cluster/auth/kubeconfig"
    local oc_bin="${workingDir}/bin/oc"

    if [ ! -x "${oc_bin}" ]; then
        oc_bin=$(command -v oc || true)
    fi
    if [ -z "${oc_bin}" ]; then
        echo "ERROR: oc binary not found (looked in ${workingDir}/bin and PATH)." | tee -a ${log}
        echo "       Cannot verify the cluster session before running sync." | tee -a ${log}
        exit 1
    fi
    if [ ! -f "${kubeconfig}" ]; then
        echo "ERROR: kubeconfig not found at ${kubeconfig}." | tee -a ${log}
        echo "       Ensure the cluster is installed before running sync.sh." | tee -a ${log}
        exit 1
    fi
    if ! KUBECONFIG="${kubeconfig}" "${oc_bin}" whoami --request-timeout=10s >/dev/null 2>&1; then
        echo "ERROR: Cannot authenticate to the cluster using ${kubeconfig}." | tee -a ${log}
        echo "       The session is likely expired or invalid, or the API is" | tee -a ${log}
        echo "       unreachable. Re-authenticate against that kubeconfig" | tee -a ${log}
        echo "       (KUBECONFIG=${kubeconfig} oc login ...) or refresh it, then" | tee -a ${log}
        echo "       re-run sync.sh." | tee -a ${log}
        exit 1
    fi
}

echo " "
echo " ██████╗░███████╗██████╗░  ██╗░░██╗░█████╗░████████╗"
echo " ██╔══██╗██╔════╝██╔══██╗  ██║░░██║██╔══██╗╚══██╔══╝"
echo " ██████╔╝█████╗░░██║░░██║  ███████║███████║░░░██║░░░"
echo " ██╔══██╗██╔══╝░░██║░░██║  ██╔══██║██╔══██║░░░██║░░░"
echo " ██║░░██║███████╗██████╔╝  ██║░░██║██║░░██║░░░██║░░░"
echo " ╚═╝░░╚═╝╚══════╝╚═════╝░  ╚═╝░░╚═╝╚═╝░░╚═╝░░░╚═╝░░░"
echo " "
echo "This script is designed to be re-run on demand "
echo "NOTE: Some functions will reuse local caches   "

if [ -e ${lck} ]; then
    echo "Existing lock ${lck} found, exiting"
    exit 1
fi
touch ${lck}

trap _cleanup EXIT

echo 'Runtime Host:'
cat /etc/redhat-release
echo ' - '
if ! [[ $(</etc/os-release) =~ CPE_NAME=\"cpe:/o:redhat:enterprise_linux:10(\.?.*)?\" ]]; then
    echo "RHEL 10 Check Failed"
    exit 1
fi

mkdir -p "$(dirname $log)"
date > "$log"

echo "Check Config .. " | tee -a ${log}
FList="ansible.cfg  bootstrap.sh  playbooks/main.yaml  playbooks/  \
        setup_ansible.sh  setup_env.sh $global_vars $certs_vars"
for x in $FList; do
    if [ -e $x ]; then
        echo "file check passed ..." $x   | tee -a ${log}
    else
        echo "Config missing : " $x  | tee -a ${log}
    fi
done

step_done

echo "Checking cluster session .. " | tee -a ${log}
    check_cluster_session
step_done

echo "Configuring environment .. "  | tee -a ${log}
    sudo bash -e ./setup_env.sh 2>&1 | tee -a ${log}
    bash -e ./setup_ansible.sh 2>&1 | tee -a ${log}
step_done

echo "Validating Config .. " | tee -a ${log}
    ANSIBLE_LOG_PATH=${log} ansible-playbook playbooks/validation/validate-schema.yaml -e fresh=false --tags validate-config
    bash ./validations.sh --skip-check-leftovers 2>&1 | tee -a "${log}"
step_done

echo "Configure MultiClusterHub .." | tee -a ${log}
    ANSIBLE_LOG_PATH=${log} ansible-playbook playbooks/06-day2.yaml -e fresh=false --tags acm-mch
step_done

echo "Building local cache .. " | tee -a ${log}
    ANSIBLE_LOG_PATH=${log} ansible-playbook playbooks/02-mirror.yaml -e fresh=false --tags mirror-registry
step_done

echo "Quay disconnected .." | tee -a ${log}
    ANSIBLE_LOG_PATH=${log} ansible-playbook playbooks/06-day2.yaml -e fresh=false --tags quay-disconnected
step_done

echo "MCE AgentServiceConfig .." | tee -a ${log}
    ANSIBLE_LOG_PATH=${log} ansible-playbook playbooks/01-prepare.yaml -e fresh=false --tags download-content
    ANSIBLE_LOG_PATH=${log} ansible-playbook playbooks/06-day2.yaml -e fresh=false --tags mce-asc
step_done

echo "ACM ClusterImageSets .." | tee -a ${log}
    ANSIBLE_LOG_PATH=${log} ansible-playbook playbooks/06-day2.yaml -e fresh=false --tags acm-cis
step_done

echo "Restart catalog pods .." | tee -a ${log}
    ANSIBLE_LOG_PATH=${log} ansible-playbook playbooks/06-day2.yaml -e fresh=false --tags restart-catalog-pods
step_done
