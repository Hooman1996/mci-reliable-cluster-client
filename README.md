# MCI Reliable Cluster Client

A take-home project for a Python client that creates and deletes a group across
every configured node of an unreliable REST cluster. This repository implements a
client of the supplied API; it does not implement the cluster or its server.

## Current status

Stage 2 implements the synchronous library and deterministic unit tests. The CLI,
Dockerfile, CI workflow, and Kubernetes manifests remain intentionally deferred.

## Reliability model

A real multi-node ACID transaction is impossible because the remote API exposes no
transaction protocol. The client will use a Saga:

1. validate the group and all normalized node URLs;
2. preflight every node with GET;
3. apply mutations sequentially;
4. reconcile ambiguous outcomes with bounded GET probes; and
5. on failure, compensate only state attributable to this operation, in reverse
   order.

Timeouts and transport errors are ambiguous: the server may have committed before
the client lost the response. The client will not blindly repeat POST or DELETE.
It will retain a machine-readable outcome for every node and keep rollback failures
separate from the original failure.

## Usage

Install the package from a checkout in a clean environment with:

```text
python -m pip install .
```

Then construct the synchronous client:

```python
from mci_cluster_client import (
    ClusterClient,
    ClusterOperationError,
    RetryPolicy,
    TimeoutConfig,
)

nodes = [
    "node1.example.com",  # normalized to HTTPS
    "https://node2.example.com",
    "https://node3.example.com/",
]

try:
    with ClusterClient(
        nodes=nodes,
        timeout=TimeoutConfig(connect=2, read=5, write=5, pool=2),
        retry=RetryPolicy(max_attempts=3, base_delay=0.1, max_delay=1.0),
    ) as client:
        create_report = client.create_group("group-123")
        delete_report = client.delete_group("group-123")
except ClusterOperationError as error:
    # Safe, JSON-compatible operation journal:
    failure = error.report.to_dict()
```

Successful reports will include normal success and idempotent no-op success.
Failures will raise a structured exception carrying the full report, primary
failure, and separate compensation failures. `close()` is idempotent. A supplied
`httpx.Client` remains caller-owned; a client constructed internally, including one
using an injected `httpx.BaseTransport`, is closed by `close()` or context-manager
exit. See [the design](docs/DESIGN.md) for the types and exact algorithm.

Bare hosts default to HTTPS. Explicit HTTP and HTTPS origins are accepted; base
paths, embedded credentials, query strings, fragments, invalid hosts, and duplicate
canonical origins are rejected before requests. This library deliberately provides
no authentication mechanism. Callers needing authentication must configure a
caller-owned `httpx.Client`, whose headers and credentials are never copied into
reports or logs. Group IDs must be non-empty, non-whitespace-only strings containing
valid Unicode scalar values; accepted IDs are preserved exactly in JSON and
percent-encoded as one GET path segment.

## Idempotency semantics

- Create when all nodes already contain the group is a successful no-op.
- Delete when all nodes already lack the group is a successful no-op.
- Mixed initial state safely converges: create changes only absent nodes, while
  delete changes only present nodes.
- A repeat after an incomplete or indeterminate operation performs fresh preflight.
  It does not reuse historical ownership; it converges from the newly observed
  per-node states.

These are client semantics, not server-side idempotency. The supplied API has no
idempotency key or conditional mutation feature.

## Development environment

Use the existing `faq` Conda environment. Do not create `.venv` and do not use
`uv venv`, `uv sync`, or `uv run` against the shared environment. Dependencies are
declared in `pyproject.toml`. `uv.lock` remains pending because registry resolution
was unavailable in this environment; it must be generated with `uv lock` before
clean CI and container builds.

After activating `faq`, the eventual quality checks are:

```text
python -m ruff format --check .
python -m ruff check .
python -m mypy src
python -m pytest
python -m pytest --cov
```

Tests will use scripted transports and injected sleep/jitter; no live service is
required.

## Scope and delivery

Remaining mandatory final work includes a CLI, executable Docker image, basic
Kubernetes manifests, and GitHub Actions CI. Creating or pushing a GitHub repository
is not authorized in this phase.

Optional enhancements are limited to an async API, bounded parallelism, durable
Saga recovery, extra telemetry exporters, a repair command, or server-supported
idempotency if a richer upstream contract becomes available. An API server, web UI,
database, operator, and service mesh are out of scope.

## Documentation

- [Requirements and assumptions](docs/REQUIREMENTS.md)
- [Architecture and algorithms](docs/DESIGN.md)
- [Test coverage matrix](docs/TEST_MATRIX.md)
- Original read-only challenge: `reference/coding-challenge.md` and
  `reference/coding-challenge.pdf`

## Important limitations

Compensation is best effort and externally visible. The client cannot distinguish
its own timeout-after-commit from a concurrent writer using only GET, cannot recover
automatically from a process crash without durable transaction state, and cannot
restore server metadata not exposed by the API. Unknown state is reported as
indeterminate rather than presented as success.
