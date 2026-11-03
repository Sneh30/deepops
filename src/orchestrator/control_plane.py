"""Long-running control-plane service (COMP-01/06/07).

Install.ps1 registers this module as the ``OpsPilotControlPlane`` Windows
Service. It owns the health poll, remediation loop, utilization sampler, and
weekly report scheduler.
"""

from __future__ import annotations

import argparse
import os
import platform
import threading
from datetime import UTC, datetime, timedelta
from pathlib import Path

import psutil

from common.config import load_config
from common.log import StructuredLogger
from common.platform import require_windows
from deployment_engine.health import HealthChecker
from deployment_engine.models import DeploymentConfig
from event_bus.publisher import EventPublisher
from incident_engine.alerts import AlertEvaluator, MetricSnapshot
from incident_engine.engine import IncidentEngine
from incident_engine.escalation import EscalationNotifier, NotificationConfig
from incident_engine.health import HealthPoller, HealthTargetSpec, PollResult
from incident_engine.remediation import RemediationExecutor
from observability.utilization import PsutilUtilizationProvider, UtilizationRecorder
from orchestrator.runtime import Runtime, build_runtime
from orchestrator.settings import default_config_dir
from reporting.weekly import ReportPeriod, WeeklyReportGenerator
from secrets_manager.dpapi import DpapiSecretsManager


def serve(args: argparse.Namespace) -> int:
    require_windows("control-plane service")
    runtime = _runtime(args)
    logger = StructuredLogger("COMP-01")
    events = runtime.events
    deployment = DeploymentConfig.load(args.deployment_config)
    alert_document = load_config(args.alert_config, "alert-rules.schema.json")
    notifier = EscalationNotifier(
        NotificationConfig.from_document(alert_document),
        credential_provider=DpapiSecretsManager(
            root_path=str(runtime.paths.secrets_root), events=events
        ).get,
    )
    active_slot = _active_slot(runtime)
    port = deployment.slot_port(active_slot)
    target = HealthTargetSpec(
        name=f"{deployment.name}-{active_slot}",
        app=deployment.name,
        service=deployment.service_name(active_slot),
        health=deployment.health_check,
        port=port,
        failure_threshold=max(
            deployment.health_check.failure_threshold,
            deployment.rollout.max_failed_health_checks,
        ),
    )
    checker = HealthChecker()
    poller = HealthPoller(runtime.store, [target], checker, events=events)
    remediator = RemediationExecutor(runtime.registry, checker)
    incidents = IncidentEngine(
        runtime.store,
        poller,
        remediator,
        notifier=notifier,
        audit=runtime.audit,
        events=events,
    )
    alerts = AlertEvaluator.from_config(args.alert_config)
    utilization = UtilizationRecorder(
        runtime.store,
        PsutilUtilizationProvider(
            host=platform.node(),
            disk_path=_system_drive(),
            process_names={deployment.name: deployment.executable or deployment.name},
        ),
        [deployment.name],
        events=events,
    )
    stop = threading.Event()
    started_at = datetime.now(UTC)
    threads = [
        threading.Thread(
            target=_health_loop,
            args=(poller, incidents, alerts, logger, stop, events),
            name="health-poll",
            daemon=True,
        ),
        threading.Thread(
            target=utilization.run, args=(stop,), name="utilization", daemon=True
        ),
        threading.Thread(
            target=_report_loop,
            args=(runtime, deployment, stop),
            name="weekly-report",
            daemon=True,
        ),
        threading.Thread(
            target=_heartbeat_loop,
            args=(events, stop, started_at, target, active_slot),
            name="heartbeat",
            daemon=True,
        ),
    ]
    events.publish(
        "control.started",
        component="COMP-01",
        message=f"control plane started; monitoring {target.service}",
        data={
            "service": target.service,
            "app": deployment.name,
            "active_slot": active_slot,
            "host": platform.node(),
            "pid": os.getpid(),
        },
    )
    for thread in threads:
        thread.start()
    logger.info(
        "control plane started",
        service=target.service,
        poll_interval_seconds=poller.interval_seconds,
        active_slot=active_slot,
    )
    try:
        while not stop.wait(1):
            pass
    except KeyboardInterrupt:
        stop.set()
    finally:
        stop.set()
        for thread in threads:
            thread.join(timeout=10)
        events.publish(
            "control.stopped",
            component="COMP-01",
            message="control plane stopped",
            data={
                "service": target.service,
                "app": deployment.name,
                "uptime_seconds": round((datetime.now(UTC) - started_at).total_seconds(), 1),
            },
            level="WARN",
        )
    return 0


def _heartbeat_loop(
    events: EventPublisher,
    stop: threading.Event,
    started_at: datetime,
    target: HealthTargetSpec,
    active_slot: str,
) -> None:
    """Publish a real liveness beat every 5s; the dashboard shows no timers."""
    while not stop.is_set():
        if stop.wait(5):
            return
        events.publish(
            "system.heartbeat",
            component="COMP-01",
            message="control plane heartbeat",
            data={
                "service": target.service,
                "app": target.app,
                "active_slot": active_slot,
                "uptime_seconds": round(
                    (datetime.now(UTC) - started_at).total_seconds(), 1
                ),
                "pid": os.getpid(),
            },
        )


