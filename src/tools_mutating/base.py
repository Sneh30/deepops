"""Shared helpers for mutating tools that call PowerShell modules.

PowerShell logic lives in lintable ``powershell/modules/*.psm1`` files rather
than opaque inline strings. Tools only compose module calls with quoted
parameters; the runner supplies scripts on stdin.
"""

from __future__ import annotations

import base64
import json
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from common.config import REPO_ROOT
from common.process import PowerShellRunner, ps_quote

MODULES_DIR = REPO_ROOT / "powershell" / "modules"


def module_script(module: str, body: str) -> str:
    module_path = MODULES_DIR / f"{module}.psm1"
    return (
        "$ErrorActionPreference = 'Stop'\n"
        f"Import-Module {ps_quote(str(module_path))} -Force\n"
        f"{body}\n"
    )


def json_pipeline(expression: str) -> str:
    return f"{expression} | ConvertTo-Json -Depth 12 -Compress"


def ps_array(values: list[str]) -> str:
    if not values:
        return "@()"
    return "@(" + ", ".join(ps_quote(value) for value in values) + ")"


def ps_bool(value: bool) -> str:
    return "$true" if value else "$false"


def encode_state(state: Mapping[str, Any]) -> str:
    payload = json.dumps(dict(state), sort_keys=True, separators=(",", ":"))
    return base64.b64encode(payload.encode("utf-8")).decode("ascii")


class PowerShellTool:
    """Mixin providing a lazily constructed production PowerShell runner."""

    _runner: PowerShellRunner | None = None

    def runner(self) -> PowerShellRunner:
        if self._runner is None:
            self._runner = PowerShellRunner()
        return self._runner

    def run_json(self, module: str, body: str, timeout_seconds: int) -> Any:
        output = self.runner().run_json(
            module_script(module, body), timeout_seconds=timeout_seconds
        )
        return output


def state_path(value: str) -> Path:
    return Path(value)
