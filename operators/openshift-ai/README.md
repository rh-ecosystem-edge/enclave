# OpenShift AI Module

This module provides optional OpenShift AI (Red Hat OpenShift Data Science) support for OpenShift clusters deployed via Enclave.

## Overview

OpenShift AI is **opt-in** - it's an optional tier 2 module that adds AI/ML capabilities to your OpenShift cluster:

- **Red Hat OpenShift AI (RHOAI)**: ML platform for model serving and inference
- **KServe**: Model serving framework for production AI workloads
- **Supporting Operators**: NFD, Service Mesh, Cert Manager, and other dependencies

## Architecture

```
Tier 1: Base Enclave (always deployed)
  └── OpenShift cluster with core operators

Tier 2: OpenShift AI (optional) ← THIS MODULE
  ├── RHOAI operator (rhods-operator)
  ├── Node Feature Discovery (NFD)
  ├── Service Mesh (servicemeshoperator3)
  ├── Cert Manager (openshift-cert-manager-operator)
  ├── Supporting operators (RHCL, LWS, Limitador, Authorino, DNS)
  └── DataScienceCluster with KServe

Tier 3: NVIDIA Drivers (optional)
  └── Requires Tier 2 (this module)
```

## Quick Start

### Day0 Installation (During Deployment)

**Connected Mode**:
```bash
# Enable OpenShift AI in configuration
cat >> config/openshift-ai.yaml <<EOF
enable_openshift_ai: true
EOF

# Deploy cluster with OpenShift AI
make deploy-cluster-connected
make deploy-openshift-ai
```

**Disconnected Mode**:
```bash
# Enable OpenShift AI in configuration
cat >> config/openshift-ai.yaml <<EOF
enable_openshift_ai: true
EOF

# Mirror images and deploy
make deploy-cluster-disconnected
make deploy-openshift-ai
```

### Day2 Installation (Post-Deployment)

Add OpenShift AI to an existing cluster:
```bash
# Create configuration
cp config/openshift-ai.yaml.example config/openshift-ai.yaml
# Edit config/openshift-ai.yaml as needed

# Install OpenShift AI
make day2-openshift-ai
```

## Configuration

### Basic Configuration

Create `config/openshift-ai.yaml` from the example:

```bash
cp config/openshift-ai.yaml.example config/openshift-ai.yaml
```

Minimal configuration:
```yaml
---
enable_openshift_ai: true
```

### Advanced Configuration

Customize DataScienceCluster components (see `operators/openshift-ai/defaults/main.yaml`):

```yaml
---
enable_openshift_ai: true

# Enable additional AI components
openshift_ai_components:
  codeflare: "Managed"        # Distributed workloads
  dashboard: "Managed"        # Web UI
  datasciencepipelines: "Managed"  # ML pipelines
  kserve: "Managed"          # Model serving (default)
  kueue: "Managed"           # Job queuing
  modelmeshserving: "Removed"
  ray: "Managed"             # Distributed computing
  trainingoperator: "Managed" # Model training
  trustyai: "Removed"
  workbenches: "Managed"     # Development environments
```

### Disable Supporting Operators

If you already have certain operators installed:

```yaml
openshift_ai_support_operators:
  nfd_enabled: false  # Skip NFD if already installed
  servicemesh_enabled: true
  cert_manager_enabled: true
```

## Components

### Core Operators

1. **rhods-operator** (Red Hat OpenShift AI)
   - Namespace: `redhat-ods-operator`
   - Channel: `fast-3.x`
   - Purpose: Main AI platform operator

2. **nfd** (Node Feature Discovery)
   - Namespace: `openshift-nfd`
   - Purpose: Detects hardware features (GPUs, accelerators)
   - Required for: NVIDIA GPU support

3. **servicemeshoperator3**
   - Namespace: `openshift-operators`
   - Purpose: Service mesh for KServe networking
   - Required for: Model serving

4. **openshift-cert-manager-operator**
   - Namespace: `cert-manager-operator`
   - Purpose: TLS certificate management
   - Required for: Secure communications

### Supporting Operators

5. **rhcl-operator** (Red Hat Connectivity Link)
6. **leader-worker-set** (Distributed workloads)
7. **limitador-operator** (Rate limiting)
8. **authorino-operator** (Authorization)
9. **dns-operator** (DNS management)

