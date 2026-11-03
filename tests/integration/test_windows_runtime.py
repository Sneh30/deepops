"""Real DPAPI round-trip and Windows-container mode enforcement."""

from __future__ import annotations

import pytest

from common.errors import PreconditionFailed
from common.process import PowerShellRunner
from secrets_manager.dpapi import DpapiSecretsManager


def test_dpapi_secret_round_trip(tmp_path, powershell: str) -> None:
    manager = DpapiSecretsManager(
        runner=PowerShellRunner(executable=powershell), root_path=str(tmp_path)
    )
    manager.set("integration-secret", "correct horse battery staple")
    assert manager.get("integration-secret") == "correct horse battery staple"
    assert "integration-secret" in manager.list_names()
    assert manager.delete("integration-secret") is True
    assert manager.get("integration-secret") is None


def test_dpapi_rejects_tampered_ciphertext(tmp_path, powershell: str) -> None:
    manager = DpapiSecretsManager(
        runner=PowerShellRunner(executable=powershell), root_path=str(tmp_path)
    )
    manager.set("integration-secret", "value")
    path = tmp_path / "integration-secret.secret"
    path.write_text("tampered", encoding="utf-8")
    with pytest.raises(PreconditionFailed):
        manager.get("integration-secret")


def test_container_mode_is_windows_or_docker_absent(powershell: str) -> None:
    import shutil

    if shutil.which("docker") is None:
        pytest.skip("Docker is not installed; Windows-container checks are blocked")
    from tools_mutating.container import DeployWindowsContainerTool

    tool = DeployWindowsContainerTool(
        runner=PowerShellRunner(executable=powershell)
    )
    state = tool.preview(
        {
            "container_name": "opspilot-preflight",
            "image": "mcr.microsoft.com/windows/nanoserver:ltsc2022",
            "host_port": 18080,
            "container_port": 8080,
        }
    )
    assert state.status == "preview"
    assert state.evidence["would_check_docker_ostype"] == "windows"
