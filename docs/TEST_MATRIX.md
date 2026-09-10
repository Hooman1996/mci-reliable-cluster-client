# Test Matrix

## Test strategy

All unit tests use in-memory transports. Exact-contract tests use a scripted
transport that asserts request method, normalized URL, encoded path, JSON body,
order, and the exact response/exception returned. Invariant tests use a small
stateful transport that independently tracks group presence and can fail before or
after mutation commit. Injected sleep and jitter make retry tests instant and
deterministic. No test requires DNS, sockets, a live cluster, Docker, GitHub, or
Kubernetes.

The IDs below are implemented test names, parametrized test families, or repository
verification checks.

| Requirement / risk | Planned tests | Expected evidence |
| --- | --- | --- |
| M-01 client creates on every node | `test_all_absent_create_succeeds_on_every_node_with_exact_contract` | GET all, POST each, complete per-node success report |
| M-01 client deletes on every node | `test_all_present_delete_succeeds_on_every_node_with_exact_contract` | GET all, DELETE each, complete per-node success report |
| M-02 unstable API and rollback | `test_middle_node_failure_rolls_create_back_in_reverse_order`, `test_delete_failure_recreates_only_changed_node` | failure stops forward work and restores attributable changes |
| M-03 / D-06 README assumptions and usage | documentation review | README documents runnable library/CLI usage, assumptions, and limitations |
| M-04 / R-16 no live services | autouse `block_real_network`, `ScriptedTransport.assert_done` | sockets fail immediately and every expected request is scripted |
| M-05 Git repository | repository hygiene review | repository metadata and standalone tracked content are verified before publication |
| M-06 / D-03 executable image | `test_container.py` static audit; hosted CI build and offline smoke checks | declared entrypoint invokes the client CLI without secrets or root privileges |
| M-07 / D-04 Kubernetes | offline `kubectl kustomize` structural audit; client dry-run where kubectl does not require API discovery | exactly one ConfigMap and one Job; secure non-root Pod; ConfigMap node reference; no automatic Job retry or workload execution during validation |
| M-08 / D-05 quality gates | CI workflow plus local command audit | format, lint, mypy, pytest, and coverage commands run |
| R-01 Saga, no ACID claim | rollback tests plus documentation audit | report distinguishes forward and compensation phases; docs disclaim ACID |
| R-02 invalid group IDs | `test_invalid_group_ids_fail_without_network`, `test_valid_group_id_is_not_silently_stripped` | only non-string/empty/whitespace-only values fail; valid value remains exact |
| R-02 invalid nodes | `test_invalid_node_collections_are_rejected` | rejects empty list, bad scheme/host, credentials, path, query, and fragment |
| R-02 normalization | `test_bare_hosts_and_explicit_urls_are_canonicalized`, `test_duplicate_nodes_after_canonicalization_are_rejected` | HTTPS default, canonical origin, stable order, no duplicate mutation |
| R-03 unknown preflight | `test_preflight_retry_exhaustion_still_checks_all_nodes_before_failing` | all nodes preflight; no POST/DELETE when any state is unknown |
| R-03 malformed preflight | `test_malformed_preflight_exhausts_to_unknown` | malformed/mismatched 200 never counts as present |
| R-04 create initial state | `test_all_present_create_is_idempotent_noop`, `test_mixed_state_create_converges_without_touching_present_nodes` | repeated create safe; only absent nodes receive POST |
| R-04 delete initial state | `test_all_absent_delete_is_idempotent_noop`, `test_mixed_state_delete_converges_without_touching_absent_nodes` | repeated delete safe; only present nodes receive DELETE |
| R-05 complete node journal | `test_report_is_immutable_and_json_serializable` plus reliability report assertions | all phases, attempts, errors, ownership, and final state are typed and serializable |
| R-06 create timeout/5xx after commit | `test_ambiguous_create_committed_is_inferred_success`, stateful `test_commit_before_ambiguous_evidence_is_reconciled` | no second POST; independent state proves present; inferred ownership recorded |
| R-06 delete timeout/5xx after commit | `test_ambiguous_delete_committed_is_inferred_success`, stateful `test_commit_before_ambiguous_evidence_is_reconciled` | no second DELETE; independent state proves absent; inferred ownership recorded |
| R-06 ambiguous response with no transition | parametrized `test_ambiguous_mutation_with_no_transition_fails_without_self_compensation` | original state is retained and primary error is chained |
| R-07 transient reconciliation | `test_get_retries_timeout_and_5xx_then_succeeds_with_bounded_backoff` | bounded GET sequence and deterministic injected delay/jitter |
| R-07 malformed reconciliation | `test_malformed_mutation_reconciliation_is_retained_before_rollback` | malformed JSON recorded as unknown before safest compensation |
| R-07 reconciliation exhaustion | `test_permanently_inconclusive_mutation_attempts_safest_compensation` | exact retry bounds, possible ownership, unresolved final state |
| R-08 no deletion of pre-existing create state | `test_mixed_create_failure_never_compensates_unchanged_present_node`, `test_all_present_create_is_idempotent_noop` | unchanged existence never triggers DELETE |
| R-08 delete compensation ownership | `test_delete_failure_recreates_only_changed_node` | no POST for unchanged/unattempted nodes |
| R-09 reverse rollback | `test_middle_node_failure_rolls_create_back_in_reverse_order` | transport call log proves LIFO compensation |
| R-10 rollback failure | `test_rollback_continues_after_failure_and_preserves_primary_error` | rollback continues; primary failure and compensation errors remain separate |
| R-11 create idempotency | `test_all_present_create_is_idempotent_noop` | preflight-only successful no-op |
| R-11 delete idempotency | `test_all_absent_delete_is_idempotent_noop` | preflight-only successful no-op |
| R-11 repeat after incomplete rollback | `test_repeat_after_incomplete_rollback_uses_fresh_state` | no historical ownership; only currently divergent nodes mutate |
| R-12 timeout propagation | `test_timeout_values_are_attached_to_every_request` | transport sees explicit connect/read/write/pool limits |
| R-12 backoff/jitter bounds | `test_get_retries_timeout_and_5xx_then_succeeds_with_bounded_backoff`, `test_retry_delay_rejects_invalid_arguments_and_bounds_large_exponents` | injected functions receive deterministic bounded delays |
| R-13 retry selection | all ambiguous-mutation tests | GET reaches retry policy while each POST/DELETE phase has one request |
| R-14 result/exception serialization | `test_report_is_immutable_and_json_serializable`, `test_operation_error_requires_a_primary_error` | callers branch on types/enums without parsing messages |
| R-15 secret safety | `test_logs_never_include_headers_or_response_bodies` | authorization and response secrets absent from logs |
| R-17 order and not-attempted nodes | `test_failure_on_first_node_stops_forward_mutation`, reverse rollback test | every node remains represented in stable order; suffix is not attempted |
| Compensation ambiguity resolves | `test_ambiguous_compensation_accepts_verified_restored_state` | verified initial state counts as reconciled success |
| Compensation failure and continuation | `test_rollback_continues_after_failure_and_preserves_primary_error` | rollback incomplete and primary cause unchanged |
| Unresolved create/delete inverse | `test_permanently_inconclusive_mutation_attempts_safest_compensation`, `test_inconclusive_delete_attempts_inverse_create_from_known_preflight` | one inverse request follows known preflight state and dispatched mutation |
| Stateful Saga invariants | `test_successful_operation_reaches_requested_state`, `test_resolved_failure_restores_exact_preflight_state`, `test_rollback_failure_is_reported_as_indeterminate_and_non_atomic` | actual modeled state agrees with successful or restored reports; incomplete rollback never claims success |
| Local concurrency | `test_close_waits_for_active_operation_and_then_closes_cleanly`, `test_operation_lock_is_released_after_failure` | close cannot overtake an active operation; failure paths release the instance lock |
| URL path injection | `test_group_id_is_percent_encoded_as_one_get_path_segment` | reserved characters cannot alter endpoint structure |
| Redirect/security behavior | `test_redirect_is_not_followed_and_is_reconciled` plus constructor inspection | mutation not forwarded; internally created httpx client retains TLS verification default |
| D-01 package behavior | all unit tests above | implementation satisfies documented public contract |
| CLI dispatch and node sources | `test_create_dispatches_with_repeated_nodes_and_compact_json`, `test_delete_dispatches_and_pretty_prints`, environment fallback/override tests | action and stable node order reach the client; CLI nodes replace, rather than merge with, environment nodes |
| CLI numeric configuration | `test_every_numeric_option_reaches_existing_configuration_models`, `test_invalid_numeric_configuration_fails_before_client_execution` | every timeout/retry option uses the public models and invalid values prevent execution |
| CLI output and status mapping | compact/pretty, success/no-op, rolled-back, indeterminate, and rollback-failure tests in `test_cli.py` | exactly one JSON report uses stable enum strings on stdout; diagnostics use stderr; exit is `0`, `3`, or `4` from actual report state |
| CLI expected local failures | missing/malformed nodes, validation, argparse, and KeyboardInterrupt tests in `test_cli.py` | no report or traceback; stable exit `2` or `130`; no client/network call when configuration is invalid |
| CLI execution methods/lifecycle | help/version tests, `test_execute_uses_context_manager_and_dispatches`, entrypoint tests, module smoke commands | installed and module entrypoints share the parser; client context always exits |
| D-02 dependency reproducibility | unconditional CI `uv lock --check` followed by `uv sync --locked --all-groups`; `test_container.py` locked-build assertions | a missing/stale lock fails CI and the Docker build; only the locked runtime graph is exported and collected for the runtime image |
| Repository cleanliness | audit plus CI ignore check | no `.venv`, caches, credentials, build output, or modified references |

## Coverage interpretation

Coverage must exercise decision branches, not just lines: each state transition,
ambiguous result, ownership gate, rollback path, and exception status needs an
assertion. `pyproject.toml` enforces 95% branch-aware coverage; the current suite
exceeds it.
