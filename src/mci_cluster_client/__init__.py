"""Public API for the MCI reliable cluster client."""

__version__ = "0.1.0"

from .client import ClusterClient
from .exceptions import (
    ClientClosedError,
    ClusterClientError,
    ClusterOperationError,
    ValidationError,
)
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

__all__ = [
    "Action",
    "ClientClosedError",
    "ClusterClient",
    "ClusterClientError",
    "ClusterOperationError",
    "CompensationOutcome",
    "ErrorInfo",
    "ErrorPhase",
    "NodeOutcome",
    "NodeState",
    "OperationReport",
    "OverallStatus",
    "ReconciliationOutcome",
    "RequestOutcome",
    "RetryPolicy",
    "TimeoutConfig",
    "TransitionOwnership",
    "ValidationError",
    "__version__",
]
