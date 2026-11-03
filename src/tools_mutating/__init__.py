"""COMP-03 public surface."""

from tools_mutating.baseline import ApplyHardeningTool
from tools_mutating.container import DeployWindowsContainerTool
from tools_mutating.firewall import ApplyFirewallRuleTool
from tools_mutating.proxy import ConfigureReverseProxyTool
from tools_mutating.registry import MutatingToolRegistry
from tools_mutating.service import (
    DeployWindowsServiceTool,
    RemoveWindowsServiceTool,
    RestartWindowsServiceTool,
)

__all__ = [
    "ApplyFirewallRuleTool",
    "ApplyHardeningTool",
    "ConfigureReverseProxyTool",
    "DeployWindowsContainerTool",
    "DeployWindowsServiceTool",
    "MutatingToolRegistry",
    "RemoveWindowsServiceTool",
    "RestartWindowsServiceTool",
]
