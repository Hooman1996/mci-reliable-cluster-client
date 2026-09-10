from __future__ import annotations

import json
import logging
import runpy
from collections.abc import Sequence

import pytest

from mci_cluster_client import (
    Action,
    ClusterOperationError,
    CompensationOutcome,
    ErrorInfo,
    ErrorPhase,
    NodeOutcome,
    NodeState,
    OperationReport,
    OverallStatus,
    RequestOutcome,
    RetryPolicy,
    TimeoutConfig,
    ValidationError,
    cli,
)

N1 = "https://node1.example.com"
N2 = "https://node2.example.com"


def make_report(
    status: OverallStatus = OverallStatus.SUCCEEDED,
    *,
    action: Action = Action.CREATE,
    nodes: tuple[NodeOutcome, ...] | None = None,
    compensation_errors: tuple[ErrorInfo, ...] = (),
) -> OperationReport:
    successful = status in (OverallStatus.SUCCEEDED, OverallStatus.NOOP)
    primary_error = None
    if not successful:
        primary_error = ErrorInfo(
            node=N1,
            phase=ErrorPhase.MUTATION,
            code="unexpected_status",
            message="POST returned HTTP 500",
            status_code=500,
        )
    if nodes is None:
        initial = NodeState.ABSENT
        final = NodeState.PRESENT if successful else initial
        nodes = (
            NodeOutcome(
                node=N1,
                initial_state=initial,
                mutation_required=True,
                preflight_attempts=1,
                request_outcome=RequestOutcome.CONFIRMED_SUCCESS,
                mutation_attempts=1,
                final_state=final,
            ),
        )
    return OperationReport(
        operation_id="operation-1",
        action=action,
        group_id="g",
        status=status,
        nodes=nodes,
        primary_error=primary_error,
        compensation_errors=compensation_errors,
    )


def run_with_report(
    monkeypatch: pytest.MonkeyPatch,
    report: OperationReport,
) -> list[cli.CliConfig]:
    configs: list[cli.CliConfig] = []

    def fake_execute(config: cli.CliConfig) -> OperationReport:
        configs.append(config)
        return report

    monkeypatch.setattr(cli, "execute", fake_execute)
    return configs


