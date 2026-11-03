"""Clock abstraction so incident/retry logic is testable without sleeping."""

from __future__ import annotations

import time
from datetime import UTC, datetime
from typing import Protocol


class Clock(Protocol):
    def now(self) -> datetime: ...

    def monotonic(self) -> float: ...

    def sleep(self, seconds: float) -> None: ...


class SystemClock:
    """Production clock: UTC wall time plus monotonic duration source."""

    def now(self) -> datetime:
        return datetime.now(UTC)

    def monotonic(self) -> float:
        return time.monotonic()

    def sleep(self, seconds: float) -> None:
        if seconds > 0:
            time.sleep(seconds)


def utc_now_iso() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds").replace("+00:00", "Z")
