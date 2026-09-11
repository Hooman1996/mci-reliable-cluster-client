# Requirements

## Status

This document records the interpreted specification for the MCI API-consumer
take-home assignment. It includes the complete upstream API contract, required
client behavior, engineering assumptions, and known ambiguities needed to evaluate
the repository on its own.

## Upstream API contract

Every configured node exposes the same REST API:

| Action | Method and path | JSON body / response | Documented success | Documented error |
| --- | --- | --- | --- | --- |
| Create | `POST /v1/group/` | request `{"groupId": str}` | `201 Created` | `400`, perhaps already exists |
| Delete | `DELETE /v1/group/` | request `{"groupId": str}` | `200 OK` | none specified |
| Read | `GET /v1/group/{groupId}/` | response `{"groupId": str}` | implied `200 OK` | `404 Not Found` |

Only this remote contract is in scope. The submission must not implement the
cluster nodes or their API.

## Mandatory challenge requirements

- **M-01 — Client:** Deliver a Python library/module that creates or deletes one
  group on every configured remote node.
- **M-02 — Reliability:** Expect unstable nodes, including transport errors,
  timeouts, 5xx responses, malformed or unexpected responses, ambiguous outcomes,
  and partial success. On operation failure, make the best safe effort to restore
  the pre-operation state.
- **M-03 — Interpretation and usage:** State assumptions and provide clear usage in
  `README.md`.
- **M-04 — Unit tests:** Provide unit tests; live end-to-end services are not
  required and must not be needed by the test suite.
- **M-05 — Packaging:** Maintain the work as a Git repository suitable for
  publication to GitHub.
- **M-06 — Container:** Provide a Dockerfile or Containerfile for an image that
  executes the client. Hosted CI is configured to build the multi-stage Dockerfile
  and run offline CLI smoke tests after a push.
- **M-07 — Kubernetes:** Provide basic Kubernetes manifests under `manifests/`
  showing how the executable client is run. A secure, one-shot Job and its
  ConfigMap/Kustomize base are delivered.
- **M-08 — Quality:** Include reasonable code-quality measures. For this repository
  that includes formatting, linting, static typing, unit tests, and coverage.

`httpx` is a recommendation in the assignment, not an upstream protocol
requirement. This design adopts it as the sole runtime dependency unless later
implementation evidence justifies a change.

## Mandatory reliability and behavior requirements

- **R-01 — No false ACID claim:** The client must use a Saga/compensation workflow.
  It must explicitly state that a distributed ACID transaction is impossible
  without server-side transaction support.
- **R-02 — Validate before mutation:** Validate `group_id` and the entire node list,
  normalize node URLs, reject duplicate canonical URLs, and finish these checks
  before any network mutation.
- **R-03 — Preflight:** GET every node before mutation and record whether the group
  was initially present, absent, or unknown. Any unknown preflight state prevents
  mutation.
- **R-04 — Safe convergence:** For create, mutate only initially absent nodes and
  leave initially present nodes unchanged; all-present is an idempotent no-op. For
  delete, mutate only initially present nodes and leave initially absent nodes
  unchanged; all-absent is an idempotent no-op. Mixed state is valid input and must
  converge in stable node order.
- **R-05 — Per-node journal:** Record each node's normalized URL, initial state,
  preflight evidence, mutation result, reconciliation result, ownership decision,
  compensation result, attempt counts, safe error information, and best-known
  final state.
- **R-06 — Ambiguous mutation:** Treat request timeouts, transport failures, 5xx
  responses, and any response that does not establish the documented result as
  ambiguous where a request may have reached the server. Never blindly repeat the
  mutation.
- **R-07 — Reconciliation:** Resolve an ambiguous mutation with a bounded series of
  GET probes. A valid `200` containing the requested `groupId` means present;
  `404` means absent; wrong IDs, malformed JSON/schema, other statuses, or exhausted
  failures mean unknown.
- **R-08 — Compensation ownership:** Never delete during create rollback merely
  because the group exists. Delete only when the node was initially absent and this
  operation dispatched create; normally the transition is confirmed or safely
  inferred. Likewise, recreate during delete rollback only when the node was
  initially present and this operation dispatched delete. If reconciliation stays
  unknown, one inverse request is the safest restoration attempt under the explicit
  no-concurrent-same-group-writer assumption. Never compensate an unchanged,
  unattempted, or pre-existing state, and never claim restoration without evidence.
- **R-09 — Reverse rollback:** After a forward failure, stop new mutations and
  compensate attributable changes in reverse mutation order.
- **R-10 — Failure preservation:** Preserve the original forward failure as the
  primary cause. Report compensation failures separately without masking it.
- **R-11 — Idempotent calls:** Repeated create after global success returns a
  successful no-op; repeated delete after global success returns a successful
  no-op. A repeat following incomplete rollback uses fresh preflight and safely
  converges only nodes that still differ from the requested state; it never reuses
  historical ownership.
