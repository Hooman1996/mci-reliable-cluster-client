from __future__ import annotations

import logging

import httpx
import pytest

from mci_cluster_client import (
    ClusterClient,
    ClusterOperationError,
    CompensationOutcome,
    ErrorPhase,
    NodeState,
    OverallStatus,
    ReconciliationOutcome,
    RequestOutcome,
    RetryPolicy,
    TransitionOwnership,
)

from .helpers import Step, client_for, get_absent, get_present, mutate

N1 = "https://node1.example.com"
N2 = "https://node2.example.com"
N3 = "https://node3.example.com"


def test_failure_on_first_node_stops_forward_mutation() -> None:
    client, transport = client_for(
        [N1, N2],
        [
            get_absent(N1),
            get_absent(N2),
            mutate(N1, "POST", 400),
            get_absent(N1),
        ],
    )

    with pytest.raises(ClusterOperationError) as raised:
        client.create_group("g")

    report = raised.value.report
    assert report.status is OverallStatus.FAILED
    assert report.primary_error is raised.value.primary_error
    assert report.primary_error.status_code == 400
    assert report.nodes[0].final_state is NodeState.ABSENT
    assert report.nodes[1].request_outcome is RequestOutcome.NOT_ATTEMPTED
    assert report.compensation_errors == ()
    transport.assert_done()


def test_middle_node_failure_rolls_create_back_in_reverse_order() -> None:
    client, transport = client_for(
        [N1, N2, N3],
        [
            get_absent(N1),
            get_absent(N2),
            get_absent(N3),
            mutate(N1, "POST", 201),
            mutate(N2, "POST", 201),
            mutate(N3, "POST", 500),
            get_absent(N3),
            mutate(N2, "DELETE", 200),
            mutate(N1, "DELETE", 200),
        ],
    )

    with pytest.raises(ClusterOperationError) as raised:
        client.create_group("g")

    report = raised.value.report
    assert report.status is OverallStatus.ROLLED_BACK
    assert [request.url.host for request in transport.calls[-2:]] == [
        "node2.example.com",
        "node1.example.com",
    ]
    assert report.nodes[2].transition_ownership is TransitionOwnership.NONE
    assert all(node.final_state is NodeState.ABSENT for node in report.nodes)
    transport.assert_done()


def test_mixed_create_failure_never_compensates_unchanged_present_node() -> None:
    client, transport = client_for(
        [N1, N2, N3],
        [
            get_present(N1),
            get_absent(N2),
            get_absent(N3),
            mutate(N2, "POST", 201),
            mutate(N3, "POST", 400),
            get_absent(N3),
            mutate(N2, "DELETE", 200),
        ],
    )

    with pytest.raises(ClusterOperationError) as raised:
        client.create_group("g")

    report = raised.value.report
    assert report.status is OverallStatus.ROLLED_BACK
    assert report.nodes[0].mutation_required is False
    assert report.nodes[0].compensation_outcome is CompensationOutcome.NOT_REQUIRED
    assert [request.url.host for request in transport.calls if request.method == "DELETE"] == [
        "node2.example.com"
    ]
    transport.assert_done()


def test_delete_failure_recreates_only_changed_node() -> None:
    client, transport = client_for(
        [N1, N2],
        [
            get_present(N1),
            get_present(N2),
            mutate(N1, "DELETE", 200),
            mutate(N2, "DELETE", 500),
            get_present(N2),
            mutate(N1, "POST", 201),
        ],
    )

    with pytest.raises(ClusterOperationError) as raised:
        client.delete_group("g")

    assert raised.value.report.status is OverallStatus.ROLLED_BACK
    assert raised.value.report.nodes[0].compensation_outcome is (
        CompensationOutcome.CONFIRMED_SUCCESS
    )
    assert raised.value.report.nodes[1].compensation_outcome is CompensationOutcome.NOT_REQUIRED
    transport.assert_done()


