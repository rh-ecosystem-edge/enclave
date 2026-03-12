# OC-Mirror Progress Monitoring

## Overview

The OC-Mirror progress monitor displays real-time statistics during mirror operations in disconnected deployments, making it easier to track progress and identify issues.

## Features

### Visual Progress Display

```
═══════════════════════════════════════════════════════════════════
                   OC-Mirror Progress Monitor
═══════════════════════════════════════════════════════════════════

⏱️  Runtime: 00:15:23
📦 Workspace: 45.3GB

Progress: [████████████████░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░] 32%

Statistics:
  ✓ Completed:  156
  ⊘ Skipped:    12
  ✗ Failed:     3
  ∑ Total:      485

⏰ Estimated remaining: 00:35:12

Current operation:
  → registry.redhat.io/openshift4/ose-oauth-proxy

Recent errors:
  ✗ ...redhat.io/rhoai/odh-operator-bundle@sha256:e87a96c8...
  ✗ ...redhat.io/rhacm2/node-exporter-rhel9@sha256:dddf699...

═══════════════════════════════════════════════════════════════════
Press Ctrl+C to stop monitoring (oc-mirror will continue)
Log: /home/cloud-user/logs/oc-mirror.progress.1234567890.log
Last update: 2026-03-12 10:15:45
```

### Features

- **Real-time statistics**: Image counts, progress percentage, workspace size
- **Progress bar**: Visual indicator of completion
- **Time estimates**: Elapsed time and estimated time remaining
- **Current operation**: Shows which image is currently being mirrored
- **Error tracking**: Displays recent failures for quick diagnosis
- **Auto-refresh**: Updates every 5 seconds (configurable)
- **Non-intrusive**: Press Ctrl+C to stop monitoring without affecting oc-mirror

## Automatic Usage

The progress monitor runs **automatically** during disconnected deployments when you run:

```bash
make deploy-cluster-mirror
```

Or when using the full disconnected workflow:

```bash
make deploy-cluster
```

The Ansible playbook will:
1. Start oc-mirror in the background
2. Launch the progress monitor in the foreground
3. Display real-time updates
4. Wait for completion and show final summary

## Manual Usage

You can also run the progress monitor manually:

### On Landing Zone

```bash
# Start oc-mirror in background
~/bin/oc-mirror --v2 \
  --log-level info \
  --authfile ~/config/pull-secret.json \
  -c ~/config/imagesetconfiguration.yaml \
  --workspace file://~/config/oc-mirror-workspace \
  docker://$(hostname):8443 \
  --dest-tls-verify=false \
  > ~/logs/oc-mirror.log 2>&1 &

# Start progress monitor
~/enclave/scripts/monitoring/oc_mirror_progress.sh ~/logs/oc-mirror.log
```

### Custom Update Interval

```bash
# Update every 10 seconds instead of 5
~/enclave/scripts/monitoring/oc_mirror_progress.sh ~/logs/oc-mirror.log 10
```

## Understanding the Output

### Progress Bar
- **Green filled blocks (█)**: Completed work
- **Gray empty blocks (░)**: Remaining work
- **Percentage**: (Completed + Skipped) / Total * 100

### Statistics

- **✓ Completed**: Successfully mirrored images
- **⊘ Skipped**: Images skipped (e.g., operator bundles with failed dependencies)
- **✗ Failed**: Images that failed to mirror
- **∑ Total**: Total images detected in the catalog

### Time Estimates

- **Runtime**: Time since oc-mirror started
- **Estimated remaining**: Based on average time per image (only shown after 10+ images and 1+ minute)

### Recent Errors

Shows the last 3 image errors to help identify patterns:
- Manifest mismatches
- Timeout errors
- Network failures
- Authentication issues

## Troubleshooting

### Progress monitor doesn't start

**Symptom**: Monitor exits immediately with "Log file not found"

**Cause**: OC-mirror hasn't started yet or log path is incorrect

**Solution**:
```bash
# Wait for log file to be created
ls -l ~/logs/oc-mirror*.log

# Verify the correct log file
~/enclave/scripts/monitoring/oc_mirror_progress.sh ~/logs/oc-mirror.progress.*.log
```

### Progress stuck at same percentage

**Symptom**: No updates for several minutes

