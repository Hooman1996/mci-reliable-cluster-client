"""Immutable public result and configuration models."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from math import isfinite, ldexp
from typing import Any


class Action(StrEnum):
    """Cluster operation requested by the caller."""

    CREATE = "create"
    DELETE = "delete"


class OverallStatus(StrEnum):
    """Overall outcome of a cluster operation."""

    SUCCEEDED = "succeeded"
    NOOP = "noop"
    FAILED = "failed"
    ROLLED_BACK = "rolled_back"
    ROLLBACK_INCOMPLETE = "rollback_incomplete"
    INDETERMINATE = "indeterminate"


class NodeState(StrEnum):
    """Best-known group state at one node."""

    PRESENT = "present"
    ABSENT = "absent"
    UNKNOWN = "unknown"


class RequestOutcome(StrEnum):
    """Direct result of the forward mutation request."""

    NOT_REQUIRED = "not_required"
    NOT_ATTEMPTED = "not_attempted"
    CONFIRMED_SUCCESS = "confirmed_success"
    AMBIGUOUS = "ambiguous"


class ReconciliationOutcome(StrEnum):
    """Result of GET probes following an ambiguous request."""

    NOT_REQUIRED = "not_required"
    PRESENT = "present"
    ABSENT = "absent"
    UNKNOWN = "unknown"


class CompensationOutcome(StrEnum):
    """Result of trying to restore a node to its initial state."""

    NOT_REQUIRED = "not_required"
    NOT_ATTEMPTED = "not_attempted"
    CONFIRMED_SUCCESS = "confirmed_success"
    RECONCILED_SUCCESS = "reconciled_success"
    FAILED = "failed"
    AMBIGUOUS = "ambiguous"


class TransitionOwnership(StrEnum):
    """How confidently the operation owns a possible state transition."""

    NONE = "none"
    CONFIRMED = "confirmed"
    INFERRED = "inferred"
    POSSIBLE = "possible"


class ErrorPhase(StrEnum):
    """Phase in which safe error evidence was captured."""

    PREFLIGHT = "preflight"
    MUTATION = "mutation"
    RECONCILIATION = "reconciliation"
    COMPENSATION = "compensation"
    COMPENSATION_RECONCILIATION = "compensation_reconciliation"


def _finite_number(name: str, value: float, *, allow_zero: bool = False) -> None:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not isfinite(value):
        raise ValueError(f"{name} must be a finite number")
    if value < 0 if allow_zero else value <= 0:
        qualifier = "non-negative" if allow_zero else "positive"
        raise ValueError(f"{name} must be {qualifier}")


@dataclass(frozen=True, slots=True)
class TimeoutConfig:
    """Per-request HTTP timeout values, in seconds."""

    connect: float = 2.0
    read: float = 5.0
    write: float = 5.0
    pool: float = 2.0

    def __post_init__(self) -> None:
        for name in ("connect", "read", "write", "pool"):
            _finite_number(name, getattr(self, name))

    def to_dict(self) -> dict[str, float]:
        return {
            "connect": float(self.connect),
            "read": float(self.read),
            "write": float(self.write),
            "pool": float(self.pool),
        }


@dataclass(frozen=True, slots=True)
class RetryPolicy:
    """Bounded GET retry and backoff configuration."""

    max_attempts: int = 3
    base_delay: float = 0.1
    max_delay: float = 1.0
    jitter_ratio: float = 0.0

    def __post_init__(self) -> None:
        if isinstance(self.max_attempts, bool) or not isinstance(self.max_attempts, int):
            raise ValueError("max_attempts must be an integer")
        if self.max_attempts < 1:
            raise ValueError("max_attempts must be at least 1")
        _finite_number("base_delay", self.base_delay, allow_zero=True)
        _finite_number("max_delay", self.max_delay, allow_zero=True)
        if self.base_delay > self.max_delay:
            raise ValueError("base_delay must not exceed max_delay")
        if (
            isinstance(self.jitter_ratio, bool)
            or not isinstance(self.jitter_ratio, (int, float))
            or not isfinite(self.jitter_ratio)
            or not 0 <= self.jitter_ratio <= 1
        ):
            raise ValueError("jitter_ratio must be between 0 and 1")

    def delay_for(self, retry_index: int, random_value: float) -> float:
        """Return the bounded delay before a zero-based retry."""

        if retry_index < 0:
            raise ValueError("retry_index must be non-negative")
        if not 0 <= random_value <= 1:
            raise ValueError("random_value must be between 0 and 1")
        try:
            exponential = ldexp(float(self.base_delay), retry_index)
        except OverflowError:
            exponential = float("inf")
        capped = min(exponential, float(self.max_delay))
        spread = capped * float(self.jitter_ratio)
        jittered = capped - spread + (2 * spread * random_value)
        return min(float(self.max_delay), max(0.0, jittered))


@dataclass(frozen=True, slots=True)
class ErrorInfo:
    """Serializable, secret-safe failure information."""

    node: str
    phase: ErrorPhase
    code: str
    message: str
    status_code: int | None = None
    exception_type: str | None = None

    def to_dict(self) -> dict[str, str | int | None]:
        return {
            "node": self.node,
            "phase": self.phase.value,
            "code": self.code,
            "message": self.message,
            "status_code": self.status_code,
            "exception_type": self.exception_type,
        }


@dataclass(frozen=True, slots=True)
class NodeOutcome:
    """Complete operation journal for one configured node."""

    node: str
    initial_state: NodeState = NodeState.UNKNOWN
    mutation_required: bool | None = None
    preflight_attempts: int = 0
    preflight_error: ErrorInfo | None = None
    request_outcome: RequestOutcome = RequestOutcome.NOT_ATTEMPTED
    mutation_attempts: int = 0
    request_error: ErrorInfo | None = None
    reconciliation_outcome: ReconciliationOutcome = ReconciliationOutcome.NOT_REQUIRED
    reconciliation_attempts: int = 0
    reconciliation_error: ErrorInfo | None = None
    transition_ownership: TransitionOwnership = TransitionOwnership.NONE
    compensation_outcome: CompensationOutcome = CompensationOutcome.NOT_REQUIRED
    compensation_attempts: int = 0
    compensation_error: ErrorInfo | None = None
    compensation_reconciliation_outcome: ReconciliationOutcome = ReconciliationOutcome.NOT_REQUIRED
    compensation_reconciliation_attempts: int = 0
    compensation_reconciliation_error: ErrorInfo | None = None
    final_state: NodeState = NodeState.UNKNOWN

    def to_dict(self) -> dict[str, Any]:
        return {
            "node": self.node,
            "initial_state": self.initial_state.value,
            "mutation_required": self.mutation_required,
            "preflight_attempts": self.preflight_attempts,
            "preflight_error": _error_dict(self.preflight_error),
            "request_outcome": self.request_outcome.value,
            "mutation_attempts": self.mutation_attempts,
            "request_error": _error_dict(self.request_error),
            "reconciliation_outcome": self.reconciliation_outcome.value,
            "reconciliation_attempts": self.reconciliation_attempts,
            "reconciliation_error": _error_dict(self.reconciliation_error),
            "transition_ownership": self.transition_ownership.value,
            "compensation_outcome": self.compensation_outcome.value,
            "compensation_attempts": self.compensation_attempts,
            "compensation_error": _error_dict(self.compensation_error),
            "compensation_reconciliation_outcome": (self.compensation_reconciliation_outcome.value),
            "compensation_reconciliation_attempts": self.compensation_reconciliation_attempts,
            "compensation_reconciliation_error": _error_dict(
                self.compensation_reconciliation_error
            ),
            "final_state": self.final_state.value,
        }


@dataclass(frozen=True, slots=True)
class OperationReport:
    """Machine-readable result of one cluster-wide operation."""

    operation_id: str
    action: Action
    group_id: str
    status: OverallStatus
    nodes: tuple[NodeOutcome, ...]
    primary_error: ErrorInfo | None = None
    compensation_errors: tuple[ErrorInfo, ...] = ()

    @property
    def succeeded(self) -> bool:
        return self.status in (OverallStatus.SUCCEEDED, OverallStatus.NOOP)

    @property
    def has_unresolved_final_state(self) -> bool:
        return any(node.final_state is NodeState.UNKNOWN for node in self.nodes)

    def to_dict(self) -> dict[str, Any]:
        return {
            "operation_id": self.operation_id,
            "action": self.action.value,
            "group_id": self.group_id,
            "status": self.status.value,
            "succeeded": self.succeeded,
            "has_unresolved_final_state": self.has_unresolved_final_state,
            "nodes": [node.to_dict() for node in self.nodes],
            "primary_error": _error_dict(self.primary_error),
            "compensation_errors": [error.to_dict() for error in self.compensation_errors],
        }


def _error_dict(error: ErrorInfo | None) -> dict[str, str | int | None] | None:
    return None if error is None else error.to_dict()
