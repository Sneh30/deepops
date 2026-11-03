"""Mission Control dashboard service (COMP-13, UI_SPEC.md)."""

from dashboard_ui.app import create_app
from dashboard_ui.config import DashboardConfig
from dashboard_ui.service import DashboardService

__all__ = ["DashboardConfig", "DashboardService", "create_app"]
