"""Static checks for the Windows install scripts (Mac-runnable)."""

from __future__ import annotations

import json
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
INSTALL_STACK = REPO_ROOT / "src" / "observability" / "install_stack.ps1"
INSTALL_PS1 = REPO_ROOT / "powershell" / "install" / "install.ps1"
DASHBOARD = REPO_ROOT / "src" / "observability" / "grafana" / "dashboards" / "opspilot.json"


def test_install_stack_pins_every_artifact_and_checksum() -> None:
    text = INSTALL_STACK.read_text(encoding="utf-8")
    pins = {
        "windows_exporter-0.32.1-amd64.msi": (
            "8e1ade6a2fbcdeb4774c300b598667cd7888246be8fcc4b586d11b3680433981"
        ),
        "prometheus-3.15.1.windows-amd64.zip": (
            "ba25b5e590520cb9fb028edef99bdc8523244d2d489b8c629e0d453d94fb030b"
        ),
        "grafana-13.3.0.windows-amd64.zip": (
            "133f0a0c7b905a6c7e4f3df596cf4df535ca78f1e3ef7e5be5f3f2178a51f75d"
        ),
        "nssm-2.24.zip": "727d1e42275c605e0f04aba98095c38a8e1e46def453cdffce42869428aa6743",
    }
    for artifact, digest in pins.items():
        assert artifact in text
        assert digest in text
    assert "releases/latest" not in text
    assert "Get-FileHash" in text
    assert "sha256" in text.lower()


def test_install_stack_requires_windows_and_supports_whatif() -> None:
    text = INSTALL_STACK.read_text(encoding="utf-8")
    assert "#Requires -Version 7.2" in text
    assert "SupportsShouldProcess" in text
    assert "if (-not $IsWindows)" in text
    assert "Get-OpsPilotStackPlan" in text
    assert "$WhatIfPreference" in text


def test_install_ps1_implements_lettered_upgrade_sequence() -> None:
    text = INSTALL_PS1.read_text(encoding="utf-8")
    ordered = [
        "Invoke-OpsPilotCheckpoint",
        "Stop-Service -Name 'OpsPilotControlPlane'",
        "Copy-OpsPilotPayload",
        "Initialize-OpsPilotPythonEnvironment",
        "Start-Service -Name 'OpsPilotControlPlane'",
        "Invoke-OpsPilotHealthCheck",
        "Invoke-OpsPilotRollback",
        "Write-OpsPilotHandoffResult",
    ]
    cursor = 0
    for marker in ordered:
        position = text.find(marker, cursor)
        assert position >= 0, f"missing or out-of-order marker: {marker}"
        cursor = position + len(marker)
    assert "if (-not $IsWindows)" in text
    assert "OpsPilotControlPlane" in text
    assert "NT AUTHORITY\\LocalService" in text


def test_grafana_dashboard_is_valid_json_with_expected_panels() -> None:
    document = json.loads(DASHBOARD.read_text(encoding="utf-8"))
    assert document["title"]
    panels = document["panels"]
    assert len(panels) >= 3
    titles = {panel["title"] for panel in panels}
    assert "Host CPU" in titles
    assert all(panel["targets"] for panel in panels)


def test_dashboard_installer_registers_a_separate_windows_service() -> None:
    text = (
        REPO_ROOT / "powershell" / "modules" / "OpsPilot.Install.psm1"
    ).read_text(encoding="utf-8")
    assert "$script:DashboardServiceName = 'OpsPilotDashboardUI'" in text
    assert "function Install-OpsPilotDashboard" in text
    assert "run_dashboard.py" in text
    assert "dashboard.yaml" in text
    assert "StartupType    = 'Automatic'" in text
    assert "NT AUTHORITY\\LocalService" in text
    assert "OpsPilotDashboardUI" in text
    assert "Install-OpsPilotDashboard" in text.split("Export-ModuleMember", 1)[1]
    # The dashboard is a separate service, never a child of the control plane.
    assert "New-Service @parameters" in text


def test_install_ps1_installs_and_upgrades_the_dashboard_service() -> None:
    text = INSTALL_PS1.read_text(encoding="utf-8")
    assert "OpsPilotDashboardUI" in text
    assert "Install-OpsPilotDashboard" in text
    assert "[int]$DashboardPort = 8090" in text
    assert "[string]$DashboardHost = '127.0.0.1'" in text
    assert text.rindex("Install-OpsPilotControlPlane") < text.rindex(
        "Install-OpsPilotDashboard"
    )
    assert "dashboard: $($dashboard.status)" in text
    assert "dashboard: $($dashboard.service_name)" in text
    # Upgrade must stop the dashboard before the payload copy and reinstall it
    # after the control plane is healthy.
    stop_dashboard = text.index("Stop-Service -Name 'OpsPilotDashboardUI'")
    copy_payload = text.index("Copy-OpsPilotPayload", stop_dashboard)
    reinstall = text.index("Install-OpsPilotDashboard", copy_payload)
    assert stop_dashboard < copy_payload < reinstall


def test_dashboard_service_entry_point_uses_config_and_data_dir() -> None:
    text = (REPO_ROOT / "run_dashboard.py").read_text(encoding="utf-8")
    assert "dashboard_ui.app" in text
    assert "main" in text
    config = (REPO_ROOT / "config" / "defaults" / "dashboard.yaml").read_text(
        encoding="utf-8"
    )
    assert "host: 127.0.0.1" in config
    assert "port: 8090" in config
    assert "path_prefix: /mission-control" in config
    assert "enabled: false" in config
