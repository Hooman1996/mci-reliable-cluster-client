# Requirements

## Status and source

This document is the interpreted specification for the MCI API-consumer take-home
assignment. The authoritative originals are the read-only files
`reference/coding-challenge.md` and `reference/coding-challenge.pdf`. Requirements
added by the assignment-preparation brief are mandatory for this repository unless
explicitly labeled optional.

The Markdown and two-page PDF have the same material requirements and API contract.
There is one non-material editorial difference: the Markdown joins
`httpxlibrary`, while the PDF renders `httpx library`. The PDF additionally embeds
links on the Minikube and Kubernetes documentation text. These differences do not
change scope.

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
- **M-05 — Packaging:** Maintain the work as a Git repository and ultimately make
  it suitable for publication to GitHub. Creating the remote repository is outside
  this phase and requires explicit authorization.
- **M-06 — Container:** Ultimately provide a Dockerfile or Containerfile for an
  image that executes the client. This is deferred from the current bootstrap.
- **M-07 — Kubernetes:** Ultimately provide basic Kubernetes manifests under
  `manifests/` showing how the executable client is run. This is deferred from the
  current bootstrap.
- **M-08 — Quality:** Include reasonable code-quality measures. For this repository
  that includes formatting, linting, static typing, unit tests, and coverage.

`httpx` is a recommendation in the original challenge, not an upstream protocol
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
- **R-04 — Initial consistency:** For create, all-absent may proceed and all-present
  is an idempotent no-op. For delete, all-present may proceed and all-absent is an
  idempotent no-op. A mixed present/absent initial state is a conflict and causes no
  mutation.
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
  because the group exists. Delete only when the node was initially absent and the
  current operation's create was confirmed or safely inferred. Likewise, recreate
  during delete rollback only when the node was initially present and this
  operation's delete was confirmed or safely inferred. Never compensate unknown,
  unattempted, or pre-existing state.
- **R-09 — Reverse rollback:** After a forward failure, stop new mutations and
  compensate attributable changes in reverse mutation order.
- **R-10 — Failure preservation:** Preserve the original forward failure as the
  primary cause. Report compensation failures separately without masking it.
- **R-11 — Idempotent calls:** Repeated create after global success returns a
  successful no-op; repeated delete after global success returns a successful
  no-op. A repeat following incomplete rollback is governed by fresh preflight and
  may surface a mixed-state conflict rather than guessing ownership.
- **R-12 — Bounds:** Every HTTP request has explicit connect/read/write/pool
  timeouts. Read retries and reconciliation probes have finite attempt and elapsed
  budgets with exponential backoff and jitter.
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

## Mandatory final repository requirements

These are required before final submission but intentionally not implemented in
this specification/bootstrap phase:

- **D-01:** Production client implementation and complete unit test suite.
- **D-02:** A reproducible `uv.lock` generated from all dependencies declared in
  `pyproject.toml`, without syncing the shared `faq` environment.
- **D-03:** Dockerfile/Containerfile with useful executable behavior.
- **D-04:** Basic Kubernetes manifests appropriate to that executable.
- **D-05:** GitHub Actions CI running format check, lint, mypy, unit tests, and
  coverage from a clean dependency installation.
- **D-06:** Final README with runnable installation and usage instructions.

## Working assumptions

- Node entries may omit a scheme; normalization defaults them to `https`. Only
  `http` and `https` are accepted. User information, query strings, fragments, and
  non-root base paths are rejected; trailing slashes and default ports are
  canonicalized before duplicate detection.
- `group_id` is a non-empty string with no leading/trailing whitespace or control
  characters. It is URL-encoded as one path segment and sent unchanged in JSON.
  The upstream specification defines no tighter character or length constraint.
- A valid GET `200` response is a JSON object whose `groupId` exactly equals the
  requested value. The API documents no response body for POST or DELETE, so only
  their status codes are required on expected success.
- Group state consists only of `groupId`, as shown by the challenge. This makes a
  delete compensation capable of recreating the documented state. Hidden server
  metadata, if any, cannot be restored by this API.
- No independent actor mutates the same `group_id` during one client operation.
  The client can serialize same-process calls, but the API supplies no mechanism
  to enforce this assumption across processes.
- A minimal one-shot CLI is expected later so a container can execute the library;
  Kubernetes should model it as a `Job`, not invent a long-running API service.

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
- The required Python versions, package/import name, coverage threshold, license,
  and publication visibility are not specified. This bootstrap proposes Python
  3.11+ and import name `mci_cluster_client`.

## Optional enhancements

The following are explicitly non-mandatory and must not delay or complicate the
core submission:

- An async API or bounded parallel mutation mode after sequential semantics are
  proven.
- Persistent Saga recovery across client-process crashes.
- Metrics/tracing exporters beyond safe structured logging.
- A repair/reconciliation command for pre-existing mixed cluster state.
- Server-supported idempotency keys or conditional requests if the remote contract
  is extended.

An API server, web UI, database, Kubernetes operator, and service mesh are out of
scope, not enhancements for this assignment.

