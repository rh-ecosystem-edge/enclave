# Standalone private vLLM serving demonstration

This optional `vllm` addon installs one shared private runtime and one active,
operator-selected model per deployment. It uses a native Deployment, not
OpenShift AI, KServe, Knative, llm-d or a serving operator. It does not reinstall
GPU drivers. There is no Route, Ingress, gateway, LoadBalancer or NodePort.

**An authenticated shared HTTPS gateway and per-caller rate/concurrency/token
limits are required prerequisites for shared external access. They are NOT
implemented by this PR. Do not expose the runtime externally until separately
secured gateway integration has been completed.** The ClusterIP Service does
not authenticate callers. NetworkPolicy selectors are network reachability
controls, not caller identity, authorization or rate limiting. No shared API
secret is supplied. Any opted-in client can use the entire model API; permit
only a separately secured gateway or trusted test clients.

## Operator prerequisites and configuration

Prepare ACM-managed OpenShift targets with a NetworkPolicy-enforcing CNI,
working storage and the existing `nvidia-gpu` addon prerequisites (NFD, GPU
operator, drivers/device plugin and schedulable `nvidia.com/gpu`). The addon
requests whole GPU resources; the operator must ensure dedicated, non-time-sliced,
non-shared GPU allocation if that is required. It provides no hard tenant GPU
execution isolation, serving HA or multi-user capacity guarantee. Multi-GPU
tensor parallelism is within one pod on one node, not distributed serving.

Create the `enclave-vllm` namespace on each intended target first. Preload a PVC
there, with a directory containing all weights, config, tokenizer and the model's
own chat template. Use an immutable, operator-controlled snapshot/artifact. Record
its immutable revision or content identity in `artifactIdentity`; the addon only
records that string in a pod annotation, it does NOT verify model hashes or
revision contents. Protect the PVC from other writers, and make it readable by
the namespace's restricted arbitrary UID/group. Nothing downloads or populates
models. No Hugging Face token is consumed. Missing local files fail startup,
rather than being fetched. Models requiring `trust-remote-code` are unsupported.

Write `config/plugins/vllm.yaml` with a top-level `vllm` mapping. Required keys:

| Key | Operator-supplied value |
| --- | --- |
| `runtimeImage` | Fully qualified OCI reference with `@sha256:` and a real 64-digit digest |
| `modelPVC` | Existing PVC name in `enclave-vllm` on every selected target |
| `modelPath` | Directory relative to PVC root, e.g. `models/revision_1` (no traversal) |
| `artifactIdentity` | Immutable model release/content identifier the operator attests |

No runtime image has been selected or validated by this demonstration. Supply
one from an authorized source and verify architecture, model/precision support,
CUDA/driver compatibility, and arbitrary-UID/restricted SCC operation. The image
must provide `python3`, `vllm.entrypoints.openai.api_server`, `/health`, the OpenAI
completion API, and all CLI flags in `plugins/vllm/files/serving.yaml.j2`, including
`--disable-log-requests`, `--generation-config vllm` and
`--chat-template-content-format`. Pinning an image is not a compatibility test.
Tests use **synthetic references under registry.example.com**, which are not
pullable images or approved artifact digests. Never deploy these fixtures.

Optional keys (configuration sizing, not measured requirements):

| Key | Default | Constraint / meaning |
| --- | --- | --- |
| `dtype` | `bfloat16` | `auto`, `half`, `bfloat16`, `float`; match model/hardware |
| `maxModelLen` | 4096 | 128–131072 total prompt **plus output** tokens |
| `maxNumSeqs` | 2 | 1–32 scheduler sequence limit, not two-user capacity |
| `gpuCount` | 1 | 1, 2, 4, 8 full GPUs and matching tensor parallel size |
| `cpu` | `"4"` | Positive whole CPU string; request equals limit |
| `memory` | `16Gi` | Positive Mi/Gi quantity; request equals limit |
| `shmSize` | `2Gi` | Memory-backed `/dev/shm`, charged to pod memory |
| `cacheSize` | `4Gi` | Bounded disk emptyDir for `/cache`, includes temporary files |
| `chatTemplateContentFormat` | `auto` | `auto`, `string`, `openai`; model-local template |
| `enableSmokeTest` | false | Opt-in target-side real completion request |

The served API alias is always `enclave-llm`. Generation uses vLLM defaults rather
than automatically loading model-provided generation settings. Chat requests use
the preloaded tokenizer's template. No arbitrary CLI strings or executable chat
templates can be supplied through addon configuration. Review model artifacts as
trusted code/data inputs even without remote-code execution. Request body logging
is disabled; do not enable sensitive prompt logging at a future gateway.

Optional reference only: Qwen3-8B BF16 on one L4 24GB GPU is a pilot
candidate, but is **unbenchmarked and unvalidated**, not a default, dependency or
support claim. Choose compatible model, precision, context and memory after
testing. One dedicated GPU per replica, 4096 context and two scheduler sequences
are a starting profile, not a throughput/concurrency guarantee.

