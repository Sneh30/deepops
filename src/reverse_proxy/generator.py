"""Caddy configuration generation (COMP-10, FR-030/FR-031).

The generator emits the complete Caddyfile. Validation is deliberately strict:
site addresses and upstreams are the only user-controlled strings that reach
Caddy, so anything that could open a new Caddyfile token/directive is refused
rather than escaped heuristically.
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from common.errors import PreconditionFailed

ACME_STAGING_DIRECTORY = "https://acme-staging-v02.api.letsencrypt.org/directory"
ACME_PRODUCTION_DIRECTORY = "https://acme-v02.api.letsencrypt.org/directory"

_HOST_RE = re.compile(
    r"^(?=.{1,253}$)(?!-)[A-Za-z0-9-]{1,63}(?<!-)(\.(?!-)[A-Za-z0-9-]{1,63}(?<!-))*$"
)
_UPSTREAM_RE = re.compile(r"^(?:(https?)://)?([A-Za-z0-9._-]+):([0-9]{1,5})$")
_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
_PATH_PREFIX_RE = re.compile(r"^/[A-Za-z0-9._~/-]*[A-Za-z0-9._~-]$")


def _valid_path_prefix(value: str) -> bool:
    if not _PATH_PREFIX_RE.fullmatch(value) or "//" in value:
        return False
    # Path traversal segments are never legitimate route prefixes.
    return all(segment not in {"", ".", ".."} for segment in value.split("/")[1:])


@dataclass(frozen=True)
class ProxyRoute:
    hostname: str
    upstream_host: str = "127.0.0.1"
    upstream_port: int = 8080
    tls: bool = True
    extra_upstreams: tuple[str, ...] = ()
    headers: Mapping[str, str] = field(default_factory=dict)
    # Optional path-scoped route (e.g. the Mission Control dashboard at
    # /mission-control on the same hostname as the managed application).
    path_prefix: str | None = None
    # Server-Sent Events and other long-lived streaming responses need Caddy
    # to flush each write immediately rather than buffering.
    streaming: bool = False

    def __post_init__(self) -> None:
        if not _HOST_RE.fullmatch(self.hostname) or self.hostname.lower() == "localhost":
            raise PreconditionFailed(f"invalid proxy hostname: {self.hostname!r}")
        if not 1 <= int(self.upstream_port) <= 65535:
            raise PreconditionFailed(f"invalid upstream port: {self.upstream_port}")
        if (
            not _HOST_RE.fullmatch(self.upstream_host)
            and self.upstream_host != "127.0.0.1"
            and not re.fullmatch(r"[A-Za-z0-9._-]+", self.upstream_host)
        ):
            raise PreconditionFailed(f"invalid upstream host: {self.upstream_host!r}")
        if self.path_prefix is not None and not _valid_path_prefix(self.path_prefix):
            raise PreconditionFailed(
                f"invalid route path prefix: {self.path_prefix!r}"
            )
        for upstream in self.extra_upstreams:
            _validate_upstream(upstream)
        for name, value in self.headers.items():
            if not re.fullmatch(r"[A-Za-z0-9-]+", name):
                raise PreconditionFailed(f"invalid header name: {name!r}")
            if any(character in value for character in "\r\n{}"):
                raise PreconditionFailed(f"invalid header value for {name}")

    @property
    def site_address(self) -> str:
        if self.tls:
            # A bare hostname makes Caddy obtain and renew its own certificate.
            return self.hostname
        return f"http://{self.hostname}"

    def upstreams(self) -> list[str]:
        primary = f"{self.upstream_host}:{self.upstream_port}"
        return [primary, *self.extra_upstreams]


@dataclass(frozen=True)
class CaddyConfig:
    email: str
    routes: tuple[ProxyRoute, ...]
    acme_staging: bool = True
    admin_disabled: bool = True

    def __post_init__(self) -> None:
        if not _EMAIL_RE.fullmatch(self.email):
            raise PreconditionFailed(f"invalid ACME account email: {self.email!r}")
        if not self.routes:
            raise PreconditionFailed("at least one proxy route is required")
        by_host: dict[str, list[ProxyRoute]] = {}
        for route in self.routes:
            by_host.setdefault(route.hostname.lower(), []).append(route)
        for routes in by_host.values():
            if len(routes) == 1:
                continue
            base_routes = [route for route in routes if route.path_prefix is None]
            path_routes = [route for route in routes if route.path_prefix is not None]
            if len(base_routes) > 1:
                raise PreconditionFailed("duplicate proxy hostname in configuration")
            prefixes = [route.path_prefix for route in path_routes]
            if len(set(prefixes)) != len(prefixes):
                raise PreconditionFailed("duplicate proxy path prefix in configuration")

    @property
    def acme_directory(self) -> str:
        return ACME_STAGING_DIRECTORY if self.acme_staging else ACME_PRODUCTION_DIRECTORY

    def render(self) -> str:
        lines: list[str] = ["{"]
        lines.append(f"    email {self.email}")
        lines.append(f"    acme_ca {self.acme_directory}")
        if self.admin_disabled:
            lines.append("    admin off")
        lines.append("}")
        for site_address, routes in self._sites():
            lines.append("")
            lines.append(f"{site_address} {{")
            lines.append("    encode zstd gzip")
            base: ProxyRoute | None = None
            scoped: list[ProxyRoute] = []
            for route in routes:
                if route.path_prefix is None:
                    base = route
                else:
                    scoped.append(route)
            for route in scoped:
                lines.append(f"    handle_path {route.path_prefix}/* {{")
                self._render_upstream(lines, route, indent="        ")
                lines.append("    }")
            if base is not None:
                if scoped:
                    lines.append("    handle {")
                    self._render_upstream(lines, base, indent="        ")
                    lines.append("    }")
                else:
                    self._render_upstream(lines, base, indent="    ")
            lines.append("}")
        return "\n".join(lines) + "\n"

    def _sites(self) -> list[tuple[str, list[ProxyRoute]]]:
        grouped: dict[str, list[ProxyRoute]] = {}
        for route in self.routes:
            grouped.setdefault(route.hostname.lower(), []).append(route)
        return [
            (routes[0].site_address, routes) for routes in grouped.values()
        ]

    @staticmethod
    def _render_upstream(
        lines: list[str], route: ProxyRoute, *, indent: str
    ) -> None:
        if route.headers:
            lines.append(f"{indent}header {{")
            for name, value in route.headers.items():
                lines.append(f"{indent}    {name} {value}")
            lines.append(f"{indent}}}")
        upstreams = " ".join(route.upstreams())
        if route.streaming:
            lines.append(f"{indent}reverse_proxy {upstreams} {{")
            lines.append(f"{indent}    flush_interval -1")
            lines.append(f"{indent}}}")
        else:
            lines.append(f"{indent}reverse_proxy {upstreams}")

    def write(self, path: str | Path) -> Path:
        target = Path(path)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(self.render(), encoding="utf-8", newline="\n")
        return target


def _validate_upstream(value: str) -> None:
    match = _UPSTREAM_RE.fullmatch(value)
    if match is None or not 1 <= int(match.group(3)) <= 65535:
        raise PreconditionFailed(f"invalid upstream: {value!r}")


def routes_for_ports(
    hostname: str,
    ports: Iterable[int],
    *,
    tls: bool = True,
) -> tuple[ProxyRoute, ...]:
    """Build one route per host port against loopback; useful for blue/green."""
    routes = []
    for port in ports:
        if not 1 <= int(port) <= 65535:
            raise PreconditionFailed(f"invalid application port: {port}")
        routes.append(ProxyRoute(hostname=hostname, upstream_port=int(port), tls=tls))
    return tuple(routes)


def from_deployment_config(
    document: Mapping[str, Any],
    *,
    hostname: str,
    email: str,
    tls: bool = True,
    acme_staging: bool = True,
    headers: Mapping[str, str] | None = None,
) -> CaddyConfig:
    app = document.get("app")
    if not isinstance(app, Mapping):
        raise PreconditionFailed("deployment config has no app object")
    ports = app.get("ports")
    if not isinstance(ports, list) or not ports:
        raise PreconditionFailed("deployment config has no application ports")
    port = int(ports[0])
    route = ProxyRoute(
        hostname=hostname, upstream_port=port, tls=tls, headers=headers or {}
    )
    return CaddyConfig(
        email=email, routes=(route,), acme_staging=acme_staging
    )


def write_caddyfile(config: CaddyConfig, path: str | Path) -> Path:
    return config.write(path)
