"""Public exceptions for the reliable cluster client."""

from __future__ import annotations

from .models import ErrorInfo, OperationReport


class ClusterClientError(Exception):
    """Base class for client errors."""


class ValidationError(ClusterClientError, ValueError):
    """Input or client configuration is invalid."""

    def __init__(self, field: str, message: str) -> None:
        self.field = field
        self.detail = message
        super().__init__(f"Invalid {field}: {message}")


class ClientClosedError(ClusterClientError):
    """The client wrapper has already been closed."""


class ClusterOperationError(ClusterClientError):
    """A cluster operation failed, with its complete safe journal attached."""

    def __init__(self, report: OperationReport) -> None:
        if report.primary_error is None:
            raise ValueError("an unsuccessful operation report must have a primary error")
        self.report = report
        self._primary_error = report.primary_error
        super().__init__(
            f"Cluster {report.action.value} operation {report.operation_id} "
            f"ended with status {report.status.value}"
        )

    @property
    def primary_error(self) -> ErrorInfo:
        return self._primary_error

    @property
    def compensation_errors(self) -> tuple[ErrorInfo, ...]:
        return self.report.compensation_errors
