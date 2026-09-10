# Design

## Status

This design describes the implemented synchronous Python client and its thin
one-shot CLI for the supplied REST API. It does not implement the cluster service
and does not claim distributed ACID guarantees.

## Public API proposal

The public import package is `mci_cluster_client`:

```python
from mci_cluster_client import ClusterClient, OperationReport, RetryPolicy, TimeoutConfig

with ClusterClient(
    nodes=["node1.example.com", "https://node2.example.com"],
    timeout=TimeoutConfig(connect=2.0, read=5.0, write=5.0, pool=2.0),
    retry=RetryPolicy(max_attempts=3, base_delay=0.1, max_delay=1.0),
) as client:
    created: OperationReport = client.create_group("group-123")
    deleted: OperationReport = client.delete_group("group-123")
```

Constructor injection accepts either an HTTP transport or a caller-owned
`httpx.Client`, plus sleep, random, and operation-ID callables for deterministic
tests. Authentication, custom TLS, or headers belong at that HTTP boundary; reports
must not expose them. Configuration objects are immutable. `create_group` and
`delete_group` either return a complete successful report (including no-op success)
or raise a structured `ClusterOperationError` carrying the complete unsuccessful
report. `close()` and context-manager exit close only an internally created HTTP
client; a supplied client remains caller-owned.

## Component boundaries

| Component | Responsibility | Must not do |
| --- | --- | --- |
| `ClusterClient` | Validate an operation, coordinate preflight/forward/rollback, return or raise with a report | Construct raw requests or hide partial outcomes |
| URL and ID validation | Canonicalize nodes, reject unsafe/duplicate inputs, encode GET path segment | Make network calls |
| `NodeApi` | Map GET/POST/DELETE to the documented endpoint and classify responses | Decide cross-node rollback policy |
| Injectable transport | Execute one bounded HTTP request | Retry or log secret-bearing request data implicitly |
| Retry/reconciliation policy | Bound GET probes, calculate exponential backoff and jitter through injected functions | Resend ambiguous mutations by default |
| Saga journal | Accumulate immutable/serializable per-node evidence and ownership | Infer ownership from existence alone |
| Result/exception model | Expose machine-readable success, failure, rollback, and indeterminate details | Replace the original failure with a rollback error |
| CLI | Resolve process configuration, call `ClusterClient` once, serialize its report, and select an exit code | Reimplement transaction, retry, reconciliation, or compensation policy |

`httpx` is the planned HTTP implementation, but orchestration depends on a small
transport protocol so tests can script exact responses and exceptions.

## Validation and URL normalization

All validation finishes before the first mutation. Node normalization will:

1. require a non-empty node list of strings;
2. add `https://` when the scheme is omitted;
3. lowercase scheme and DNS host, apply IDNA through the URL library, remove a root
   trailing slash, and canonicalize default ports;
4. allow only `http`/`https` and require a host;
5. reject user information, query, fragment, and non-root base paths; and
6. reject duplicate canonical URLs while preserving input order.

The group ID rules are those in `docs/REQUIREMENTS.md`; the ID is encoded as one
GET path segment to prevent path injection.

## Node state and journal model

The operation journal has one entry for every node and retains input order.

| Field | Values |
| --- | --- |
| `initial_state` | `present`, `absent`, `unknown` |
| `preflight_attempts` / `preflight_error` | attempt count and last sanitized inconclusive evidence |
| `request_outcome` | `not_required`, `not_attempted`, `confirmed_success`, `ambiguous` |
| `reconciliation_outcome` | `not_required`, `present`, `absent`, `unknown` plus attempts/evidence |
| `transition_ownership` | `none`, `confirmed`, `inferred`, `possible` |
| `compensation_outcome` | `not_required`, `not_attempted`, `confirmed_success`, `reconciled_success`, `failed`, `ambiguous` |
| `final_state` | `present`, `absent`, `unknown` |

The overall `OperationReport` includes a local correlation ID, action, group ID,
`succeeded`/`noop`/`failed`/`rolled_back`/`rollback_incomplete`/`indeterminate`
status, and all node entries. The correlation ID is for diagnostics only; the
upstream API does not accept it as an idempotency key.

## Preflight decision

GET every node using the read retry policy. Validate a 200 body strictly; classify
404 as absent. If any node remains unknown, raise `ClusterOperationError` without mutation.
Known present/absent states may be mixed. Each action selects only nodes whose
initial state differs from its target, while already-converged nodes remain
unchanged.

