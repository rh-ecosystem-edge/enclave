# NVIDIA GPU Support Module

This module provides optional NVIDIA GPU support for OpenShift clusters deployed via Enclave.

## Overview

NVIDIA GPU support is **opt-in** and **requires OpenShift AI** (Tier 2) to be deployed first.

## Prerequisites

**REQUIRED**: OpenShift AI must be deployed before installing NVIDIA support.

```bash
# Deploy OpenShift AI first
make deploy-openshift-ai

# Then deploy NVIDIA
make deploy-nvidia
```

## Quick Start

### Day0 Installation

**Connected Mode**:
```bash
# Enable NVIDIA after deploying OpenShift AI
make deploy-nvidia
```

**Disconnected Mode**:
```bash
# Mirror images and install
make deploy-nvidia-disconnected
```

### Day2 Installation

```bash
# Create configuration
cp config/nvidia.yaml.example config/nvidia.yaml

# Install NVIDIA
make day2-nvidia
```

## Configuration

See `operators/nvidia-gpu-operator/defaults/main.yaml` for all options.

## User Disclaimer

Before installation, you will be prompted to accept the NVIDIA EULA and support terms.

Type `yes` to proceed or `no` to cancel.

## Support Modes

1. **Certified** (default): Red Hat certified GPU operator
2. **Community**: Community GPU operator
3. **Vendor-Managed**: No operator; hardware vendor manages GPUs

## Verification

```bash
oc get clusterpolicy -n nvidia-gpu-operator
oc get pods -n nvidia-gpu-operator
```

## Documentation

Full documentation will be provided in MGMT-23443 implementation.
