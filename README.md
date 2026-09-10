# MCI Reliable Cluster Client

A synchronous Python client and one-shot CLI that create or delete one group on
every configured node of the unreliable REST API in the supplied take-home
assignment. The project implements an API consumer, not the remote cluster service.

## Scope

The client validates inputs, preflights every node, converges mixed state, reconciles
ambiguous mutations with bounded reads, and compensates attributable changes after
failure. Results are immutable, machine-readable operation reports.

It does not provide a server, web UI, database, authentication scheme, durable Saga
store, distributed lock, or background repair service. Docker, Kubernetes, CI, and
GitHub publication are deliberately outside Stage 3.

## Installation

Reviewers can install a checkout into a clean Python 3.11+ environment:

```text
python -m pip install .
mci-cluster --help
```

The only runtime dependency is `httpx`. `uv.lock` is pending because registry access
was unavailable; generate it in the later delivery stage before reproducible clean
builds.

Local development uses the existing `faq` Conda environment. A project `.venv` is
intentionally not used, and `uv sync`, `uv run`, and `uv venv` must not be run against
the shared environment:

```text
conda activate faq
python -m pytest
PYTHONPATH=src python -m mci_cluster_client --help
```

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

After `conda activate faq`, run:

```text
python -m ruff format .
python -m ruff check .
python -m mypy src
python -m pytest
python -m pytest --cov=mci_cluster_client --cov-branch --cov-report=term-missing
PYTHONPATH=src python -m mci_cluster_client --help
PYTHONPATH=src python -m mci_cluster_client create --help
PYTHONPATH=src python -m mci_cluster_client delete --help
git diff --check
git status --short
test ! -e .venv
```

All tests use scripted or stateful in-memory transports and block real socket
connections.

## Repository layout

```text
src/mci_cluster_client/   library, result models, transport helpers, and CLI
tests/                    deterministic library and CLI unit tests
docs/                     interpreted requirements, design, and test matrix
reference/                read-only original challenge sources
pyproject.toml            package metadata, console command, tools, dependencies
```

## Pending delivery artifacts

- **Docker:** pending; the next stage can use `mci-cluster` as its image entrypoint.
- **Kubernetes:** pending; the eventual one-shot workload should be modeled as a Job.
- **CI and lock file:** pending until the later delivery stage and registry access.

See [DESIGN.md](docs/DESIGN.md) for the exact state machine and
[TEST_MATRIX.md](docs/TEST_MATRIX.md) for executable evidence. The original challenge
under `reference/` remains read-only.
