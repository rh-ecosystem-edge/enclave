# Split Mirroring Implementation

This document describes the split mirroring feature that optimizes disconnected deployment time by parallelizing OpenShift installation with operator mirroring.

## Overview

Traditional mirroring approach:
```
Mirror Everything → Deploy OpenShift → Install Operators
     30+ mins           20+ mins         10+ mins
     Total: ~60+ minutes
```

Split mirroring approach:
```
Mirror Core → Deploy OpenShift + Mirror Operators (parallel) → Install Operators
  15 mins        20 mins + 15 mins (parallel)                    10 mins
  Total: ~45 minutes (25% faster)
```

## Components

### Phase 2a: Core Mirroring (`02a-mirror-core.yaml`)
**Purpose:** Mirror only essential components needed for OpenShift installation
- OpenShift platform images and catalogs
- Foundation plugins marked with `mirror: core` (LVMS, ODF)
- Essential base images

**Duration:** ~15 minutes (reduced from ~30 minutes)

### Phase 2b: Operator Mirroring (`02b-mirror-operators.yaml`)
**Purpose:** Mirror operators and images for post-installation
- Operators from `defaults/operators.yaml` (Quay, ACM, GitOps, etc.)
- Model operators from `defaults/model_operators.yaml`
- Post-install plugin operators not marked `mirror: core`
- Additional images (Vault, ArgoCD, etc.)

**Duration:** ~15 minutes (runs in parallel with OpenShift deployment)

### Supporting Files

#### Templates
- `imagesetconfiguration-core.yaml.j2` - Core components imageset
- `imagesetconfiguration-operators.yaml.j2` - Post-install operators imageset

#### Tasks
- `mirror_registry_core.yaml` - Core mirroring tasks
- `mirror_registry_operators.yaml` - Operator mirroring tasks
- `collect_post_install_plugin_operators.yaml` - Collect non-core plugin operators

#### Orchestration
- `02-mirror-split.yaml` - Orchestrates split mirroring with parallel execution
- `main-split.yaml` - Main playbook using split mirroring
- `wait-operator-mirror.yaml` - Wait for background operator mirroring

## Usage

### Option 1: Use Split Mirroring Main Playbook (Recommended)
```bash
# Full deployment with optimized split mirroring
ansible-playbook playbooks/main-split.yaml -e workingDir=/home/cloud-user

# Connected mode (no mirroring)
ansible-playbook playbooks/main-split.yaml -e workingDir=/home/cloud-user -e disconnected=false
```

### Option 2: Manual Step-by-Step Execution
```bash
# Step 1: Prepare environment
ansible-playbook playbooks/01-prepare.yaml -e workingDir=/home/cloud-user

# Step 2: Execute split mirroring (starts operator mirroring in background)
ansible-playbook playbooks/02-mirror-split.yaml -e workingDir=/home/cloud-user

# Step 3: Deploy OpenShift (runs in parallel with operator mirroring)
ansible-playbook playbooks/03-deploy.yaml -e workingDir=/home/cloud-user

# Step 4: Wait for operator mirroring to complete
ansible-playbook playbooks/wait-operator-mirror.yaml -e workingDir=/home/cloud-user

# Step 5: Continue with post-installation
ansible-playbook playbooks/04-post-install.yaml -e workingDir=/home/cloud-user
ansible-playbook playbooks/05-operators.yaml -e workingDir=/home/cloud-user
# ... etc
```

### Option 3: Individual Phase Execution
```bash
# Execute only core mirroring
ansible-playbook playbooks/02a-mirror-core.yaml -e workingDir=/home/cloud-user

# Execute only operator mirroring
ansible-playbook playbooks/02b-mirror-operators.yaml -e workingDir=/home/cloud-user
```

### Fallback: Traditional Sequential Mirroring
```bash
# Use original sequential mirroring if needed
ansible-playbook playbooks/main.yaml -e workingDir=/home/cloud-user
```

## Plugin Configuration

Plugins control their mirroring behavior through the `mirror` field in `plugin.yaml`:

### Core Mirroring (Required for OpenShift Installation)
```yaml
name: lvms
type: foundation
mirror: core  # Mirrored in Phase 2a
operators:
  - name: lvms-operator
    # ... operator config
```

### Post-Install Mirroring (Can be delayed)
```yaml
name: my-addon
type: addon
mirror: post-install  # Mirrored in Phase 2b
operators:
  - name: my-operator
    # ... operator config
```

### No Mirror Field (Default to Post-Install)
```yaml
name: my-other-addon
type: addon
# No mirror field = mirrored in Phase 2b
operators:
  - name: my-other-operator
    # ... operator config
```

## Monitoring and Troubleshooting

### Log Files
- Core mirroring: `$workingDir/logs/oc-mirror-core.progress.*.log`
- Operator mirroring: `$workingDir/logs/oc-mirror-operators.progress.*.log`
- Background operator mirroring: `$workingDir/logs/operator-mirror.*.log`

### Process Monitoring
```bash
# Check if operator mirroring is still running
cat $workingDir/logs/operator-mirror.pid

# Monitor operator mirroring progress
tail -f $workingDir/logs/operator-mirror.*.log

# Check exit status
cat $workingDir/logs/operator-mirror.exit
```

### Common Issues

#### Operator Mirroring Failed
```bash
# Check exit status
cat $workingDir/logs/operator-mirror.exit

# View detailed logs
cat $workingDir/logs/operator-mirror.*.log
cat $workingDir/logs/oc-mirror-operators.progress.*.log
```

#### Mirror Registry Not Ready
If `02b-mirror-operators.yaml` fails because mirror registry isn't ready:
```bash
# Verify registry is running
podman ps | grep quay-app

# Re-run operator mirroring manually
ansible-playbook playbooks/02b-mirror-operators.yaml -e workingDir=/home/cloud-user
```

## Performance Benefits

### Time Savings
- **Total deployment time:** Reduced from ~60 minutes to ~45 minutes (25% improvement)
- **Critical path optimization:** Only core components block OpenShift deployment
- **Resource utilization:** Network and disk I/O optimized through parallelization

### Disk Space
- **Core mirroring:** ~75GB (reduced from ~150GB)
- **Operator mirroring:** ~75GB (separate workspace)
- **Total:** Same total space, but spread across two workspaces

### Network Optimization
- **Reduced retries:** Smaller core imageset has fewer network issues
- **Parallel downloads:** Two oc-mirror processes can utilize full bandwidth
- **Earlier start:** OpenShift deployment starts 15+ minutes sooner

## Implementation Details

### Core vs Post-Install Separation
The split is determined by analyzing plugin configurations:
- **Core plugins:** `mirror: core` or foundation plugins without mirror field
- **Post-install plugins:** Any other mirror value or addon plugins

### Workspace Isolation
- Core mirroring: `oc-mirror-workspace-core`
- Operator mirroring: `oc-mirror-workspace-operators`

This prevents workspace conflicts and enables parallel execution.

### Exit Status Tracking
Background operator mirroring saves its exit status to `operator-mirror.exit` file, allowing the wait playbook to detect failures and report them appropriately.