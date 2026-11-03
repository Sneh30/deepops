"""COMP-08 deployment engine: blue/green rollout and Windows image definitions."""

from deployment_engine.dockerfile import WindowsImageSpec, spec_from_deployment
from deployment_engine.engine import CaddyProxyCutover, DeploymentEngine, ProxyCutover
from deployment_engine.health import HealthChecker, HealthOutcome
from deployment_engine.models import (
    DeploymentConfig,
    DeploymentResult,
    HealthTarget,
    RolloutConfig,
)

__all__ = [
    "CaddyProxyCutover",
    "DeploymentConfig",
    "DeploymentEngine",
    "DeploymentResult",
    "HealthChecker",
    "HealthOutcome",
    "HealthTarget",
    "ProxyCutover",
    "RolloutConfig",
    "WindowsImageSpec",
    "spec_from_deployment",
]
