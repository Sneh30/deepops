"""FastAPI/ASGI surface for the Mission Control dashboard (COMP-13)."""

from __future__ import annotations

import argparse
import hmac
import json
import os
import sys
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles

from common.config import REPO_ROOT
from dashboard_ui.config import DashboardConfig
from dashboard_ui.service import DashboardService, IngestError
from orchestrator.settings import default_data_dir

DEFAULT_CONFIG = REPO_ROOT / "config" / "defaults" / "dashboard.yaml"
DEFAULT_FRONTEND = REPO_ROOT / "frontend" / "dashboard"

Scope = dict[str, Any]
Receive = Callable[[], Awaitable[dict[str, Any]]]
Send = Callable[[dict[str, Any]], Awaitable[None]]


class PrefixStripMiddleware:
    """Accept both ``/`` and ``<path_prefix>/...`` without duplicating routes.

    Caddy uses ``handle_path`` so the backend normally sees the stripped path.
    This middleware keeps direct localhost access at the configured prefix
    working too, which is useful when diagnosing the service without Caddy.
    """

    def __init__(self, app: Any, prefix: str) -> None:
        self.app = app
        self.prefix = prefix.rstrip("/")

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope.get("type") in {"http", "websocket"} and self.prefix:
            path = str(scope.get("path", ""))
            if path == self.prefix or path.startswith(self.prefix + "/"):
                scope = dict(scope)
                scope["path"] = path[len(self.prefix) :] or "/"
                scope["root_path"] = str(scope.get("root_path", "")) + self.prefix
        await self.app(scope, receive, send)


def create_app(
    config: DashboardConfig | None = None,
    *,
    service: DashboardService | None = None,
    data_dir: str | Path | None = None,
    frontend_dir: str | Path | None = None,
) -> FastAPI:
    resolved_data_dir = Path(data_dir) if data_dir is not None else default_data_dir()
    resolved_config = config or DashboardConfig.load(
        DEFAULT_CONFIG, data_dir=resolved_data_dir
    )
    dashboard = service or DashboardService(
        resolved_config, data_dir=resolved_data_dir
    )
    assets = Path(frontend_dir) if frontend_dir is not None else DEFAULT_FRONTEND

    @asynccontextmanager
    async def lifespan(_: FastAPI) -> AsyncIterator[None]:
        await dashboard.start()
        try:
            yield
        finally:
            await dashboard.stop()

    app = FastAPI(
        title=resolved_config.title,
        version="1",
        docs_url=None,
        redoc_url=None,
        openapi_url=None,
        lifespan=lifespan,
    )
    app.state.dashboard = dashboard
    app.add_middleware(PrefixStripMiddleware, prefix=resolved_config.path_prefix)
    if resolved_config.allowed_origins:
        app.add_middleware(
            CORSMiddleware,
            allow_origins=list(resolved_config.allowed_origins),
            allow_methods=["GET", "POST"],
            allow_headers=["*"],
        )

    @app.get("/api/health")
    async def health() -> dict[str, Any]:
        return dashboard.health()

    @app.get("/api/state")
    async def state() -> dict[str, Any]:
        return dashboard.state_payload()

    @app.get("/api/events/recent")
    async def recent(
        limit: int = Query(default=200, ge=1, le=2000)
    ) -> dict[str, Any]:
        return {"events": dashboard.recent_events(limit=limit)}

    @app.get("/api/events/stream")
    async def stream(request: Request) -> StreamingResponse:
        return StreamingResponse(
            await dashboard.stream(request),
            media_type="text/event-stream",
            headers={
                "Cache-Control": "no-cache, no-transform",
                "Connection": "keep-alive",
                "X-Accel-Buffering": "no",
            },
        )

    @app.get("/api/summary")
    async def summary() -> dict[str, Any]:
        return dashboard.summary()

    @app.get("/api/metrics")
    async def metrics(
        points: int = Query(default=120, ge=1, le=720)
    ) -> dict[str, Any]:
        return dashboard.metrics_payload(points=points)

    @app.get("/api/logs")
    async def logs(
        level: str | None = None,
        component: str | None = None,
        limit: int = Query(default=200, ge=1, le=2000),
    ) -> dict[str, Any]:
        try:
            return dashboard.logs(level=level, component=component, limit=limit)
        except ValueError as error:
            raise HTTPException(status_code=422, detail=str(error)) from error

    @app.get("/api/trace")
    async def trace(
        limit: int = Query(default=200, ge=1, le=2000)
    ) -> dict[str, Any]:
        return {"entries": dashboard.trace(limit=limit)}

    @app.get("/api/tokens")
    async def tokens(stream_id: str | None = None) -> dict[str, Any]:
        return dashboard.tokens(stream_id=stream_id)

    @app.post("/api/ingest/session", status_code=202)
    async def ingest_session(request: Request) -> JSONResponse:
        _authorize_ingest(request, resolved_config)
        payload = await _read_json(request, resolved_config)
        try:
            event = dashboard.ingest_session(payload)
        except IngestError as error:
            raise HTTPException(status_code=error.status_code, detail=str(error)) from error
        if event is None:
            raise HTTPException(status_code=503, detail="event journal write failed")
        return JSONResponse(
            {"status": "accepted", "event_id": event.id, "type": event.type},
            status_code=202,
        )

    @app.post("/api/ingest/query", status_code=202)
    async def ingest_query(request: Request) -> JSONResponse:
        _authorize_ingest(request, resolved_config)
        payload = await _read_json(request, resolved_config)
        try:
            event = dashboard.ingest_query(payload)
        except IngestError as error:
            raise HTTPException(status_code=error.status_code, detail=str(error)) from error
        if event is None:
            raise HTTPException(status_code=503, detail="event journal write failed")
        return JSONResponse(
            {"status": "accepted", "event_id": event.id, "type": event.type},
            status_code=202,
        )

    index = assets / "index.html"
    if index.exists():
        app.mount("/", StaticFiles(directory=str(assets), html=True), name="dashboard")
    else:  # pragma: no cover - packaging defect, surfaced clearly
        @app.get("/")
        async def missing_frontend() -> JSONResponse:
            return JSONResponse(
                {
                    "status": "error",
                    "detail": f"dashboard frontend assets not found: {assets}",
                },
                status_code=503,
            )

    return app