## Placement, mirroring and rollout

1. Prepare GPU/storage/model artifacts on each target as above. `order: 120`
   follows GPU addon order 110 but does not prove GPU readiness.
2. Enable `vllm` in the configured addon list, or run the existing standalone
   `make deploy-plugin PLUGIN=vllm` workflow. This submits an ACM Policy with an
   explicit PlacementRule and PlacementBinding on the hub. Only available
   ManagedClusters labelled `enclave.vllm/serve=true` receive the workload. The GPU
   addon's placement label alone does not select targets for this addon.
3. In disconnected environments, `make deploy-plugin PLUGIN=vllm` enables addon
   mirroring. The descriptor's configured digest feeds
   `templates/plugin-imageset.yaml.j2`, oc-mirror to the Landing Zone registry,
   then the Quay Enterprise mirror flow and generated IDMS. The descriptor also
   supplies the exact source repository and mirror repository path to the
   registry/MCE integration. Deployment retains the source digest reference and
   relies on cluster mirror configuration, not a fabricated replacement tag.
   Ordinary phase 2 / phase 5 alone do **not** automatically mirror addon images.
   `make mirror-plugin PLUGIN=vllm` is the existing mirror-only tagged path;
   it skips pre-validation, so validate config first (or invoke
   `playbooks/deploy-plugin.yaml` with `--tags pre-validate,mirror`,
   `-e plugin_name=vllm -e plugin_mirror=true` and normal environment arguments).
   Connected environments must have the pinned image available via approved
   registry access; mirroring requires source pull authorization outside Git.
4. Inspect ACM compliance and target rollout. The default post-validation only
   reports submission, not readiness or inference. For the opt-in smoke set
   `enableSmokeTest: true` and pass `vllm_smoke_kubeconfig` (a local target
   kubeconfig **path**, never its contents) via normal Ansible extra variables.
   The path must be nonempty and refer to an existing file; check mode is rejected.
   Run against each selected target. It waits for Ready then executes a real
   loopback `/v1/completions` request from the server container, checking returned
   served alias, choices and positive generated-token usage without logging the
   response. Missing, skipped or nonzero exec results fail the smoke check.
   This does not test network caller paths,
   chat semantics, gateway authentication or capacity. It needs target exec rights.
5. By default ingress and egress are denied for all namespace pods. To permit a
   separately secured gateway or trusted test client, authorized administrators
   must label **both** its namespace and pod `enclave.vllm/caller=true`. The single
   peer combines namespaceSelector AND podSelector; only TCP 8000 is allowed.
   Do not delegate these labels to untrusted tenants. No DNS/outbound access is
   granted to the server. Image pulls are node-side, not pod egress. The two
   addon NetworkPolicies use exact-spec reconciliation, but Kubernetes policies
   are additive: administrators must not introduce other permissive policies in
   this dedicated namespace. Privileged/host-network clients require separate
   cluster controls; these policies do not constrain cluster administrators.

Internal API address: `http://enclave-llm.enclave-vllm.svc:8000/v1`.
This is private plaintext transport behind the required future secured gateway,
not an externally reachable endpoint. Restrict cluster access accordingly.

Changes to runtime image, artifact identity, model path, precision or context
update the pod template and recreate the single replica (downtime, no hot swap).
Changing PVC contents in place is unsupported. Remove placement and explicitly
inspect/clean target resources when retiring the addon; no automatic model/PVC
deletion or policy-pruning lifecycle is promised.

## Local checks versus target validation

Using the locked uv environment and `ansible_collections.txt` collections:

```sh
make -f Makefile.ci validate-plugins
make -f Makefile.ci validate-json-schema
ansible-playbook plugins/vllm/test-fixtures/validate.yaml
git diff --check
```

The plugin-local Ansible harness renders the actual workload and core image-set
and registry templates, asserts placement, private networking, security, offline
flags, resources and multiple model profiles, and rejects invalid schema fixtures.
Local synthetic exec/response tests verify smoke failure handling without
performing inference or connecting to Kubernetes.
It performs no downloads, cluster connections or deployments. These playbooks are
not part of the Python tooling unit suite. Core `validate-templates` covers fixed
core templates and does not replace this plugin test.

Live GPU startup/inference, restricted SCC and arbitrary UID writes, offline
failure behavior, actual image mirroring/pulls, PVC permissions/storage behavior,
NetworkPolicy allow/deny, model/chat correctness, memory/throughput and future
gateway authentication/TLS/per-caller limits all require separate target tests.
None is established by a local render/schema test.

Public references: [vLLM Kubernetes deployment](https://docs.vllm.ai/en/latest/deployment/k8s/),
[engine arguments](https://docs.vllm.ai/en/latest/configuration/engine_args/),
[security guidance](https://docs.vllm.ai/en/latest/usage/security/),
and [standalone Red Hat AI Inference deployment](https://docs.redhat.com/en/documentation/red_hat_ai_inference/3.4/html/deploy_the_standalone_red_hat_ai_inference_container_in_openshift_container_platform).