## Create transaction algorithm

1. Validate all inputs and preflight all nodes.
2. If every node is present, return `noop`.
3. Select initially absent nodes and POST only those nodes, sequentially in
   configured order. Initially present nodes are unchanged.
4. A 201 is confirmed success and creates compensation ownership. For an ambiguous
   or non-success result, do not repeat POST; reconcile with GET.
5. If reconciliation finds the requested group present, record the original
   mutation evidence, mark success as inferred, grant operation ownership under the
   documented no-concurrent-writer assumption, and continue.
6. If reconciliation finds absent or remains unknown, stop. Preserve that event as
   the forward failure and mark later nodes `not_attempted`.
7. Walk successful owned creates, including an inferred create on the failing node
   when applicable, in reverse order. If the failing mutation remains unresolved,
   also include it as a possible transition because its preflight was absent and
   this operation dispatched POST. DELETE each once. Reconcile uncertain
   compensation results with GET; absent is compensation success.
8. Raise `ClusterOperationError` with the full report. A complete rollback is
   `rolled_back`; any failed/unknown compensation is `rollback_incomplete` or
   `indeterminate`. The original create failure stays primary.

An unresolved ambiguous create is not treated as a confirmed transition. Under the
documented no-concurrent-same-group-writer assumption, however, its known absent
preflight plus dispatched POST makes one inverse DELETE the safest attempt to
restore the exact initial state. If verification is still unavailable, the report
remains indeterminate.

## Delete transaction algorithm

1. Validate all inputs and preflight all nodes.
2. If every node is absent, return `noop`.
3. Select initially present nodes and DELETE only those nodes, sequentially in
   configured order. Initially absent nodes are unchanged.
4. A 200 is confirmed success. For an ambiguous or non-success result, do not repeat
   DELETE; reconcile with GET.
5. If reconciliation finds absent, record an inferred successful deletion under
   the no-concurrent-writer assumption and continue. If it finds present or remains
   unknown, stop and preserve the original failure.
6. In reverse order, POST once for each attributable deletion. Include an unresolved
   dispatched DELETE as a possible transition under the no-concurrent-writer
   assumption. Reconcile uncertain compensation; a valid GET showing the group
   present is compensation success.
7. Raise `ClusterOperationError` with the complete report and separate rollback
   failures.

Delete compensation only restores the documented `groupId`. It cannot restore
undocumented server metadata. An unresolved mutation remains explicitly uncertain
even though it receives the single safest inverse attempt described above.

## Reconciliation rules

- Probe only with GET, a finite attempt limit, bounded per-request timeouts, and
  bounded delay between attempts.
- `200` plus a JSON object with exactly the requested `groupId` establishes
  `present`; `404` establishes `absent`.
- Malformed JSON, a non-object, missing/wrong/non-string `groupId`, redirects,
  unexpected statuses, timeouts, transport errors, and response decoding failures
  are failed probe evidence, not proof of either state.
- Retry failed reads with exponential backoff and jitter until a conclusive state
  or the budget is exhausted. Record every attempt in sanitized form.
- Reconciliation never erases the mutation's original timeout/status/error; it adds
  the best-known resulting state.
- A conclusive desired state can promote an ambiguous mutation to reconciled
  success. A conclusive original state is a forward failure. Exhaustion is
  indeterminate.

## Compensation ownership rules

Existence is not ownership. A create rollback DELETE requires all three facts:
initially absent, this operation dispatched POST, and success was confirmed or the
absent-to-present transition was safely inferred. A delete rollback POST similarly
requires initially present, this operation dispatched DELETE, and the
present-to-absent transition was confirmed or safely inferred.

Unchanged pre-existing state, unattempted nodes, and failed preflight are never
mutated as compensation. A still-unknown dispatched mutation may receive one
inverse attempt based on its exact known preflight state and the single-writer
assumption, not based on current existence. Ownership is per operation and is not
inferred from a prior process or repeated call.

## Exception and result model

Public exception hierarchy:

