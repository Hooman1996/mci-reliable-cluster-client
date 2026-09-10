from __future__ import annotations

import json
from collections.abc import Iterable
from dataclasses import dataclass
from typing import Any, Literal
from urllib.parse import unquote

import httpx

from mci_cluster_client import ClusterClient, RetryPolicy

UNSET = object()


@dataclass(frozen=True)
class Step:
    method: str
    url: str
    status: int | None = None
    response_json: object = UNSET
    response_content: bytes | None = None
    expected_json: object = UNSET
    error: str | None = None


class ScriptedTransport(httpx.BaseTransport):
    def __init__(self, steps: Iterable[Step]) -> None:
        self.steps = list(steps)
        self.calls: list[httpx.Request] = []
        self.closed = False

    def handle_request(self, request: httpx.Request) -> httpx.Response:
        self.calls.append(request)
        if not self.steps:
            raise AssertionError(f"unexpected request: {request.method} {request.url}")
        step = self.steps.pop(0)
        assert request.method == step.method
        assert str(request.url) == step.url
        if step.expected_json is not UNSET:
            assert json.loads(request.content) == step.expected_json
        if step.error == "timeout":
            raise httpx.ReadTimeout("scripted timeout", request=request)
        if step.error == "connect":
            raise httpx.ConnectError("scripted connection failure", request=request)
        if step.error == "programming":
            raise AssertionError("scripted programming error")
        assert step.status is not None
        if step.response_json is not UNSET:
            return httpx.Response(step.status, request=request, json=step.response_json)
        return httpx.Response(step.status, request=request, content=step.response_content or b"")

    def close(self) -> None:
        self.closed = True

    def assert_done(self) -> None:
        assert self.steps == []


FaultEffect = Literal[
    "timeout_before",
    "timeout_after",
    "decoding_after",
    "status_before",
    "status_after",
]


@dataclass(frozen=True)
class StatefulFault:
    method: str
    node: str
    occurrence: int
    effect: FaultEffect
    status: int = 500


class StatefulTransport(httpx.BaseTransport):
    """Tiny tests-only model of group presence plus deterministic request faults."""

    def __init__(
        self,
        initial_groups: dict[str, Iterable[str]],
        faults: Iterable[StatefulFault] = (),
    ) -> None:
        self.groups = {node: set(groups) for node, groups in initial_groups.items()}
        self.faults = tuple(faults)
        self.calls: list[httpx.Request] = []
        self._request_counts: dict[tuple[str, str], int] = {}

    def handle_request(self, request: httpx.Request) -> httpx.Response:
        self.calls.append(request)
        node = self._node_origin(request.url)
        key = (request.method, node)
        occurrence = self._request_counts.get(key, 0) + 1
        self._request_counts[key] = occurrence
        fault = next(
            (
                candidate
                for candidate in self.faults
                if (candidate.method, candidate.node, candidate.occurrence)
                == (request.method, node, occurrence)
            ),
            None,
        )
        group_id = self._group_id(request)

        if fault is not None and fault.effect == "timeout_before":
            raise httpx.ReadTimeout("stateful timeout before commit", request=request)
        if fault is not None and fault.effect == "status_before":
            return httpx.Response(fault.status, request=request)

        if request.method == "POST":
            self.groups[node].add(group_id)
        elif request.method == "DELETE":
            self.groups[node].discard(group_id)

        if fault is not None and fault.effect == "timeout_after":
            raise httpx.ReadTimeout("stateful timeout after commit", request=request)
        if fault is not None and fault.effect == "decoding_after":
            return httpx.Response(
                201 if request.method == "POST" else 200,
                request=request,
                headers={"Content-Encoding": "gzip"},
                content=b"not-a-gzip-stream",
            )
        if fault is not None and fault.effect == "status_after":
            return httpx.Response(fault.status, request=request)

        if request.method == "GET":
            if group_id in self.groups[node]:
                return httpx.Response(200, request=request, json={"groupId": group_id})
            return httpx.Response(404, request=request)
        return httpx.Response(201 if request.method == "POST" else 200, request=request)

    def has_group(self, node: str, group_id: str) -> bool:
        return group_id in self.groups[node]

    @staticmethod
    def _node_origin(url: httpx.URL) -> str:
        host = f"[{url.host}]" if ":" in url.host else url.host
        port = "" if url.port is None else f":{url.port}"
        return f"{url.scheme}://{host}{port}"

    @staticmethod
    def _group_id(request: httpx.Request) -> str:
        if request.method == "GET":
            return unquote(request.url.path.rstrip("/").rsplit("/", 1)[-1])
        payload = json.loads(request.content)
        assert isinstance(payload, dict)
        group_id = payload["groupId"]
        assert isinstance(group_id, str)
        return group_id


def get_absent(node: str, group_id: str = "g") -> Step:
    return Step("GET", f"{node}/v1/group/{group_id}/", status=404)


def get_present(node: str, group_id: str = "g") -> Step:
    return Step(
        "GET",
        f"{node}/v1/group/{group_id}/",
        status=200,
        response_json={"groupId": group_id},
    )


def mutate(node: str, method: str, status: int, group_id: str = "g") -> Step:
    return Step(
        method,
        f"{node}/v1/group/",
        status=status,
        expected_json={"groupId": group_id},
    )


def client_for(
    nodes: Iterable[str],
    steps: Iterable[Step],
    *,
    retry: RetryPolicy | None = None,
    sleep: Any = lambda _: None,
    random: Any = lambda: 0.5,
) -> tuple[ClusterClient, ScriptedTransport]:
    transport = ScriptedTransport(steps)
    client = ClusterClient(
        nodes,
        transport=transport,
        retry=retry or RetryPolicy(max_attempts=1),
        sleep=sleep,
        random=random,
        operation_id_factory=lambda: "operation-1",
    )
    return client, transport