def test_rollback_continues_after_failure_and_preserves_primary_error() -> None:
    client, transport = client_for(
        [N1, N2, N3],
        [
            get_absent(N1),
            get_absent(N2),
            get_absent(N3),
            mutate(N1, "POST", 201),
            mutate(N2, "POST", 201),
            mutate(N3, "POST", 400),
            get_absent(N3),
            mutate(N2, "DELETE", 500),
            get_present(N2),
            mutate(N1, "DELETE", 200),
        ],
    )

    with pytest.raises(ClusterOperationError) as raised:
        client.create_group("g")

    report = raised.value.report
    assert report.status is OverallStatus.ROLLBACK_INCOMPLETE
    assert report.primary_error.status_code == 400
    assert len(report.compensation_errors) == 1
    assert raised.value.compensation_errors == report.compensation_errors
    assert report.compensation_errors[0].code == "state_not_restored"
    assert report.nodes[0].compensation_outcome is CompensationOutcome.CONFIRMED_SUCCESS
    assert report.nodes[1].compensation_outcome is CompensationOutcome.FAILED
    assert report.nodes[1].final_state is NodeState.PRESENT
    transport.assert_done()


def test_ambiguous_compensation_accepts_verified_restored_state() -> None:
    client, transport = client_for(
        [N1, N2],
        [
            get_absent(N1),
            get_absent(N2),
            mutate(N1, "POST", 201),
            mutate(N2, "POST", 500),
            get_absent(N2),
            Step("DELETE", f"{N1}/v1/group/", expected_json={"groupId": "g"}, error="timeout"),
            get_absent(N1),
        ],
    )

    with pytest.raises(ClusterOperationError) as raised:
        client.create_group("g")

    node = raised.value.report.nodes[0]
    assert raised.value.report.status is OverallStatus.ROLLED_BACK
    assert node.compensation_outcome is CompensationOutcome.RECONCILED_SUCCESS
    assert node.compensation_reconciliation_outcome is ReconciliationOutcome.ABSENT
    assert node.compensation_error is not None
    assert raised.value.compensation_errors == ()
    transport.assert_done()


@pytest.mark.parametrize(
    ("mutation_step", "expected_status"),
    [
        (Step("POST", f"{N1}/v1/group/", expected_json={"groupId": "g"}, error="timeout"), None),
        (mutate(N1, "POST", 500), 500),
    ],
    ids=["timeout-after-commit", "500-after-commit"],
)
def test_ambiguous_create_committed_is_inferred_success(
    mutation_step: Step, expected_status: int | None
) -> None:
    client, transport = client_for(
        [N1],
        [get_absent(N1), mutation_step, get_present(N1)],
    )

    report = client.create_group("g")

    node = report.nodes[0]
    assert report.status is OverallStatus.SUCCEEDED
    assert node.request_outcome is RequestOutcome.AMBIGUOUS
    assert node.reconciliation_outcome is ReconciliationOutcome.PRESENT
    assert node.transition_ownership is TransitionOwnership.INFERRED
    assert node.request_error is not None
    assert node.request_error.status_code == expected_status
    transport.assert_done()


@pytest.mark.parametrize(
    ("mutation_step", "expected_status"),
    [
        (
            Step("DELETE", f"{N1}/v1/group/", expected_json={"groupId": "g"}, error="timeout"),
            None,
        ),
        (mutate(N1, "DELETE", 500), 500),
    ],
    ids=["timeout-after-commit", "500-after-commit"],
)
def test_ambiguous_delete_committed_is_inferred_success(
    mutation_step: Step, expected_status: int | None
) -> None:
    client, transport = client_for(
        [N1],
        [get_present(N1), mutation_step, get_absent(N1)],
    )

    report = client.delete_group("g")

    node = report.nodes[0]
    assert report.status is OverallStatus.SUCCEEDED
    assert node.reconciliation_outcome is ReconciliationOutcome.ABSENT
    assert node.transition_ownership is TransitionOwnership.INFERRED
    assert node.request_error is not None
    assert node.request_error.status_code == expected_status
    transport.assert_done()


@pytest.mark.parametrize("action", ["create", "delete"])
def test_ambiguous_mutation_with_no_transition_fails_without_self_compensation(
    action: str,
) -> None:
    if action == "create":
        initial = get_absent(N1)
        mutation = Step("POST", f"{N1}/v1/group/", expected_json={"groupId": "g"}, error="timeout")
        reconciliation = get_absent(N1)
    else:
        initial = get_present(N1)
        mutation = Step(
            "DELETE", f"{N1}/v1/group/", expected_json={"groupId": "g"}, error="timeout"
        )
        reconciliation = get_present(N1)
    client, transport = client_for([N1], [initial, mutation, reconciliation])

    with pytest.raises(ClusterOperationError) as raised:
        getattr(client, f"{action}_group")("g")

    node = raised.value.report.nodes[0]
    assert raised.value.report.status is OverallStatus.FAILED
    assert node.transition_ownership is TransitionOwnership.NONE
    assert node.compensation_outcome is CompensationOutcome.NOT_REQUIRED
    assert raised.value.__cause__ is not None
    transport.assert_done()