```text
ClusterClientError
|-- ValidationError
|-- ClientClosedError
`-- ClusterOperationError
```

Every exception is safe to stringify. `ClusterOperationError` exposes `.report`,
`.primary_error`, and an ordered `.compensation_errors` collection. Node failures
use stable enums/codes plus
sanitized status and exception category; callers do not need to parse messages.
Validation failures that occur before a journal exists contain field-level details
and no secret-bearing values.

## Command-line contract

The installed `mci-cluster` command and `python -m mci_cluster_client` share
`main(argv: Sequence[str] | None = None) -> int`. The `create` and `delete`
subcommands require a group ID and at least one node. Repeatable `--node` values take
complete precedence over the comma-separated `MCI_CLUSTER_NODES` fallback; the two
sources are not merged. Empty environment items are invalid.

The CLI constructs `TimeoutConfig` and `RetryPolicy` directly, so their existing
finite/range validation runs before client construction or network access. It uses
`ClusterClient` as a context manager and dispatches exactly one operation. A report
is emitted once to stdout as compact JSON, or indented JSON with `--pretty`; logs
and safe diagnostics use stderr. The CLI does not expose authentication, headers,
tokens, response bodies, or tracebacks for expected failures.

`ExitCode` defines the process contract: `0` for success/no-op, `2` for syntax or
configuration/validation errors, `3` for a known failed operation with complete
rollback, `4` for indeterminate state or incomplete/failed compensation, and `130`
for Ctrl+C. `ClusterOperationError` uses its existing report as the sole structured
error format. Unexpected programming errors remain visible to the caller.

## Retry policy

Defaults are three total GET attempts, 0.1-second base delay, 1-second delay cap,
no jitter, and explicit HTTP timeouts of 2 seconds connect/pool and 5 seconds
read/write. Callers can opt into symmetric jitter bounded by the configured delay
cap. Configuration rejects non-finite or invalid values.

Delay follows capped exponential backoff, with injected jitter and sleep. The same
read policy applies independently to each preflight and reconciliation probe, so an
operation cannot retry forever.

POST and DELETE each get one attempt by default. The supplied API defines neither
idempotency keys nor DELETE-on-absent behavior, so neither mutation is blindly
retried. A later opt-in retry must cite an extended server contract proving the
specific operation safe.

## Concurrency decision

The baseline performs mutations and compensation sequentially in configured order.
This makes the causal journal and reverse rollback order deterministic, stops after
the smallest known partial-success prefix, reduces simultaneous load on an unstable
API, and makes the assignment easier to reason about. Preflight is also sequential
for deterministic evidence and tests; bounded parallel preflight is only an
optional future optimization.

Each `ClusterClient` instance uses a small operation lock, serializing operations
through that instance. It cannot coordinate separate client instances, processes,
or external writers. Parallel mutation is an optional enhancement because it
increases the number of in-flight ambiguous outcomes and eliminates a simple
rollback stack.

## Observability and security

Emit optional structured events for operation start/end, per-node phase and
classification, retry count, duration, and rollback summary. Use normalized node
origin, local correlation ID, action, and hashed or explicitly safe group ID fields.
Do not log request/response bodies by default. Always redact authorization,
proxy-authorization, cookies, API keys, transport configuration, and arbitrary
headers. Exception reprs and serialized reports follow the same rule.

Use TLS verification by default. Redirects are disabled so credentials and mutations
are not forwarded to an unexpected host. Authentication and custom certificate
configuration are injected; the library does not discover credentials or store
secrets.

## Known distributed-systems limitations

- Client-side compensation is not atomic isolation or durability. Other clients can
  observe intermediate partial state.
- The client can crash between a mutation and journal/compensation. Without durable
  server or client transaction state, automatic recovery is impossible.
- A timeout plus GET observes state, not causality. The inferred-ownership rule
  relies on no concurrent external writer for the same group.
- Reconciliation can itself fail or return stale data. The outcome must then remain
  indeterminate rather than claiming success or rollback.
- Compensation can fail, and a new call cannot reconstruct ownership from current
  existence alone.
- Delete compensation recreates only `groupId`; undisclosed metadata or side effects
  cannot be restored.
- Nodes may diverge after a reported success because the remote system supplies no
  ongoing replication or consensus guarantee.

## Later delivery design

The implemented `src/` package includes the one-shot CLI. The future Docker image
will execute `mci-cluster` as a non-root user. Kubernetes manifests should use a Job
with node/action/group configuration and Secret references, not an invented service.
GitHub Actions will install from the declared lock in a clean environment and run
the five repository quality commands. Those delivery artifacts remain deferred.