def _health_loop(
    poller: HealthPoller,
    incidents: IncidentEngine,
    alerts: AlertEvaluator,
    logger: StructuredLogger,
    stop: threading.Event,
    events: EventPublisher,
) -> None:
    while not stop.is_set():
        results = poller.poll_once()
        for result in results:
            if result.triggered:
                events.publish(
                    "agent.thinking",
                    component="COMP-01",
                    message=(
                        f"threshold crossed for {result.target.service}: "
                        f"{result.consecutive_failures} consecutive failures"
                    ),
                    data={
                        "observation": "health_threshold_crossed",
                        "service": result.target.service,
                        "consecutive_failures": result.consecutive_failures,
                        "error": result.error,
                    },
                    level="WARN",
                )
                events.publish(
                    "agent.decision",
                    component="COMP-01",
                    message=f"decision: attempt automatic remediation for {result.target.service}",
                    data={
                        "decision": "attempt_remediation",
                        "service": result.target.service,
                        "rationale": "health failure threshold reached",
                    },
                )
                incident = incidents.handle_failure(result)
                events.publish(
                    "agent.result",
                    component="COMP-01",
                    message=f"remediation outcome for {result.target.service}: {incident.status}",
                    data={
                        "incident_id": incident.id,
                        "service": result.target.service,
                        "status": incident.status,
                        "attempt_count": incident.attempts,
                    },
                    level="INFO" if incident.status == "Resolved" else "WARN",
                )
        for alert in alerts.evaluate(_snapshot(_metric_values(results))):
            logger.warning(
                "alert firing",
                alert=alert.name,
                severity=alert.severity,
                metric=alert.metric,
                value=alert.value,
                threshold=alert.threshold,
            )
            events.publish(
                "alert.firing",
                component="COMP-07",
                message=f"alert firing: {alert.name} ({alert.metric}={alert.value})",
                data={
                    "alert": alert.name,
                    "severity": alert.severity,
                    "metric": alert.metric,
                    "value": alert.value,
                    "threshold": alert.threshold,
                },
                level="ERROR" if alert.severity in {"critical", "error"} else "WARN",
            )
        stop.wait(poller.interval_seconds)


def _metric_values(results: list[PollResult]) -> dict[str, float]:
    memory = psutil.virtual_memory()
    disk = psutil.disk_usage(_system_drive())
    values = {
        "cpu_percent": float(psutil.cpu_percent(interval=None)),
        "memory_percent": float(memory.percent),
        "disk_percent": float(disk.percent),
    }
    for result in results:
        values["service_up"] = 1.0 if result.healthy else 0.0
        values["health_check_failed"] = 0.0 if result.healthy else 1.0
    return values


def _snapshot(values: dict[str, float]) -> MetricSnapshot:
    return MetricSnapshot(values=values)


def _report_loop(runtime: Runtime, deployment: DeploymentConfig, stop: threading.Event) -> None:
    generator = WeeklyReportGenerator(runtime.store, runtime.audit, events=runtime.events)
    interval = 7 * 24 * 60 * 60
    while not stop.is_set():
        # Sleep first so a restart does not regenerate an unchanged report.
        if stop.wait(interval):
            return
        end = datetime.now(UTC)
        period = ReportPeriod(start=end - timedelta(days=7), end=end)
        destination = runtime.paths.data_dir / "reports" / "weekly.md"
        generator.write(destination, period=period, applications=[deployment.name])


def _active_slot(runtime: Runtime) -> str:
    for record in runtime.store.list_deployments():
        details = record.get("details", {})
        if record.get("status") == "success" and details.get("active") is True:
            return str(record.get("slot"))
    return "blue"


def _system_drive() -> str:

    return os.environ.get("SYSTEMDRIVE", "C:") + "\\"


def _runtime(args: argparse.Namespace) -> Runtime:
    return build_runtime(data_dir=args.data_dir, config_dir=args.config_dir)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="opspilot-control-plane",
        description="Run the OpsPilot control-plane polling service.",
    )
    parser.add_argument("--data-dir", default=None)
    parser.add_argument("--config-dir", default=None)
    parser.add_argument(
        "--deployment-config", default=None, help="deployment YAML path"
    )
    parser.add_argument("--alert-config", default=None, help="alert-rules YAML path")
    parser.add_argument("--host-config", default=None, help="host YAML path")
    args = parser.parse_args(argv)
    config_dir = args.config_dir or str(default_config_dir())
    if args.deployment_config is None:
        args.deployment_config = str(Path(config_dir) / "deployment.yaml")
    if args.alert_config is None:
        args.alert_config = str(Path(config_dir) / "alert-rules.yaml")
    if args.host_config is None:
        args.host_config = str(Path(config_dir) / "host.yaml")
    return serve(args)


if __name__ == "__main__":
    raise SystemExit(main())
