"""Command-line interface for one-shot cluster group operations."""

from __future__ import annotations

import argparse
import json
import logging
import os
import sys
from collections.abc import Iterator, Mapping, Sequence
from contextlib import contextmanager
from dataclasses import dataclass
from enum import IntEnum
from typing import NoReturn, Protocol

from . import __version__
from .client import ClusterClient
from .exceptions import ClusterOperationError, ValidationError
from .models import OperationReport, OverallStatus, RetryPolicy, TimeoutConfig

_LOGGER_NAME = "mci_cluster_client"
_ENV_NODES = "MCI_CLUSTER_NODES"


class ExitCode(IntEnum):
    """Stable process exit codes exposed by the CLI."""

    SUCCESS = 0
    USAGE_ERROR = 2
    OPERATION_FAILED = 3
    INDETERMINATE = 4
    INTERRUPTED = 130


class _ParserExit(Exception):
    def __init__(self, status: int) -> None:
        self.status = status


class _ArgumentParser(argparse.ArgumentParser):
    def exit(self, status: int = 0, message: str | None = None) -> NoReturn:
        del message
        raise _ParserExit(status)

    def error(self, message: str) -> NoReturn:
        self.print_usage(sys.stderr)
        self._print_message(f"{self.prog}: error: {message}\n", sys.stderr)
        raise _ParserExit(ExitCode.USAGE_ERROR)


class _Client(Protocol):
    def __enter__(self) -> _Client: ...

    def __exit__(self, exc_type: object, exc: object, traceback: object) -> None: ...

    def create_group(self, group_id: str) -> OperationReport: ...

    def delete_group(self, group_id: str) -> OperationReport: ...


class _ClientFactory(Protocol):
    def __call__(
        self,
        nodes: Sequence[str],
        *,
        timeout: TimeoutConfig,
        retry: RetryPolicy,
    ) -> _Client: ...


@dataclass(frozen=True, slots=True)
class CliConfig:
    action: str
    group_id: str
    nodes: tuple[str, ...]
    timeout: TimeoutConfig
    retry: RetryPolicy
    log_level: int
    pretty: bool


def build_parser() -> argparse.ArgumentParser:
    """Build the public argument parser without reading environment state."""

    parser = _ArgumentParser(
        prog="mci-cluster",
        description="Reliably create or delete one group across every configured node.",
    )
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    subparsers = parser.add_subparsers(dest="action", required=True)
    for action in ("create", "delete"):
        command = subparsers.add_parser(
            action,
            help=f"{action} a group across the configured cluster",
            description=f"{action.capitalize()} a group across every configured cluster node.",
        )
        command.add_argument("group_id", metavar="GROUP_ID")
        command.add_argument(
            "--node",
            action="append",
            dest="nodes",
            metavar="HOST",
            help=("cluster node; repeat for multiple nodes (overrides MCI_CLUSTER_NODES)"),
        )
        _add_configuration_arguments(command)
    return parser


def _add_configuration_arguments(parser: argparse.ArgumentParser) -> None:
    timeout_defaults = TimeoutConfig()
    retry_defaults = RetryPolicy()
    parser.add_argument("--connect-timeout", type=float, default=timeout_defaults.connect)
    parser.add_argument("--read-timeout", type=float, default=timeout_defaults.read)
    parser.add_argument("--write-timeout", type=float, default=timeout_defaults.write)
    parser.add_argument("--pool-timeout", type=float, default=timeout_defaults.pool)
    parser.add_argument("--get-attempts", type=int, default=retry_defaults.max_attempts)
    parser.add_argument("--backoff-base", type=float, default=retry_defaults.base_delay)
    parser.add_argument("--backoff-cap", type=float, default=retry_defaults.max_delay)
    parser.add_argument("--jitter", type=float, default=retry_defaults.jitter_ratio)
    parser.add_argument(
        "--log-level",
        choices=("CRITICAL", "ERROR", "WARNING", "INFO", "DEBUG"),
        default="WARNING",
        type=str.upper,
    )
    parser.add_argument("--pretty", action="store_true", help="indent the JSON report")


