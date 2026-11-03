"""Windows Service entry point for the OpsPilot control plane.

Installed by ``powershell/install/install.ps1`` as the binary path of the
``OpsPilotControlPlane`` service. It puts ``src`` on sys.path so the service can
run from an uninstalled checkout without relying on the process working
directory (Windows SCM starts services in ``C:\\Windows\\System32``).
"""

from __future__ import annotations

import sys
from pathlib import Path

SRC = Path(__file__).resolve().parent / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from orchestrator.control_plane import main  # noqa: E402

if __name__ == "__main__":
    raise SystemExit(main())
