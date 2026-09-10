"""Synchronous Saga coordinator for the unreliable cluster group API."""

from __future__ import annotations

import json
import logging
import random as random_module
import time
import uuid
from collections.abc import Callable, Iterable
from dataclasses import dataclass, replace
from threading import Lock
from typing import NoReturn

import httpx

from .exceptions import ClientClosedError, ClusterOperationError, ValidationError
from .models import (
    Action,
    CompensationOutcome,
    ErrorInfo,
    ErrorPhase,
    NodeOutcome,
    NodeState,
    OperationReport,
    OverallStatus,
    ReconciliationOutcome,
    RequestOutcome,
    RetryPolicy,
    TimeoutConfig,
    TransitionOwnership,
)
from .transport import collection_url, group_url, httpx_timeout, normalize_nodes

logger = logging.getLogger(__name__)


@dataclass(slots=True)
class _ProbeResult:
    state: NodeState
    attempts: int
    error: ErrorInfo | None
    cause: httpx.RequestError | None


@dataclass(slots=True)
class _RequestResult:
    outcome: RequestOutcome
    error: ErrorInfo | None
    cause: httpx.RequestError | None


class ClusterClient:
    """Create and delete groups across configured nodes using a compensating Saga."""

    def __init__(
        self,
        nodes: Iterable[str],
        *,
        timeout: TimeoutConfig | None = None,
        retry: RetryPolicy | None = None,
        http_client: httpx.Client | None = None,
        transport: httpx.BaseTransport | None = None,
        sleep: Callable[[float], None] = time.sleep,
        random: Callable[[], float] = random_module.random,
        operation_id_factory: Callable[[], str] | None = None,
    ) -> None:
        self._nodes = normalize_nodes(nodes)
        self._timeout = timeout or TimeoutConfig()
        self._retry = retry or RetryPolicy()
        if http_client is not None and transport is not None:
            raise ValidationError("transport", "pass either http_client or transport, not both")

        self._request_timeout = httpx_timeout(self._timeout)
        self._owns_http_client = http_client is None
        self._http = http_client or httpx.Client(
            timeout=self._request_timeout,
            transport=transport,
            follow_redirects=False,
        )
        self._sleep = sleep
        self._random = random
        self._operation_id_factory = operation_id_factory or (lambda: uuid.uuid4().hex)
        self._closed = False
        self._operation_lock = Lock()

    @property
    def nodes(self) -> tuple[str, ...]:
        """Canonical node origins in stable configuration order."""

        return self._nodes

    @property
    def is_closed(self) -> bool:
        return self._closed

    def __enter__(self) -> ClusterClient:
        self._ensure_open()
        return self

    def __exit__(self, exc_type: object, exc: object, traceback: object) -> None:
        self.close()

    def close(self) -> None:
        """Close resources owned by this wrapper; safe to call repeatedly."""

        with self._operation_lock:
            if self._closed:
                return
            if self._owns_http_client:
                self._http.close()
            self._closed = True

    def create_group(self, group_id: str) -> OperationReport:
        """Converge all nodes to PRESENT, compensating on failure."""

        return self._operate(Action.CREATE, group_id)

    def delete_group(self, group_id: str) -> OperationReport:
        """Converge all nodes to ABSENT, compensating on failure."""

        return self._operate(Action.DELETE, group_id)

    def _operate(self, action: Action, group_id: str) -> OperationReport:
        self._ensure_open()
        self._validate_group_id(group_id)
        with self._operation_lock:
            self._ensure_open()
            return self._run_saga(action, group_id)

    def _run_saga(self, action: Action, group_id: str) -> OperationReport:
        operation_id = self._operation_id_factory()
        outcomes: list[NodeOutcome] = []
        first_preflight_failure: tuple[ErrorInfo, httpx.RequestError | None] | None = None

        for node in self._nodes:
            probe = self._probe_state(node, group_id, ErrorPhase.PREFLIGHT, operation_id, action)
            outcome = NodeOutcome(
                node=node,
                initial_state=probe.state,
                preflight_attempts=probe.attempts,
                preflight_error=probe.error,
                final_state=probe.state,
            )
            outcomes.append(outcome)
            if probe.state is NodeState.UNKNOWN and first_preflight_failure is None:
                error = probe.error or self._state_error(
                    node,
                    ErrorPhase.PREFLIGHT,
                    "preflight_unknown",
                    "GET preflight could not establish node state",
                )
                first_preflight_failure = (error, probe.cause)

        if first_preflight_failure is not None:
            preflight_error, cause = first_preflight_failure
            report = OperationReport(
                operation_id=operation_id,
                action=action,
                group_id=group_id,
                status=OverallStatus.INDETERMINATE,
                nodes=tuple(outcomes),
                primary_error=preflight_error,
            )
            self._raise_operation(report, cause)

        target_state = NodeState.PRESENT if action is Action.CREATE else NodeState.ABSENT
        for index, outcome in enumerate(outcomes):
            required = outcome.initial_state is not target_state
            outcomes[index] = replace(
                outcome,
                mutation_required=required,
                request_outcome=(
                    RequestOutcome.NOT_ATTEMPTED if required else RequestOutcome.NOT_REQUIRED
                ),
            )

        mutation_indexes = [
            index for index, outcome in enumerate(outcomes) if outcome.mutation_required
        ]
        if not mutation_indexes:
            return OperationReport(
                operation_id=operation_id,
                action=action,
                group_id=group_id,
                status=OverallStatus.NOOP,
                nodes=tuple(outcomes),
            )

        compensation_indexes: list[int] = []
        primary_error: ErrorInfo | None = None
        primary_cause: httpx.RequestError | None = None

        for index in mutation_indexes:
            node = outcomes[index].node
            request = self._mutate(node, group_id, action, operation_id, ErrorPhase.MUTATION)
            outcomes[index] = replace(
                outcomes[index],
                request_outcome=request.outcome,
                mutation_attempts=1,
                request_error=request.error,
            )

            if request.outcome is RequestOutcome.CONFIRMED_SUCCESS:
                outcomes[index] = replace(
                    outcomes[index],
                    final_state=target_state,
                    transition_ownership=TransitionOwnership.CONFIRMED,
                )
                compensation_indexes.append(index)
                continue

            reconciliation = self._probe_state(
                node,
                group_id,
                ErrorPhase.RECONCILIATION,
                operation_id,
                action,
            )
            outcomes[index] = replace(
                outcomes[index],
                reconciliation_outcome=self._reconciliation_outcome(reconciliation.state),
                reconciliation_attempts=reconciliation.attempts,
                reconciliation_error=reconciliation.error,
                final_state=reconciliation.state,
            )

            if reconciliation.state is target_state:
                outcomes[index] = replace(
                    outcomes[index], transition_ownership=TransitionOwnership.INFERRED
                )
                compensation_indexes.append(index)
                continue

            primary_error = request.error or self._state_error(
                node,
                ErrorPhase.MUTATION,
                "mutation_failed",
                "mutation did not reach the requested state",
            )
            primary_cause = request.cause
            if reconciliation.state is NodeState.UNKNOWN:
                outcomes[index] = replace(
                    outcomes[index], transition_ownership=TransitionOwnership.POSSIBLE
                )
                compensation_indexes.append(index)
            break

        if primary_error is None:
            return OperationReport(
                operation_id=operation_id,
                action=action,
                group_id=group_id,
                status=OverallStatus.SUCCEEDED,
                nodes=tuple(outcomes),
            )

        compensation_errors = self._compensate(
            outcomes,
            compensation_indexes,
            action,
            group_id,
            operation_id,
        )
        status = self._failure_status(outcomes, bool(compensation_indexes))
        report = OperationReport(
            operation_id=operation_id,
            action=action,
            group_id=group_id,
            status=status,
            nodes=tuple(outcomes),
            primary_error=primary_error,
            compensation_errors=tuple(compensation_errors),
        )
        self._raise_operation(report, primary_cause)

    def _compensate(
        self,
        outcomes: list[NodeOutcome],
        indexes: list[int],
        action: Action,
        group_id: str,
        operation_id: str,
    ) -> list[ErrorInfo]:
        errors: list[ErrorInfo] = []
        compensation_action = Action.DELETE if action is Action.CREATE else Action.CREATE

        for index in reversed(indexes):
            outcome = outcomes[index]
            request = self._mutate(
                outcome.node,
                group_id,
                compensation_action,
                operation_id,
                ErrorPhase.COMPENSATION,
            )
            if request.outcome is RequestOutcome.CONFIRMED_SUCCESS:
                outcomes[index] = replace(
                    outcome,
                    compensation_outcome=CompensationOutcome.CONFIRMED_SUCCESS,
                    compensation_attempts=1,
                    final_state=outcome.initial_state,
                )
                continue

            reconciliation = self._probe_state(
                outcome.node,
                group_id,
                ErrorPhase.COMPENSATION_RECONCILIATION,
                operation_id,
                action,
            )
            if reconciliation.state is outcome.initial_state:
                compensation_outcome = CompensationOutcome.RECONCILED_SUCCESS
                final_error = None
            elif reconciliation.state is NodeState.UNKNOWN:
                compensation_outcome = CompensationOutcome.AMBIGUOUS
                final_error = (
                    reconciliation.error
                    or request.error
                    or self._state_error(
                        outcome.node,
                        ErrorPhase.COMPENSATION,
                        "compensation_unknown",
                        "compensation could not establish the initial state",
                    )
                )
            else:
                compensation_outcome = CompensationOutcome.FAILED
                final_error = self._state_error(
                    outcome.node,
                    ErrorPhase.COMPENSATION_RECONCILIATION,
                    "state_not_restored",
                    "compensation verification found the node outside its initial state",
                )

            outcomes[index] = replace(
                outcome,
                compensation_outcome=compensation_outcome,
                compensation_attempts=1,
                compensation_error=request.error,
                compensation_reconciliation_outcome=self._reconciliation_outcome(
                    reconciliation.state
                ),
                compensation_reconciliation_attempts=reconciliation.attempts,
                compensation_reconciliation_error=reconciliation.error,
                final_state=reconciliation.state,
            )
            if final_error is not None:
                errors.append(final_error)

        return errors

    def _probe_state(
        self,
        node: str,
        group_id: str,
        phase: ErrorPhase,
        operation_id: str,
        action: Action,
    ) -> _ProbeResult:
        last_error: ErrorInfo | None = None
        last_cause: httpx.RequestError | None = None

        for attempt in range(1, self._retry.max_attempts + 1):
            try:
                response = self._http.request(
                    "GET",
                    group_url(node, group_id),
                    timeout=self._request_timeout,
                    follow_redirects=False,
                )
            except httpx.RequestError as exc:
                last_cause = exc
                last_error = self._request_error(node, phase, "GET", exc)
            else:
                last_cause = None
                state, error = self._state_from_response(response, node, group_id, phase)
                if state is not NodeState.UNKNOWN:
                    return _ProbeResult(state, attempt, last_error, last_cause)
                last_error = error

            if attempt < self._retry.max_attempts:
                delay = self._retry.delay_for(attempt - 1, self._random())
                logger.warning(
                    "retrying cluster state probe",
                    extra={"operation_id": operation_id, "action": action.value, "node": node},
                )
                self._sleep(delay)

        return _ProbeResult(NodeState.UNKNOWN, self._retry.max_attempts, last_error, last_cause)

    def _state_from_response(
        self,
        response: httpx.Response,
        node: str,
        group_id: str,
        phase: ErrorPhase,
    ) -> tuple[NodeState, ErrorInfo | None]:
        if response.status_code == 404:
            return NodeState.ABSENT, None
        if response.status_code != 200:
            return NodeState.UNKNOWN, self._http_status_error(
                node, phase, "GET", response.status_code
            )

        try:
            payload = response.json()
        except (json.JSONDecodeError, UnicodeDecodeError):
            return NodeState.UNKNOWN, self._state_error(
                node,
                phase,
                "malformed_response",
                "GET returned malformed JSON",
                status_code=200,
            )
        if not isinstance(payload, dict) or payload.get("groupId") != group_id:
            return NodeState.UNKNOWN, self._state_error(
                node,
                phase,
                "invalid_response_schema",
                "GET response did not contain the expected groupId",
                status_code=200,
            )
        return NodeState.PRESENT, None

    def _mutate(
        self,
        node: str,
        group_id: str,
        action: Action,
        operation_id: str,
        phase: ErrorPhase,
    ) -> _RequestResult:
        method = "POST" if action is Action.CREATE else "DELETE"
        expected_status = 201 if action is Action.CREATE else 200
        try:
            response = self._http.request(
                method,
                collection_url(node),
                json={"groupId": group_id},
                timeout=self._request_timeout,
                follow_redirects=False,
            )
        except httpx.RequestError as exc:
            logger.warning(
                "cluster mutation had an ambiguous request outcome",
                extra={"operation_id": operation_id, "action": action.value, "node": node},
            )
            return _RequestResult(
                RequestOutcome.AMBIGUOUS,
                self._request_error(node, phase, method, exc),
                exc,
            )

        if response.status_code == expected_status:
            logger.info(
                "cluster mutation confirmed",
                extra={"operation_id": operation_id, "action": action.value, "node": node},
            )
            return _RequestResult(RequestOutcome.CONFIRMED_SUCCESS, None, None)

        logger.warning(
            "cluster mutation returned an ambiguous status",
            extra={"operation_id": operation_id, "action": action.value, "node": node},
        )
        return _RequestResult(
            RequestOutcome.AMBIGUOUS,
            self._http_status_error(node, phase, method, response.status_code),
            None,
        )

    def _failure_status(
        self, outcomes: list[NodeOutcome], compensation_was_needed: bool
    ) -> OverallStatus:
        if any(outcome.final_state is NodeState.UNKNOWN for outcome in outcomes):
            return OverallStatus.INDETERMINATE
        if any(outcome.final_state is not outcome.initial_state for outcome in outcomes):
            return OverallStatus.ROLLBACK_INCOMPLETE
        if compensation_was_needed:
            return OverallStatus.ROLLED_BACK
        return OverallStatus.FAILED

    @staticmethod
    def _reconciliation_outcome(state: NodeState) -> ReconciliationOutcome:
        return ReconciliationOutcome(state.value)

    @staticmethod
    def _validate_group_id(group_id: str) -> None:
        if not isinstance(group_id, str):
            raise ValidationError("group_id", "must be a string")
        if not group_id or group_id.isspace():
            raise ValidationError("group_id", "must not be empty or whitespace-only")
        if any(0xD800 <= ord(character) <= 0xDFFF for character in group_id):
            raise ValidationError("group_id", "must contain valid Unicode scalar values")

    def _ensure_open(self) -> None:
        if self._closed:
            raise ClientClosedError("ClusterClient is closed")

    @staticmethod
    def _request_error(
        node: str,
        phase: ErrorPhase,
        method: str,
        error: httpx.RequestError,
    ) -> ErrorInfo:
        code = "transport_error" if isinstance(error, httpx.TransportError) else "request_error"
        return ErrorInfo(
            node=node,
            phase=phase,
            code=code,
            message=f"{method} request failed with {type(error).__name__}",
            exception_type=type(error).__name__,
        )

    @staticmethod
    def _http_status_error(
        node: str,
        phase: ErrorPhase,
        method: str,
        status_code: int,
    ) -> ErrorInfo:
        code = "server_error" if status_code >= 500 else "unexpected_status"
        return ErrorInfo(
            node=node,
            phase=phase,
            code=code,
            message=f"{method} returned HTTP {status_code}",
            status_code=status_code,
        )

    @staticmethod
    def _state_error(
        node: str,
        phase: ErrorPhase,
        code: str,
        message: str,
        *,
        status_code: int | None = None,
    ) -> ErrorInfo:
        return ErrorInfo(
            node=node,
            phase=phase,
            code=code,
            message=message,
            status_code=status_code,
        )

    @staticmethod
    def _raise_operation(report: OperationReport, cause: httpx.RequestError | None) -> NoReturn:
        error = ClusterOperationError(report)
        if cause is None:
            raise error
        raise error from cause
