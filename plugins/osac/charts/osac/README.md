# OSAC Umbrella Helm Chart

This directory contains the umbrella chart for the Open Sovereign AI Cloud (OSAC)
deployment via Enclave.

## Sub-chart Dependencies

The `Chart.yaml` declares four sub-chart dependencies that must be populated
before deployment:

| Alias | Sub-chart directory | Source repository |
|-------|-------------------|-------------------|
| `operatorCrds` | `charts/operator-crds/` | `osac-project/osac-operator` |
| `operator` | `charts/operator/` | `osac-project/osac-operator` |
| `service` | `charts/service/` | `osac-project/fulfillment-service` |
| `aap` | `charts/aap/` | `osac-project/osac-aap` |

## Populating Sub-charts

### Option A: From osac-installer

1. Clone `osac-project/osac-installer`
2. Run `helm dependency build` in the osac-installer chart directory
3. Copy the resulting `charts/` contents into this directory

### Option B: From individual repos

1. Clone each component repository listed above
2. Copy each component's `charts/` directory into the corresponding path under
   `charts/osac/charts/`
3. Each sub-chart directory must contain its own `Chart.yaml`

## Verification

After populating, verify the chart structure:

```
charts/osac/
  Chart.yaml          # This file
  charts/
    operator-crds/
      Chart.yaml
      templates/
    operator/
      Chart.yaml
      templates/
    service/
      Chart.yaml
      templates/
    aap/
      Chart.yaml
      templates/
```
