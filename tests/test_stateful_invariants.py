from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from threading import Event

import httpx
import pytest

from mci_cluster_client import (
    ClusterClient,
    ClusterOperationError,
    NodeState,
    OverallStatus,
    RetryPolicy,
    TransitionOwnership,
)

from .helpers import FaultEffect, StatefulFault, StatefulTransport

N1 = "https://node1.example.com"
N2 = "https://node2.example.com"
N3 = "https://node3.example.com"
NODES = (N1, N2, N3)


def stateful_client(
    initial: tuple[bool, bool, bool],
    faults: tuple[StatefulFault, ...] = (),
    *,
    retry: RetryPolicy | None = None,
) -> tuple[ClusterClient, StatefulTransport]:
    transport = StatefulTransport(
        {node: ({"g"} if present else set()) for node, present in zip(NODES, initial, strict=True)},
        faults,
    )
    client = ClusterClient(
        NODES,
        transport=transport,
        retry=retry or RetryPolicy(max_attempts=1),
        sleep=lambda _: None,
        operation_id_factory=lambda: "stateful-operation",
    )
    return client, transport


def cluster_state(transport: StatefulTransport) -> tuple[bool, bool, bool]:
    return (
        transport.has_group(N1, "g"),
        transport.has_group(N2, "g"),
        transport.has_group(N3, "g"),
    )


@pytest.mark.parametrize(
    ("initial", "method_name", "expected"),
    [
        ((False, False, False), "create_group", (True, True, True)),
        ((True, True, True), "delete_group", (False, False, False)),
    ],
)
def test_successful_operation_reaches_requested_state(
    initial: tuple[bool, bool, bool],
    method_name: str,
    expected: tuple[bool, bool, bool],
) -> None:
    client, transport = stateful_client(initial)

    report = getattr(client, method_name)("g")

    assert report.status is OverallStatus.SUCCEEDED
    assert cluster_state(transport) == expected
    assert all(node.final_state is not NodeState.UNKNOWN for node in report.nodes)


@pytest.mark.parametrize(
    ("initial", "method_name", "forward_method", "failure_node"),
    [
        ((False, False, False), "create_group", "POST", N3),
        ((True, True, True), "delete_group", "DELETE", N3),
        ((True, False, False), "create_group", "POST", N3),
        ((False, True, True), "delete_group", "DELETE", N3),
    ],
)
def test_resolved_failure_restores_exact_preflight_state(
    initial: tuple[bool, bool, bool],
    method_name: str,
    forward_method: str,
    failure_node: str,
) -> None:
    client, transport = stateful_client(
        initial,
        (StatefulFault(forward_method, failure_node, 1, "status_before", 500),),
    )

    with pytest.raises(ClusterOperationError) as raised:
        getattr(client, method_name)("g")

    assert raised.value.report.status is OverallStatus.ROLLED_BACK
    assert cluster_state(transport) == initial
    assert tuple(node.final_state is NodeState.PRESENT for node in raised.value.report.nodes) == (
        initial
    )


@pytest.mark.parametrize(
    ("initial", "method_name", "forward_method", "expected"),
    [
        ((False, False, False), "create_group", "POST", (True, True, True)),
        ((True, True, True), "delete_group", "DELETE", (False, False, False)),
    ],
)
@pytest.mark.parametrize(
    ("effect", "fault_status"),
    [
        ("timeout_after", 500),
        ("decoding_after", 500),
        ("status_after", 202),
        ("status_after", 307),
        ("status_after", 409),
        ("status_after", 500),
    ],
)
def test_commit_before_ambiguous_evidence_is_reconciled(
    initial: tuple[bool, bool, bool],
    method_name: str,
    forward_method: str,
    expected: tuple[bool, bool, bool],
    effect: FaultEffect,
    fault_status: int,
) -> None:
    client, transport = stateful_client(
        initial,
        (
            StatefulFault(
                forward_method,
                N2,
                1,
                effect,
                fault_status,
            ),
        ),
    )

    report = getattr(client, method_name)("g")

    assert report.status is OverallStatus.SUCCEEDED
    assert cluster_state(transport) == expected
    assert report.nodes[1].transition_ownership is TransitionOwnership.INFERRED
    assert sum(request.method == forward_method for request in transport.calls) == 3


