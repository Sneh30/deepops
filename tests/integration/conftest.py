"""Windows-only integration suite (IMPLEMENTATION.md §0 and §7).

This suite must fail fast off Windows. A skip is not acceptable here: a skipped
suite can be mistaken for a passing Windows verification, so the entire run is
aborted with a clear Windows-only message instead.
"""

from __future__ import annotations

import shutil
import sys

import pytest

pytestmark = pytest.mark.windows_only


def pytest_configure(config: pytest.Config) -> None:
    if sys.platform != "win32":
        raise pytest.UsageError(
            "Windows-only integration suite: the current host is "
            f"{sys.platform}. Run `make test-integration` on the Windows-side "
            "agent; Mac-side authoring uses `make lint` and `make test` only."
        )


@pytest.fixture(scope="session")
def powershell() -> str:
    executable = shutil.which("pwsh")
    if not executable:
        raise pytest.UsageError(
            "PowerShell 7 (pwsh) must be installed on the Windows verification host."
        )
    return executable
