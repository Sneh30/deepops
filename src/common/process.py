"""Subprocess and PowerShell execution with structured results.

All PowerShell output is captured as data; errors are raised as typed
OpsPilotError instances with redacted evidence. On macOS authoring hosts only
``FakeRunner`` is used by tests — the production runner refuses to execute
Windows-targeted scripts off-Windows unless explicitly configured otherwise
(for PowerShell static analysis during development).
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import time
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol

from common.errors import ExecutionFailed, TimeoutExceeded
from common.platform import powershell_executable, require_windows
from common.redaction import redact_text


@dataclass(frozen=True)
class CommandResult:
    args: tuple[str, ...]
    returncode: int
    stdout: str
    stderr: str
    timed_out: bool = False
    duration_ms: int = 0

    def json_output(self) -> Any:
        """Parse the last non-empty stdout line as JSON."""
        lines = [line.strip() for line in self.stdout.splitlines() if line.strip()]
        if not lines:
            raise ExecutionFailed("command produced no JSON output")
        try:
            return json.loads(lines[-1])
        except json.JSONDecodeError as error:
            raise ExecutionFailed(
                "command output was not valid JSON",
                details={"error": str(error), "stdout": redact_text(self.stdout[-4000:])},
            ) from error


class CommandRunner(Protocol):
    def run(
        self,
        args: Sequence[str],
        *,
        input_text: str | None = None,
        timeout_seconds: float = 120,
        cwd: str | Path | None = None,
    ) -> CommandResult: ...


class SubprocessRunner:
    """Captures stdout/stderr and converts timeouts into structured results."""

    def run(
        self,
        args: Sequence[str],
        *,
        input_text: str | None = None,
        timeout_seconds: float = 120,
        cwd: str | Path | None = None,
    ) -> CommandResult:
        started = time.monotonic()
        try:
            completed = subprocess.run(
                list(args),
                input=input_text,
                capture_output=True,
                text=True,
                timeout=timeout_seconds,
                check=False,
                cwd=str(cwd) if cwd is not None else None,
                encoding="utf-8",
                errors="replace",
            )
            duration_ms = int((time.monotonic() - started) * 1000)
            return CommandResult(
                args=tuple(str(arg) for arg in args),
                returncode=completed.returncode,
                stdout=completed.stdout or "",
                stderr=completed.stderr or "",
                duration_ms=duration_ms,
            )
        except subprocess.TimeoutExpired as error:
            stdout = error.stdout or ""
            stderr = error.stderr or ""
            if isinstance(stdout, bytes):
                stdout = stdout.decode("utf-8", errors="replace")
            if isinstance(stderr, bytes):
                stderr = stderr.decode("utf-8", errors="replace")
            duration_ms = int((time.monotonic() - started) * 1000)
            return CommandResult(
                args=tuple(str(arg) for arg in args),
                returncode=-1,
                stdout=stdout,
                stderr=stderr,
                timed_out=True,
                duration_ms=duration_ms,
            )
        except FileNotFoundError as error:
            raise ExecutionFailed(
                f"executable not found: {args[0]}",
                details={"error": str(error)},
            ) from error


class PowerShellRunner:
    """Runs a PowerShell script supplied on stdin (never via shell string)."""

    def __init__(
        self,
        *,
        executable: str | None = None,
        require_windows_host: bool = True,
        runner: CommandRunner | None = None,
    ) -> None:
        if require_windows_host:
            require_windows("PowerShell tool execution")
            self.executable = executable or powershell_executable()
        else:
            self.executable = (
                executable
                or os.environ.get("OPSPILOT_PWSH")
                or shutil.which("pwsh")
                or "pwsh"
            )
        self._runner = runner or SubprocessRunner()

    def run_script(
        self,
        script: str,
        *,
        timeout_seconds: float = 120,
        cwd: str | Path | None = None,
    ) -> CommandResult:
        result = self._runner.run(
            [
                self.executable,
                "-NoLogo",
                "-NoProfile",
                "-NonInteractive",
                "-Command",
                "-",
            ],
            input_text=script,
            timeout_seconds=timeout_seconds,
            cwd=cwd,
        )
        if result.timed_out:
            raise TimeoutExceeded(
                f"PowerShell script exceeded {timeout_seconds:.0f}s",
                details={
                    "stdout": redact_text(result.stdout[-2000:]),
                    "stderr": redact_text(result.stderr[-2000:]),
                },
            )
        if result.returncode != 0:
            raise ExecutionFailed(
                f"PowerShell script exited with code {result.returncode}",
                details={
                    "returncode": result.returncode,
                    "stdout": redact_text(result.stdout[-4000:]),
                    "stderr": redact_text(result.stderr[-4000:]),
                },
            )
        return result

    def run_json(
        self,
        script: str,
        *,
        timeout_seconds: float = 120,
        cwd: str | Path | None = None,
    ) -> Any:
        result = self.run_script(script, timeout_seconds=timeout_seconds, cwd=cwd)
        return result.json_output()


def ps_quote(value: str) -> str:
    """Quote a value as a single-quoted PowerShell literal."""
    return "'" + value.replace("'", "''") + "'"