## DataScienceCluster Configuration

By default, only **KServe** is enabled for minimal AI inference:

```yaml
apiVersion: datasciencecluster.opendatahub.io/v1
kind: DataScienceCluster
metadata:
  name: default-dsc
spec:
  components:
    kserve:
      managementState: Managed
    # All other components: Removed
```

To enable more components, customize `openshift_ai_components` in your configuration.

## Disconnected Environments

For air-gapped deployments:

1. **Mirror OpenShift AI images**:
   ```bash
   make mirror-openshift-ai
   ```

2. **Install operator**:
   ```bash
   make day2-openshift-ai
   ```

### Mirrored Operators

The following operators are mirrored from `registry.redhat.io/redhat/redhat-operator-index:v4.20`:

- nfd
- limitador-operator
- authorino-operator
- dns-operator
- rhcl-operator
- leader-worker-set
- rhods-operator
- servicemeshoperator3
- openshift-cert-manager-operator

### Blocked Images

To reduce mirror size, the following RHOAI images are **blocked** (excluded from mirroring):

- Dashboard images (odh-dashboard-*)
- CodeFlare images (odh-codeflare-*)
- Data Science Pipelines (odh-data-science-pipelines-*)
- Kueue images (odh-kueue-*)
- ModelMesh images (odh-modelmesh-*)
- KubeRay images (odh-kuberay-*)
- Training images (odh-training-*)
- TrustyAI images (odh-trustyai-*)
- Workbench images (odh-workbench-*)

These are only mirrored if you enable the corresponding components in `openshift_ai_components`.

## Verification

Check OpenShift AI installation:

```bash
# Verify namespace
oc get namespace redhat-ods-operator

# Verify operator
oc get subscription rhods-operator -n redhat-ods-operator

# Verify DataScienceCluster
oc get datasciencecluster

# Check all AI components
oc get pods -n redhat-ods-applications
```

## Makefile Targets

| Target | Description | Usage |
|--------|-------------|-------|
| `deploy-openshift-ai` | Deploy OpenShift AI (day0, connected) | After `make deploy-cluster-connected` |
| `day2-openshift-ai` | Deploy OpenShift AI (day2, post-deployment) | On existing cluster |
| `mirror-openshift-ai` | Mirror OpenShift AI images only | For disconnected prep |

## Ansible Tags

For granular control:

```bash
# Run all OpenShift AI tasks
ansible-playbook playbooks/08-openshift-ai.yaml --tags openshift-ai

# Mirror only
ansible-playbook playbooks/08-openshift-ai.yaml --tags openshift-ai-mirror

# Install only (images already mirrored)
ansible-playbook playbooks/08-openshift-ai.yaml --tags openshift-ai-install

# Verify only
ansible-playbook playbooks/08-openshift-ai.yaml --tags openshift-ai-verify

# Skip OpenShift AI in day2 flow
ansible-playbook playbooks/06-day2.yaml --skip-tags openshift-ai
```

## Time Estimates

| Mode | Mirror Time | Install Time | Total |
|------|-------------|--------------|-------|
| Connected | N/A | ~15-20 min | ~15-20 min |
| Disconnected | ~10-15 min | ~15-20 min | ~25-35 min |

## Troubleshooting

### Operator Not Installing

Check subscription status:
```bash
oc get subscription rhods-operator -n redhat-ods-operator -o yaml
```

### DataScienceCluster Not Ready

Check DSC status:
```bash
oc get datasciencecluster default-dsc -o yaml
```

### Missing Components

Verify all required operators are installed:
```bash
oc get operators
```

## Next Steps

After deploying OpenShift AI, you can:

1. **Add NVIDIA GPU support** (Tier 3):
   ```bash
   make deploy-nvidia
   ```

2. **Deploy AI models** using KServe

3. **Enable additional components** by updating `openshift_ai_components`

## References

- [Red Hat OpenShift AI Documentation](https://access.redhat.com/documentation/en-us/red_hat_openshift_ai)
- [KServe Documentation](https://kserve.github.io/website/)
- [Node Feature Discovery](https://github.com/kubernetes-sigs/node-feature-discovery)
