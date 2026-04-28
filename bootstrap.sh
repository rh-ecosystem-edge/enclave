#!/bin/bash -e
set -o pipefail
set -e

usage() {
    echo "Usage: $0 [OPTIONS]"
    echo ""
    echo "Options:"
    echo "  --global-vars FILE    Path to global vars file (default: config/global.yaml)"
    echo "  --certs-vars FILE     Path to certificates vars file (default: config/certificates.yaml)"
    echo "  --step STEP           Run a single step instead of all steps"
    echo "  --non-interactive     Skip interactive prompts"
    echo "  -h, --help            Show this help message"
    echo ""
    echo "Available steps:"
    echo "  setup               Configure environment (setup_env + setup_ansible)"
    echo "  validate            Validate configuration (schema + validations)"
    echo "  download-content    Download control binaries and dependency content (Phase 1)"
    echo "  build-cache         Build local cache (Phase 2 + configure-abi)"
    echo "  acquire-hardware    Acquire and validate hardware (Phase 3a)"
    echo "  deploy              Deploy management cluster (Phase 3b)"
    echo "  post-install        Post-install configuration (Phase 4)"
    echo "  operators           Deploy management apps (Phase 5)"
    echo "  day2                Day-2 operations (Phase 6)"
    echo "  discovery           Discover nodes (Phase 7)"
    echo "  partner-overlay     Deploy partner overlay (optional)"
    exit "${1:-0}"
}

global_vars=config/global.yaml
certs_vars=config/certificates.yaml
cloud_infra_vars=config/cloud_infra.yaml
run_step=""
non_interactive=false

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
        --step)
            run_step="$2"
            shift 2
            ;;
        --non-interactive)
            non_interactive=true
            shift
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

# Validate --step value if provided
valid_steps="setup validate download-content build-cache acquire-hardware deploy post-install operators day2 discovery partner-overlay"
if [ -n "$run_step" ]; then
    step_valid=false
    for s in $valid_steps; do
        if [ "$s" = "$run_step" ]; then
            step_valid=true
            break
        fi
    done
    if [ "$step_valid" = false ]; then
        echo "Error: Unknown step '$run_step'"
        echo "Valid steps: $valid_steps"
        exit 1
    fi
fi

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

# Determine deployment mode: env var takes precedence, then config file
is_disconnected=true
if [ "${ENCLAVE_DEPLOYMENT_MODE:-}" = "connected" ]; then
    is_disconnected=false
elif [ "$(getValue .disconnected 2>/dev/null)" = "false" ]; then
    is_disconnected=false
fi

EXTRA_VARS=""
if [ "$is_disconnected" = false ]; then
    # Use --extra-vars= with JSON to pass boolean false (not string "false")
    # The key=value syntax (-e disconnected=false) passes a string, which
    # Jinja2 evaluates as truthy, breaking template selection.
    EXTRA_VARS='--extra-vars={"disconnected":false}'
fi

_cleanup(){
    rm -fr "${lck}"
}

if [ "$non_interactive" = false ]; then
    read -rp "Press Enter to start .. " -n1 -s
fi

if [ -e ${lck} ]; then
    echo "Existing lock ${lck} found, exiting"
    exit 1
fi
touch ${lck}

trap _cleanup EXIT

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

# --- Step functions ---

