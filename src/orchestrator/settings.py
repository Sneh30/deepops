"""Runtime paths and component wiring (COMP-01)."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from common.config import REPO_ROOT
from common.platform import is_windows


def default_data_dir() -> Path:
    override = os.environ.get("OPSPILOT_DATA_DIR")
    if override:
        return Path(override)
    if is_windows():
        program_data = os.environ.get("PROGRAMDATA", r"C:\ProgramData")
        return Path(program_data) / "OpsPilot"
    return Path.home() / ".opspilot"


@dataclass(frozen=True)
class RuntimePaths:
    data_dir: Path
    db_path: Path
    audit_path: Path
    log_dir: Path
    event_path: Path
    caddy_config: Path
    secrets_root: Path

    @classmethod
    def under(cls, data_dir: str | Path) -> RuntimePaths:
        root = Path(data_dir)
        return cls(
            data_dir=root,
            db_path=root / "state" / "opspilot.db",
            audit_path=root / "logs" / "audit.jsonl",
            log_dir=root / "logs",
            event_path=root / "logs" / "events.jsonl",
            caddy_config=root / "caddy" / "Caddyfile",
            secrets_root=root / "secrets",
        )

    def ensure(self) -> None:
        for directory in (
            self.data_dir,
            self.db_path.parent,
            self.log_dir,
            self.caddy_config.parent,
            self.secrets_root,
        ):
            directory.mkdir(parents=True, exist_ok=True)


def default_config_dir() -> Path:
    override = os.environ.get("OPSPILOT_CONFIG_DIR")
    return Path(override) if override else REPO_ROOT / "config" / "defaults"
