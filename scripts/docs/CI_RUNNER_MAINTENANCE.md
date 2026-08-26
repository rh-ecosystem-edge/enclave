# GitHub Actions Runner Maintenance

Operational guide for maintaining self-hosted runners on the Enclave Lab CI machine.
For initial setup, see [CI_RUNNER_SETUP.md](CI_RUNNER_SETUP.md).

## Check Runner Status

```bash
sudo systemctl status actions.runner.rh-ecosystem-edge-enclave.*.service --lines=0 --no-pager
```

## Check Runner Logs

```bash
# Live logs
sudo journalctl -u actions.runner.rh-ecosystem-edge-enclave.*.service -f

# Runner diagnostic logs
tail -f ~/action-runners/runner-N/_diag/Runner_*.log
```

## Restart a Runner

```bash
RUNNER_NUMBER=<runner-number>  # 1, 2, 3, ...
RUNNER_NAME="$(hostname | cut -d. -f1)-runner-$(printf '%02d' $RUNNER_NUMBER)"
sudo systemctl restart actions.runner.rh-ecosystem-edge-enclave.$RUNNER_NAME.service
```

## Update Runner Software

```bash
RUNNER_NUMBER=<runner-number>  # 1, 2, 3, ...
RUNNER_NAME="$(hostname | cut -d. -f1)-runner-$(printf '%02d' $RUNNER_NUMBER)"
cd ~/action-runners/runner-$RUNNER_NUMBER
sudo systemctl stop actions.runner.rh-ecosystem-edge-enclave.$RUNNER_NAME.service

VERSION=<new-version>
curl -o actions-runner-linux-x64-$VERSION.tar.gz -L \
  https://github.com/actions/runner/releases/download/v$VERSION/actions-runner-linux-x64-$VERSION.tar.gz
tar xzf ./actions-runner-linux-x64-$VERSION.tar.gz

sudo systemctl start actions.runner.rh-ecosystem-edge-enclave.$RUNNER_NAME.service
```

## Re-register a Failed Runner

Use this when a runner is in `failed (Result: oom-kill)` state or its GitHub registration has
expired. GitHub removes non-ephemeral runners offline for more than 14 days, and ephemeral
runners offline for more than 1 day.

### 1. Get fresh tokens

Removal and registration use separate API endpoints — generate both upfront. Tokens expire in ~1 hour.

```bash
REMOVE_TOKEN=$(gh api --method POST repos/rh-ecosystem-edge/enclave/actions/runners/remove-token --jq '.token')
REGISTRATION_TOKEN=$(gh api --method POST repos/rh-ecosystem-edge/enclave/actions/runners/registration-token --jq '.token')
```

### 2. Re-register

Run as the `github-runner` user (not root):

```bash
RUNNER_NUMBER=<runner-number>  # 1, 2, 3, ...

## Check the runner's current labels before removal so they can be preserved
gh api repos/rh-ecosystem-edge/enclave/actions/runners \
  --jq ".runners[] | select(.name | test(\"$(hostname | cut -d. -f1)-runner-$(printf '%02d' $RUNNER_NUMBER)\")) | .labels[].name"

CUSTOM_LABELS=<label1,label2,...>  # comma-separated, no spaces — use labels from the output above

## Stop and uninstall the service
cd ~/action-runners/runner-$RUNNER_NUMBER
RUNNER_NAME="$(hostname | cut -d. -f1)-runner-$(printf '%02d' $RUNNER_NUMBER)"
sudo systemctl stop actions.runner.rh-ecosystem-edge-enclave.$RUNNER_NAME.service
sudo ./svc.sh uninstall

## Remove registration and re-register with labels
./config.sh remove --token "$REMOVE_TOKEN"
./config.sh --url https://github.com/rh-ecosystem-edge/enclave --token "$REGISTRATION_TOKEN" \
  --name $RUNNER_NAME --unattended --replace \
  --labels self-hosted,X64,Linux,$CUSTOM_LABELS

## Reinstall and start the service
sudo ./svc.sh install github-runner
sudo systemctl start actions.runner.rh-ecosystem-edge-enclave.$RUNNER_NAME.service
sudo systemctl status actions.runner.rh-ecosystem-edge-enclave.$RUNNER_NAME.service --lines=0 --no-pager
```

## Troubleshooting

### Runner shows offline