step_setup() {
    echo 'Runtime Host:' | tee -a ${log}
    cat /etc/redhat-release | tee -a ${log}
    echo ' - '
    if ! [[ $(</etc/os-release) =~ CPE_NAME=\"cpe:/o:redhat:enterprise_linux:10(\.?.*)?\" ]]; then
        echo "RHEL 10 Check Failed"
        exit 1
    fi

    echo "Configuring environment .. "  | tee -a ${log}
    sudo bash -e ./setup_env.sh 2>&1 | tee -a ${log}
    bash -e ./setup_ansible.sh 2>&1 | tee -a ${log}
    step_done
}

step_validate() {
    echo "Validating Config .. "  | tee -a ${log}
    ANSIBLE_LOG_PATH=${log} ansible-playbook playbooks/validation/validate-schema.yaml -e@$global_vars -e@$certs_vars $EXTRA_VARS --tags schema-validation
    bash ./validations.sh --global-vars $global_vars --certs-vars $certs_vars 2>&1 | tee -a ${log}
    step_done
}

step_download_content() {
    echo "Downloading Deps Content .. " | tee -a ${log}
    # Download control binaries (oc, helm, etc.) first - required by download-content tasks
    ANSIBLE_LOG_PATH=${log} ansible-playbook playbooks/01-prepare.yaml -e@$global_vars -e@$certs_vars $EXTRA_VARS --tags download-control-binaries
    ANSIBLE_LOG_PATH=${log} ansible-playbook playbooks/01-prepare.yaml -e@$global_vars -e@$certs_vars $EXTRA_VARS --tags download-content
    step_done
}

step_build_cache() {
    echo "Building local cache .. " | tee -a ${log}
    if [ "$is_disconnected" = false ]; then
        echo "Connected mode - skipping mirror registry setup" | tee -a ${log}
    else
        ANSIBLE_LOG_PATH=${log} ansible-playbook playbooks/02-mirror.yaml -e@$global_vars -e@$certs_vars $EXTRA_VARS --tags mirror-registry
    fi
    ANSIBLE_LOG_PATH=${log} ansible-playbook playbooks/03-deploy.yaml -e@$global_vars -e@$certs_vars $EXTRA_VARS --tags configure-abi
    step_done
}

step_acquire_hardware() {
    echo "Acquiring Hardware .. " | tee -a ${log}
    # setup content for and boot machines
    ANSIBLE_LOG_PATH=${log} ansible-playbook playbooks/03-deploy.yaml -e@$global_vars -e@$certs_vars $EXTRA_VARS --tags hardware,pre-install-validate
    step_done
}

step_deploy() {
    echo "Deploying management cluster .. " | tee -a ${log}
    # deploy Red Hat payload cluster
    ANSIBLE_LOG_PATH=${log} ansible-playbook playbooks/03-deploy.yaml -e@$global_vars -e@$certs_vars $EXTRA_VARS --tags wait-deployment
    step_done
}

step_post_install() {
    echo "Post install config.. " | tee -a ${log}
    # Apply SSL certificates
    ANSIBLE_LOG_PATH=${log} ansible-playbook playbooks/04-post-install.yaml -e@$global_vars -e@$certs_vars $EXTRA_VARS --tags post-install-config
    step_done
}

step_operators() {
    echo "Deploying management apps  .. " | tee -a ${log}
    # deploy Red Hat payload cluster
    ANSIBLE_LOG_PATH=${log} ansible-playbook playbooks/05-operators.yaml -e@$global_vars -e@$certs_vars $EXTRA_VARS --tags operators
    step_done
}

step_day2() {
    echo "Clair disconnected .." | tee -a ${log}
    ANSIBLE_LOG_PATH=${log} ansible-playbook playbooks/06-day2.yaml -e@$global_vars -e@$certs_vars $EXTRA_VARS --tags clair-disconnected
    step_done

    echo "Catalog source ACM policy .." | tee -a ${log}
    ANSIBLE_LOG_PATH=${log} ansible-playbook playbooks/06-day2.yaml -e@$global_vars -e@$certs_vars $EXTRA_VARS --tags acm-policy-catalogsources
    step_done
}

step_discovery() {
    # Showing login information
    printf '%b\n' "$(tail -3 ${workingDir}/ocp-cluster/.openshift_install.log \
      | cut -d= -f4- \
      | sed -e 's/^"//' -e 's/"$//' -e 's/\\n/\
/g' -e 's/\\"/"/g')"

    echo "Start discovering nodes.. " | tee -a ${log}
    if [ -f $cloud_infra_vars ]; then
        if ! ANSIBLE_LOG_PATH=${log} ansible-playbook -e @$global_vars -e @$certs_vars -e @$cloud_infra_vars $EXTRA_VARS playbooks/07-configure-discovery.yaml; then
            echo -e "\\033[31m WARNING! \033[0m  Discovery hosts has failed, please check config and rerun: ANSIBLE_LOG_PATH=${log} ansible-playbook -e @$global_vars -e @$certs_vars -e @$cloud_infra_vars playbooks/07-configure-discovery.yaml"
        fi
    fi
    step_done
}

step_partner_overlay() {
    echo "Deploying Partner OverLay .. " | tee -a ${log}
    if [ -f ./partner-install/start.sh ]; then
        bash ./partner-install/start.sh ${workingDir}/ocp-cluster/auth/kubeconfig ${global_vars} ${certs_vars} 2>&1 | tee -a ${log}
    else
        echo "Partner OverLay not found, skipping" | tee -a ${log}
    fi
    step_done
}

# --- Execution ---

if [ -n "${run_step}" ]; then
    # Single step mode: convert step name to function name (e.g., download-content -> step_download_content)
    func_name="step_${run_step//-/_}"
    "$func_name"
else
    # Full run mode: execute all steps sequentially
    step_setup
    step_validate
    step_download_content
    step_build_cache
    step_acquire_hardware
    step_deploy
    step_post_install
    step_operators
    step_day2
    step_discovery
    step_partner_overlay
fi

# teardown / cleanout
