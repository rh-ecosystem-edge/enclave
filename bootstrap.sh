#!/bin/bash -e
set -o pipefail
set -e

usage() {
    echo "Usage: $0 [OPTIONS]"
    echo ""
    echo "Options:"
    echo "  --global-vars FILE    Path to global vars file (default: config/global.yaml)"
    echo "  --certs-vars FILE     Path to certificates vars file (default: config/certificates.yaml)"
    echo "  -h, --help            Show this help message"
    exit "${1:-0}"
}

global_vars=config/global.yaml
certs_vars=config/certificates.yaml
cloud_infra_vars=config/cloud_infra.yaml

while [[ $# -gt 0 ]]; do
    case $1 in
        --global-vars)
            global_vars="$2"
            shift 2
            ;;
        --certs-vars)
            certs_vars="$2"
            shift 2
            ;;
        -h|--help)
            usage
            ;;
        *)
            echo "Error: Unknown option '$1'"
            usage 1
            ;;
    esac
done

echo " "
echo " ██████╗░███████╗██████╗░  ██╗░░██╗░█████╗░████████╗"
echo " ██╔══██╗██╔════╝██╔══██╗  ██║░░██║██╔══██╗╚══██╔══╝"
echo " ██████╔╝█████╗░░██║░░██║  ███████║███████║░░░██║░░░"
echo " ██╔══██╗██╔══╝░░██║░░██║  ██╔══██║██╔══██║░░░██║░░░"
echo " ██║░░██║███████╗██████╔╝  ██║░░██║██║░░██║░░░██║░░░"
echo " ╚═╝░░╚═╝╚══════╝╚═════╝░  ╚═╝░░╚═╝╚═╝░░╚═╝░░░╚═╝░░░"
echo " "
echo "This script is designed to be re-run on demand "
echo "NOTE: Every run will destroy the entire cloud  "
echo "      Some functions will reuse local caches   "
echo ""
echo "Config files:"
echo "  Global vars:  $global_vars"
echo "  Certificates: $certs_vars"
echo "  Cloud infra:  $cloud_infra_vars"
echo ""

getValue(){
    python -c 'import sys, yaml, json; print(json.dumps(yaml.safe_load(sys.stdin)))' < $global_vars \
        | jq -r $1
}

step_done(){
    echo -e "\e[38;5;10m Done...\033[0m" | tee -a ${log}
    date | tee -a ${log}
}

if [ ! -f "$global_vars" ]; then
    echo "Error: $global_vars not found."
    echo "Copy config/global.example.yaml to $global_vars and fill in your values."
    exit 1
fi

if [ ! -f "$certs_vars" ]; then
    echo "Error: $certs_vars not found."
    echo "Copy config/certificates.example.yaml to $certs_vars and fill in your values."
    exit 1
fi

if [ ! -f "$cloud_infra_vars" ]; then
    echo "Error: $cloud_infra_vars not found."
    echo "Copy config/cloud_infra.example.yaml to $cloud_infra_vars and fill in your values."
    exit 1
fi

workingDir=$(getValue .workingDir)
lck=~/.lck-rh-lz
DSTAMP=$(date +%Y%m%d_%H%M%S)
logdir=${workingDir}/logs
log="$logdir/${DSTAMP}"

_cleanup(){
    rm -fr "${lck}"
}

read -rp "Press Enter to start .. " -n1 -s

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
        setup_ansible.sh  setup_env.sh $global_vars $certs_vars $cloud_infra_vars"
for x in $FList; do
    if [ -e $x ]; then
        echo "file check passed ..." $x   | tee -a ${log}
    else
        echo "Config missing : " $x  | tee -a ${log}
    fi
done

step_done

echo "Configuring environment .. "  | tee -a ${log}
    sudo bash -e ./setup_env.sh 2>&1 | tee -a ${log}
    bash -e ./setup_ansible.sh 2>&1 | tee -a ${log}
step_done

echo "Validating Config .. "  | tee -a ${log}
    ansible-playbook playbooks/validation/validate-schema.yaml -e@$global_vars -e@$certs_vars --tags schema-validation 2>&1 | tee -a ${log}
    bash ./validations.sh --global-vars $global_vars --certs-vars $certs_vars 2>&1 | tee -a ${log}
