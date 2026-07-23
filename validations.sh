#!/bin/bash

SKIP_CHECK_LEFTOVERS=false
for arg in "$@"; do
  case "$arg" in
    --skip-check-leftovers) SKIP_CHECK_LEFTOVERS=true ;;
  esac
done

# Config file paths — single source of truth matching load-vars.yaml
global_vars=config/global.yaml
certs_vars=config/certificates.yaml
cloud_infra_vars=config/cloud_infra.yaml

getValue(){
    python -c 'import sys, yaml, json; print(json.dumps(yaml.safe_load(sys.stdin)))' < $vars_rendered \
        | jq -r $1
}
toYaml(){
    python -c 'import sys, yaml, json; print(yaml.dump(json.load(sys.stdin), default_flow_style=False))'
}

validation_section(){
    name=$1
    echo "$1:" | pr -T -o 4
}

validation(){
    state=$1
    name=$2
    desc="$3"
    msg="$4"

    case "${state}" in
        pass|fail) ;;
        *)
            echo "Error: Invalid state '$state'. Must be 'pass' or 'fail'." >&2
            exit 1 ;;
    esac

    if [[ $# -lt 3 ]]; then
        echo "ERROR. Usage: validation state name message [description]"
        exit 1
    fi

    (
        echo "- name: $name"
        echo "  status: ${state^^}"
        echo "  description: ${desc}"
        if [[ -n "$msg" ]]; then
            echo '  message: |'
            echo "$msg" | pr -T -o 6
        fi
    ) | pr -T -o 8

    if [[ ${state} != "pass" ]]; then
        exit 1
    fi
}

checkDNS(){
    local check_name=$1
    local name=$2
    local value=$3
    local dns_server=$default_dns
    local result

    result=$(dig @$dns_server +short +timeout=2 +tries=1 $name 2>/dev/null)

    if [[ -z "$result" ]]; then
        validation fail $check_name "Failed to resolve $name"
    fi

    if [[ $result != $value ]]; then
        validation fail $check_name "$name points to $result. It does not match with $value"
    fi
    validation pass $check_name "$name points to $value"
}

checkIP(){
    local check_subnet_net ip_net prefix
    local check_name="$1"
    local ip=$2
    local check_subnet=$3

    check_subnet_net=$(ipcalc --no-decorate --network $check_subnet)
    prefix=$(ipcalc --no-decorate --prefix $check_subnet)
    ip_net=$(ipcalc --no-decorate --network $ip/$prefix)
    if [[ "$ip_net" == "$check_subnet_net" ]]; then
        validation pass $check_name "$check_name $ip in $check_subnet"
    else
        validation fail $check_name "$check_name $ip does not belong to network $check_subnet"
    fi
}

echo "validations:"

validation_section "syntax"
# Render a vars.yaml file. This allows variable interpolation in vars.yaml
vars_rendered=$(mktemp)
trap 'rm -f "$vars_rendered"' exit
if ! vars_rendering=$(ansible localhost -e "@$global_vars" -e "@$certs_vars" -e "@$cloud_infra_vars" -m copy -a "content={{ hostvars['localhost'] | to_yaml }} dest=${vars_rendered}" 2>&1); then
    validation fail vars_yaml "Configuration files could not be parsed" "$vars_rendering"
fi
validation pass vars_yaml "Configuration syntax OK"

# gather info
root_dir=$(getValue .workingDir)
cluster_name=$(getValue .clusterName)
base_domain=$(getValue .baseDomain)
cluster_fqdn=$cluster_name.$base_domain
default_dns=$(getValue .defaultDNS)
default_gateway=$(getValue .defaultGateway)
quayHostname_dns=$(getValue .quayHostname)
if [[ -z "$quayHostname_dns" || "$quayHostname_dns" == "null" ]]; then
    quayHostname_dns="mirror.${base_domain}"
fi
api_vip=$(getValue .apiVIP)
ingress_vip=$(getValue .ingressVIP)
machine_network=$(getValue .machineNetwork)
rendezvous_ip=$(getValue .rendezvousIP)

## Leftover resources check
if [[ "$SKIP_CHECK_LEFTOVERS" == "false" ]]; then
    validation_section "check_leftovers"

    # Check for metal3 systemd units left from a previous bootstrap
    metal3_units=$(systemctl list-unit-files 'metal3-*.service' --no-legend 2>/dev/null || true)
    if [[ -n "$metal3_units" ]]; then
        validation fail metal3_systemd "Metal3 systemd units detected from a previous installation" "$metal3_units"
    fi
    validation pass metal3_systemd "No metal3 systemd units found"

    # Check root podman context
    root_pods=$(sudo podman pod ls --format "{{.Name}}" 2>/dev/null | grep -E 'metal3|quay' || true)
    if [[ -n "$root_pods" ]]; then
        validation fail root_podman_pods "Root podman pods with metal3/quay detected" "$root_pods"
    fi
    validation pass root_podman_pods "No metal3/quay root podman pods"

    root_containers=$(sudo podman ps -a --format "{{.Names}}" 2>/dev/null | grep -E 'metal3|ironic|httpd|baremetal-operator' || true)
    if [[ -n "$root_containers" ]]; then
        validation fail root_podman_containers "Root podman containers detected" "$root_containers"
    fi
    validation pass root_podman_containers "No leftover root podman containers"

    root_volumes=$(sudo podman volume ls --format "{{.Name}}" 2>/dev/null | grep -E 'metal3|quay' || true)
    if [[ -n "$root_volumes" ]]; then
        validation fail root_podman_volumes "Root podman volumes with metal3/quay detected" "$root_volumes"
    fi
    validation pass root_podman_volumes "No leftover root podman volumes"

    # Check user podman context
    user_pods=$(podman pod ls --format "{{.Name}}" 2>/dev/null | grep -E 'metal3|quay' || true)
    if [[ -n "$user_pods" ]]; then
        validation fail user_podman_pods "User podman pods with metal3/quay detected" "$user_pods"
    fi
    validation pass user_podman_pods "No metal3/quay user podman pods"

    user_containers=$(podman ps -a --format "{{.Names}}" 2>/dev/null | grep -E 'metal3|ironic|httpd|baremetal-operator' || true)
    if [[ -n "$user_containers" ]]; then
        validation fail user_podman_containers "User podman containers detected" "$user_containers"
    fi
    validation pass user_podman_containers "No leftover user podman containers"

    user_volumes=$(podman volume ls --format "{{.Name}}" 2>/dev/null | grep -E 'metal3|quay' || true)
    if [[ -n "$user_volumes" ]]; then
        validation fail user_podman_volumes "User podman volumes with metal3/quay detected" "$user_volumes"
    fi
    validation pass user_podman_volumes "No leftover user podman volumes"
fi

## IP Validations
validation_section "ip_address"

if [[ "$api_vip" == "$ingress_vip" ]]; then
    validation fail api_ingress "API VIP ($api_vip) and Ingress VIP ($ingress_vip) are the same"
fi
validation pass api_ingress "API VIP ($api_vip) and Ingress VIP ($ingress_vip) are different"

checkIP default_gateway $default_gateway $machine_network
checkIP api_vip $api_vip $machine_network
checkIP ingress_vip $ingress_vip $machine_network
checkIP rendezvous_ip $rendezvous_ip $machine_network

## DNS Validations
validation_section "dns"

# Make sure that LZ DNS is defaultDNS
if grep -q 127.0.0.53 /etc/resolv.conf; then
    # systemd-resolved in use, get DNS list from systemd
    if systemctl is-active --quiet systemd-resolved; then
        lz_dns="$(resolvectl status | awk '/Current DNS Server:/ {print $NF}')"
    else
        validation fail default_dns "Landing Zone DNS could not be determined. It points to 127.0.0.53 but systemd-resolved is inactive"
    fi
else
    # DNS should be in /etc/resolv.conf
    lz_dns="$(awk '/^nameserver/ {print $2}' /etc/resolv.conf)"
fi

if ! echo "$lz_dns" | grep -qx $default_dns; then
    message="defaultDNS: $default_dns
Landing Zone DNS: $lz_dns"
    validation fail default_dns "Landing Zone DNS does not point to defaultDNS" "$message"
fi
validation pass default_dns "Landing Zone DNS points to defaultDNS $default_dns"

# Validate that defaultDNS is reachable and responding
# Test by resolving a well-known domain through the defaultDNS server
test_result=$(dig @$default_dns +short +timeout=2 +tries=1 google.com 2>/dev/null)
if [[ -z "$test_result" ]]; then
    validation fail default_dns_works "defaultDNS server $default_dns is not reachable or not responding" "Ensure the DNS server at $default_dns is accessible and functioning"
fi
validation pass default_dns_works "defaultDNS server $default_dns is reachable and responding"

# Validate DNS (api and *.apps point to the proper VIPS)
checkDNS api api.$cluster_fqdn $api_vip
checkDNS ingress something.apps.$cluster_fqdn $ingress_vip

# Validate that mirror registry in quayHostname points to an IP of the LZ
mirror_ip=$(dig +short $quayHostname_dns | tail -n1)
if [[ -z "$mirror_ip" ]]; then
    validation fail quay_hostname "Failed to resolve $quayHostname_dns"
fi
if ! ip -o a | awk '{print $4}' | cut -d/ -f1 | grep -qx $mirror_ip; then
    validation fail quay_hostname "$quayHostname_dns is not pointing to an IP address of the Landing Zone" "$quayHostname_dns points to $mirror_ip"
fi
validation pass quay_hostname "$quayHostname_dns points to $mirror_ip and Landing Zone has that IP"

## Pull Secret
validation_section pull_secret
# Check that pull secret works
ps_image_check=quay.io/openshift-release-dev/ocp-v4.0-art-dev:latest
if ! podman manifest inspect --authfile <(getValue .pullSecret) $ps_image_check > /dev/null 2>&1; then
    validation fail pull_secret "Could not download an image using the pull secret"
fi
validation pass pull_secret "Pull secret validated"

## Redfish
validation_section redfish

number_of_hosts=$(getValue .agent_hosts | jq length)
if [[ $number_of_hosts -ne 3 ]]; then
    validation fail number_of_agent_hosts "Number of agent_hosts is ${number_of_hosts}. It should be 3"
fi
validation pass number_of_agent_hosts "Number of agent_hosts is 3"

# validate redfish access
for host in 0 1 2; do
    host_name=$(getValue .agent_hosts[$host].name)
    redfish=$(getValue .agent_hosts[$host].redfish)
    redfish_user=$(getValue .agent_hosts[$host].redfishUser)
    redfish_password=$(getValue .agent_hosts[$host].redfishPassword)
    redfish_response=$(mktemp)
    redfish_curl_return=$(curl -s -w '%{http_code}' -o "$redfish_response" -u "${redfish_user}:${redfish_password}" -k "https://${redfish}/redfish/v1/Systems")
    if [[ $redfish_curl_return != 200 ]]; then
        rm -f "$redfish_response"
        validation fail $host_name "Connection to $host_name ($redfish) is not healthy. Return code: $redfish_curl_return"
    else
        validation pass $host_name "Redfish connection to $host_name ($redfish) successful"

        bmc_system_id=$(getValue ".agent_hosts[$host].bmcSystemId")
        system_count=$(jq -r '."Members@odata.count" // (.Members | length)' "$redfish_response" 2>/dev/null)
        if [[ -z "$bmc_system_id" || "$bmc_system_id" == "null" ]]; then
            if [[ ! "$system_count" =~ ^[0-9]+$ ]]; then
                rm -f "$redfish_response"
                validation fail "${host_name}_system_id" "Could not determine system count from BMC on $host_name ($redfish). Set bmcSystemId explicitly"
            elif [[ "$system_count" -gt 1 ]]; then
                rm -f "$redfish_response"
                validation fail "${host_name}_system_id" "BMC on $host_name ($redfish) manages $system_count systems but bmcSystemId is not set. Set bmcSystemId in agent_hosts config"
            else
                validation pass "${host_name}_system_id" "BMC on $host_name ($redfish) has a single system, bmcSystemId not required"
            fi
        else
            if jq -e --arg id "/redfish/v1/Systems/${bmc_system_id}" '.Members[]? | select(."@odata.id" == $id)' "$redfish_response" > /dev/null 2>&1; then
                validation pass "${host_name}_system_id" "bmcSystemId '${bmc_system_id}' found on $host_name ($redfish)"
            else
                available_systems=$(jq -r '.Members[]?."@odata.id" // empty' "$redfish_response" 2>/dev/null | sed 's|/redfish/v1/Systems/||' | paste -sd ', ')
                rm -f "$redfish_response"
                validation fail "${host_name}_system_id" "bmcSystemId '${bmc_system_id}' not found on $host_name ($redfish). Available systems: ${available_systems}"
            fi
        fi
        rm -f "$redfish_response"
    fi
done

## Validate S3 configuration (only for RadosGWStorage backend)
quay_backend=$(getValue .quayBackend)
if [[ "$quay_backend" == "RadosGWStorage" ]]; then
    validation_section s3_config

    s3_bucket=$(getValue .quayBackendRGWConfiguration.bucket_name)
    s3_hostname=$(getValue .quayBackendRGWConfiguration.hostname)
    s3_port=$(getValue .quayBackendRGWConfiguration.port)
    if [[ -z "$s3_port" || "$s3_port" == "null" ]]; then
        s3_port=443
    fi
    s3_is_secure=$(getValue .quayBackendRGWConfiguration.is_secure)
    if [[ -z "$s3_is_secure" || "$s3_is_secure" == "null" ]]; then
        s3_is_secure=true
    fi
    if [[ "$s3_is_secure" == true ]]; then
        s3_protocol=https
    else
        s3_protocol=http
    fi
    s3_endpoint=${s3_protocol}://${s3_hostname}:${s3_port}

    min_chunk=$(getValue .quayBackendRGWConfiguration.minimum_chunk_size_mb)
    if [[ -n "$min_chunk" && "$min_chunk" != "null" ]]; then
        if ! [[ "$min_chunk" =~ ^[0-9]+$ ]]; then
            validation fail minimum_chunk_size_mb "minimum_chunk_size_mb must be an integer, got: $min_chunk"
        else
            validation pass minimum_chunk_size_mb "minimum_chunk_size_mb is a valid integer ($min_chunk)"
        fi
    fi

    max_chunk=$(getValue .quayBackendRGWConfiguration.maximum_chunk_size_mb)
    if [[ -n "$max_chunk" && "$max_chunk" != "null" ]]; then
        if ! [[ "$max_chunk" =~ ^[0-9]+$ ]]; then
            validation fail maximum_chunk_size_mb "maximum_chunk_size_mb must be an integer, got: $max_chunk"
        else
            validation pass maximum_chunk_size_mb "maximum_chunk_size_mb is a valid integer ($max_chunk)"
        fi
    fi

    s3_endpoint_curl_return=$(curl -o /dev/null -s -w '%{http_code}' $s3_endpoint)
    if [[ \
            $s3_endpoint_curl_return != 200 && \
            $s3_endpoint_curl_return != 403 \
        ]]; then
        validation fail s3_endpoint "Endpoint $s3_endpoint is not healthy. Return code: $s3_endpoint_curl_return"
    fi
    validation pass s3_endpoint "Endpoint $s3_endpoint is healthy"

    if ! AWS_ACCESS_KEY_ID=$(getValue .quayBackendRGWConfiguration.access_key) \
        AWS_SECRET_ACCESS_KEY=$(getValue .quayBackendRGWConfiguration.secret_key) \
        aws \
        --endpoint-url "$s3_endpoint" s3 ls "s3://$s3_bucket" > /dev/null 2>&1
    then
        validation fail s3_bucket "Bucket $s3_bucket is not accessible. Check bucket name and credentials"
    fi
    validation pass s3_bucket "Bucket $s3_bucket is accessible"
else
    validation_section s3_config
    validation pass s3_skipped "S3 validation skipped (quayBackend is $quay_backend, not RadosGWStorage)"
fi

## SSL Certificates
validation_section ssl_certificates

if output=$(enclave --log-format plain tools check-root-ca --config "${certs_vars}" 2>&1); then
    validation pass root_ca "Root CA check passed"
else
    validation fail root_ca "Root CA check failed" "$output"
fi

if output=$(enclave --log-format plain tools check-certificate-chains api \
    --config "${certs_vars}" \
    --hostname "api.${cluster_fqdn}" 2>&1); then
    validation pass cert_api "API certificate chain check passed"
else
    validation fail cert_api "API certificate chain check failed" "$output"
fi

# Pass the wildcard pattern as the hostname so cert_covers_hostname confirms the
# cert's SAN is exactly "*.apps.<cluster_fqdn>", matching what OpenShift ingress requires.
if output=$(enclave --log-format plain tools check-certificate-chains ingress \
    --config "${certs_vars}" \
    --hostname "*.apps.${cluster_fqdn}" 2>&1); then
    validation pass cert_ingress "Ingress certificate chain check passed"
else
    validation fail cert_ingress "Ingress certificate chain check failed" "$output"
fi

lz_bmc_hostname=$(getValue .lzBmcHostname 2>/dev/null || true)
lz_bmc_ip=$(getValue .lzBmcIP 2>/dev/null || true)
[[ "$lz_bmc_hostname" == "null" ]] && lz_bmc_hostname=""
[[ "$lz_bmc_ip" == "null" ]] && lz_bmc_ip=""
ironic_cert_name="${lz_bmc_hostname:-$lz_bmc_ip}"
if [[ -n "${ironic_cert_name}" ]]; then
    if output=$(enclave --log-format plain tools check-certificate-chains ironic \
        --config "${certs_vars}" \
        --hostname "${ironic_cert_name}" 2>&1); then
        validation pass cert_ironic "Ironic certificate check passed"
    else
        validation fail cert_ironic "Ironic certificate check failed" "$output"
    fi
fi

## Check nmstate config (if defined)
validation_section nmstate

if [[ $(getValue .agent_hosts | jq '[.[] | select(has("networkConfig"))] | length') -gt 0 ]]; then
    for agent in $(getValue .agent_hosts | jq -r ".[] | .name"); do
        agent_host=$(getValue .agent_hosts | jq --arg agent $agent -r '.[] | select(.name==$agent)')
        hasNetworkConfig=$(jq 'has("networkConfig")' <<< "$agent_host")
        if [[ $hasNetworkConfig == false ]]; then
            validation pass $agent "Host $agent has no networkConfig. Skipping validation"
            continue
        fi

        if ! jq .networkConfig <<< "$agent_host" | toYaml | nmstatectl validate -q > /dev/null; then
            validation fail $agent "Host $agent has invalid networkConfig"
        fi
        validation pass $agent "Host $agent has valid advanced network configuration"
    done
else
    validation pass no_hosts "No advanced network configuration found in any host. Skipping validation"
fi

## Check HTTP server
validation_section httpd

check_uuid=$(uuidgen)
webserver=$(getValue .lzBmcIP)
sudo tee /var/www/html/.validation_check.txt > /dev/null <<< $check_uuid
check_webserver=$(curl -s http://$webserver/.validation_check.txt)
sudo rm -f /var/www/html/.validation_check.txt
if [[ "$check_uuid" != "$check_webserver" ]]; then
    validation fail httpd "Content in /var/www/html cannot be retrieved via http"
fi
validation pass httpd "Dummy file could be retrieved via http"

## Check mirror-registry port
validation_section mirror_registry
mirror_port=8443
if ! sudo lsof -i :$mirror_port > /dev/null; then
    validation pass port_available "TCP port $mirror_port is not in use"
else
    if [[ $(sudo lsof -i :$mirror_port | grep LISTEN | awk '{print $3}' | tail -n1) != "$USER" ]]; then
        validation fail port_available "Some other user is already using port $mirror_port"
    fi

    if [[ $(sudo lsof -i :$mirror_port | grep LISTEN | awk '{print $1}' | tail -n1) != "pasta.avx" ]]; then
        validation fail port_available "Some process different than the mirror registry is using port $mirror_port"
    fi

    for pod_id in $(podman pod ps -q); do
        pod_name=$(podman pod inspect "$pod_id" --format json | jq -r '.[] | select(.InfraConfig.PortBindings | has("'$mirror_port'/tcp")) | .Name')
        if [[ "$pod_name" == "" ]]; then
            continue
        fi
        if [[ "$pod_name" != "quay-pod" ]]; then
            validation fail port_available "Some pod different than the mirror registry is using port $mirror_port ($pod_name)"
        fi
        pod_datadir=$(podman inspect quay-app | jq -r '.[].Mounts[] | select(.Type=="bind") | .Source' | rev | cut -d / -f 3- | rev)
        if [[ $(readlink -f "$pod_datadir") != $(readlink -f "${root_dir}") ]]; then
            validation fail port_available "mirror registry is running pointing to a different workingDir ($pod_datadir)"
        fi
    done
    validation pass port_available "mirror-registry is up and running. Reusing it"
fi

# END OF VALIDATIONS