def test_permanently_inconclusive_mutation_attempts_safest_compensation() -> None:
    sleeps: list[float] = []
    retry = RetryPolicy(max_attempts=2, base_delay=0.1, max_delay=1)
    client, transport = client_for(
        [N1],
        [
            get_absent(N1),
            Step("POST", f"{N1}/v1/group/", expected_json={"groupId": "g"}, error="timeout"),
            Step("GET", f"{N1}/v1/group/g/", error="timeout"),
            Step("GET", f"{N1}/v1/group/g/", error="timeout"),
            Step(
                "DELETE",
                f"{N1}/v1/group/",
                expected_json={"groupId": "g"},
                error="timeout",
            ),
            Step("GET", f"{N1}/v1/group/g/", error="timeout"),
            Step("GET", f"{N1}/v1/group/g/", error="timeout"),
        ],
        retry=retry,
        sleep=sleeps.append,
    )

    with pytest.raises(ClusterOperationError) as raised:
        client.create_group("g")

    report = raised.value.report
    node = report.nodes[0]
    assert report.status is OverallStatus.INDETERMINATE
    assert report.has_unresolved_final_state
    assert node.transition_ownership is TransitionOwnership.POSSIBLE
    assert node.compensation_outcome is CompensationOutcome.AMBIGUOUS
    assert len(report.compensation_errors) == 1
    assert sleeps == [0.1, 0.1]
    transport.assert_done()


def test_inconclusive_delete_attempts_inverse_create_from_known_preflight() -> None:
    client, transport = client_for(
        [N1],
        [
            get_present(N1),
            Step(
                "DELETE",
                f"{N1}/v1/group/",
                expected_json={"groupId": "g"},
                error="timeout",
            ),
            Step("GET", f"{N1}/v1/group/g/", error="timeout"),
            mutate(N1, "POST", 201),
        ],
    )

    with pytest.raises(ClusterOperationError) as raised:
        client.delete_group("g")

    node = raised.value.report.nodes[0]
    assert raised.value.report.status is OverallStatus.ROLLED_BACK
    assert node.transition_ownership is TransitionOwnership.POSSIBLE
    assert node.compensation_outcome is CompensationOutcome.CONFIRMED_SUCCESS
    assert node.final_state is NodeState.PRESENT
    transport.assert_done()


def test_malformed_mutation_reconciliation_is_retained_before_rollback() -> None:
    client, transport = client_for(
        [N1],
        [
            get_absent(N1),
            mutate(N1, "POST", 500),
            Step("GET", f"{N1}/v1/group/g/", status=200, response_content=b"not-json"),
            mutate(N1, "DELETE", 200),
        ],
    )

    with pytest.raises(ClusterOperationError) as raised:
        client.create_group("g")

    report = raised.value.report
    node = report.nodes[0]
    assert report.status is OverallStatus.ROLLED_BACK
    assert report.primary_error.status_code == 500
    assert node.reconciliation_outcome is ReconciliationOutcome.UNKNOWN
    assert node.reconciliation_error is not None
    assert node.reconciliation_error.code == "malformed_response"
    assert node.compensation_outcome is CompensationOutcome.CONFIRMED_SUCCESS
    assert report.to_dict()["primary_error"]["status_code"] == 500
    transport.assert_done()


@pytest.mark.parametrize(
    "bad_steps",
    [
        [
            Step("GET", f"{N1}/v1/group/g/", status=200, response_content=b"not-json"),
            Step("GET", f"{N1}/v1/group/g/", status=200, response_content=b"still-not-json"),
        ],
        [
            Step("GET", f"{N1}/v1/group/g/", status=200, response_json={"groupId": "wrong"}),
            Step("GET", f"{N1}/v1/group/g/", status=200, response_json=[]),
        ],
    ],
    ids=["malformed-json", "wrong-group-id-or-schema"],
)
def test_malformed_preflight_exhausts_to_unknown(bad_steps: list[Step]) -> None:
    client, transport = client_for(
        [N1], bad_steps, retry=RetryPolicy(max_attempts=2, base_delay=0, max_delay=0)
    )

    with pytest.raises(ClusterOperationError) as raised:
        client.create_group("g")

    report = raised.value.report
    assert report.status is OverallStatus.INDETERMINATE
    assert report.nodes[0].initial_state is NodeState.UNKNOWN
    assert report.nodes[0].preflight_attempts == 2
    assert report.primary_error.phase is ErrorPhase.PREFLIGHT
    assert transport.calls[-1].method == "GET"
    transport.assert_done()


