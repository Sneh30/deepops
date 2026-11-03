"""Deployment configuration and result models (COMP-08)."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from common.config import load_config
from common.errors import PreconditionFailed


@dataclass(frozen=True)
class HealthTarget:
    kind: str
    url: str | None = None
    host: str | None = None
    port: int | None = None
    process_name: str | None = None
    interval_seconds: int = 15
    timeout_seconds: int = 5
    success_threshold: int = 1
    failure_threshold: int = 3


@dataclass(frozen=True)
class RolloutConfig:
    strategy: str
    verification_window_seconds: int
    stability_window_seconds: int
    max_failed_health_checks: int
    blue_port: int | None = None
    green_port: int | None = None


@dataclass(frozen=True)
class DeploymentConfig:
    name: str
    package_type: str
    package_path: str
    version: str
    environment: str
    ports: tuple[int, ...]
    health_check: HealthTarget
    rollout: RolloutConfig
    service_account: str | None = None
    executable: str | None = None
    arguments: tuple[str, ...] = ()
    container_image: str | None = None
    container_port: int | None = None
    env: Mapping[str, str] = field(default_factory=dict)

    @classmethod
    def from_document(cls, document: Mapping[str, Any]) -> DeploymentConfig:
        app = document.get("app")
        rollout = document.get("rollout")
        if not isinstance(app, Mapping) or not isinstance(rollout, Mapping):
            raise PreconditionFailed("deployment config requires app and rollout objects")
        health = app.get("health_check")
        if not isinstance(health, Mapping):
            raise PreconditionFailed("deployment config requires app.health_check")
        container = app.get("container")
        if container is not None and not isinstance(container, Mapping):
            raise PreconditionFailed("app.container must be an object")
        package_type = str(app["package_type"])
        if package_type == "windows_service" and not app.get("service_account"):
            raise PreconditionFailed(
                "windows_service deployments require an explicit least-privilege service_account"
            )
        if package_type == "windows_container" and not container:
            raise PreconditionFailed("windows_container deployments require app.container")
        return cls(
            name=str(app["name"]),
            package_type=package_type,
            package_path=str(app["package_path"]),
            version=str(app["version"]),
            environment=str(app["environment"]),
            ports=tuple(int(port) for port in app["ports"]),
            health_check=HealthTarget(
                kind=str(health["type"]),
                url=str(health["url"]) if health.get("url") else None,
                host=str(health.get("host", "127.0.0.1")),
                port=int(health["port"]) if health.get("port") else None,
                process_name=str(health["process_name"])
                if health.get("process_name")
                else None,
                interval_seconds=int(health.get("interval_seconds", 15)),
                timeout_seconds=int(health.get("timeout_seconds", 5)),
                success_threshold=int(health.get("success_threshold", 1)),
                failure_threshold=int(health.get("failure_threshold", 3)),
            ),
            rollout=RolloutConfig(
                strategy=str(rollout["strategy"]),
                verification_window_seconds=int(rollout["verification_window_seconds"]),
                stability_window_seconds=int(rollout["stability_window_seconds"]),
                max_failed_health_checks=int(rollout["max_failed_health_checks"]),
                blue_port=int(rollout["blue_port"]) if rollout.get("blue_port") else None,
                green_port=int(rollout["green_port"]) if rollout.get("green_port") else None,
            ),
            service_account=str(app["service_account"]) if app.get("service_account") else None,
            executable=str(app["executable"]) if app.get("executable") else None,
            arguments=tuple(str(argument) for argument in app.get("arguments", [])),
            container_image=str(container["image"]) if container else None,
            container_port=int(container["container_port"]) if container else None,
            env={str(key): str(value) for key, value in dict(app.get("env", {})).items()},
        )

    @classmethod
    def load(cls, path: str | Path) -> DeploymentConfig:
        return cls.from_document(load_config(path, "deployment-config.schema.json"))

    def slot_port(self, slot: str) -> int:
        if slot == "blue":
            if self.rollout.blue_port is not None:
                return self.rollout.blue_port
            return self.ports[0]
        if slot == "green":
            if self.rollout.green_port is not None:
                return self.rollout.green_port
            candidate = self.ports[0] + 1
            if candidate > 65535:
                raise PreconditionFailed(
                    "cannot allocate a green port above 65535; set rollout.green_port"
                )
            return candidate
        raise PreconditionFailed(f"unknown deployment slot: {slot!r}")

    def service_name(self, slot: str) -> str:
        return f"{self.name}-{slot}"

    def container_name(self, slot: str) -> str:
        return f"{self.name}-{slot}"


@dataclass
class DeploymentResult:
    app: str
    version: str
    active_slot: str
    previous_slot: str | None
    status: str
    candidate_checkpoint_id: str | None = None
    proxy_checkpoint_id: str | None = None
    health: dict[str, Any] = field(default_factory=dict)
    evidence: dict[str, Any] = field(default_factory=dict)

    @property
    def success(self) -> bool:
        return self.status == "success"

    def to_dict(self) -> dict[str, Any]:
        return {
            "app": self.app,
            "version": self.version,
            "active_slot": self.active_slot,
            "previous_slot": self.previous_slot,
            "status": self.status,
            "candidate_checkpoint_id": self.candidate_checkpoint_id,
            "proxy_checkpoint_id": self.proxy_checkpoint_id,
            "health": self.health,
            "evidence": self.evidence,
        }
