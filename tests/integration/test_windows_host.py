"""Real Windows host capability checks (read-only)."""

from __future__ import annotations

import shutil
import sys

import pytest

from common.platform import require_supported_python
from common.process import PowerShellRunner


def test_running_on_windows() -> None:
    assert sys.platform == "win32"


def test_python_version_floor() -> None:
    require_supported_python()


def test_powershell_seven_available() -> None:
    executable = shutil.which("pwsh")
    assert executable, "PowerShell 7 (pwsh) must be on PATH"


def test_production_runner_executes_powershell(powershell: str) -> None:
    runner = PowerShellRunner(executable=powershell)
    result = runner.run_script("$PSVersionTable.PSVersion.Major | ConvertTo-Json")
    assert result.returncode == 0
    assert int(result.json_output()) >= 7


def test_service_module_reports_a_known_service(powershell: str) -> None:
    from tools_mutating.service import RestartWindowsServiceTool

    tool = RestartWindowsServiceTool(runner=PowerShellRunner(executable=powershell))
    state = tool.capture_state({"service_name": "Spooler"})
    assert state["service"]["name"] == "Spooler"


def test_baseline_module_reads_state(powershell: str) -> None:
    from incident_engine.drift import PowerShellBaselineProvider

    provider = PowerShellBaselineProvider(
        runner=PowerShellRunner(executable=powershell)
    )
    state = provider.live_state()
    assert "firewall_profiles" in state
    assert "tls" in state


def test_terraform_binary_present_or_documented_skip() -> None:
    executable = shutil.which("terraform")
    if executable is None:
        pytest.skip("Terraform binary is not on PATH; install it per ENGINEERING_REFERENCE.md")
    import subprocess

    completed = subprocess.run(
        [executable, "version", "-json"], capture_output=True, text=True, check=False
    )
    assert completed.returncode == 0
