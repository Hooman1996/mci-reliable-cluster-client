from __future__ import annotations

import json
from collections.abc import Iterator
from dataclasses import FrozenInstanceError

import httpx
import pytest

from mci_cluster_client import (
    Action,
    ClientClosedError,
    ClusterClient,
    ClusterOperationError,
    NodeState,
    OperationReport,
    OverallStatus,
    RetryPolicy,
    TimeoutConfig,
    ValidationError,
)

from .helpers import ScriptedTransport, Step, client_for, get_present, mutate

N1 = "https://node1.example.com"
N2_HTTP = "http://node2.example.com"


def test_bare_hosts_and_explicit_urls_are_canonicalized() -> None:
    transport = ScriptedTransport([])
    client = ClusterClient(
        [
            "NODE1.EXAMPLE.COM/",
            "http://Node2.Example.Com:80/",
            "https://[2001:db8::1]:443",
            "https://192.0.2.1:443/",
        ],
        transport=transport,
    )

    assert client.nodes == (N1, N2_HTTP, "https://[2001:db8::1]", "https://192.0.2.1")
    client.close()


@pytest.mark.parametrize(
    "nodes",
    [
        None,
        1,
        [],
        "node.example.com",
        [1],
        [""],
        [" node.example.com"],
        ["https://node .example.com"],
        ["ftp://node.example.com"],
        ["https://user:secret@node.example.com"],
        ["https://node.example.com/base"],
        ["https://node.example.com?q=1"],
        ["https://node.example.com?"],
        ["https://node.example.com#fragment"],
        ["https://node.example.com#"],
        ["https://bad_host.example.com"],
        ["https://node.example.com:bad"],
        ["https://0177.0.0.1"],
        ["https://[192.0.2.1]"],
        ["https://[v1.example]"],
        ["https://"],
        ["https://."],
        ["https://\ud800"],
    ],
)
def test_invalid_node_collections_are_rejected(nodes: object) -> None:
    with pytest.raises(ValidationError) as raised:
        ClusterClient(nodes)  # type: ignore[arg-type]

    assert raised.value.field == "nodes"


@pytest.mark.parametrize(
    "nodes",
    [
        ["node.example.com", "https://NODE.example.com/"],
        ["https://node.example.com", "https://NODE.example.com:443/"],
        ["http://node.example.com", "http://node.example.com:80/"],
        ["https://node.example.com.", "node.example.com"],
    ],
)
def test_duplicate_nodes_after_canonicalization_are_rejected(nodes: list[str]) -> None:
    with pytest.raises(ValidationError, match="duplicate"):
        ClusterClient(nodes)


@pytest.mark.parametrize("group_id", [None, 1, "", " ", "\t\n", "\ud800"])
def test_invalid_group_ids_fail_without_network(group_id: object) -> None:
    client, transport = client_for([N1], [])

    with pytest.raises(ValidationError) as raised:
        client.create_group(group_id)  # type: ignore[arg-type]

    assert raised.value.field == "group_id"
    assert transport.calls == []


def test_valid_group_id_is_not_silently_stripped() -> None:
    group_id = " group "
    encoded = "%20group%20"
    client, transport = client_for(
        [N1],
        [
            Step("GET", f"{N1}/v1/group/{encoded}/", status=404),
            mutate(N1, "POST", 201, group_id),
        ],
    )

    report = client.create_group(group_id)

    assert report.group_id == group_id
    transport.assert_done()


def test_group_id_is_percent_encoded_as_one_get_path_segment() -> None:
    group_id = "a/b ?#ü"
    encoded = "a%2Fb%20%3F%23%C3%BC"
    client, transport = client_for(
        [N1],
        [
            Step("GET", f"{N1}/v1/group/{encoded}/", status=404),
            mutate(N1, "POST", 201, group_id),
        ],
    )

    client.create_group(group_id)

    assert transport.calls[0].url.raw_path == f"/v1/group/{encoded}/".encode()
    transport.assert_done()


@pytest.mark.parametrize(
    ("config", "field"),
    [
        (TimeoutConfig(connect=1, read=1, write=1, pool=1), "connect"),
        (RetryPolicy(max_attempts=1, base_delay=0, max_delay=0, jitter_ratio=0), "max_attempts"),
    ],
)
def test_configuration_models_are_immutable(config: object, field: str) -> None:
    with pytest.raises(FrozenInstanceError):
        setattr(config, field, 2)


@pytest.mark.parametrize(
    ("factory", "match"),
    [
        (lambda: TimeoutConfig(connect=0), "connect"),
        (lambda: TimeoutConfig(read=float("inf")), "read"),
        (lambda: RetryPolicy(max_attempts=0), "max_attempts"),
        (lambda: RetryPolicy(max_attempts=True), "max_attempts"),
        (lambda: RetryPolicy(base_delay=-1), "base_delay"),
        (lambda: RetryPolicy(base_delay=2, max_delay=1), "base_delay"),
        (lambda: RetryPolicy(jitter_ratio=1.1), "jitter_ratio"),
    ],
)
def test_invalid_configuration_is_rejected(factory: object, match: str) -> None:
    with pytest.raises(ValueError, match=match):
        factory()  # type: ignore[operator]


