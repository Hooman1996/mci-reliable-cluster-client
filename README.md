# MCI Reliable Cluster Client

[![CI](https://github.com/Hooman1996/mci-reliable-cluster-client/actions/workflows/ci.yml/badge.svg)](https://github.com/Hooman1996/mci-reliable-cluster-client/actions/workflows/ci.yml)

A synchronous Python client and one-shot CLI that create or delete one group on
every configured node of an unreliable REST API. The project implements an API
consumer, not the remote cluster service.

## Scope

The client validates inputs, preflights every node, converges mixed state, reconciles
ambiguous mutations with bounded reads, and compensates attributable changes after
failure. Results are immutable, machine-readable operation reports.

It does not provide a server, web UI, database, authentication scheme, durable Saga
store, distributed lock, or background repair service.

## Installation

Install a clone into an isolated Python 3.11 or 3.12 environment:

```bash
python -m venv .venv
source .venv/bin/activate
python -m pip install .
mci-cluster --help
```

The only runtime dependency is `httpx`. The committed `uv.lock` is mandatory and
records both runtime and development dependencies. Create an exact uv-managed
development environment with:

```text
uv sync --locked --all-groups
```

`--locked` makes a missing or stale lockfile an error instead of changing it.

## Container image

BuildKit uses the committed `uv.lock` to export the exact runtime-only dependency
set, builds the application and dependency wheels in a builder stage, then installs
only from that wheel collection with index access disabled in a clean Python 3.12
slim Bookworm runtime stage:

```text
docker buildx build --check .
docker buildx build --load --tag mci-cluster-client:local .
```

The image defaults to `--help`, so running it with no arguments is safe and does not
contact a cluster. Help and version output also require no network:

```text
docker run --rm mci-cluster-client:local
docker run --rm mci-cluster-client:local --help
docker run --rm mci-cluster-client:local --version
docker run --rm mci-cluster-client:local create --help
docker run --rm mci-cluster-client:local delete --help
```

Real operations receive nodes at invocation time. `--node` is repeatable:

```text
docker run --rm mci-cluster-client:local \
  create group-123 \
  --node https://node1.example.com \
  --node https://node2.example.com

docker run --rm mci-cluster-client:local \
  delete group-123 \
  --node https://node1.example.com \
  --node https://node2.example.com
```

Alternatively, pass a comma-separated node list through `MCI_CLUSTER_NODES`:

```text
docker run --rm \
  -e MCI_CLUSTER_NODES=https://node1.example.com,https://node2.example.com \
  mci-cluster-client:local create group-123
```

After an operation attempt, stdout contains one JSON report and diagnostics go to
stderr. Exit codes are `0` for success/no-op, `2` for usage or validation errors,
`3` for a known failure with complete rollback, `4` for an indeterminate or
incompletely rolled-back result, and `130` for interruption.

The image embeds no nodes, group IDs, credentials, tokens, certificates, or other
environment-specific configuration. Authentication is outside the challenge API
contract and remains the caller's responsibility; deployments that add it should
inject it at runtime through an appropriate caller-controlled HTTP boundary.

The runtime uses the dedicated numeric user and group `10001:10001` and a root-owned,
non-writable working directory. It is designed to run with no Linux capabilities
and no writable filesystem. For example, use the intended restrictions as follows:

```text
docker run --rm --network none --read-only --cap-drop ALL \
  --security-opt no-new-privileges \
  mci-cluster-client:local --help
```

There is no `HEALTHCHECK`: this is a finite CLI/job rather than a service. For the
same reason, the Kubernetes artifact is a `Job`, not a `Deployment`.

The builder uses the official uv image at the intentionally pinned `0.12.11` tag.
It checks that `uv.lock` agrees with `pyproject.toml` before exporting dependencies;
a missing or stale lock therefore fails the build. uv, Hatchling, build tools,
development dependencies, caches, tests, and the source checkout do not enter the
runtime stage.

## Kubernetes Job

The basic deployment example under [`manifests/`](manifests/README.md) runs the
finite CLI as a `Job`, not a continuously restarted Deployment. Its
`backoffLimit: 0` prevents Kubernetes from repeating a potentially indeterminate
mutation after the client's own bounded retries and compensation have finished.

Render it offline before use, and replace the example hosts, group ID, and image:

```text
kubectl kustomize manifests
```

After reviewing those values, the operational lifecycle is:

```text
kubectl apply -k manifests
kubectl logs job/mci-group-operation
kubectl delete -k manifests
```

See the [manifest guide](manifests/README.md) for the client-side dry-run,
wait/status commands, image replacement, kind workflow, security settings, and safe
rerun guidance.

## Library usage

```python
from mci_cluster_client import (
    ClusterClient,
    ClusterOperationError,
    RetryPolicy,
    TimeoutConfig,
)

try:
    with ClusterClient(
        nodes=["node1.example.com", "https://node2.example.com"],
        timeout=TimeoutConfig(connect=2, read=5, write=5, pool=2),
        retry=RetryPolicy(max_attempts=3, base_delay=0.1, max_delay=1, jitter_ratio=0),
    ) as client:
        report = client.create_group("group-123")
except ClusterOperationError as error:
    report = error.report

payload = report.to_dict()
```

Bare node hosts default to HTTPS. Callers that need credentials, custom headers, or
TLS customization must supply their own `httpx.Client`; secret-bearing data is not
included in reports or logs.

## CLI usage

Installation provides `mci-cluster`; source checkouts can use
`PYTHONPATH=src python -m mci_cluster_client` with the same arguments:

```text
mci-cluster create GROUP_ID --node HOST [--node HOST ...]
mci-cluster delete GROUP_ID --node HOST [--node HOST ...]

mci-cluster create group-123 \
  --node node1.example.com \
  --node https://node2.example.com \
  --connect-timeout 2 --read-timeout 5 --write-timeout 5 --pool-timeout 2 \
  --get-attempts 3 --backoff-base 0.1 --backoff-cap 1 --jitter 0 \
  --log-level INFO --pretty

mci-cluster delete group-123 --node node1.example.com
mci-cluster --version
```

`--node` is repeatable. Alternatively, `MCI_CLUSTER_NODES` supplies a comma-separated
list; surrounding whitespace on each item is removed, while missing or empty items
are rejected:

```text
export MCI_CLUSTER_NODES='node1.example.com, https://node2.example.com'
mci-cluster create group-123
```

Configuration precedence is simple: one or more CLI `--node` values replace the
environment list completely; the two sources are never combined. Other options use
their CLI values or the defaults from `TimeoutConfig` and `RetryPolicy`. The CLI does
not read authentication data or accept arbitrary headers or tokens.

### Output and exit codes

After an attempted cluster operation, stdout contains exactly one JSON report:
compact by default or indented with `--pretty`. Logs and safe human diagnostics go
to stderr. Syntax, configuration, and validation failures occur before a report
exists and therefore write diagnostics only to stderr.

| Code | Meaning |
| ---: | --- |
| `0` | Succeeded, including an idempotent no-op |
| `2` | CLI syntax, configuration, or input validation error |
| `3` | Operation failed, rollback is complete, and final state is known |
| `4` | Final state is indeterminate, or compensation failed/remains unresolved |
| `130` | Interrupted with Ctrl+C |

Example compact success report (node entries contain the full per-phase journal):

```json
{"operation_id":"7c79d6dbe0e84f95a854bc197237f391","action":"create","group_id":"group-123","status":"succeeded","succeeded":true,"has_unresolved_final_state":false,"nodes":[{"node":"https://node1.example.com","initial_state":"absent","mutation_required":true,"preflight_attempts":1,"preflight_error":null,"request_outcome":"confirmed_success","mutation_attempts":1,"request_error":null,"reconciliation_outcome":"not_required","reconciliation_attempts":0,"reconciliation_error":null,"transition_ownership":"confirmed","compensation_outcome":"not_required","compensation_attempts":0,"compensation_error":null,"compensation_reconciliation_outcome":"not_required","compensation_reconciliation_attempts":0,"compensation_reconciliation_error":null,"final_state":"present"}],"primary_error":null,"compensation_errors":[]}
```

Enum fields use stable lowercase strings, nested errors are JSON objects, and nodes
remain in configured order. Expected failures do not expose tracebacks, response
bodies, credentials, cookies, tokens, or raw headers.

## Transaction behavior

The remote API has no transaction primitive, idempotency key, conditional mutation,
or version. The client therefore uses a sequential compensating Saga:

1. Validate the exact group ID and every normalized, unique node URL.
2. GET every node. If any preflight state stays unknown, perform no mutation.
3. Mutate only nodes whose known state differs from the requested final state.
4. Never blindly retry POST or DELETE. Reconcile ambiguous results with bounded GETs.
5. Stop at the first unresolved forward failure.
6. Compensate owned changes in reverse mutation order and report the best-known state.

Create when all nodes are present and delete when all nodes are absent are successful
no-ops. Mixed state is valid: create touches only absent nodes, while delete touches
only present nodes. A later invocation always performs fresh preflight and converges
from current state; it does not reuse ownership from an earlier operation.

Timeouts, transport failures, 5xx responses, redirects, and undocumented statuses can
mean that a mutation committed despite its response being lost. The client sends no
second mutation. It probes with GET up to `max_attempts`, using bounded exponential
backoff and optional jitter. A conclusive desired state permits progress but retains
the original ambiguous evidence; inconclusive state remains `unknown`.

Rollback is based on operation ownership, never existence alone. Create compensation
may delete only a group initially absent and created, inferred, or possibly changed by
this invocation. Delete compensation may recreate only a group initially present and
deleted, inferred, or possibly changed by this invocation. Unchanged and unattempted
nodes are never compensated. The primary failure is preserved separately from ordered
compensation errors. Recreating after delete restores only the documented `groupId`,
not undocumented server metadata.

## Failure handling and assumptions

`create_group` and `delete_group` return an `OperationReport` on success or no-op.
They raise `ClusterOperationError` for operation failures; its `.report`,
`.primary_error`, and `.compensation_errors` preserve the complete safe journal.
Invalid inputs raise `ValidationError`; a closed client raises `ClientClosedError`.
Unexpected programming errors are not converted into normal operation reports.

The design assumes no independent writer changes the same group during one operation.
Group state is assumed to consist only of the documented `groupId`. A valid GET is
either `404` or `200` with an exact matching string `groupId`. Group IDs are preserved
exactly and percent-encoded as one GET path segment. Node origins may be HTTP or HTTPS,
but HTTPS is the default for a bare host.

## Distributed-systems limitations

This Saga does not provide distributed atomicity, exactly-once delivery, isolation
from external writers, durable crash recovery, or cross-process locking. Partial
state is externally visible. A timeout followed by GET observes state rather than
causality; reconciliation may be stale or unavailable; compensation may fail; and a
process can crash between mutation and rollback. The client reports uncertainty
instead of claiming guarantees the upstream API cannot support.

## Development and verification

Create the development environment and run the quality gates from the repository
root:

```bash
uv sync --locked --all-groups
make lock-check
make verify
make k8s-render
```

All tests use scripted or stateful in-memory transports and block real socket
connections.

The `Makefile` provides the same commands through `make help`. `make verify` runs
the non-mutating formatting check, lint, strict type check, branch-aware coverage,
and offline CLI smoke tests. Docker build/check/smoke and offline Kubernetes render
targets are separate so they can be run only when their runtimes are available.

## Continuous integration

GitHub Actions runs on pushes to `main`, pull requests, and manual dispatches. Its
Python matrix matches the declared support policy by testing 3.11 and 3.12. Every
entry first requires `uv lock --check`, then installs all declared dependency groups
with `uv sync --locked --all-groups` in an isolated CI environment and runs
deterministic tests with branch coverage. A missing or stale lockfile fails CI.
Python 3.12 additionally gates Ruff formatting and lint, strict Mypy, offline CLI
help/version smoke tests, and package construction. Successful 3.12 runs retain the
wheel, source distribution, and coverage XML for seven days.

The Docker job waits for the whole Python matrix, checks the Dockerfile with
BuildKit, builds without publishing, and exercises only help/version commands with
networking disabled. It also verifies the non-root entrypoint/default command,
hardened read-only execution, image size visibility, the presence of `httpx`, and
the absence of development tools. The Kubernetes job also waits for Python and
only runs `kubectl kustomize`: it confirms exactly one ConfigMap and one Job, rejects
other resource kinds, and checks key lifecycle, resource, and security fields. It
does not configure a cluster or kubeconfig, apply resources, publish packages or
images, use repository secrets, commit changes, or push changes.

The Docker build consumes the same committed lockfile as CI and fails when it does
not match `pyproject.toml`. Hosted CI
([https://github.com/Hooman1996/mci-reliable-cluster-client/actions/workflows/ci.yml](https://github.com/Hooman1996/mci-reliable-cluster-client/actions/workflows/ci.yml))
verifies the committed lock, Python 3.11 and 3.12, package construction, the locked
Docker build, network-disabled hardened container smoke tests, and offline
Kubernetes rendering. The workflow verifies artifacts but does not publish
packages or images.

## Repository layout

```text
src/mci_cluster_client/   library, result models, transport helpers, and CLI
tests/                    deterministic library and CLI unit tests
docs/                     interpreted requirements, design, and test matrix
pyproject.toml            package metadata, console command, tools, dependencies
Dockerfile                multi-stage non-root executable image
.dockerignore             minimal, secret-safe Docker build context
manifests/                Kustomize base for the one-shot Kubernetes Job
Makefile                  unified local and CI developer commands
.github/workflows/ci.yml  read-only verification workflow; no publishing
```

See [DESIGN.md](docs/DESIGN.md) for the exact state machine and
[TEST_MATRIX.md](docs/TEST_MATRIX.md) for executable evidence.