def resolve_config(
    namespace: argparse.Namespace,
    environ: Mapping[str, str],
) -> CliConfig:
    """Resolve environment fallback and validate existing model configuration."""

    nodes = _resolve_nodes(namespace.nodes, environ)
    timeout = TimeoutConfig(
        connect=namespace.connect_timeout,
        read=namespace.read_timeout,
        write=namespace.write_timeout,
        pool=namespace.pool_timeout,
    )
    retry = RetryPolicy(
        max_attempts=namespace.get_attempts,
        base_delay=namespace.backoff_base,
        max_delay=namespace.backoff_cap,
        jitter_ratio=namespace.jitter,
    )
    return CliConfig(
        action=namespace.action,
        group_id=namespace.group_id,
        nodes=nodes,
        timeout=timeout,
        retry=retry,
        log_level=getattr(logging, namespace.log_level),
        pretty=namespace.pretty,
    )


def _resolve_nodes(cli_nodes: Sequence[str] | None, environ: Mapping[str, str]) -> tuple[str, ...]:
    if cli_nodes:
        return tuple(cli_nodes)

    raw_nodes = environ.get(_ENV_NODES)
    if raw_nodes is None:
        raise ValidationError("nodes", "use --node or set MCI_CLUSTER_NODES")
    nodes = tuple(item.strip() for item in raw_nodes.split(","))
    if not nodes or any(not node for node in nodes):
        raise ValidationError(
            "MCI_CLUSTER_NODES",
            "must be a comma-separated list with no empty items",
        )
    return nodes


def execute(config: CliConfig, client_factory: _ClientFactory = ClusterClient) -> OperationReport:
    """Construct a client and execute exactly one requested operation."""

    with client_factory(config.nodes, timeout=config.timeout, retry=config.retry) as client:
        if config.action == "create":
            return client.create_group(config.group_id)
        return client.delete_group(config.group_id)


def report_exit_code(report: OperationReport) -> ExitCode:
    """Map the structured final state to the public process contract."""

    if report.succeeded:
        return ExitCode.SUCCESS
    if (
        report.status in (OverallStatus.FAILED, OverallStatus.ROLLED_BACK)
        and not report.has_unresolved_final_state
        and not report.compensation_errors
    ):
        return ExitCode.OPERATION_FAILED
    return ExitCode.INDETERMINATE


def _write_report(report: OperationReport, *, pretty: bool) -> None:
    if pretty:
        rendered = json.dumps(report.to_dict(), ensure_ascii=False, indent=2)
    else:
        rendered = json.dumps(report.to_dict(), ensure_ascii=False, separators=(",", ":"))
    print(rendered)


@contextmanager
def _cli_logging(level: int) -> Iterator[None]:
    logger = logging.getLogger(_LOGGER_NAME)
    handler = logging.StreamHandler(sys.stderr)
    handler.setFormatter(logging.Formatter("%(levelname)s: %(message)s"))
    previous_level = logger.level
    previous_propagate = logger.propagate
    logger.addHandler(handler)
    logger.setLevel(level)
    logger.propagate = False
    try:
        yield
    finally:
        logger.removeHandler(handler)
        logger.setLevel(previous_level)
        logger.propagate = previous_propagate


def main(argv: Sequence[str] | None = None) -> int:
    """Run the CLI and return a stable process exit code."""

    parser = build_parser()
    try:
        namespace = parser.parse_args(argv)
        config = resolve_config(namespace, os.environ)
    except _ParserExit as exc:
        return exc.status
    except (ValidationError, ValueError) as exc:
        print(f"mci-cluster: configuration error: {exc}", file=sys.stderr)
        return ExitCode.USAGE_ERROR

    try:
        with _cli_logging(config.log_level):
            report = execute(config)
    except ClusterOperationError as exc:
        report = exc.report
        _write_report(report, pretty=config.pretty)
        print(
            f"mci-cluster: cluster {config.action} failed with status {report.status.value}",
            file=sys.stderr,
        )
        return report_exit_code(report)
    except ValidationError as exc:
        print(f"mci-cluster: configuration error: {exc}", file=sys.stderr)
        return ExitCode.USAGE_ERROR
    except KeyboardInterrupt:
        print("mci-cluster: interrupted", file=sys.stderr)
        return ExitCode.INTERRUPTED

    _write_report(report, pretty=config.pretty)
    return report_exit_code(report)


def entrypoint() -> None:
    """Installed console-script entry point."""

    raise SystemExit(main())


__all__ = ["CliConfig", "ExitCode", "build_parser", "entrypoint", "execute", "main"]