1. Check status and logs:

   ```bash
   RUNNER_NUMBER=<runner-number>  # 1, 2, 3, ...
   RUNNER_NAME="$(hostname | cut -d. -f1)-runner-$(printf '%02d' $RUNNER_NUMBER)"
   sudo systemctl status actions.runner.rh-ecosystem-edge-enclave.$RUNNER_NAME.service --lines=20
   sudo journalctl -u actions.runner.rh-ecosystem-edge-enclave.$RUNNER_NAME.service -n 50
   ```

1. Try restarting first. If it fails with auth errors, the registration has expired — follow the
   [Re-register a Failed Runner](#re-register-a-failed-runner) section above.

### Permission denied errors

```bash
# Verify user groups (should include libvirt)
groups github-runner

# Check sudo permissions
sudo -l -U github-runner

# Test libvirt access
sudo -u github-runner virsh list --all
```

### Disk space issues

Check usage:

```bash
df -h /opt/dev-scripts
df -h /home/github-runner
df -h /var/lib/libvirt/images
```

If stale VM images are consuming space, follow the [Stale VM and Storage Cleanup](#stale-vm-and-storage-cleanup) section below.

## Stale VM and Storage Cleanup

> **Note:** All `virsh` and storage cleanup commands must be run as `root`. Running as the
> `github-runner` user will show an empty VM and pool list even when VMs and pools exist.

```bash
sudo su -
```

### Find running VMs and their start times

```bash
virsh list

for vm in $(virsh list --name); do
  pid=$(ps -eo pid,args | grep "guest=${vm}," | grep -v grep | awk '{print $1}')
  if [[ -n "$pid" ]]; then
    start=$(ps -o lstart= -p "$pid" 2>/dev/null)
    echo "$vm: $start (pid: $pid)"
  else
    echo "$vm: no qemu process found"
  fi
done
```

- A VM with an old start time may indicate a stuck/runaway qemu process
- A VM with no qemu process found indicates the process is gone but libvirt still tracks it

### Destroy stuck VMs

Check state before destroying:

```bash
VM_NAME=<vm-name>
virsh domstate $VM_NAME --reason
virsh destroy $VM_NAME
```

### Inventory storage pools and volumes

```bash
virsh pool-list --all

virsh pool-list --all | awk 'NR>2 && $1!="" {print $1}' | while read pool; do
  echo "=== $pool ==="
  virsh vol-list --pool $pool
done

# Check volume sizes
virsh vol-list --pool default --details | awk 'NR>2'
```

### Check if volumes are still referenced

```bash
grep -r "agent-x86_64-iso-.*\.img\|boot-.*\.img" /etc/libvirt/ 2>/dev/null
```

For each hit, verify the referencing VM definition is actually stale:

```bash
virsh list --all  # check whether the referencing VM is still defined
```

Only proceed with deletion if the referencing VM is either no longer defined in
`virsh list --all`, or you explicitly confirm the definition is stale and can be removed.
A shut-off VM is still a valid defined VM — do not treat it as stale without confirmation.

### Delete cluster-specific pools

> **Warning:** The following commands permanently delete all volumes and pool directories.
> Verify the pool list with `virsh pool-list` and confirm all listed pools are stale before
> running.

Deletes all volumes, removes pool directories, and unregisters pools from libvirt:

```bash
for pool in $(virsh pool-list --all | awk 'NR>2 && $1~/^(eci|ecd)-/ {print $1}'); do
  virsh vol-list --pool $pool | awk 'NR>2 && $1!="" {print $1}' | \
    xargs -I{} virsh vol-delete --pool $pool {}
  virsh pool-destroy $pool
  virsh pool-delete $pool
  virsh pool-undefine $pool
done
```

### Delete orphaned ISOs from the default pool

Review the volume list first, then delete only the known CI-generated ISO patterns
(`agent-x86_64-iso-*` and `boot-*`):

```bash
# Review what is present before deleting
virsh vol-list --pool default --details | awk 'NR>2'

# Delete only orphaned agent installer and boot ISO files
virsh vol-list --pool default | awk 'NR>2 && $1~/^(agent-x86_64-iso-.*\.img|boot-.*\.img)$/ {print $1}' | \
  xargs -I{} virsh vol-delete --pool default {}
```

### Remove stale VM XML definitions

```bash
virsh list --all --name | while read vm; do
  [[ -z "$vm" ]] && continue
  state=$(virsh domstate "$vm" 2>/dev/null)
  if [[ "$state" != "shut off" ]]; then
    echo "Skipping $vm (state: $state) — destroy it first"
    continue
  fi
  virsh undefine "$vm" && echo "Undefined $vm" || echo "Failed to undefine $vm"
done
```

Or for specific VMs:

```bash
virsh undefine <vm-name>
```