step_done

echo "Downloading Deps Content .. " | tee -a ${log}
    ansible-playbook playbooks/01-prepare.yaml -e@$global_vars -e@$certs_vars --tags download-content 2>&1 | tee -a ${log}
step_done

echo "Building local cache .. " | tee -a ${log}
    # get oc / helm / mirror content etc
    ansible-playbook playbooks/01-prepare.yaml -e@$global_vars -e@$certs_vars --tags download-control-binaries 2>&1 | tee -a ${log}
    ansible-playbook playbooks/02-mirror.yaml -e@$global_vars -e@$certs_vars --tags mirror-registry 2>&1 | tee -a ${log}
    ansible-playbook playbooks/03-deploy.yaml -e@$global_vars -e@$certs_vars --tags configure-abi 2>&1 | tee -a ${log}
step_done

#echo "Sanity lockdown .. " | tee -a ${log}
    # firewall work as needed
    # check state on LZ machine, lock down users, network NAT disable etc
#step_done

echo "Acquiring Hardware .. " | tee -a ${log}
    # setup content for and boot machines
    ansible-playbook playbooks/03-deploy.yaml -e@$global_vars -e@$certs_vars --tags hardware,pre-install-validate 2>&1 | tee -a ${log}
step_done

echo "Deploying management cluster .. " | tee -a ${log}
    # deploy Red Hat payload cluster
    ansible-playbook playbooks/03-deploy.yaml -e@$global_vars -e@$certs_vars --tags wait-deployment 2>&1 | tee -a ${log}
step_done

echo "Post install config.. " | tee -a ${log}
    # Apply SSL certificates
    ansible-playbook playbooks/04-post-install.yaml -e@$global_vars -e@$certs_vars --tags post-install-config 2>&1 | tee -a ${log}
step_done

echo "Deploying management apps  .. " | tee -a ${log}
    # deploy Red Hat payload cluster
    ansible-playbook playbooks/05-operators.yaml -e@$global_vars -e@$certs_vars --tags operators 2>&1 | tee -a ${log}
step_done

echo "Clair disconnected .." | tee -a ${log}
    ansible-playbook playbooks/06-day2.yaml -e@$global_vars -e@$certs_vars --tags clair-disconnected 2>&1 | tee -a ${log}
step_done

echo "Catalog source ACM policy .." | tee -a ${log}
    ansible-playbook playbooks/06-day2.yaml -e@$global_vars -e@$certs_vars --tags acm-policy-catalogsources 2>&1 | tee -a ${log}
step_done

# Showing login information
printf '%b\n' "$(tail -3 ${workingDir}/ocp-cluster/.openshift_install.log \
  | cut -d= -f4- \
  | sed -e 's/^"//' -e 's/"$//' -e 's/\\n/\
/g' -e 's/\\"/"/g')"

echo "Start discovering nodes.. " | tee -a ${log}
    if [ -f $cloud_infra_vars ]; then
        if ! ansible-playbook -e @$global_vars -e @$certs_vars -e @$cloud_infra_vars playbooks/07-configure-discovery.yaml 2>&1 | tee -a ${log}; then
            echo -e "\\033[31m WARNING! \033[0m  Discovery hosts has failed, please check config and rerun: ansible-playbook -e @$global_vars -e @$certs_vars -e @$cloud_infra_vars playbooks/07-configure-discovery.yaml" | tee -a ${log}
        fi
    fi
step_done

echo "Deploying Partner OverLay .. " | tee -a ${log}
    if [ -f ./partner-install/start.sh ]; then
        bash ./partner-install/start.sh ${workingDir}/ocp-cluster/auth/kubeconfig ${global_vars} ${certs_vars} 2>&1 | tee -a ${log}
    else
        echo "Partner OverLay not found, skipping" | tee -a ${log}
    fi
step_done

#echo "Service Validation and HealthCheck ..TBD " | tee -a ${log}
#step_done

# teardown / cleanout