**Cause**:
- Large image is downloading
- Network timeout
- OC-mirror may have hung

**Solution**:
```bash
# Check if oc-mirror is still running
ps aux | grep oc-mirror

# Check recent log activity
tail -f ~/logs/oc-mirror.progress.*.log

# If hung, kill and restart
pkill oc-mirror
```

### "Total: 0" displayed

**Symptom**: Statistics show 0 total images

**Cause**: OC-mirror is still initializing or catalog enumeration hasn't completed

**Solution**: Wait 30-60 seconds. OC-mirror needs time to enumerate all images from the catalog.

### Monitor exits but oc-mirror still running

**Symptom**: Script exits with "completed" but oc-mirror is still running

**Cause**: Log parsing detected completion marker prematurely

**Solution**:
```bash
# Check if oc-mirror is still running
ps aux | grep oc-mirror

# Monitor manually
tail -f ~/logs/oc-mirror.progress.*.log
```

## Technical Details

### How It Works

1. **Background execution**: OC-mirror runs in background with output redirected to log file
2. **Log tailing**: Progress monitor continuously reads the log file
3. **Pattern matching**: Parses log for specific patterns:
   - `"mirroring image docker://..."` - counts total images
   - `"successfully mirrored"` - counts completed
   - `"error mirroring image"` - counts failures
   - `"skipping operator bundle"` - counts skipped
4. **Statistics calculation**: Computes percentages, averages, estimates
5. **Display refresh**: Updates screen every N seconds (default: 5)
6. **Completion detection**: Looks for `"Total images mirrored:"` in log

### Log Patterns Monitored

```bash
# Image processing
mirroring image docker://registry.redhat.io/...
successfully mirrored
error mirroring image
skipping operator bundle

# Completion
Total images mirrored:
Phase.*:

# Errors
FATAL
```

### Performance Impact

- **CPU**: Minimal (~0.5-1% on modern systems)
- **Memory**: < 10 MB
- **I/O**: Reads log file every N seconds
- **Network**: None (reads local log only)

## Integration with CI/CD

The progress monitor works in CI/CD pipelines but output may be buffered. To see real-time updates in GitHub Actions:

1. **Use line-buffered mode**: Progress updates flush immediately
2. **Increase update interval**: Use 10-15 second intervals to reduce log volume
3. **Check workflow logs**: Updates appear in the "Phase 2: Mirror Registry" step

## Configuration

### Environment Variables

None required. All configuration via command-line arguments.

### Ansible Variables

The playbook task uses these variables (defined in `defaults/deployment.yaml`):

- `ocMirrorLogLevel`: Log verbosity (default: `info`)
- `workingDir`: Working directory path
- `pullSecretPath`: Pull secret file location
- `quayHostname`: Local Quay registry hostname

### Customization

To change update interval globally, edit `playbooks/tasks/mirror_registry.yaml`:

```yaml
# Change from 5 to 10 seconds
{{ workingDir }}/enclave/scripts/monitoring/oc_mirror_progress.sh "$LOG_FILE" 10
```

## Comparison with Standard Output

### Before (No Progress Monitoring)

```
TASK [Start oc-mirror process] ************************************************
... (no output for 30+ minutes) ...
ok: [localhost]
```

**Issues**:
- No visibility into progress
- No way to know if it's stuck or working
- Can't estimate completion time
- Errors only visible after complete failure

### After (With Progress Monitoring)

```
TASK [Start oc-mirror process with progress monitoring] ***********************
OC-Mirror started with PID: 12345

═══════════════════════════════════════════════════════════════
                   OC-Mirror Progress Monitor
═══════════════════════════════════════════════════════════════
⏱️  Runtime: 00:15:23
Progress: [████████████████░░░░░░░░░░░░░░░] 32%
...
```

**Benefits**:
- ✅ Real-time visibility
- ✅ Progress tracking
- ✅ Time estimates
- ✅ Early error detection
- ✅ Better troubleshooting

## See Also

- [OC-Mirror Troubleshooting Guide](../OC_MIRROR_TROUBLESHOOTING.md)
- [Deployment Guide - Disconnected Mode](DEPLOYMENT_GUIDE.md#disconnected-mode)
- [Mirror Registry Setup](DEPLOYMENT_GUIDE.md#phase-2-mirror-registry)
