"""Canonical event names and component/tile mappings (COMP-13, IMPL-702)."""

from __future__ import annotations

from collections.abc import Mapping

# The eight tiles in UI_SPEC.md §3. Keep this ordering: the frontend renders the
# status strip in exactly this order.
COMPONENT_TILES: tuple[tuple[str, str], ...] = (
    ("COMP-09", "Provisioning"),
    ("COMP-08", "Deploy"),
    ("COMP-10", "Proxy/TLS"),
    ("COMP-06", "Metrics"),
    ("COMP-07", "Incident Engine"),
    ("COMP-11", "Secrets"),
    ("COMP-12", "Audit"),
    ("COMP-04", "Approval Gate"),
)

COMPONENT_NAMES: dict[str, str] = {
    **dict(COMPONENT_TILES),
    "COMP-01": "Control Plane",
    "COMP-02": "Read-Only Tools",
    "COMP-03": "Mutating Tools",
    "COMP-05": "Checkpoint Store",
    "COMP-13": "Mission Control",
}

TILE_IDS = frozenset(dict(COMPONENT_TILES))

# Event names are deliberately descriptive and stable: they are the contract
# between producers and the dashboard reducer. They mirror the structured
# log/audit records each component already writes; the dashboard invents no
# upstream facts.
EVENT_TYPES: dict[str, str] = {
    "control.started": "COMP-01",
    "control.stopped": "COMP-01",
    "system.heartbeat": "COMP-01",
    "agent.thinking": "COMP-01",
    "agent.decision": "COMP-01",
    "agent.result": "COMP-01",
    "agent.token": "COMP-01",
    "tool.previewed": "COMP-03",
    "tool.started": "COMP-03",
    "tool.completed": "COMP-03",
    "tool.failed": "COMP-03",
    "tool.blocked": "COMP-03",
    "tool.retry": "COMP-03",
    "tool.rollback_failed": "COMP-03",
    "approval.requested": "COMP-04",
    "approval.granted": "COMP-04",
    "approval.rejected": "COMP-04",
    "approval.consumed": "COMP-04",
    "checkpoint.created": "COMP-05",
    "checkpoint.restored": "COMP-05",
    "checkpoint.restore_failed": "COMP-05",
    "metrics.sample": "COMP-06",
    "metrics.scrape": "COMP-06",
    "metrics.scrape_failed": "COMP-06",
    "incident.opened": "COMP-07",
    "incident.remediated": "COMP-07",
    "incident.resolved": "COMP-07",
    "incident.escalated": "COMP-07",
    "health.poll": "COMP-07",
    "alert.firing": "COMP-07",
    "deployment.started": "COMP-08",
    "deployment.phase": "COMP-08",
    "deployment.completed": "COMP-08",
    "deployment.failed": "COMP-08",
    "deployment.rolled_back": "COMP-08",
    "provision.started": "COMP-09",
    "provision.planned": "COMP-09",
    "provision.applied": "COMP-09",
    "provision.failed": "COMP-09",
    "proxy.applied": "COMP-10",
    "proxy.failed": "COMP-10",
    "secret.accessed": "COMP-11",
    "secret.stored": "COMP-11",
    "secret.deleted": "COMP-11",
    "audit.appended": "COMP-12",
    "session.opened": "COMP-13",
    "session.heartbeat": "COMP-13",
    "session.closed": "COMP-13",
    "query.started": "COMP-13",
    "query.completed": "COMP-13",
    "query.denied": "COMP-13",
    "report.generated": "COMP-01",
}

ACTIVE_EVENTS = frozenset(
    {
        "tool.started",
        "deployment.started",
        "deployment.phase",
        "provision.started",
    }
)

COMPLETION_EVENTS = frozenset(
    {
        "tool.completed",
        "tool.failed",
        "deployment.completed",
        "deployment.failed",
        "deployment.rolled_back",
        "provision.applied",
        "provision.failed",
        "proxy.applied",
        "proxy.failed",
    }
)

FAULT_EVENTS = frozenset(
    {
        "tool.failed",
        "tool.rollback_failed",
        "deployment.failed",
        "deployment.rolled_back",
        "provision.failed",
        "proxy.failed",
        "checkpoint.restore_failed",
        "metrics.scrape_failed",
        "incident.escalated",
    }
)

SUCCESS_EVENTS = frozenset(
    {
        "tool.completed",
        "deployment.completed",
        "provision.applied",
        "proxy.applied",
        "checkpoint.restored",
        "metrics.scrape",
        "incident.resolved",
    }
)

# A tool call belongs to a visible platform capability when one exists.
TILE_FOR_TOOL: Mapping[str, str] = {
    "deploy_windows_service": "COMP-08",
    "deploy_windows_container": "COMP-08",
    "configure_reverse_proxy": "COMP-10",
}


def tile_for_tool(name: str) -> str | None:
    return TILE_FOR_TOOL.get(name)


def component_for_event(event_type: str) -> str | None:
    return EVENT_TYPES.get(event_type)


def is_error_level(level: str) -> bool:
    return level.upper() in {"ERROR", "CRITICAL"}


def event_tile(event_type: str, component: str, data: Mapping[str, object]) -> str | None:
    explicit = data.get("tile")
    if isinstance(explicit, str) and explicit in TILE_IDS:
        return explicit
    if component in TILE_IDS:
        return component
    return None
