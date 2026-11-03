"""Security-sensitive firewall rule tool (COMP-09, FR-060).

This is the canonical approval-gate exercise tool: category
``security_sensitive`` always requires a human approval reference.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from common.contracts import MutatingTool, ToolResult, mutating_contract, result_schema
from common.errors import PreconditionFailed, VerificationFailed
from common.process import ps_quote
from tools_mutating.base import (
    PowerShellTool,
    encode_state,
    json_pipeline,
    module_script,
)


class ApplyFirewallRuleTool(PowerShellTool, MutatingTool):
    def __init__(self, runner: Any = None) -> None:
        self._runner = runner
        self.contract = mutating_contract(
            name="apply_firewall_rule",
            description=(
                "Create or update a named Windows Firewall rule. Security-sensitive: "
                "requires human approval."
            ),
            input_schema={
                "type": "object",
                "additionalProperties": False,
                "required": ["rule_name", "direction", "action", "protocol", "local_port"],
                "properties": {
                    "rule_name": {"type": "string", "pattern": "^[A-Za-z0-9][A-Za-z0-9 _.-]{0,127}$"},
                    "direction": {"enum": ["Inbound", "Outbound"]},
                    "action": {"enum": ["Allow", "Block"]},
                    "protocol": {"enum": ["TCP", "UDP"]},
                    "local_port": {"type": "string", "pattern": "^[0-9,-]{1,64}$"},
                    "remote_address": {"type": "string", "default": "Any"},
                    "profile": {"enum": ["Any", "Domain", "Private", "Public"], "default": "Any"},
                },
            },
            output_schema=result_schema(
                {
                    "rule_name": {"type": "string"},
                    "direction": {"type": "string"},
                    "action": {"type": "string"},
                    "local_port": {"type": "string"},
                    "enabled": {"type": "boolean"},
                }
            ),
            timeout_seconds=90,
            risk_categories=("security_sensitive",),
            verification=("Get-NetFirewallRule shows the rule enabled with expected action",),
        )

    def preview(self, payload: Mapping[str, Any]) -> ToolResult:
        return ToolResult(
            status="preview",
            output={
                "rule_name": str(payload["rule_name"]),
                "direction": str(payload["direction"]),
                "action": str(payload["action"]),
                "local_port": str(payload["local_port"]),
                "enabled": False,
            },
            evidence={
                "would_create_or_update_rule": True,
                "remote_address": payload.get("remote_address", "Any"),
                "profile": payload.get("profile", "Any"),
            },
        )

    def _state(self, rule_name: str) -> dict[str, Any]:
        output = self.run_json(
            "OpsPilot.Firewall",
            json_pipeline(f"Get-OpsPilotFirewallRule -Name {ps_quote(rule_name)}"),
            self.contract.timeout_seconds,
        )
        if not isinstance(output, dict):
            raise PreconditionFailed("firewall state did not return a JSON object")
        return output

    def capture_state(self, payload: Mapping[str, Any]) -> dict[str, Any]:
        return {"rule": self._state(str(payload["rule_name"]))}

    def apply(self, payload: Mapping[str, Any]) -> ToolResult:
        body = json_pipeline(
            "New-OpsPilotFirewallRule "
            f"-Name {ps_quote(str(payload['rule_name']))} "
            f"-Direction {ps_quote(str(payload['direction']))} "
            f"-Action {ps_quote(str(payload['action']))} "
            f"-Protocol {ps_quote(str(payload['protocol']))} "
            f"-LocalPort {ps_quote(str(payload['local_port']))} "
            f"-RemoteAddress {ps_quote(str(payload.get('remote_address', 'Any')))} "
            f"-Profile {ps_quote(str(payload.get('profile', 'Any')))}"
        )
        output = self.runner().run_json(
            module_script("OpsPilot.Firewall", body),
            timeout_seconds=self.contract.timeout_seconds,
        )
        if not isinstance(output, dict):
            raise PreconditionFailed("firewall update did not return a JSON object")
        return ToolResult(
            status="success",
            output={
                "rule_name": str(payload["rule_name"]),
                "direction": str(output.get("direction", payload["direction"])),
                "action": str(output.get("action", payload["action"])),
                "local_port": str(payload["local_port"]),
                "enabled": bool(output.get("enabled", True)),
            },
            evidence={"module": "OpsPilot.Firewall"},
        )

    def verify(self, payload: Mapping[str, Any], result: ToolResult) -> None:
        state = self._state(str(payload["rule_name"]))
        if not state.get("exists") or not state.get("enabled"):
            raise VerificationFailed(
                "firewall rule is missing or disabled after update",
                details={"rule": state},
            )
        if str(state.get("action", "")).lower() != str(payload["action"]).lower():
            raise VerificationFailed(
                "firewall rule action does not match the requested action",
                details={"rule": state},
            )
        result.evidence["verified_state"] = state

    def restore(self, state: Mapping[str, Any]) -> None:
        rule_state = state.get("rule")
        if not isinstance(rule_state, dict):
            raise PreconditionFailed("checkpoint has no firewall rule state")
        self.runner().run_script(
            module_script(
                "OpsPilot.Firewall",
                json_pipeline(
                    "Restore-OpsPilotFirewallRuleState "
                    f"-StateBase64 {ps_quote(encode_state(rule_state))}"
                ),
            ),
            timeout_seconds=self.contract.timeout_seconds,
        )
