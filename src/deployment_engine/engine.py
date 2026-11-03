"""Blue/green deployment engine (COMP-08, FR-012/FR-013).

Sequence (SAS.md §7):
1. start the candidate version in the inactive slot, alongside the old one;
2. health-check the candidate before any traffic moves;
3. cut traffic over through COMP-10 (an externally-visible action, so it
   carries its own approval context);
4. keep the previous slot running for fast rollback;
5. on any failure, restore the candidate's checkpoint and, if cutover already
   happened, the proxy's checkpoint too.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any, Protocol

from checkpoint_store.rollback import RollbackManager
from checkpoint_store.store import CheckpointStore
from common.clock import Clock, SystemClock
from common.contracts import ToolContext, ToolResult
from common.errors import ApprovalRequired, PreconditionFailed
from common.log import StructuredLogger
from deployment_engine.health import HealthChecker
from deployment_engine.models import DeploymentConfig, DeploymentResult
from event_bus.publisher import EventPublisher, NullEventPublisher
from reverse_proxy.generator import CaddyConfig, ProxyRoute
from tools_mutating.registry import MutatingToolRegistry


class ProxyCutover(Protocol):
    def cutover(
        self, *, port: int, context: ToolContext, rollback_plan: str
    ) -> ToolResult: ...


class CaddyProxyCutover:
    """Cut traffic over by asking COMP-03 to reload Caddy.

    The Mission Control dashboard (COMP-13) is mounted on the same hostname at
    ``dashboard_path`` when ``dashboard_port`` is supplied. It is a separate
    service (NFR-014), so its route points at its own loopback upstream and
    streams SSE without buffering.
    """

    def __init__(
        self,
        registry: MutatingToolRegistry,
        *,
        config_path: str,
        hostname: str,
        email: str,
        acme_staging: bool = True,
        service_name: str = "caddy",
        dashboard_port: int | None = 8090,
        dashboard_path: str = "/mission-control",
        events: EventPublisher | None = None,
    ) -> None:
        self._registry = registry
        self._config_path = config_path
        self._hostname = hostname
        self._email = email
        self._acme_staging = acme_staging
        self._service_name = service_name
        self._dashboard_port = dashboard_port
        self._dashboard_path = dashboard_path
        self._events = events or NullEventPublisher()

    def routes(self, port: int) -> tuple[ProxyRoute, ...]:
        routes = [ProxyRoute(hostname=self._hostname, upstream_port=port, tls=True)]
        if self._dashboard_port is not None:
            routes.append(
                ProxyRoute(
                    hostname=self._hostname,
                    upstream_port=self._dashboard_port,
                    tls=True,
                    path_prefix=self._dashboard_path,
                    streaming=True,
                )
            )
        return tuple(routes)

    def caddyfile(self, port: int) -> str:
        config = CaddyConfig(
            email=self._email, routes=self.routes(port), acme_staging=self._acme_staging
        )
        return config.render()

    def cutover(
        self, *, port: int, context: ToolContext, rollback_plan: str
    ) -> ToolResult:
        result = self._registry.execute(
            "configure_reverse_proxy",
            {
                "config_path": self._config_path,
                "caddyfile": self.caddyfile(port),
                "service_name": self._service_name,
            },
            context=context,
            rollback_plan=rollback_plan,
        )
        self._events.publish(
            "proxy.applied" if result.status == "success" else "proxy.failed",
            component="COMP-10",
            message=(
                f"Caddy cutover applied on port {port}"
                if result.status == "success"
                else f"Caddy cutover failed on port {port}"
            ),
            data={
                "hostname": self._hostname,
                "port": port,
                "dashboard_port": self._dashboard_port,
                "dashboard_path": self._dashboard_path
                if self._dashboard_port is not None
                else None,
                "status": result.status,
                "checkpoint_id": result.checkpoint_id,
            },
            level="INFO" if result.status == "success" else "ERROR",
        )
        return result


class DeploymentEngine:
    def __init__(
        self,
        registry: MutatingToolRegistry,
        store: CheckpointStore,
        health_checker: HealthChecker,
        *,
        proxy: ProxyCutover | None = None,
        clock: Clock | None = None,
        logger: StructuredLogger | None = None,
        events: EventPublisher | None = None,
    ) -> None:
        self._registry = registry
        self._store = store
        self._health = health_checker
        self._proxy = proxy
        self._clock = clock or SystemClock()
        self._logger = logger or StructuredLogger("COMP-08")
        self._rollback = RollbackManager(store, registry)
        self._events = events or NullEventPublisher()

    def active_slot(self, app: str) -> str | None:
        for record in self._store.list_deployments(app):
            details = record.get("details", {})
            if record.get("status") == "success" and details.get("active") is True:
                return str(record.get("slot"))
        return None

    def deploy(
        self,
        config: DeploymentConfig,
        *,
        context: ToolContext | None = None,
        cutover_context: ToolContext | None = None,
        rollback_plan: str = "",
    ) -> DeploymentResult:
        if config.rollout.strategy != "blue_green":
            raise PreconditionFailed(
                f"unsupported rollout strategy: {config.rollout.strategy}"
            )
        tool_context = context or ToolContext(environment=config.environment)
        previous_slot = self.active_slot(config.name)
        slot = "green" if previous_slot in (None, "blue") else "blue"
        port = config.slot_port(slot)
        result = DeploymentResult(
            app=config.name,
            version=config.version,
            active_slot=previous_slot or "none",
            previous_slot=previous_slot,
            status="in_progress",
            evidence={"candidate_slot": slot, "candidate_port": port},
        )
        self._events.publish(
            "deployment.started",
            component="COMP-08",
            message=f"deployment started: {config.name} {config.version} → {slot}",
            data={
                "app": config.name,
                "version": config.version,
                "slot": slot,
                "port": port,
                "previous_slot": previous_slot,
                "phase": "candidate_start",
                "status": "in_progress",
            },
        )

        payload = self._deployment_payload(config, slot, port)
        tool_name = (
            "deploy_windows_service"
            if config.package_type == "windows_service"
            else "deploy_windows_container"
        )
        plan = rollback_plan or (
            f"stop and remove candidate slot {config.service_name(slot)}; "
            "restore the prior proxy configuration if cutover occurred"
        )

        try:
            deployment = self._registry.execute(
                tool_name, payload, context=tool_context, rollback_plan=plan
            )
        except ApprovalRequired as error:
            self._record(config, slot, "blocked", None, result.evidence)
            result.status = "blocked"
            result.evidence["approval_request_id"] = error.request_id
            raise

        result.candidate_checkpoint_id = deployment.checkpoint_id
        result.evidence["deployment_tool"] = tool_name
        result.evidence["deployment_result"] = deployment.to_dict()
        if deployment.status != "success":
            result.status = "failed" if deployment.status == "failed" else "rolled_back"
            self._record(
                config, slot, result.status, deployment.checkpoint_id, result.evidence
            )
            return result
        self._events.publish(
            "deployment.phase",
            component="COMP-08",
            message=f"candidate slot {slot} started; verifying health before cutover",
            data={
                "app": config.name,
                "version": config.version,
                "slot": slot,
                "phase": "candidate_health_check",
                "status": "in_progress",
            },
        )

        health = self._health.wait(
            config.health_check,
            port=port,
            window_seconds=config.rollout.verification_window_seconds,
        )
        result.health = health.to_dict()
        if not health.healthy:
            result.status = self._rollback_candidate(
                result, "candidate health check failed before cutover"
            )
            self._record(
                config, slot, result.status, result.candidate_checkpoint_id, result.evidence
            )
            return result

        if self._proxy is None:
            result.evidence["cutover"] = "skipped: no proxy configured"
        else:
            self._events.publish(
                "deployment.phase",
                component="COMP-08",
                message=f"cutting traffic over to slot {slot}",
                data={
                    "app": config.name,
                    "version": config.version,
                    "slot": slot,
                    "phase": "cutover",
                    "status": "in_progress",
                },
            )
            try:
                cutover = self._proxy.cutover(
                    port=port,
                    context=cutover_context or tool_context,
                    rollback_plan=plan,
                )
            except ApprovalRequired as error:
                result.status = self._rollback_candidate(
                    result, "proxy cutover requires approval that was not supplied"
                )
                result.evidence["approval_request_id"] = error.request_id
                self._record(
                    config, slot, result.status, result.candidate_checkpoint_id, result.evidence
                )
                raise
            except Exception as error:
                result.status = self._rollback_candidate(
                    result, f"proxy cutover failed: {error}"
                )
                self._record(
                    config, slot, result.status, result.candidate_checkpoint_id, result.evidence
                )
                return result

            result.proxy_checkpoint_id = cutover.checkpoint_id
            result.evidence["cutover"] = cutover.to_dict()
            if cutover.status != "success":
                result.status = self._rollback_candidate(
                    result, "proxy cutover returned a non-success status"
                )
                self._record(
                    config, slot, result.status, result.candidate_checkpoint_id, result.evidence
                )
                return result

        active_health = self._health.wait(
            config.health_check,
            port=port,
            window_seconds=config.rollout.verification_window_seconds,
        )
        result.health["post_cutover"] = active_health.to_dict()
        if not active_health.healthy:
            result.status = self._rollback_after_cutover(
                result, "post-cutover health check failed"
            )
            self._record(
                config, slot, result.status, result.candidate_checkpoint_id, result.evidence
            )
            return result

        result.status = "success"
        result.active_slot = slot
        result.evidence["stability_window_seconds"] = config.rollout.stability_window_seconds
        result.evidence["previous_slot_retained"] = previous_slot
        self._record(
            config, slot, "success", result.candidate_checkpoint_id, result.evidence, active=True
        )
        return result

    def _deployment_payload(
        self, config: DeploymentConfig, slot: str, port: int
    ) -> dict[str, Any]:
        if config.package_type == "windows_service":
            if not config.executable or not config.service_account:
                raise PreconditionFailed(
                    "windows_service deployment requires executable and service_account"
                )
            return {
                "service_name": config.service_name(slot),
                "package_path": config.package_path,
                "executable": config.executable,
                "version": config.version,
                "arguments": _arguments_for_port(
                    config.arguments, config.ports[0], port
                ),
                "service_account": config.service_account,
            }
        if not config.container_image or config.container_port is None:
            raise PreconditionFailed("windows_container deployment is missing image/port")
        return {
            "container_name": config.container_name(slot),
            "image": config.container_image,
            "host_port": port,
            "container_port": config.container_port,
        }

    def _rollback_candidate(self, result: DeploymentResult, reason: str) -> str:
        result.evidence["failure_reason"] = reason
        if not result.candidate_checkpoint_id:
            return "failed"
        try:
            rollback = self._rollback.rollback(result.candidate_checkpoint_id)
            result.evidence["candidate_rollback"] = rollback.to_dict()
            return "rolled_back"
        except Exception as error:
            result.evidence["candidate_rollback_error"] = str(error)
            self._logger.critical(
                "deployment rollback failed", app=result.app, error=str(error)
            )
            return "failed"

    def _rollback_after_cutover(self, result: DeploymentResult, reason: str) -> str:
        result.evidence["failure_reason"] = reason
        failure = False
        if result.proxy_checkpoint_id:
            try:
                rollback = self._rollback.rollback(result.proxy_checkpoint_id)
                result.evidence["proxy_rollback"] = rollback.to_dict()
            except Exception as error:
                failure = True
                result.evidence["proxy_rollback_error"] = str(error)
        candidate_status = self._rollback_candidate(result, reason)
        if failure:
            return "failed"
        return candidate_status

    def _record(
        self,
        config: DeploymentConfig,
        slot: str,
        status: str,
        checkpoint_id: str | None,
        evidence: Mapping[str, Any],
        *,
        active: bool = False,
    ) -> None:
        details = dict(evidence)
        details["active"] = active
        self._store.record_deployment(
            app=config.name,
            version=config.version,
            slot=slot,
            status=status,
            checkpoint_id=checkpoint_id,
            details=details,
        )
        event_type = {
            "success": "deployment.completed",
            "failed": "deployment.failed",
            "rolled_back": "deployment.rolled_back",
            "blocked": "deployment.phase",
        }.get(status, "deployment.phase")
        self._events.publish(
            event_type,
            component="COMP-08",
            message=f"deployment {status}: {config.name} {config.version} ({slot})",
            data={
                "app": config.name,
                "version": config.version,
                "slot": slot,
                "status": status,
                "checkpoint_id": checkpoint_id,
                "failure_reason": evidence.get("failure_reason"),
            },
            level="ERROR"
            if status == "failed"
            else ("WARN" if status in {"rolled_back", "blocked"} else "INFO"),
        )


def _arguments_for_port(
    arguments: tuple[str, ...], base_port: int, slot_port: int
) -> list[str]:
    rendered: list[str] = []
    for argument in arguments:
        if "{port}" in argument:
            rendered.append(argument.replace("{port}", str(slot_port)))
        elif argument == str(base_port):
            rendered.append(str(slot_port))
        else:
            rendered.append(argument)
    return rendered
