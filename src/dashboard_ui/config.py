"""Mission Control dashboard configuration (COMP-13, IMPL-703/713).

The dashboard is a separate process from the Control Plane (NFR-014). Its
configuration is deliberately self-contained: bind address, event journal
location, state-retention policy, Prometheus scrape settings, and the
optional session/query ingest contract used by per-database adapters
(ADR-008). No secrets live in this file.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from common.config import load_config
from common.errors import ConfigValidationError

_HEX_COLOR = re.compile(r"^#[0-9A-Fa-f]{6}$")
_PATH_PREFIX = re.compile(r"^/[A-Za-z0-9._~/-]*[A-Za-z0-9._~-]$")
_URL = re.compile(r"^https?://[^\s]+$")
_ENV_NAME = re.compile(r"^[A-Z][A-Z0-9_]{2,127}$")


@dataclass(frozen=True)
class MetricQuery:
    name: str
    query: str


@dataclass(frozen=True)
class DashboardConfig:
    version: str
    host: str
    port: int
    path_prefix: str
    title: str
    allowed_origins: tuple[str, ...]
    journal_path: Path
    journal_max_bytes: int
    journal_max_files: int
    poll_interval_seconds: float
    session_grace_seconds: int
    session_idle_seconds: int
    query_stale_seconds: int
    recent_events: int
    trace_limit: int
    token_buffer_chars: int
    snapshot_path: Path
    snapshot_interval_seconds: int
    palette: tuple[str, ...]
    prometheus_url: str
    prometheus_timeout_seconds: int
    scrape_interval_seconds: int
    metric_queries: tuple[MetricQuery, ...]
    ingest_enabled: bool
    ingest_token_env: str
    max_request_bytes: int
    log_glob: str
    log_limit: int

    @classmethod
    def from_document(
        cls, document: Mapping[str, Any], *, data_dir: str | Path
    ) -> DashboardConfig:
        root = Path(data_dir)
        try:
            server = document["server"]
            journal = document["journal"]
            state = document["state"]
            services = document["services"]
            observability = document["observability"]
            ingest = document["ingest"]
            logs = document["logs"]
            assert isinstance(server, Mapping)
            assert isinstance(journal, Mapping)
            assert isinstance(state, Mapping)
            assert isinstance(services, Mapping)
            assert isinstance(observability, Mapping)
            assert isinstance(ingest, Mapping)
            assert isinstance(logs, Mapping)

            host = str(server["host"])
            port = int(server["port"])
            path_prefix = str(server["path_prefix"])
            title = str(server["title"])
            allowed_origins = tuple(str(value) for value in server.get("allowed_origins", []))
            journal_value = journal.get("path")
            journal_path = (
                Path(str(journal_value))
                if journal_value
                else root / "logs" / "events.jsonl"
            )
            snapshot_value = state.get("snapshot_path")
            snapshot_path = (
                Path(str(snapshot_value))
                if snapshot_value
                else root / "dashboard" / "state.json"
            )
            queries_raw = observability.get("queries", [])
            if not isinstance(queries_raw, list):
                raise ConfigValidationError("observability.queries must be a list")
            metric_queries = tuple(
                MetricQuery(name=str(item["name"]), query=str(item["query"]))
                for item in queries_raw
                if isinstance(item, Mapping)
            )
            palette = tuple(str(value) for value in services.get("palette", []))
            result = cls(
                version=str(document["version"]),
                host=host,
                port=port,
                path_prefix=path_prefix,
                title=title,
                allowed_origins=allowed_origins,
                journal_path=journal_path,
                journal_max_bytes=int(journal.get("max_bytes", 20 * 1024 * 1024)),
                journal_max_files=int(journal.get("max_files", 3)),
                poll_interval_seconds=max(
                    0.01, float(journal.get("poll_interval_ms", 50)) / 1000.0
                ),
                session_grace_seconds=int(state.get("session_grace_seconds", 30)),
                session_idle_seconds=int(state.get("session_idle_seconds", 3600)),
                query_stale_seconds=int(state.get("query_stale_seconds", 600)),
                recent_events=int(state.get("recent_events", 500)),
                trace_limit=int(state.get("trace_limit", 200)),
                token_buffer_chars=int(state.get("token_buffer_chars", 20000)),
                snapshot_path=snapshot_path,
                snapshot_interval_seconds=int(state.get("snapshot_interval_seconds", 5)),
                palette=palette,
                prometheus_url=str(observability.get("prometheus_url", "")),
                prometheus_timeout_seconds=int(
                    observability.get("timeout_seconds", 5)
                ),
                scrape_interval_seconds=int(
                    observability.get("scrape_interval_seconds", 15)
                ),
                metric_queries=metric_queries,
                ingest_enabled=bool(ingest.get("enabled", False)),
                ingest_token_env=str(
                    ingest.get("token_env", "OPSPILOT_DASHBOARD_INGEST_TOKEN")
                ),
                max_request_bytes=int(ingest.get("max_request_bytes", 65536)),
                log_glob=str(logs.get("glob", "*.jsonl")),
                log_limit=int(logs.get("limit", 500)),
            )
        except (KeyError, TypeError, ValueError) as error:
            raise ConfigValidationError(
                f"dashboard configuration is incomplete or invalid: {error}"
            ) from error
        result.validate()
        return result

    @classmethod
    def load(cls, path: str | Path, *, data_dir: str | Path) -> DashboardConfig:
        document = load_config(path, "dashboard.schema.json")
        return cls.from_document(document, data_dir=data_dir)

    def validate(self) -> None:
        problems: list[str] = []
        if self.version != "1":
            problems.append("version must be '1'")
        if not self.host or any(character.isspace() for character in self.host):
            problems.append("server.host must be a non-empty host name or address")
        if not 1 <= self.port <= 65535:
            problems.append("server.port must be between 1 and 65535")
        if not _PATH_PREFIX.fullmatch(self.path_prefix):
            problems.append(
                "server.path_prefix must start with '/', contain no spaces/braces, "
                "and not end with '/'"
            )
        if self.session_grace_seconds < 0 or self.session_grace_seconds > 600:
            problems.append("state.session_grace_seconds must be between 0 and 600")
        if self.session_idle_seconds < 60 or self.session_idle_seconds > 86400:
            problems.append("state.session_idle_seconds must be between 60 and 86400")
        if self.query_stale_seconds < 1 or self.query_stale_seconds > 86400:
            problems.append("state.query_stale_seconds must be between 1 and 86400")
        if self.recent_events < 10 or self.recent_events > 10000:
            problems.append("state.recent_events must be between 10 and 10000")
        if self.trace_limit < 10 or self.trace_limit > 5000:
            problems.append("state.trace_limit must be between 10 and 5000")
        if self.token_buffer_chars < 1000 or self.token_buffer_chars > 1_000_000:
            problems.append("state.token_buffer_chars must be between 1000 and 1000000")
        if self.snapshot_interval_seconds < 1 or self.snapshot_interval_seconds > 300:
            problems.append("state.snapshot_interval_seconds must be between 1 and 300")
        if not self.palette:
            problems.append("services.palette must contain at least one colour")
        for color in self.palette:
            if not _HEX_COLOR.fullmatch(color):
                problems.append(f"services.palette entry is not a #rrggbb colour: {color!r}")
        if self.prometheus_url and not _URL.fullmatch(self.prometheus_url):
            problems.append("observability.prometheus_url must be an http(s) URL")
        if self.prometheus_timeout_seconds < 1 or self.prometheus_timeout_seconds > 120:
            problems.append("observability.timeout_seconds must be between 1 and 120")
        if self.scrape_interval_seconds < 5 or self.scrape_interval_seconds > 3600:
            problems.append("observability.scrape_interval_seconds must be between 5 and 3600")
        if self.metric_queries:
            names = [query.name for query in self.metric_queries]
            if len(set(names)) != len(names):
                problems.append("observability.queries names must be unique")
            for query in self.metric_queries:
                if not query.name or not query.query:
                    problems.append("observability.queries entries need name and query")
        if self.ingest_enabled and not _ENV_NAME.fullmatch(self.ingest_token_env):
            problems.append("ingest.token_env must be an environment variable name")
        if self.max_request_bytes < 1024 or self.max_request_bytes > 1_048_576:
            problems.append("ingest.max_request_bytes must be between 1024 and 1048576")
        if not self.log_glob:
            problems.append("logs.glob must not be empty")
        if self.log_limit < 1 or self.log_limit > 10000:
            problems.append("logs.limit must be between 1 and 10000")
        if problems:
            raise ConfigValidationError(
                "dashboard configuration failed validation: " + "; ".join(problems),
                details={"errors": problems},
            )
