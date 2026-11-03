"""Windows Service entry point for the Mission Control dashboard (COMP-13).

Installed by ``powershell/install/install.ps1`` as the binary path of the
``OpsPilotDashboardUI`` service. It is deliberately a separate process from
``run_control_plane.py`` so a dashboard failure cannot affect the Control Plane
(NFR-014). Like the control-plane entry point, it puts ``src`` on sys.path so
the service does not depend on the process working directory.
"""

from __future__ import annotations

import sys
from pathlib import Path

SRC = Path(__file__).resolve().parent / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from dashboard_ui.app import main  # noqa: E402

if __name__ == "__main__":
    raise SystemExit(main())
