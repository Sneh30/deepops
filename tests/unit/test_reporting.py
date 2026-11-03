from __future__ import annotations

from datetime import timedelta

from reporting.weekly import ReportPeriod, WeeklyReportGenerator


def test_report_period_rejects_inverted_range() -> None:
    import pytest

    now = ReportPeriod.last_week().end
    with pytest.raises(ValueError):
        ReportPeriod(start=now, end=now - timedelta(days=1))


def test_weekly_report_rolls_up_operational_data(store, audit, clock) -> None:
    store.record_health_sample(service="app-green", healthy=True, sampled_at=clock.now())
    store.record_health_sample(service="app-green", healthy=False, sampled_at=clock.now())
    store.record_utilization(
        host="host",
        app="app",
        cpu_percent=20.0,
        memory_mb=100.0,
        disk_percent=50.0,
        sampled_at=clock.now(),
    )
    store.create_incident(
        app="app",
        service="app-green",
        summary="down",
        details={},
        incident_id="inc-1",
        status="Detected",
    )
    store.update_incident("inc-1", status="Resolved", attempts=1, resolved=True)
    store.record_deployment(
        app="app",
        version="1.4.2",
        slot="green",
        status="success",
        checkpoint_id=None,
        details={"active": True},
    )
    audit.append(
        action="restart_windows_service",
        inputs={"service_name": "app-green"},
        outcome="success",
        checkpoint_id=None,
        approval_ref=None,
        evidence={},
    )
    period = ReportPeriod(
        start=clock.now() - timedelta(days=1),
        end=clock.now() + timedelta(days=4000),
    )
    report = WeeklyReportGenerator(store, audit, clock=clock).generate(
        period=period, applications=["app"]
    )
    assert report.uptime["samples"] == 2
    assert report.uptime["healthy_samples"] == 1
    assert report.incidents["total"] == 1
    assert report.incidents["resolved"] == 1
    assert report.remediations["automatic_attempts"] == 1
    assert report.deployments["successful"] == 1
    assert report.utilization["app"]["cpu_percent"]["avg"] == 20.0
    assert report.audit["total"] == 1
    markdown = report.render_markdown()
    assert "# OpsPilot Weekly Operational Report" in markdown
    assert "app" in markdown


def test_weekly_report_writes_markdown_and_json(tmp_path, store, audit, clock) -> None:
    period = ReportPeriod(start=clock.now() - timedelta(days=1), end=clock.now())
    target = tmp_path / "reports" / "weekly.md"
    generator = WeeklyReportGenerator(store, audit, clock=clock)
    written = generator.write(target, period=period)
    assert written == target
    assert target.read_text(encoding="utf-8").startswith("# OpsPilot Weekly")
    json_path = target.with_suffix(".json")
    assert json_path.exists()
    assert '"period"' in json_path.read_text(encoding="utf-8")