def _authorize_ingest(request: Request, config: DashboardConfig) -> None:
    if not config.ingest_enabled:
        raise HTTPException(
            status_code=503,
            detail=(
                "session/query ingest is disabled; a named per-database adapter "
                "must be configured before query blink is enabled (ADR-008)"
            ),
        )
    token = os.environ.get(config.ingest_token_env)
    if not token:
        raise HTTPException(
            status_code=503,
            detail=f"ingest token environment variable is not set: {config.ingest_token_env}",
        )
    header = request.headers.get("authorization", "")
    if not hmac.compare_digest(header, f"Bearer {token}"):
        raise HTTPException(status_code=401, detail="invalid ingest token")


async def _read_json(
    request: Request, config: DashboardConfig
) -> dict[str, Any]:
    length = request.headers.get("content-length")
    if length is not None:
        try:
            declared = int(length)
        except ValueError as error:
            raise HTTPException(status_code=400, detail="invalid Content-Length") from error
        if declared > config.max_request_bytes:
            raise HTTPException(status_code=413, detail="request body is too large")
    body = await request.body()
    if len(body) > config.max_request_bytes:
        raise HTTPException(status_code=413, detail="request body is too large")
    try:
        payload = json.loads(body.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise HTTPException(status_code=400, detail="request body must be JSON") from error
    if not isinstance(payload, dict):
        raise HTTPException(status_code=400, detail="request body must be a JSON object")
    return payload


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="opspilot-dashboard",
        description="Run the OpsPilot Mission Control dashboard service (COMP-13).",
    )
    parser.add_argument("--data-dir", default=None)
    parser.add_argument("--config", default=str(DEFAULT_CONFIG))
    parser.add_argument("--host", default=None)
    parser.add_argument("--port", type=int, default=None)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    data_dir = Path(args.data_dir) if args.data_dir else default_data_dir()
    config = DashboardConfig.load(args.config, data_dir=data_dir)
    host = args.host or config.host
    port = args.port or config.port
    app = create_app(config, data_dir=data_dir)
    import uvicorn

    try:
        uvicorn.run(
            app,
            host=host,
            port=port,
            log_level="info",
            access_log=False,
            timeout_keep_alive=30,
        )
    except KeyboardInterrupt:  # pragma: no cover - interactive path
        return 130
    return 0


if __name__ == "__main__":
    sys.exit(main())