def test_preflight_retries_response_decoding_errors() -> None:
    client, transport = stateful_client(
        (True, True, True),
        (StatefulFault("GET", N1, 1, "decoding_after"),),
        retry=RetryPolicy(max_attempts=2, base_delay=0, max_delay=0),
    )

    report = client.create_group("g")

    assert report.status is OverallStatus.NOOP
    assert report.nodes[0].preflight_attempts == 2
    assert report.nodes[0].preflight_error is not None
    assert report.nodes[0].preflight_error.exception_type == "DecodingError"
    assert sum(request.method == "GET" for request in transport.calls) == 4


def test_rollback_failure_is_reported_as_indeterminate_and_non_atomic() -> None:
    client, transport = stateful_client(
        (False, False, False),
        (
            StatefulFault("POST", N2, 1, "status_before", 500),
            StatefulFault("DELETE", N1, 1, "timeout_before"),
            StatefulFault("GET", N1, 2, "timeout_before"),
        ),
    )

    with pytest.raises(ClusterOperationError) as raised:
        client.create_group("g")

    report = raised.value.report
    assert report.status is OverallStatus.INDETERMINATE
    assert not report.succeeded
    assert report.has_unresolved_final_state
    assert cluster_state(transport) == (True, False, False)
    assert report.primary_error.status_code == 500
    assert len(report.compensation_errors) == 1


class BlockingTransport(httpx.BaseTransport):
    def __init__(self) -> None:
        self.entered = Event()
        self.release = Event()

    def handle_request(self, request: httpx.Request) -> httpx.Response:
        self.entered.set()
        assert self.release.wait(timeout=2)
        return httpx.Response(200, request=request, json={"groupId": "g"})


def test_close_waits_for_active_operation_and_then_closes_cleanly() -> None:
    transport = BlockingTransport()
    client = ClusterClient([N1], transport=transport, retry=RetryPolicy(max_attempts=1))

    with ThreadPoolExecutor(max_workers=2) as executor:
        operation = executor.submit(client.create_group, "g")
        assert transport.entered.wait(timeout=2)
        closing = executor.submit(client.close)
        assert not closing.done()
        transport.release.set()
        assert operation.result(timeout=2).status is OverallStatus.NOOP
        closing.result(timeout=2)

    assert client.is_closed


def test_operation_lock_is_released_after_failure() -> None:
    faults = (StatefulFault("POST", N1, 1, "status_before", 500),)
    client, transport = stateful_client((False, False, False), faults)

    with pytest.raises(ClusterOperationError):
        client.create_group("g")
    second = client.delete_group("g")

    assert second.status is OverallStatus.NOOP
    assert cluster_state(transport) == (False, False, False)


def test_repeat_after_incomplete_rollback_uses_fresh_state() -> None:
    client, transport = stateful_client(
        (False, False, False),
        (
            StatefulFault("POST", N2, 1, "status_before", 500),
            StatefulFault("DELETE", N1, 1, "status_before", 500),
        ),
    )

    with pytest.raises(ClusterOperationError) as raised:
        client.create_group("g")
    repeated = client.create_group("g")

    assert raised.value.report.status is OverallStatus.ROLLBACK_INCOMPLETE
    assert repeated.status is OverallStatus.SUCCEEDED
    assert cluster_state(transport) == (True, True, True)
    posts_to_n1 = [
        request
        for request in transport.calls
        if request.method == "POST" and request.url.host == "node1.example.com"
    ]
    assert len(posts_to_n1) == 1
