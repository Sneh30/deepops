from __future__ import annotations

import pytest

from common.errors import PreconditionFailed
from reverse_proxy.generator import (
    ACME_PRODUCTION_DIRECTORY,
    ACME_STAGING_DIRECTORY,
    CaddyConfig,
    ProxyRoute,
    from_deployment_config,
    routes_for_ports,
    write_caddyfile,
)


def test_caddyfile_contains_tls_and_reverse_proxy() -> None:
    config = CaddyConfig(
        email="ops@example.com",
        routes=(ProxyRoute(hostname="app.example.com", upstream_port=8080),),
    )
    rendered = config.render()
    assert "email ops@example.com" in rendered
    assert f"acme_ca {ACME_STAGING_DIRECTORY}" in rendered
    assert "admin off" in rendered
    assert "app.example.com {" in rendered
    assert "reverse_proxy 127.0.0.1:8080" in rendered


def test_production_acme_directory_is_used_when_requested() -> None:
    config = CaddyConfig(
        email="ops@example.com",
        routes=(ProxyRoute(hostname="app.example.com"),),
        acme_staging=False,
    )
    assert ACME_PRODUCTION_DIRECTORY in config.render()


def test_http_only_route_uses_scheme() -> None:
    route = ProxyRoute(hostname="app.example.com", tls=False)
    assert route.site_address == "http://app.example.com"


def test_invalid_hostnames_are_rejected() -> None:
    for hostname in ("", "localhost", "bad host", "-bad.example.com", "*.example.com"):
        with pytest.raises(PreconditionFailed):
            ProxyRoute(hostname=hostname)


def test_invalid_upstream_is_rejected() -> None:
    with pytest.raises(PreconditionFailed):
        ProxyRoute(hostname="app.example.com", extra_upstreams=("not-an-upstream",))


def test_duplicate_hostnames_are_rejected() -> None:
    route = ProxyRoute(hostname="app.example.com")
    with pytest.raises(PreconditionFailed):
        CaddyConfig(email="ops@example.com", routes=(route, route))


def test_header_injection_is_rejected() -> None:
    with pytest.raises(PreconditionFailed):
        ProxyRoute(
            hostname="app.example.com",
            headers={"X-Test": "value\ninjected"},
        )


def test_from_deployment_config_uses_primary_port() -> None:
    document = {"app": {"ports": [8080, 8443]}}
    config = from_deployment_config(
        document, hostname="app.example.com", email="ops@example.com"
    )
    assert config.routes[0].upstream_port == 8080


def test_routes_for_ports_builds_one_route_per_port() -> None:
    routes = routes_for_ports("app.example.com", [8080, 8081])
    assert [route.upstream_port for route in routes] == [8080, 8081]


def test_dashboard_path_route_renders_sse_flush() -> None:
    config = CaddyConfig(
        email="ops@example.com",
        routes=(
            ProxyRoute(hostname="app.example.com", upstream_port=8080),
            ProxyRoute(
                hostname="app.example.com",
                upstream_port=8090,
                path_prefix="/mission-control",
                streaming=True,
            ),
        ),
    )
    rendered = config.render()
    assert "handle_path /mission-control/* {" in rendered
    assert "reverse_proxy 127.0.0.1:8090 {" in rendered
    assert "flush_interval -1" in rendered
    assert "handle {" in rendered
    assert "reverse_proxy 127.0.0.1:8080" in rendered


def test_path_route_prefix_is_validated() -> None:
    for prefix in ("mission-control", "/", "/bad path", "/../etc", "//double"):
        with pytest.raises(PreconditionFailed):
            ProxyRoute(hostname="app.example.com", path_prefix=prefix)


def test_duplicate_path_prefixes_are_rejected() -> None:
    first = ProxyRoute(hostname="app.example.com", path_prefix="/mission-control")
    second = ProxyRoute(hostname="app.example.com", path_prefix="/mission-control")
    with pytest.raises(PreconditionFailed):
        CaddyConfig(email="ops@example.com", routes=(first, second))


def test_caddy_cutover_mounts_the_dashboard_by_default() -> None:
    from deployment_engine.engine import CaddyProxyCutover

    cutover = CaddyProxyCutover(
        object(),
        config_path="/tmp/Caddyfile",
        hostname="app.example.com",
        email="ops@example.com",
        dashboard_port=8090,
        dashboard_path="/mission-control",
    )
    routes = cutover.routes(8080)
    assert [route.path_prefix for route in routes] == [None, "/mission-control"]
    assert routes[1].streaming is True
    assert routes[1].upstream_port == 8090
    rendered = cutover.caddyfile(8080)
    assert "handle_path /mission-control/*" in rendered
    assert "127.0.0.1:8090" in rendered
    assert "flush_interval -1" in rendered


def test_caddy_cutover_can_disable_the_dashboard_route() -> None:
    from deployment_engine.engine import CaddyProxyCutover

    cutover = CaddyProxyCutover(
        object(),
        config_path="/tmp/Caddyfile",
        hostname="app.example.com",
        email="ops@example.com",
        dashboard_port=None,
    )
    assert [route.path_prefix for route in cutover.routes(8080)] == [None]
    assert "handle_path" not in cutover.caddyfile(8080)


def test_write_caddyfile(tmp_path) -> None:
    config = CaddyConfig(
        email="ops@example.com", routes=(ProxyRoute(hostname="app.example.com"),)
    )
    path = write_caddyfile(config, tmp_path / "Caddyfile")
    assert path.read_text(encoding="utf-8").endswith("\n")