def test_get_retries_timeout_and_5xx_then_succeeds_with_bounded_backoff() -> None:
    sleeps: list[float] = []
    random_values: list[float] = []

    def random_value() -> float:
        random_values.append(1.0)
        return 1.0

    client, transport = client_for(
        [N1],
        [
            Step("GET", f"{N1}/v1/group/g/", error="timeout"),
            Step("GET", f"{N1}/v1/group/g/", status=503),
            get_present(N1),
        ],
        retry=RetryPolicy(max_attempts=3, base_delay=0.5, max_delay=0.75, jitter_ratio=0.5),
        sleep=sleeps.append,
        random=random_value,
    )

    report = client.create_group("g")

    assert report.status is OverallStatus.NOOP
    assert report.nodes[0].preflight_attempts == 3
    assert report.nodes[0].preflight_error is not None
    assert sleeps == [0.75, 0.75]
    assert random_values == [1.0, 1.0]
    transport.assert_done()


def test_preflight_retry_exhaustion_still_checks_all_nodes_before_failing() -> None:
    retry = RetryPolicy(max_attempts=2, base_delay=0, max_delay=0)
    client, transport = client_for(
        [N1, N2],
        [
            Step("GET", f"{N1}/v1/group/g/", status=500),
            Step("GET", f"{N1}/v1/group/g/", status=500),
            get_absent(N2),
        ],
        retry=retry,
    )

    with pytest.raises(ClusterOperationError) as raised:
        client.delete_group("g")

    assert raised.value.report.nodes[0].initial_state is NodeState.UNKNOWN
    assert raised.value.report.nodes[1].initial_state is NodeState.ABSENT
    assert all(request.method == "GET" for request in transport.calls)
    transport.assert_done()


def test_final_http_response_does_not_chain_an_earlier_timeout() -> None:
    retry = RetryPolicy(max_attempts=2, base_delay=0, max_delay=0)
    client, transport = client_for(
        [N1],
        [
            Step("GET", f"{N1}/v1/group/g/", error="timeout"),
            Step("GET", f"{N1}/v1/group/g/", status=500),
        ],
        retry=retry,
    )

    with pytest.raises(ClusterOperationError) as raised:
        client.create_group("g")

    assert raised.value.primary_error.status_code == 500
    assert raised.value.__cause__ is None
    transport.assert_done()


def test_redirect_is_not_followed_and_is_reconciled() -> None:
    client, transport = client_for(
        [N1],
        [get_absent(N1), mutate(N1, "POST", 307), get_absent(N1)],
    )

    with pytest.raises(ClusterOperationError) as raised:
        client.create_group("g")

    assert raised.value.primary_error.status_code == 307
    assert len(transport.calls) == 3


def test_logs_never_include_headers_or_response_bodies(
    caplog: pytest.LogCaptureFixture,
) -> None:
    secret = "token-value-never-log"
    body_secret = "response-secret-never-log"

    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "GET":
            return httpx.Response(404, request=request)
        return httpx.Response(500, request=request, content=body_secret.encode())

    external = httpx.Client(
        transport=httpx.MockTransport(handler), headers={"Authorization": f"Bearer {secret}"}
    )
    client = ClusterClient(
        [N1],
        http_client=external,
        retry=RetryPolicy(max_attempts=1),
        operation_id_factory=lambda: "safe-operation-id",
    )
    try:
        with caplog.at_level(logging.WARNING), pytest.raises(ClusterOperationError):
            client.create_group("g")
    finally:
        client.close()
        external.close()

    rendered = caplog.text
    assert secret not in rendered
    assert body_secret not in rendered
    assert "safe-operation-id" in caplog.records[0].operation_id
    assert caplog.records[0].action == "create"
    assert caplog.records[0].node == N1
