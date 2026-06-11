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

### Option A: Sync script (recommended)

```bash
scripts/setup/sync_osac_chart.sh           # sync from main
scripts/setup/sync_osac_chart.sh --ref v1.0 # sync from a specific tag
```

The script clones `osac-project/osac-installer` with submodules, copies each
sub-chart into the correct path, and runs `helm dependency build`. A `.synced-ref`
file records the commit for traceability.

### Option B: Manual from individual repos

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
