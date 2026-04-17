#!/bin/bash -e
set -o pipefail
set -e

getValue(){
    python -c 'import sys, yaml, json; print(json.dumps(yaml.safe_load(sys.stdin)))' < $global_vars \
        | jq -r $1
}

step_done(){
    echo -e "\e[38;5;10m Done...\033[0m" | tee -a ${log}
    date | tee -a ${log}
}

global_vars=${1:-config/global.yaml}
certs_vars=${2:-config/certificates.yaml}
workingDir=$(getValue .workingDir)
lck=~/.lck-rh-lz
DSTAMP=$(date +%Y%m%d_%H%M%S)
logdir=${workingDir}/logs
log="$logdir/${DSTAMP}"

_cleanup(){
    rm -fr "${lck}"
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

echo "Validating Config .. " | tee -a ${log}
    ansible-playbook playbooks/validation/validate-schema.yaml -e@$global_vars -e@$certs_vars --tags schema-validation 2>&1 | tee -a ${log}
    bash ./validations.sh --global-vars $global_vars --certs-vars $certs_vars 2>&1 | tee -a ${log}
step_done

echo "Building local cache .. " | tee -a ${log}
    ansible-playbook playbooks/02-mirror.yaml -e@$global_vars -e@$certs_vars --tags mirror-registry 2>&1 | tee -a ${log}
step_done

echo "Quay disconnected .." | tee -a ${log}
    ansible-playbook playbooks/06-day2.yaml -e@$global_vars -e@$certs_vars --tags quay-disconnected 2>&1 | tee -a ${log}
step_done

echo "Clair disconnected .." | tee -a ${log}
    ansible-playbook playbooks/06-day2.yaml -e@$global_vars -e@$certs_vars --tags clair-disconnected 2>&1 | tee -a ${log}
step_done

echo "ACM ClusterImageSets .." | tee -a ${log}
    ansible-playbook playbooks/06-day2.yaml -e@$global_vars -e@$certs_vars --tags acm-cis 2>&1 | tee -a ${log}
step_done

echo "OpenShift Pipelines .." | tee -a ${log}
    ansible-playbook playbooks/06-day2.yaml -e@$global_vars -e@$certs_vars --tags openshift-pipelines 2>&1 | tee -a ${log}
step_done
