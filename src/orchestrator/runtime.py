"""Process-wide component wiring for the single-host MVP (COMP-01)."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from approval_gate.gate import ApprovalGate
from approval_gate.policy import ApprovalPolicy
from approval_gate.store import SqliteApprovalStore
from audit_log.writer import AuditLog
from checkpoint_store.store import CheckpointStore
from event_bus.journal import EventJournal
from event_bus.publisher import EventPublisher, JournalEventPublisher
from orchestrator.settings import RuntimePaths, default_config_dir, default_data_dir
from tools_mutating import (
    ApplyFirewallRuleTool,
    ApplyHardeningTool,
    ConfigureReverseProxyTool,
    DeployWindowsContainerTool,
    DeployWindowsServiceTool,
    MutatingToolRegistry,
    RemoveWindowsServiceTool,
    RestartWindowsServiceTool,
)


@dataclass
class Runtime:
    paths: RuntimePaths
    store: CheckpointStore
    audit: AuditLog
    approval_store: SqliteApprovalStore
    gate: ApprovalGate
    registry: MutatingToolRegistry
    events: EventPublisher

    def close(self) -> None:  # pragma: no cover - connections are per-call
        return None


def build_runtime(
    *,
    data_dir: str | Path | None = None,
    config_dir: str | Path | None = None,
    runner: Any = None,
    publisher: EventPublisher | None = None,
) -> Runtime:
    paths = RuntimePaths.under(data_dir or default_data_dir())
    paths.ensure()
    configs = Path(config_dir or default_config_dir())
    policy = ApprovalPolicy.from_file(configs / "approval-policy.yaml")
    events = publisher or JournalEventPublisher(EventJournal(paths.event_path))
    store = CheckpointStore(paths.db_path, events=events)
    approval_store = SqliteApprovalStore(paths.db_path)
    gate = ApprovalGate(policy, approval_store, events=events)
    audit = AuditLog(paths.audit_path, events=events)
    tools = [
        DeployWindowsServiceTool(runner=runner),
        RemoveWindowsServiceTool(runner=runner),
        RestartWindowsServiceTool(runner=runner),
        ConfigureReverseProxyTool(runner=runner),
        ApplyFirewallRuleTool(runner=runner),
        ApplyHardeningTool(runner=runner),
        DeployWindowsContainerTool(runner=runner),
    ]
    registry = MutatingToolRegistry(
        tools,
        gate=gate,
        checkpoints=store,
        audit_log=audit,
        events=events,
    )
    return Runtime(
        paths=paths,
        store=store,
        audit=audit,
        approval_store=approval_store,
        gate=gate,
        registry=registry,
        events=events,
    )