def test_retry_delay_rejects_invalid_arguments_and_bounds_large_exponents() -> None:
    policy = RetryPolicy(max_attempts=2, base_delay=0.5, max_delay=1, jitter_ratio=0.5)

    with pytest.raises(ValueError, match="retry_index"):
        policy.delay_for(-1, 0.5)
    with pytest.raises(ValueError, match="random_value"):
        policy.delay_for(0, 1.1)
    assert policy.delay_for(100_000, 0.5) == 1


def test_report_is_immutable_and_json_serializable() -> None:
    client, _ = client_for([N1], [get_present(N1)])
    report = client.create_group("g")

    rendered = json.loads(json.dumps(report.to_dict()))

    assert rendered["operation_id"] == "operation-1"
    assert rendered["action"] == "create"
    assert rendered["status"] == "noop"
    assert rendered["nodes"][0]["initial_state"] == "present"
    assert rendered["has_unresolved_final_state"] is False
    with pytest.raises(FrozenInstanceError):
        report.status = OverallStatus.FAILED  # type: ignore[misc]


def test_operation_error_requires_a_primary_error() -> None:
    report = OperationReport("id", Action.CREATE, "g", OverallStatus.FAILED, ())

    with pytest.raises(ValueError, match="primary error"):
        ClusterOperationError(report)


def test_timeout_values_are_attached_to_every_request() -> None:
    seen: list[dict[str, float]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request.extensions["timeout"])
        if request.method == "GET":
            return httpx.Response(404, request=request)
        return httpx.Response(201, request=request)

    timeout = TimeoutConfig(connect=1, read=2, write=3, pool=4)
    client = ClusterClient([N1], timeout=timeout, transport=httpx.MockTransport(handler))

    client.create_group("g")

    assert seen == [timeout.to_dict(), timeout.to_dict()]


def test_internal_transport_is_closed_by_context_manager() -> None:
    transport = ScriptedTransport([get_present(N1)])
    client = ClusterClient([N1], transport=transport)

    with client as entered:
        assert entered.create_group("g").status is OverallStatus.NOOP

    assert client.is_closed
    assert transport.closed
    with pytest.raises(ClientClosedError):
        client.create_group("g")


def test_explicit_close_is_idempotent() -> None:
    client, transport = client_for([N1], [])

    client.close()
    client.close()

    assert client.is_closed
    assert transport.closed


def test_external_http_client_remains_caller_owned() -> None:
    transport = ScriptedTransport([get_present(N1)])
    external = httpx.Client(transport=transport)

    with ClusterClient([N1], http_client=external) as client:
        assert client.create_group("g").status is OverallStatus.NOOP

    assert not external.is_closed
    assert not transport.closed
    external.close()
    assert transport.closed


def test_context_manager_closes_internal_client_when_body_raises() -> None:
    transport = ScriptedTransport([])
    client = ClusterClient([N1], transport=transport)

    with pytest.raises(RuntimeError, match="body failed"), client:
        raise RuntimeError("body failed")

    assert client.is_closed
    assert transport.closed


def test_httpx_eager_response_handling_closes_response_stream() -> None:
    class TrackingStream(httpx.SyncByteStream):
        def __init__(self) -> None:
            self.closed = False

        def __iter__(self) -> Iterator[bytes]:
            yield b'{"groupId":"g"}'

        def close(self) -> None:
            self.closed = True

    stream = TrackingStream()

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, request=request, stream=stream)

    client = ClusterClient([N1], transport=httpx.MockTransport(handler))

    assert client.create_group("g").status is OverallStatus.NOOP
    assert stream.closed


def test_passing_http_client_and_transport_is_rejected() -> None:
    external = httpx.Client(transport=httpx.MockTransport(lambda request: httpx.Response(200)))
    try:
        with pytest.raises(ValidationError, match="either"):
            ClusterClient([N1], http_client=external, transport=httpx.MockTransport(lambda _: None))
    finally:
        external.close()


def test_programming_errors_from_transport_are_not_hidden() -> None:
    client, _ = client_for(
        [N1],
        [Step("GET", f"{N1}/v1/group/g/", error="programming")],
    )

    with pytest.raises(AssertionError, match="programming"):
        client.create_group("g")


def test_manual_operation_report_success_properties() -> None:
    report = OperationReport("id", Action.DELETE, "g", OverallStatus.SUCCEEDED, ())

    assert report.succeeded
    assert not report.has_unresolved_final_state
    assert NodeState.PRESENT.value == "present"
