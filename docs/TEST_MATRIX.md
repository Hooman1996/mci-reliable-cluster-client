# Test Matrix

## Test strategy

All unit tests will use an in-memory scripted transport. Scripts assert request
method, normalized URL, encoded path, JSON body, order, timeout configuration, and
the exact response/exception returned. Injected sleep and jitter make retry tests
instant and deterministic. No test may require DNS, sockets, a live cluster,
Docker, GitHub, or Kubernetes.

The IDs below are planned test names or parametrized test families. Delivery checks
are repository-level tests or CI checks rather than live integration tests.

| Requirement / risk | Planned tests | Expected evidence |
| --- | --- | --- |
| M-01 client creates on every node | `test_create_all_absent_succeeds_on_all_nodes` | GET all, POST each, complete per-node success report |
| M-01 client deletes on every node | `test_delete_all_present_succeeds_on_all_nodes` | GET all, DELETE each, complete per-node success report |
| M-02 unstable API and rollback | `test_create_partial_failure_rolls_back_reverse_order`, `test_delete_partial_failure_rolls_back_reverse_order` | failure stops forward work and restores attributable prefix |
| M-03 / D-06 README assumptions and usage | `test_readme_contains_scope_usage_and_limitations` | lightweight documentation smoke check or audit evidence |
| M-04 / R-16 no live services | `test_suite_blocks_unexpected_transport_calls` | every request must match a script; no socket fallback |
| M-05 Git repository | submission audit | repository metadata exists; remote publication remains authorization-dependent |
| M-06 / D-03 executable image | later `test_container_metadata_and_cli_contract` plus static Docker audit | declared entrypoint invokes the client CLI without secrets/root |
| M-07 / D-04 Kubernetes | later manifest schema/static assertions | Job uses configuration/Secret references and safe pod settings |
| M-08 / D-05 quality gates | CI workflow plus local command audit | format, lint, mypy, pytest, and coverage commands run |
| R-01 Saga, no ACID claim | `test_failure_exposes_rolled_back_saga_report` plus docs audit | report distinguishes forward and compensation phases |
| R-02 invalid group IDs | `test_invalid_group_id_fails_before_io` parametrized with non-string, empty, blank, padded, and control input | structured validation error and zero transport calls |
| R-02 invalid nodes | `test_invalid_node_url_fails_before_io` | rejects empty list, bad scheme/host, userinfo, path, query, fragment |
| R-02 normalization | `test_node_urls_are_normalized_before_io`, `test_duplicate_canonical_nodes_are_rejected` | HTTPS default, canonical endpoint, stable order, zero mutation on duplicate |
| R-03 unknown preflight | `test_preflight_exhaustion_prevents_all_mutations` | unknown recorded for failed node; no POST/DELETE anywhere |
| R-03 malformed preflight | `test_preflight_malformed_json_is_unknown`, `test_preflight_wrong_group_id_is_unknown` | malformed/mismatched 200 never counts as present |
| R-04 create initial state | `test_create_all_present_is_noop`, `test_create_mixed_initial_state_is_conflict` | repeated create safe; conflict performs no POST/DELETE |
| R-04 delete initial state | `test_delete_all_absent_is_noop`, `test_delete_mixed_initial_state_is_conflict` | repeated delete safe; conflict performs no POST/DELETE |
| R-05 complete node journal | `test_report_contains_every_phase_for_every_node` | initial, mutation, reconciliation, ownership, compensation, final state and attempts serialized |
| R-06 create timeout after commit | `test_create_timeout_after_commit_reconciles_present` | no second POST; GET proves present; inferred ownership recorded |
| R-06 delete timeout after commit | `test_delete_timeout_after_commit_reconciles_absent` | no second DELETE; GET proves absent; inferred ownership recorded |
| R-06 5xx after commit | `test_mutation_5xx_is_reconciled_not_retried` parametrized by action | original 5xx retained and desired state found by GET |
| R-06 definitive/unexpected response | `test_unexpected_mutation_status_reconciles_and_stops_when_original_state_remains` | no blind retry, original evidence is primary failure |
| R-07 reconciliation transient failures | `test_reconciliation_retries_reads_until_conclusive` | bounded GET sequence and deterministic backoff |
| R-07 reconciliation malformed responses | `test_reconciliation_malformed_responses_exhaust_to_unknown` | malformed JSON/schema/wrong ID recorded, outcome indeterminate |
| R-07 reconciliation exhaustion | `test_reconciliation_timeout_exhaustion_is_indeterminate` | exact attempt/budget bound and unknown final state |
| R-08 no deletion of pre-existing create state | `test_create_noop_never_deletes_preexisting_group`, `test_create_rollback_skips_unowned_ambiguous_node` | existence alone does not trigger DELETE |
| R-08 delete compensation ownership | `test_delete_rollback_recreates_only_attributable_deletions` | no POST for unattempted/unproven node |
| R-09 reverse rollback | `test_create_partial_failure_rolls_back_reverse_order`, `test_delete_partial_failure_rolls_back_reverse_order` | transport call log proves LIFO compensation |
| R-10 rollback failure | `test_rollback_failure_does_not_mask_primary_failure` | primary forward cause preserved; separate ordered rollback failures |
| R-11 create idempotency | `test_repeated_create_after_success_is_noop` | second call only preflights and succeeds without mutation |
| R-11 delete idempotency | `test_repeated_delete_after_success_is_noop` | second call only preflights and succeeds without mutation |
| R-11 repeat after incomplete rollback | `test_repeat_after_incomplete_rollback_reports_mixed_conflict` | fresh state, no guessed historical ownership, no mutation |
| R-12 timeout propagation | `test_every_request_receives_bounded_timeout` | transport sees explicit connect/read/write/pool limits |
| R-12 backoff/jitter bounds | `test_read_retry_backoff_is_exponential_capped_and_jittered`, `test_retry_after_is_capped` | injected functions receive deterministic bounded delays |
| R-13 retry selection | `test_reads_retry_but_mutations_do_not` | GET count reaches policy; POST/DELETE count remains one per phase |
| R-14 result/exception serialization | `test_operation_report_round_trips_to_primitives`, `test_exception_exposes_report_and_stable_codes` | callers can branch without parsing human messages |
| R-15 secret safety | `test_logs_reports_and_exception_reprs_redact_secrets` | auth headers/cookies/token values absent from captured output |
| R-17 order and not-attempted nodes | `test_multi_node_failure_preserves_order_and_marks_suffix_not_attempted` | every configured node represented in stable order |
| Compensation ambiguity resolves | `test_ambiguous_compensation_reconciles_to_original_state` | compensation recorded as reconciled success without blind retry |
| Compensation malformed/exhausted | `test_compensation_reconciliation_failure_is_reported_separately` | rollback incomplete and primary cause unchanged |
| Local same-group concurrency | `test_conflicting_local_operation_is_serialized_or_rejected` | no overlapping Saga for same group/node set |
| URL path injection | `test_group_id_is_encoded_as_single_path_segment` | reserved characters cannot alter endpoint structure |
| Redirect/security behavior | `test_redirect_is_not_followed`, `test_tls_verification_defaults_on` | mutation not forwarded; secure transport default |
| D-01 package behavior | all unit tests above | implementation satisfies documented public contract |
| D-02 dependency reproducibility | later lock consistency CI check | `uv.lock` agrees with `pyproject.toml`; clean install succeeds |
| Repository cleanliness | audit plus CI ignore check | no `.venv`, caches, credentials, build output, or modified references |

## Coverage interpretation

Coverage must exercise decision branches, not just lines: each state transition,
ambiguous result, ownership gate, rollback path, and exception status needs an
assertion. A numeric threshold will be selected with the implementation and stated
in `pyproject.toml`; the challenge does not prescribe one.

