"""Read-only HTTP health probe (COMP-02).

Uses the standard library so there is no extra runtime dependency. The probe
never follows a redirect to a different host, which keeps health checks from
silently validating an unrelated endpoint.
"""

from __future__ import annotations

import time
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any, Protocol

from common.contracts import ReadOnlyTool, ToolResult, read_only_contract, result_schema
from common.errors import PreconditionFailed


@dataclass(frozen=True)
class HttpProbeResult:
    healthy: bool
    status_code: int | None
    latency_ms: float
    error: str | None = None


class HttpProber(Protocol):
    def probe(self, url: str, timeout_seconds: float) -> HttpProbeResult: ...


class UrllibProber:
    def __init__(self, user_agent: str = "OpsPilot/0.1") -> None:
        self.user_agent = user_agent

    def probe(self, url: str, timeout_seconds: float) -> HttpProbeResult:
        request = urllib.request.Request(
            url, method="GET", headers={"User-Agent": self.user_agent}
        )
        # Refuse redirects so a 302 to an attacker-controlled host cannot turn
        # an internal health check into a success signal.
        opener = urllib.request.build_opener(_NoRedirectHandler)
        started = time.monotonic()
        try:
            with opener.open(request, timeout=timeout_seconds) as response:
                latency = (time.monotonic() - started) * 1000
                status = int(response.status)
                return HttpProbeResult(
                    healthy=200 <= status < 400 and urllib.parse.urlsplit(response.url).hostname
                    == urllib.parse.urlsplit(url).hostname,
                    status_code=status,
                    latency_ms=round(latency, 2),
                )
        except urllib.error.HTTPError as error:
            latency = (time.monotonic() - started) * 1000
            return HttpProbeResult(
                healthy=False,
                status_code=int(error.code),
                latency_ms=round(latency, 2),
                error=f"HTTP {error.code}",
            )
        except (urllib.error.URLError, TimeoutError, OSError) as error:
            latency = (time.monotonic() - started) * 1000
            return HttpProbeResult(
                healthy=False,
                status_code=None,
                latency_ms=round(latency, 2),
                error=str(error),
            )


class _NoRedirectHandler(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req: Any, fp: Any, code: Any, msg: Any, headers: Any, newurl: Any) -> None:
        return None


class HttpHealthTool(ReadOnlyTool):
    def __init__(self, prober: HttpProber | None = None) -> None:
        self._prober = prober or UrllibProber()
        self.contract = read_only_contract(
            name="get_http_health",
            description="Probe an application HTTP health endpoint without following redirects.",
            input_schema={
                "type": "object",
                "additionalProperties": False,
                "required": ["url"],
                "properties": {
                    "url": {"type": "string", "pattern": "^https?://[^\\s]+$"},
                    "timeout_seconds": {
                        "type": "integer",
                        "minimum": 1,
                        "maximum": 60,
                        "default": 5,
                    },
                },
            },
            output_schema=result_schema(
                {
                    "healthy": {"type": "boolean"},
                    "status_code": {"type": ["integer", "null"]},
                    "latency_ms": {"type": "number"},
                    "error": {"type": ["string", "null"]},
                }
            ),
            timeout_seconds=60,
        )

    def run(self, payload: Mapping[str, Any]) -> ToolResult:
        url = str(payload["url"])
        if urllib.parse.urlsplit(url).hostname is None:
            raise PreconditionFailed("health-check URL must include a host")
        timeout = int(payload.get("timeout_seconds", 5))
        probe = self._prober.probe(url, timeout)
        return ToolResult(
            status="success",
            output={
                "healthy": probe.healthy,
                "status_code": probe.status_code,
                "latency_ms": probe.latency_ms,
                "error": probe.error,
            },
            evidence={"tool": "get_http_health", "url": url},
        )
