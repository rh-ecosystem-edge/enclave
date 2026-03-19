# Tekton resources

Tekton Tasks and Pipelines are provided to perform various operations in the management cluster.

## Cluster Upgrade

Performs a cluster version upgrade.

PipelineRun example:

```yaml
apiVersion: tekton.dev/v1
kind: PipelineRun
metadata:
name: cluster-upgrade-dry-run
namespace: openshift-pipelines
spec:
params:
- name: dry-run
  value: "true"
pipelineRef:
  name: cluster-upgrade
taskRunTemplate:
  serviceAccountName: cluster-upgrade
timeouts:
  pipeline: "3h"
workspaces:
- name: shared-data
  emptyDir: {}
```
