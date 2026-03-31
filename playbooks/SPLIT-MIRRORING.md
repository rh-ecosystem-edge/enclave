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

### Phase 2a: Core Mirroring (Integrated in `02-mirror-split.yaml`)
**Purpose:** Mirror only essential components needed for OpenShift installation
- OpenShift platform images and catalogs
- Foundation plugins marked with `mirror: core` (LVMS, ODF)
- Essential base images

**Duration:** ~15 minutes (reduced from ~30 minutes)
**Implementation:** Integrated into `02-mirror-split.yaml` using direct task inclusion instead of separate playbook execution

### Phase 2b: Operator Mirroring (Integrated in `02-mirror-split.yaml`)
**Purpose:** Mirror operators and images for post-installation
- Operators from `defaults/operators.yaml` (Quay, ACM, GitOps, etc.)
- Model operators from `defaults/model_operators.yaml`
- Post-install plugin operators not marked `mirror: core`
- Additional images (Vault, ArgoCD, etc.)

**Duration:** ~15 minutes (runs asynchronously with OpenShift deployment using Ansible async tasks)
**Implementation:** Integrated into `02-mirror-split.yaml` using async execution instead of separate processes

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

### Option 3: Individual Phase Execution (Deprecated)
```bash
# Execute only core mirroring (deprecated - use 02-mirror-split.yaml instead)
ansible-playbook playbooks/02a-mirror-core.yaml -e workingDir=/home/cloud-user

# Execute only operator mirroring (deprecated - use 02-mirror-split.yaml instead)
ansible-playbook playbooks/02b-mirror-operators.yaml -e workingDir=/home/cloud-user
```

**Note:** These individual playbooks are deprecated. The integrated `02-mirror-split.yaml` provides better task coordination, error handling, and monitoring capabilities.

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

### Async Task Monitoring
```bash
# Check stored async job ID
cat $workingDir/logs/operator-mirror.async_job_id

# Monitor async task status manually (using stored job ID)
ansible localhost -m async_status -a "jid=$(cat $workingDir/logs/operator-mirror.async_job_id)"

# Wait for completion using the wait playbook
ansible-playbook playbooks/wait-operator-mirror.yaml -e workingDir=/home/cloud-user
```

### Common Issues

#### Async Operator Mirroring Failed
```bash
# Check async task status using stored job ID
ansible localhost -m async_status -a "jid=$(cat $workingDir/logs/operator-mirror.async_job_id)"

# Use the wait playbook to get detailed results
ansible-playbook playbooks/wait-operator-mirror.yaml -e workingDir=/home/cloud-user
```

#### Mirror Registry Not Ready
If operator mirroring fails because mirror registry isn't ready:
```bash
# Verify registry is running
podman ps | grep quay-app

# Re-run the split mirroring (which includes better validation)
ansible-playbook playbooks/02-mirror-split.yaml -e workingDir=/home/cloud-user

# Or run the standalone operator playbook (deprecated)
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

### Async Task Management
Operator mirroring uses Ansible's built-in async functionality:
- **Job ID tracking:** Saved to `operator-mirror.async_job_id` file for reference
- **Status monitoring:** Use `ansible-module async_status` or the wait playbook
- **Error handling:** Integrated with Ansible's error reporting and retry mechanisms
- **Process isolation:** Runs within the same Ansible process context, sharing variables and configuration

This approach eliminates the need for external process management and PID files, providing better integration with Ansible's error handling and monitoring capabilities.