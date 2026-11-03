"""OpsPilot control-plane CLI (COMP-01).

Safety defaults: provisioning only plans unless ``--apply`` is explicit, and a
deployment only cuts traffic over with an approval reference for the
externally-visible Caddy change.
"""

from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Sequence
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from checkpoint_store.rollback import RollbackManager
from common.config import REPO_ROOT, canonical_json, load_config
from common.contracts import ToolContext
from common.errors import ApprovalRequired, OpsPilotError
from common.platform import require_windows
from dashboard_ui.config import DashboardConfig
from deployment_engine.engine import CaddyProxyCutover, DeploymentEngine
from deployment_engine.health import HealthChecker
from deployment_engine.models import DeploymentConfig
from incident_engine.drift import DriftDetector, PowerShellBaselineProvider
from orchestrator.runtime import Runtime, build_runtime
from orchestrator.settings import default_data_dir
from provisioning_engine.engine import ProvisioningEngine
from reporting.weekly import ReportPeriod, WeeklyReportGenerator
from tools_mutating.service import RestartWindowsServiceTool

DEFAULT_CONFIG_DIR = REPO_ROOT / "config" / "defaults"


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="opspilot", description="OpsPilot Cloud control plane"
    )
    parser.add_argument("--data-dir", default=None, help="override runtime data directory")
    parser.add_argument(
        "--config-dir", default=str(DEFAULT_CONFIG_DIR), help="directory of YAML configs"
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    check = subparsers.add_parser("check-config", help="validate all default configs")
    check.add_argument("--config-dir", dest="check_config_dir", default=None)

    provision = subparsers.add_parser("provision", help="plan/apply Terraform host baseline")
    provision.add_argument("--host-config", default=str(DEFAULT_CONFIG_DIR / "host.yaml"))
    provision.add_argument("--state-dir", default=".opspilot-terraform")
    provision.add_argument("--module-dir", default=None)
    provision.add_argument("--apply", action="store_true")
    provision.add_argument("--override", action="append", default=[])

    deploy = subparsers.add_parser("deploy", help="blue/green deploy an application")
    deploy.add_argument(
        "--deployment-config", default=str(DEFAULT_CONFIG_DIR / "deployment.yaml")
    )
    deploy.add_argument("--domain", required=True, help="public hostname for Caddy")
    deploy.add_argument("--email", required=True, help="ACME account email")
    deploy.add_argument("--acme-staging", action="store_true", default=False)
    deploy.add_argument("--cutover-approval-ref", default=None)
    deploy.add_argument("--caddy-config", default=None)
    deploy.add_argument(
        "--dashboard-port",
        type=int,
        default=8090,
        help="loopback port of the COMP-13 Mission Control dashboard",
    )
    deploy.add_argument(
        "--dashboard-path",
        default="/mission-control",
        help="path prefix Caddy routes to the dashboard",
    )

    checkpoint = subparsers.add_parser(
        "checkpoint", help="snapshot a Windows Service before an upgrade"
    )
    checkpoint.add_argument("--service", required=True)
    checkpoint.add_argument("--label", required=True)

    rollback = subparsers.add_parser("rollback", help="restore a checkpoint and verify state")
    rollback.add_argument("--checkpoint-id", required=True)
    rollback.add_argument("--actor", default="human")

    approve = subparsers.add_parser("approve", help="approve a pending gated action")
    approve.add_argument("--request-id", required=True)
    approve.add_argument("--approver", required=True)
    approve.add_argument("--note", default="")
    approve.add_argument("--rollback-plan", default=None)

    reject = subparsers.add_parser("reject", help="reject a pending gated action")
    reject.add_argument("--request-id", required=True)
    reject.add_argument("--approver", required=True)
    reject.add_argument("--note", default="")

    subparsers.add_parser("pending", help="list pending approval requests")

    health = subparsers.add_parser("health-check", help="probe an HTTP health endpoint")
    health.add_argument("--url", required=True)
    health.add_argument("--timeout", type=int, default=5)

    report = subparsers.add_parser("report", help="generate the weekly operational report")
    report.add_argument("--days", type=int, default=7)
    report.add_argument("--out", default="report.md")
    report.add_argument("--app", action="append", default=[])

    drift = subparsers.add_parser("drift", help="compare live baseline with declared intent")
    drift.add_argument("--host-config", default=str(DEFAULT_CONFIG_DIR / "host.yaml"))

    subparsers.add_parser("status", help="show local runtime state counts")

    serve = subparsers.add_parser("serve", help="run the control-plane polling service")
    serve.add_argument(
        "--deployment-config", default=str(DEFAULT_CONFIG_DIR / "deployment.yaml")
    )
    serve.add_argument(
        "--alert-config", default=str(DEFAULT_CONFIG_DIR / "alert-rules.yaml")
    )
    serve.add_argument(
        "--host-config", default=str(DEFAULT_CONFIG_DIR / "host.yaml")
    )

    return parser


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        if args.command == "check-config":
            return _check_config(args)
        if args.command == "provision":
            return _provision(args)
        if args.command == "deploy":
            return _deploy(args)
        if args.command == "checkpoint":
            return _checkpoint(args)
        if args.command == "rollback":
            return _rollback(args)
        if args.command == "approve":
            return _approve(args)
        if args.command == "reject":
            return _reject(args)
        if args.command == "pending":
            return _pending(args)
        if args.command == "health-check":
            return _health(args)
        if args.command == "report":
            return _report(args)
        if args.command == "drift":
            return _drift(args)
        if args.command == "status":
            return _status(args)
        if args.command == "serve":
            return _serve(args)
    except ApprovalRequired as error:
        _print({"status": "approval_required", "request_id": error.request_id})
        return 2
    except OpsPilotError as error:
        _print({"status": "error", **error.to_dict()}, stream=sys.stderr)
        return 1
    except KeyboardInterrupt:
        return 130
    parser.error(f"unknown command: {args.command}")
    return 2


def _runtime(args: argparse.Namespace) -> Runtime:
    return build_runtime(data_dir=args.data_dir, config_dir=args.config_dir)


def _check_config(args: argparse.Namespace) -> int:
    config_dir = Path(args.check_config_dir or args.config_dir)
    data_dir = Path(args.data_dir) if args.data_dir else default_data_dir()
    loaded = {
        "host": load_config(config_dir / "host.yaml", "host-config.schema.json"),
        "deployment": load_config(
            config_dir / "deployment.yaml", "deployment-config.schema.json"
        ),
        "alerts": load_config(config_dir / "alert-rules.yaml", "alert-rules.schema.json"),
        "approval": load_config(
            config_dir / "approval-policy.yaml", "approval-policy.schema.json"
        ),
        "dashboard": DashboardConfig.load(config_dir / "dashboard.yaml", data_dir=data_dir),
    }
    _print({"status": "ok", "validated": sorted(loaded)})
    return 0


def _provision(args: argparse.Namespace) -> int:
    runtime = _runtime(args)
    engine = ProvisioningEngine.from_repo(
        args.state_dir,
        module_dir=args.module_dir,
        events=runtime.events,
    )
    document = engine.load_host_config(args.host_config)
    overrides = _parse_overrides(args.override)
    if args.apply:
        plan, command, outputs = engine.apply(document, overrides=overrides)
        _print(
            {
                "status": "applied",
                "plan": plan.plan.summary(),
                "apply_stdout": command.stdout[-2000:],
                "outputs": outputs,
            }
        )
        return 0
    plan = engine.plan(document, overrides=overrides)
    _print({"status": "planned", **plan.to_dict()})
    return 0


def _deploy(args: argparse.Namespace) -> int:
    require_windows("blue/green deployment")
    runtime = _runtime(args)
    config = DeploymentConfig.load(args.deployment_config)
    proxy = CaddyProxyCutover(
        runtime.registry,
        config_path=args.caddy_config
        or str(runtime.paths.caddy_config),
        hostname=args.domain,
        email=args.email,
        acme_staging=args.acme_staging,
        dashboard_port=args.dashboard_port,
        dashboard_path=args.dashboard_path,
        events=runtime.events,
    )
    engine = DeploymentEngine(
        runtime.registry,
        runtime.store,
        HealthChecker(),
        proxy=proxy,
        events=runtime.events,
    )
    result = engine.deploy(
        config,
        context=ToolContext(environment=config.environment),
        cutover_context=ToolContext(
            environment=config.environment, approval_ref=args.cutover_approval_ref
        ),
    )
    _print(result.to_dict())
    return 0 if result.success else 1


def _checkpoint(args: argparse.Namespace) -> int:
    require_windows("service checkpoint")
    runtime = _runtime(args)
    tool = RestartWindowsServiceTool()
    payload = {"service_name": args.service}
    state = tool.capture_state(payload)
    checkpoint_id = runtime.store.snapshot(
        args.label,
        state,
        metadata={"tool": "restart_windows_service", "payload": payload},
    )
    _print({"status": "created", "checkpoint_id": checkpoint_id, "label": args.label})
    return 0


def _rollback(args: argparse.Namespace) -> int:
    require_windows("checkpoint rollback")
    runtime = _runtime(args)
    manager = RollbackManager(runtime.store, runtime.registry)
    result = manager.rollback(args.checkpoint_id, actor=args.actor)
    _print(result.to_dict())
    return 0 if result.restored else 1


def _approve(args: argparse.Namespace) -> int:
    runtime = _runtime(args)
    request = runtime.gate.approve(
        args.request_id,
        approver=args.approver,
        note=args.note,
        rollback_plan=args.rollback_plan,
    )
    _print(request.to_dict())
    return 0


def _reject(args: argparse.Namespace) -> int:
    runtime = _runtime(args)
    request = runtime.gate.reject(
        args.request_id, approver=args.approver, note=args.note
    )
    _print(request.to_dict())
    return 0


def _pending(args: argparse.Namespace) -> int:
    runtime = _runtime(args)
    _print({"pending": [request.to_dict() for request in runtime.gate.pending()]})
    return 0


def _health(args: argparse.Namespace) -> int:
    from deployment_engine.models import HealthTarget

    checker = HealthChecker()
    target = HealthTarget(kind="http", url=args.url, timeout_seconds=args.timeout)
    healthy, error = checker.check(target)
    _print({"healthy": healthy, "error": error, "url": args.url})
    return 0 if healthy else 1


def _report(args: argparse.Namespace) -> int:
    runtime = _runtime(args)
    days = max(1, int(args.days))
    end = datetime.now(UTC)
    period = ReportPeriod(start=end - timedelta(days=days), end=end)
    generator = WeeklyReportGenerator(runtime.store, runtime.audit, events=runtime.events)
    path = generator.write(args.out, period=period, applications=args.app)
    _print({"status": "written", "path": str(path)})
    return 0


def _drift(args: argparse.Namespace) -> int:
    require_windows("baseline drift detection")
    document = load_config(args.host_config, "host-config.schema.json")
    report = DriftDetector(PowerShellBaselineProvider()).check(document)
    _print(report.to_dict())
    return 0 if report.compliant else 1


def _status(args: argparse.Namespace) -> int:
    runtime = _runtime(args)
    _print(
        {
            "data_dir": str(runtime.paths.data_dir),
            "checkpoints": len(runtime.store.list_checkpoints()),
            "incidents": len(runtime.store.list_incidents()),
            "pending_approvals": len(runtime.gate.pending()),
            "tools": runtime.registry.names(),
        }
    )
    return 0


def _serve(args: argparse.Namespace) -> int:
    from orchestrator.control_plane import serve

    return serve(args)


def _parse_overrides(values: list[str]) -> dict[str, Any]:
    overrides: dict[str, Any] = {}
    for value in values:
        if "=" not in value:
            raise OpsPilotError(f"--override must be key=value, got {value!r}")
        key, raw = value.split("=", 1)
        try:
            parsed = json.loads(raw)
        except json.JSONDecodeError:
            parsed = raw
        overrides[key] = parsed
    return overrides


def _print(payload: dict[str, Any], *, stream: Any = None) -> None:
    (stream or sys.stdout).write(canonical_json(payload) + "\n")


if __name__ == "__main__":
    raise SystemExit(main())
