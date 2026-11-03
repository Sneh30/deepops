"""Windows-container image definition generation (IMPL-201, FR-011).

Only Windows base images are accepted. The Dockerfile is generated from the
declared deployment config so the image a Windows agent builds is traceable to
the same config used for rollout.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from common.errors import PreconditionFailed

_WINDOWS_BASE_PREFIX = "mcr.microsoft.com/windows/"


@dataclass(frozen=True)
class WindowsImageSpec:
    base_image: str
    executable: str
    port: int
    workdir: str = r"C:\app"

    def __post_init__(self) -> None:
        if not self.base_image.startswith(_WINDOWS_BASE_PREFIX):
            raise PreconditionFailed(
                "Windows container images must use an mcr.microsoft.com/windows/* base "
                "(ADR-003); Linux-container fallback is not permitted"
            )
        if not self.executable or any(character in self.executable for character in '\r\n"'):
            raise PreconditionFailed("executable must be a single legal image path")
        if not 1 <= int(self.port) <= 65535:
            raise PreconditionFailed(f"invalid container port: {self.port}")

    def render(self) -> str:
        entrypoint = self.executable.replace("/", "\\")
        return (
            "# escape=`\n"
            f"FROM {self.base_image}\n"
            "SHELL [\"powershell\", \"-NoProfile\", \"-Command\"]\n"
            f"WORKDIR {self.workdir}\n"
            "COPY . .\n"
            f"EXPOSE {self.port}\n"
            f'ENTRYPOINT ["{entrypoint}"]\n'
        )

    def write(self, path: str | Path) -> Path:
        target = Path(path)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(self.render(), encoding="utf-8", newline="\n")
        return target


def spec_from_deployment(document: Mapping[str, Any]) -> WindowsImageSpec:
    app = document.get("app")
    if not isinstance(app, Mapping):
        raise PreconditionFailed("deployment config has no app object")
    container = app.get("container")
    if not isinstance(container, Mapping):
        raise PreconditionFailed("deployment config has no container block")
    executable = app.get("executable")
    if not executable:
        # Container images may expose a service that is not a single executable;
        # require the explicit entry point rather than guessing.
        raise PreconditionFailed("container image generation requires app.executable")
    return WindowsImageSpec(
        base_image=str(container["base"]),
        executable=str(executable),
        port=int(container["container_port"]),
    )