def test_create_dispatches_with_repeated_nodes_and_compact_json(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    report = make_report()
    configs = run_with_report(monkeypatch, report)

    result = cli.main(["create", "g", "--node", N1, "--node", N2])

    captured = capsys.readouterr()
    assert result == cli.ExitCode.SUCCESS
    assert configs[0].action == "create"
    assert configs[0].nodes == (N1, N2)
    assert (
        captured.out
        == json.dumps(report.to_dict(), ensure_ascii=False, separators=(",", ":")) + "\n"
    )
    assert captured.err == ""


def test_delete_dispatches_and_pretty_prints(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    report = make_report(action=Action.DELETE)
    configs = run_with_report(monkeypatch, report)

    result = cli.main(["delete", "g", "--node", N1, "--pretty"])

    captured = capsys.readouterr()
    assert result == cli.ExitCode.SUCCESS
    assert configs[0].action == "delete"
    assert captured.out == json.dumps(report.to_dict(), ensure_ascii=False, indent=2) + "\n"
    assert '\n  "operation_id"' in captured.out


def test_environment_nodes_are_trimmed_and_used_as_fallback(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    configs = run_with_report(monkeypatch, make_report())
    monkeypatch.setenv("MCI_CLUSTER_NODES", f" {N1} , {N2} ")

    assert cli.main(["create", "g"]) == cli.ExitCode.SUCCESS

    assert configs[0].nodes == (N1, N2)


def test_cli_nodes_override_even_malformed_environment(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    configs = run_with_report(monkeypatch, make_report())
    monkeypatch.setenv("MCI_CLUSTER_NODES", "node1,,node2")

    assert cli.main(["create", "g", "--node", N1]) == cli.ExitCode.SUCCESS

    assert configs[0].nodes == (N1,)


def test_missing_nodes_is_configuration_error_without_report(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.delenv("MCI_CLUSTER_NODES", raising=False)
    monkeypatch.setattr(cli, "execute", lambda config: pytest.fail("must not execute"))

    result = cli.main(["create", "g"])

    captured = capsys.readouterr()
    assert result == cli.ExitCode.USAGE_ERROR
    assert captured.out == ""
    assert "use --node or set MCI_CLUSTER_NODES" in captured.err
    assert "Traceback" not in captured.err


@pytest.mark.parametrize("value", ["", " ", ",", "node1,", ",node1", "node1,,node2"])
def test_malformed_environment_node_list_is_rejected(
    value: str,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setenv("MCI_CLUSTER_NODES", value)
    monkeypatch.setattr(cli, "execute", lambda config: pytest.fail("must not execute"))

    assert cli.main(["delete", "g"]) == cli.ExitCode.USAGE_ERROR

    assert capsys.readouterr().out == ""


def test_every_numeric_option_reaches_existing_configuration_models(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    configs = run_with_report(monkeypatch, make_report())

    result = cli.main(
        [
            "create",
            "g",
            "--node",
            N1,
            "--connect-timeout",
            "1.1",
            "--read-timeout",
            "2.2",
            "--write-timeout",
            "3.3",
            "--pool-timeout",
            "4.4",
            "--get-attempts",
            "5",
            "--backoff-base",
            "0.6",
            "--backoff-cap",
            "7.7",
            "--jitter",
            "0.8",
            "--log-level",
            "info",
        ]
    )

    assert result == cli.ExitCode.SUCCESS
    assert configs[0].timeout == TimeoutConfig(connect=1.1, read=2.2, write=3.3, pool=4.4)
    assert configs[0].retry == RetryPolicy(
        max_attempts=5,
        base_delay=0.6,
        max_delay=7.7,
        jitter_ratio=0.8,
    )
    assert configs[0].log_level == logging.INFO


@pytest.mark.parametrize(
    "arguments",
    [
        ["--connect-timeout", "0"],
        ["--read-timeout", "nan"],
        ["--write-timeout", "-1"],
        ["--pool-timeout", "inf"],
        ["--get-attempts", "0"],
        ["--backoff-base", "-0.1"],
        ["--backoff-cap", "-1"],
        ["--jitter", "1.1"],
        ["--backoff-base", "2", "--backoff-cap", "1"],
    ],
)
def test_invalid_numeric_configuration_fails_before_client_execution(
    arguments: list[str],
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setattr(cli, "execute", lambda config: pytest.fail("must not execute"))

    result = cli.main(["create", "g", "--node", N1, *arguments])

    captured = capsys.readouterr()
    assert result == cli.ExitCode.USAGE_ERROR
    assert captured.out == ""
    assert "configuration error" in captured.err
    assert "Traceback" not in captured.err


@pytest.mark.parametrize("status", [OverallStatus.SUCCEEDED, OverallStatus.NOOP])
def test_success_and_idempotent_noop_exit_zero(
    status: OverallStatus,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    run_with_report(monkeypatch, make_report(status))

    assert cli.main(["create", "g", "--node", N1]) == cli.ExitCode.SUCCESS


def test_resolved_failure_serializes_report_and_exits_three(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    report = make_report(OverallStatus.ROLLED_BACK)
    monkeypatch.setattr(
        cli, "execute", lambda config: (_ for _ in ()).throw(ClusterOperationError(report))
    )

    result = cli.main(["create", "g", "--node", N1])

    captured = capsys.readouterr()
    assert result == cli.ExitCode.OPERATION_FAILED
    assert json.loads(captured.out)["status"] == "rolled_back"
    assert "status rolled_back" in captured.err
    assert "Traceback" not in captured.err


@pytest.mark.parametrize("status", [OverallStatus.INDETERMINATE, OverallStatus.ROLLBACK_INCOMPLETE])
def test_unresolved_or_incomplete_operation_exits_four(
    status: OverallStatus,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    nodes = (NodeOutcome(node=N1, initial_state=NodeState.ABSENT, final_state=NodeState.UNKNOWN),)
    report = make_report(status, nodes=nodes)
    monkeypatch.setattr(
        cli, "execute", lambda config: (_ for _ in ()).throw(ClusterOperationError(report))
    )

    assert cli.main(["create", "g", "--node", N1]) == cli.ExitCode.INDETERMINATE


def test_rollback_failure_exits_four_even_with_known_final_state(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    compensation_error = ErrorInfo(
        node=N1,
        phase=ErrorPhase.COMPENSATION,
        code="unexpected_status",
        message="DELETE returned HTTP 500",
        status_code=500,
    )
    nodes = (
        NodeOutcome(
            node=N1,
            initial_state=NodeState.ABSENT,
            compensation_outcome=CompensationOutcome.FAILED,
            final_state=NodeState.PRESENT,
        ),
    )
    report = make_report(
        OverallStatus.ROLLBACK_INCOMPLETE,
        nodes=nodes,
        compensation_errors=(compensation_error,),
    )
    monkeypatch.setattr(
        cli, "execute", lambda config: (_ for _ in ()).throw(ClusterOperationError(report))
    )

    assert cli.main(["create", "g", "--node", N1]) == cli.ExitCode.INDETERMINATE


def test_validation_failure_is_exit_two_and_has_no_traceback(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    def invalid(config: cli.CliConfig) -> OperationReport:
        raise ValidationError("group_id", "must not be empty")

    monkeypatch.setattr(cli, "execute", invalid)

    result = cli.main(["create", "g", "--node", N1])

    captured = capsys.readouterr()
    assert result == cli.ExitCode.USAGE_ERROR
    assert captured.out == ""
    assert "Invalid group_id" in captured.err
    assert "Traceback" not in captured.err


def test_keyboard_interrupt_is_exit_130_without_report(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    def interrupt(config: cli.CliConfig) -> OperationReport:
        raise KeyboardInterrupt

    monkeypatch.setattr(cli, "execute", interrupt)

    result = cli.main(["delete", "g", "--node", N1])

    captured = capsys.readouterr()
    assert result == cli.ExitCode.INTERRUPTED
    assert captured.out == ""
    assert captured.err == "mci-cluster: interrupted\n"


def test_report_keeps_node_order_and_serializes_enums_as_strings(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    nodes = (
        NodeOutcome(node=N2, initial_state=NodeState.ABSENT, final_state=NodeState.PRESENT),
        NodeOutcome(node=N1, initial_state=NodeState.PRESENT, final_state=NodeState.PRESENT),
    )
    run_with_report(monkeypatch, make_report(nodes=nodes))

    assert cli.main(["create", "g", "--node", N2, "--node", N1]) == 0

    rendered = json.loads(capsys.readouterr().out)
    assert [node["node"] for node in rendered["nodes"]] == [N2, N1]
    assert rendered["action"] == "create"
    assert rendered["nodes"][0]["initial_state"] == "absent"


@pytest.mark.parametrize("command", ["create", "delete"])
def test_command_help_is_successful(command: str, capsys: pytest.CaptureFixture[str]) -> None:
    result = cli.main([command, "--help"])

    captured = capsys.readouterr()
    assert result == cli.ExitCode.SUCCESS
    assert f"usage: mci-cluster {command}" in captured.out
    assert "--node HOST" in captured.out
    assert captured.err == ""


def test_version_is_successful(capsys: pytest.CaptureFixture[str]) -> None:
    assert cli.main(["--version"]) == cli.ExitCode.SUCCESS

    assert capsys.readouterr().out == "mci-cluster 0.1.0\n"


def test_argparse_error_returns_two_without_traceback(
    capsys: pytest.CaptureFixture[str],
) -> None:
    assert cli.main(["unknown"]) == cli.ExitCode.USAGE_ERROR

    captured = capsys.readouterr()
    assert captured.out == ""
    assert "invalid choice" in captured.err
    assert "Traceback" not in captured.err


@pytest.mark.parametrize(("action", "called"), [("create", "create"), ("delete", "delete")])
def test_execute_uses_context_manager_and_dispatches(
    action: str,
    called: str,
) -> None:
    events: list[str] = []
    report = make_report(action=Action(action))

    class FakeClient:
        def __enter__(self) -> FakeClient:
            events.append("enter")
            return self

        def __exit__(self, exc_type: object, exc: object, traceback: object) -> None:
            events.append("exit")

        def create_group(self, group_id: str) -> OperationReport:
            events.append(f"create:{group_id}")
            return report

        def delete_group(self, group_id: str) -> OperationReport:
            events.append(f"delete:{group_id}")
            return report

    def factory(nodes: Sequence[str], *, timeout: TimeoutConfig, retry: RetryPolicy) -> FakeClient:
        assert nodes == (N1,)
        assert timeout == TimeoutConfig()
        assert retry == RetryPolicy()
        return FakeClient()

    config = cli.CliConfig(
        action, "g", (N1,), TimeoutConfig(), RetryPolicy(), logging.WARNING, False
    )

    assert cli.execute(config, factory) is report
    assert events == ["enter", f"{called}:g", "exit"]


def test_unexpected_programming_error_is_not_swallowed(monkeypatch: pytest.MonkeyPatch) -> None:
    def broken(config: cli.CliConfig) -> OperationReport:
        raise RuntimeError("bug")

    monkeypatch.setattr(cli, "execute", broken)

    with pytest.raises(RuntimeError, match="bug"):
        cli.main(["create", "g", "--node", N1])


def test_logging_configuration_is_restored(monkeypatch: pytest.MonkeyPatch) -> None:
    package_logger = logging.getLogger("mci_cluster_client")
    previous_level = package_logger.level
    previous_propagate = package_logger.propagate
    previous_handlers = tuple(package_logger.handlers)
    run_with_report(monkeypatch, make_report())

    assert cli.main(["create", "g", "--node", N1, "--log-level", "DEBUG"]) == 0

    assert package_logger.level == previous_level
    assert package_logger.propagate is previous_propagate
    assert tuple(package_logger.handlers) == previous_handlers


def test_console_entrypoint_raises_system_exit(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(cli, "main", lambda: cli.ExitCode.INTERRUPTED)

    with pytest.raises(SystemExit) as raised:
        cli.entrypoint()

    assert raised.value.code == cli.ExitCode.INTERRUPTED


def test_python_module_entrypoint_raises_system_exit(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(cli, "main", lambda: cli.ExitCode.SUCCESS)

    with pytest.raises(SystemExit) as raised:
        runpy.run_module("mci_cluster_client.__main__", run_name="__main__")

    assert raised.value.code == cli.ExitCode.SUCCESS
