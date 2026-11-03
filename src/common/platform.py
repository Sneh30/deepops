"""Platform guards for Windows-only runtime behavior (MASTER_PROMPT.md §1)."""

from __future__ import annotations

import asyncio
import os
import shutil
import sys

from common.errors import PlatformViolation

MIN_PYTHON = (3, 11)


def is_windows() -> bool:
    return sys.platform == "win32"


def require_windows(operation: str) -> None:
    if not is_windows():
        raise PlatformViolation(
            f"{operation} is Windows-only and cannot be executed on "
            f"{sys.platform}. Route it to the Windows-side agent "
            "(IMPLEMENTATION.md §0, MASTER_PROMPT.md §7)."
        )


def require_supported_python() -> None:
    if sys.version_info < MIN_PYTHON:
        raise PlatformViolation(
            f"Python {MIN_PYTHON[0]}.{MIN_PYTHON[1]}+ is required; "
            f"running {sys.version_info.major}.{sys.version_info.minor}"
        )


def event_loop_policy_name() -> str:
    return type(asyncio.get_event_loop_policy()).__name__


def assert_proactor_event_loop_policy() -> None:
    """Fail loudly if Windows is not using the Proactor event loop.

    ``SelectorEventLoop`` cannot run subprocesses on Windows (CPython asyncio
    platform docs), which would break every PowerShell-backed tool.
    """
    if not is_windows():
        return
    name = event_loop_policy_name()
    if "Proactor" not in name:
        raise PlatformViolation(
            f"Windows asyncio policy is {name}; ProactorEventLoop is required "
            "for subprocess-based tool execution (ENGINEERING_REFERENCE.md §6)."
        )


def powershell_executable() -> str:
    override = os.environ.get("OPSPILOT_PWSH")
    if override:
        return override
    found = shutil.which("pwsh")
    if not found:
        raise PlatformViolation(
            "PowerShell 7+ (pwsh) was not found. Install it on the Windows host "
            "before running OpsPilot tools."
        )
    return found
