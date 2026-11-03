"""Weekly operational report generation (COMP-06/COMP-07, FR-080/FR-082)."""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from audit_log.writer import AuditLog
from checkpoint_store.store import CheckpointStore
from common.clock import Clock, SystemClock
from common.config import canonical_json
from event_bus.publisher import EventPublisher, NullEventPublisher


@dataclass(frozen=True)
class ReportPeriod:
    start: datetime
    end: datetime

    def __post_init__(self) -> None:
        if self.end <= self.start:
            raise ValueError("report end must be after report start")

    @classmethod
    def last_week(cls, now: datetime | None = None) -> ReportPeriod:
        end = now or datetime.now(UTC)
        return cls(start=end - timedelta(days=7), end=end)

    def to_dict(self) -> dict[str, str]:
        return {"start": self.start.isoformat(), "end": self.end.isoformat()}


@dataclass(frozen=True)
class WeeklyReport:
    period: ReportPeriod
    generated_at: datetime
    uptime: dict[str, Any]
    incidents: dict[str, Any]
    remediations: dict[str, Any]
    deployments: dict[str, Any]
    utilization: dict[str, Any]
    audit: dict[str, Any]
    applications: tuple[str, ...] = field(default_factory=tuple)

    def to_dict(self) -> dict[str, Any]:
        return {
            "period": self.period.to_dict(),
            "generated_at": self.generated_at.isoformat(),
            "applications": list(self.applications),
            "uptime": self.uptime,
            "incidents": self.incidents,
            "remediations": self.remediations,
            "deployments": self.deployments,
            "utilization": self.utilization,
            "audit": self.audit,
        }

    def to_json(self) -> str:
        return canonical_json(self.to_dict())

    def render_markdown(self) -> str:
        lines = [
            "# OpsPilot Weekly Operational Report",
            "",
            f"Period: {self.period.start.isoformat()} → {self.period.end.isoformat()}",
            f"Generated: {self.generated_at.isoformat()}",
            "",
            "## Uptime",
            f"- Samples: {self.uptime['samples']}",
            f"- Healthy samples: {self.uptime['healthy_samples']}",
            f"- Uptime: {self.uptime['uptime_percent'] if self.uptime['uptime_percent'] is not None else 'no data'}",
            "",
            "## Incidents",
            f"- Total: {self.incidents['total']}",
            f"- Resolved: {self.incidents['resolved']}",
            f"- Escalated: {self.incidents['escalated']}",
            f"- Human-resolved: {self.incidents['human_resolved']}",
            "",
            "## Remediations",
            f"- Automatic attempts: {self.remediations['automatic_attempts']}",
            f"- Successful automatic remediations: {self.remediations['resolved_automatically']}",
            f"- Escalations sent: {self.remediations['escalations_sent']}",
            "",
            "## Deployments",
            f"- Total: {self.deployments['total']}",
            f"- Successful: {self.deployments['successful']}",
            f"- Rolled back: {self.deployments['rolled_back']}",
            f"- Failed: {self.deployments['failed']}",
            "",
            "## Resource Utilization (min / avg / max)",
        ]
        if not self.utilization:
            lines.append("- No utilization samples in period.")
        for app, rollup in sorted(self.utilization.items()):
            lines.append(
                f"- {app}: CPU {rollup['cpu_percent']['min']} / "
                f"{rollup['cpu_percent']['avg']} / {rollup['cpu_percent']['max']} %, "
                f"memory {rollup['memory_mb']['min']} / {rollup['memory_mb']['avg']} / "
                f"{rollup['memory_mb']['max']} MB, disk "
                f"{rollup['disk_percent']['min']} / {rollup['disk_percent']['avg']} / "
                f"{rollup['disk_percent']['max']} %"
            )
        lines.extend(
            [
                "",
                "## Audit Activity",
                f"- Entries: {self.audit['total']}",
                f"- Failed actions: {self.audit['failed']}",
                f"- Rolled-back actions: {self.audit['rolled_back']}",
                f"- Top actions: {', '.join(f'{name} ({count})' for name, count in self.audit['top_actions'])}",
                "",
            ]
        )
        return "\n".join(lines)


class WeeklyReportGenerator:
    def __init__(
        self,
        store: CheckpointStore,
        audit: AuditLog,
        *,
        clock: Clock | None = None,
        events: EventPublisher | None = None,
    ) -> None:
        self._store = store
        self._audit = audit
        self._clock = clock or SystemClock()
        self._events = events or NullEventPublisher()

    def generate(
        self,
        *,
        period: ReportPeriod | None = None,
        applications: list[str] | None = None,
    ) -> WeeklyReport:
        selected = period or ReportPeriod.last_week(self._clock.now())
        incidents = self._store.list_incidents(start=selected.start, end=selected.end)
        deployments = [
            record
            for record in self._store.list_deployments()
            if selected.start <= _parse(record["created_at"]) <= selected.end
        ]
        entries = self._audit.query(start=selected.start, end=selected.end)
        action_counts = Counter(entry.action for entry in entries)
        apps = tuple(
            sorted(
                set(applications or [])
                | {incident.app for incident in incidents}
                | {record["app"] for record in deployments}
            )
        )
        utilization = {}
        for app in apps:
            rollup = self._store.utilization_rollup(
                start=selected.start, end=selected.end, app=app
            )
            if rollup.count:
                utilization[app] = rollup.to_dict()
        return WeeklyReport(
            period=selected,
            generated_at=self._clock.now(),
            uptime=self._store.health_summary(start=selected.start, end=selected.end),
            incidents={
                "total": len(incidents),
                "resolved": sum(1 for item in incidents if item.status == "Resolved"),
                "escalated": sum(1 for item in incidents if item.status == "Escalated"),
                "human_resolved": sum(
                    1 for item in incidents if item.status == "Human-Resolved"
                ),
                "detected": sum(1 for item in incidents if item.status == "Detected"),
            },
            remediations={
                "automatic_attempts": sum(item.attempts for item in incidents),
                "resolved_automatically": sum(
                    1 for item in incidents if item.status == "Resolved"
                ),
                "escalations_sent": sum(
                    1
                    for entry in entries
                    if entry.action == "incident_escalation" and entry.outcome == "success"
                ),
            },
            deployments={
                "total": len(deployments),
                "successful": sum(1 for item in deployments if item["status"] == "success"),
                "rolled_back": sum(
                    1 for item in deployments if item["status"] == "rolled_back"
                ),
                "failed": sum(1 for item in deployments if item["status"] == "failed"),
            },
            utilization=utilization,
            audit={
                "total": len(entries),
                "failed": sum(1 for entry in entries if entry.outcome == "failed"),
                "rolled_back": sum(
                    1 for entry in entries if entry.outcome == "rolled_back"
                ),
                "top_actions": action_counts.most_common(5),
            },
            applications=apps,
        )

    def write(
        self,
        destination: str | Path,
        *,
        period: ReportPeriod | None = None,
        applications: list[str] | None = None,
    ) -> Path:
        report = self.generate(period=period, applications=applications)
        target = Path(destination)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(report.render_markdown(), encoding="utf-8")
        target.with_suffix(".json").write_text(
            report.to_json() + "\n", encoding="utf-8"
        )
        self._events.publish(
            "report.generated",
            component="COMP-01",
            message=f"weekly report written: {target.name}",
            data={
                "path": str(target),
                "period_start": report.period.start.isoformat(),
                "period_end": report.period.end.isoformat(),
                "incidents": report.incidents,
                "remediations": report.remediations,
                "deployments": report.deployments,
            },
        )
        return target


def _parse(value: str) -> datetime:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)
