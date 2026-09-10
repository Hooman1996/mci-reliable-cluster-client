# Kubernetes Job example

> **Warning:** these manifests are executable examples. Applying them attempts to
> create `example-group` on the three `example.com` hosts in the ConfigMap. Replace
> the hosts, group ID, and image with real, reviewed values before applying.

## Why this is a Job

`mci-cluster` is a finite transactional command, not a server. A Kubernetes `Job`
preserves that one-shot lifecycle and its exit status; a Deployment would keep
restarting a command that is supposed to finish. No Service, Ingress, persistent
storage, RBAC, or long-running controller is needed.

The Job has `backoffLimit: 0`. The client already performs bounded GET retries,
reconciles ambiguous mutations, and compensates attributable changes. Kubernetes
must not automatically launch the entire mutation again when its result may be
indeterminate. An operator must inspect the JSON report and process exit code before
deciding whether another invocation is safe.

## Required customization

Before applying:

1. Replace every example host in `configmap.yaml`.
2. Replace `example-group` in `job.yaml` with the intended group ID.
3. Keep `create` as the first argument or change it to `delete`.
4. Point the Kustomize image transformer at an image that exists in the target
   registry or container runtime.

The ConfigMap contains only the non-secret node list. It intentionally contains no
group ID, credentials, token, certificate, or authorization header. Authentication
is caller/environment specific and is not defined by the challenge contract, so no
fabricated Secret or authentication settings are included.

## Render and validate locally

Render exactly one ConfigMap and one Job without contacting a cluster API:

```bash
kubectl kustomize manifests
```

Ask `kubectl` to perform a client-side dry-run without server schema validation:

```bash
kubectl apply --dry-run=client --validate=false -k manifests -o yaml
```

This dry-run renders and processes the local resources but does not create a Job or
execute the client. If a particular `kubectl` build unexpectedly attempts discovery,
use `kubectl kustomize manifests` as the fully offline structural check; do not
change kubeconfig or create a cluster merely to validate these files.

## Image replacement

`kustomization.yaml` has an `images` transformer. Change `newName` and `newTag`
there instead of editing the Job:

```yaml
images:
  - name: mci-cluster-client
    newName: registry.example.com/your-team/mci-cluster-client
    newTag: 0.1.0
```

The registry path above is only a placeholder; this project has not published a
GHCR or other remote image.

## Apply, observe, and remove

Applying is the point at which the example would cause a real mutation. Recheck the
rendered hosts, action, group ID, image, and current cluster context first:

```bash
kubectl apply -k manifests

kubectl wait --for=condition=complete \
  --timeout=330s job/mci-group-operation

kubectl logs job/mci-group-operation

kubectl get pods \
  -l app.kubernetes.io/name=mci-cluster-client

kubectl get job mci-group-operation

kubectl get pods \
  -l app.kubernetes.io/name=mci-cluster-client \
  -o jsonpath='{range .items[*]}{.metadata.name}{"\t"}{.status.containerStatuses[0].state.terminated.exitCode}{"\n"}{end}'

kubectl delete -k manifests
```

The client writes one operation report as JSON to stdout and diagnostics to stderr.
Kubernetes log retrieval may present both streams together, so identify the JSON
object when reviewing `kubectl logs`. Exit code `0` means success or no-op, `3`
means a known failure with complete rollback, and `4` means the final state is
indeterminate or rollback is incomplete. A failed Job will not satisfy the
`complete` wait condition; inspect the Job, Pod exit code, and logs instead.

## Change or repeat an operation

To delete rather than create, change the first argument in `job.yaml`:

```yaml
args: ["delete", "example-group"]
```

Job pod templates are effectively immutable after creation. To change the action,
group ID, image, or node configuration for another operation, delete and recreate
the completed Job or use a new Job name.

Do not automatically rerun an exit-4 operation. First inspect the report's per-node
initial, mutation, reconciliation, compensation, and final states. A new invocation
performs fresh preflight and may safely converge known current state, but it cannot
recover transition ownership from the previous process or make an unresolved
operation atomic.

## Local kind workflow

Once the Docker image is buildable, a local image can be loaded into an existing
kind cluster. These commands are documentation only; this stage does not create or
contact a cluster:

```bash
docker buildx build --load \
  --tag mci-cluster-client:0.1.0 .

kind load docker-image \
  mci-cluster-client:0.1.0 \
  --name YOUR_KIND_CLUSTER

kubectl apply -k manifests
```

The Docker build must succeed before `kind load docker-image` can work. The local
tag matches the Kustomize default and `imagePullPolicy: IfNotPresent` lets a kind
node use the loaded image.

## Security and resources

The Pod requires a non-root process and the runtime-default seccomp profile. The
container fixes UID/GID `10001`, disables privilege escalation, makes its root
filesystem read-only, and drops every Linux capability. Service account token
automounting is disabled because the client does not call the Kubernetes API. No
`fsGroup` or writable volume is needed.

Requests of `50m` CPU and `64Mi` memory, with limits of `250m` CPU and `128Mi`
memory, are modest initial operational defaults for this small Python HTTP client.
They have not been load-tested and should be observed and adjusted for real node
counts and network behavior. The Job has one completion, one Pod at a time, a
five-minute active deadline, and a 30-second termination grace period.

## Current limitations

- The image is implemented but has not been built successfully because official
  PyPI timed out while resolving build dependencies; no runtime or kind validation
  is currently possible.
- `uv.lock` is absent, so image dependency resolution is not fully reproducible.
- The example hosts do not provide a real cluster API and must be replaced.
- Authentication and TLS customization are unspecified by the challenge and are
  not modeled by these manifests.
- Client-side rendering validates structure, not admission policies, runtime image
  availability, network policy, DNS, certificates, credentials, quotas, or actual
  connectivity in a target cluster.
