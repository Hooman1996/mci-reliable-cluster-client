from __future__ import annotations

from mci_cluster_client import (
    Action,
    NodeState,
    OverallStatus,
    RequestOutcome,
    TransitionOwnership,
)

from .helpers import client_for, get_absent, get_present, mutate

N1 = "https://node1.example.com"
N2 = "https://node2.example.com"
N3 = "https://node3.example.com"


def test_all_absent_create_succeeds_on_every_node_with_exact_contract() -> None:
    client, transport = client_for(
        [N1, N2, N3],
        [
            get_absent(N1),
            get_absent(N2),
            get_absent(N3),
            mutate(N1, "POST", 201),
            mutate(N2, "POST", 201),
            mutate(N3, "POST", 201),
        ],
    )

    report = client.create_group("g")

    assert report.action is Action.CREATE
    assert report.status is OverallStatus.SUCCEEDED
    assert report.succeeded
    assert [node.final_state for node in report.nodes] == [NodeState.PRESENT] * 3
    assert all(node.mutation_required for node in report.nodes)
    assert all(node.request_outcome is RequestOutcome.CONFIRMED_SUCCESS for node in report.nodes)
    assert all(node.transition_ownership is TransitionOwnership.CONFIRMED for node in report.nodes)
    transport.assert_done()


def test_all_present_create_is_idempotent_noop() -> None:
    client, transport = client_for(
        [N1, N2],
        [get_present(N1), get_present(N2)],
    )

    report = client.create_group("g")

    assert report.status is OverallStatus.NOOP
    assert [node.mutation_required for node in report.nodes] == [False, False]
    assert all(node.request_outcome is RequestOutcome.NOT_REQUIRED for node in report.nodes)
    transport.assert_done()


def test_mixed_state_create_converges_without_touching_present_nodes() -> None:
    client, transport = client_for(
        [N1, N2, N3],
        [
            get_present(N1),
            get_absent(N2),
            get_present(N3),
            mutate(N2, "POST", 201),
        ],
    )

    report = client.create_group("g")

    assert report.status is OverallStatus.SUCCEEDED
    assert [node.initial_state for node in report.nodes] == [
        NodeState.PRESENT,
        NodeState.ABSENT,
        NodeState.PRESENT,
    ]
    assert [node.mutation_required for node in report.nodes] == [False, True, False]
    assert [request.method for request in transport.calls] == ["GET", "GET", "GET", "POST"]
    transport.assert_done()


def test_all_present_delete_succeeds_on_every_node_with_exact_contract() -> None:
    client, transport = client_for(
        [N1, N2],
        [
            get_present(N1),
            get_present(N2),
            mutate(N1, "DELETE", 200),
            mutate(N2, "DELETE", 200),
        ],
    )

    report = client.delete_group("g")

    assert report.action is Action.DELETE
    assert report.status is OverallStatus.SUCCEEDED
    assert [node.final_state for node in report.nodes] == [NodeState.ABSENT, NodeState.ABSENT]
    transport.assert_done()


def test_all_absent_delete_is_idempotent_noop() -> None:
    client, transport = client_for([N1, N2], [get_absent(N1), get_absent(N2)])

    report = client.delete_group("g")

    assert report.status is OverallStatus.NOOP
    assert [node.mutation_required for node in report.nodes] == [False, False]
    transport.assert_done()


def test_mixed_state_delete_converges_without_touching_absent_nodes() -> None:
    client, transport = client_for(
        [N1, N2, N3],
        [
            get_absent(N1),
            get_present(N2),
            get_absent(N3),
            mutate(N2, "DELETE", 200),
        ],
    )

    report = client.delete_group("g")

    assert report.status is OverallStatus.SUCCEEDED
    assert [node.mutation_required for node in report.nodes] == [False, True, False]
    assert [request.method for request in transport.calls] == ["GET", "GET", "GET", "DELETE"]
    transport.assert_done()