- **R-12 — Bounds:** Every HTTP request has explicit connect/read/write/pool
  timeouts. Read retries and reconciliation probes have a finite attempt count and
  bounded exponential backoff with optional bounded jitter.
- **R-13 — Retry selection:** Retry GET operations by default. Retry other
  operations only if the server contract establishes idempotency. Because the
  supplied API provides no idempotency key or conditional mutation, POST and DELETE
  are one-shot by default and ambiguous outcomes are reconciled instead.
- **R-14 — Structured contract:** Expose typed result objects and structured
  exceptions containing machine-readable per-node outcomes, including a distinct
  `indeterminate`/`rollback_incomplete` result when certainty is impossible.
- **R-15 — Security:** Accept authentication through an injectable HTTP boundary
  and never include secrets, authorization material, cookies, or unredacted request
  headers in logs, reprs, errors, or serialized reports.
- **R-16 — Deterministic tests:** Tests use a mocked/scripted injectable transport.
  Backoff sleeping and jitter/randomness are injected so tests do not sleep or
  depend on nondeterminism.
- **R-17 — Multi-node semantics:** Preserve configured node order, stop forward
  processing at the first unreconciled failure, mark later nodes not attempted, and
  retain a complete outcome for every configured node.

## Repository deliverables

The repository includes the production client, container definition, Kubernetes
Job, CI workflow, unified developer commands, and their documentation. The reviewed
dependency lock is committed and mandatory:

- **D-01 — Delivered:** Production client implementation and comprehensive unit
  test suite.
- **D-02 — Delivered:** CI checks the committed `uv.lock` and synchronizes all
  dependency groups in locked mode. The container exports its runtime-only
  dependency set from the same lock and installs from an isolated wheel collection.
- **D-03 — Delivered:** Multi-stage, non-root Dockerfile/Containerfile with useful
  executable behavior; hosted CI completed the build and network-disabled,
  hardened runtime smoke tests successfully.
- **D-04 — Delivered:** Basic Kubernetes manifests appropriate to that executable.
- **D-05 — Delivered:** GitHub Actions CI runs format check, lint, Mypy, unit tests,
  and branch coverage from a clean dependency installation.
- **D-06 — Delivered:** Final README with runnable library and CLI instructions.

## Working assumptions

- Node entries may omit a scheme; normalization defaults them to `https`. Only
  `http` and `https` are accepted. User information, query strings, fragments, and
  non-root base paths are rejected; trailing slashes and default ports are
  canonicalized before duplicate detection.
- `group_id` is a non-empty, non-whitespace-only string containing valid Unicode
  scalar values. Valid values are not stripped or otherwise changed. The value is
  URL-encoded as one path segment and sent unchanged in JSON. The upstream
  specification defines no tighter character or length constraint.
- A valid GET `200` response is a JSON object whose `groupId` exactly equals the
  requested value. The API documents no response body for POST or DELETE, so only
  their status codes are required on expected success.
- Group state consists only of `groupId`, as shown by the challenge. This makes a
  delete compensation capable of recreating the documented state. Hidden server
  metadata, if any, cannot be restored by this API.
- No independent actor mutates the same `group_id` during one client operation.
  The client can serialize same-process calls, but the API supplies no mechanism
  to enforce this assumption across processes.
- The one-shot CLI is the container entrypoint. Kubernetes models it as a `Job`,
  not a long-running API service, and disables whole-Job retries for ambiguous
  mutations.

## Unresolved specification ambiguities

- Authentication, TLS customization, headers, and credential discovery are not
  defined.
- The allowed `groupId` character set and maximum length are not defined.
- DELETE error statuses and whether deleting an absent group is server-idempotent
  are not defined.
- The meaning of unexpected 2xx, 3xx, 4xx (other than create 400), 5xx, and response
  bodies is not defined.
- The upstream API offers no idempotency key, conditional request, version,
  transaction identifier, or atomic multi-node primitive.
- The challenge asks to deploy a client but does not define invocation arguments,
  scheduling, exit codes, or whether Kubernetes should run a Job or Deployment.
- The challenge does not specify Python versions, package/import name, coverage
  threshold, repository visibility, or a license. This implementation supports and
  tests Python 3.11 and 3.12, uses import name `mci_cluster_client`, and requires 95%
  branch-aware coverage. The delivered repository is public at the requester's
  direction; no license has been selected.

## Optional enhancements

The following are explicitly non-mandatory and must not delay or complicate the
core submission:

- An async API or bounded parallel mutation mode after sequential semantics are
  proven.
- Persistent Saga recovery across client-process crashes.
- Metrics/tracing exporters beyond safe structured logging.
- A dry-run or explicit repair command for states that normal convergence cannot
  safely classify.
- Server-supported idempotency keys or conditional requests if the remote contract
  is extended.

An API server, web UI, database, Kubernetes operator, and service mesh are out of
scope, not enhancements for this assignment.
