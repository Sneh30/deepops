from __future__ import annotations

from pathlib import Path

import pytest

from common.config import (
    REPO_ROOT,
    canonical_json,
    load_config,
    load_schema,
    sha256_hex,
    validate_document,
)
from common.errors import ConfigValidationError

SCHEMAS = {
    "host.yaml": "host-config.schema.json",
    "deployment.yaml": "deployment-config.schema.json",
    "alert-rules.yaml": "alert-rules.schema.json",
    "approval-policy.yaml": "approval-policy.schema.json",
}


@pytest.mark.parametrize("filename,schema", SCHEMAS.items())
def test_default_configs_validate(filename: str, schema: str) -> None:
    config = load_config(REPO_ROOT / "config" / "defaults" / filename, schema)
    assert config["version"] == "1"


@pytest.mark.parametrize("schema_name", [*SCHEMAS.values(), "tool-contract.schema.json"])
def test_schemas_are_valid_json_schema(schema_name: str) -> None:
    schema = load_schema(schema_name)
    assert schema["$schema"] == "https://json-schema.org/draft/2020-12/schema"
    assert schema["type"] == "object"


def test_host_config_rejects_linux() -> None:
    document = {
        "version": "1",
        "host": {"name": "x", "os_family": "linux", "timezone": "UTC"},
        "network": {"allowed_inbound_tcp_ports": [80], "metrics_scrape_cidrs": []},
    }
    with pytest.raises(ConfigValidationError):
        validate_document(document, "host-config.schema.json")


def test_deployment_config_rejects_linux_base_image() -> None:
    document = {
        "version": "1",
        "app": {
            "name": "app",
            "package_type": "windows_container",
            "package_path": "C:\\pkg",
            "version": "1.0",
            "environment": "production",
            "ports": [80],
            "health_check": {
                "type": "tcp",
                "port": 80,
                "interval_seconds": 15,
                "timeout_seconds": 5,
                "failure_threshold": 3,
            },
            "container": {
                "image": "example/app:1.0",
                "base": "ubuntu:22.04",
                "container_port": 80,
            },
        },
        "rollout": {
            "strategy": "blue_green",
            "verification_window_seconds": 60,
            "stability_window_seconds": 60,
            "max_failed_health_checks": 3,
        },
    }
    with pytest.raises(ConfigValidationError):
        validate_document(document, "deployment-config.schema.json")


def test_unknown_field_rejected() -> None:
    document = {
        "version": "1",
        "host": {"name": "x", "os_family": "windows", "timezone": "UTC"},
        "network": {"allowed_inbound_tcp_ports": [80], "metrics_scrape_cidrs": []},
        "surprise": True,
    }
    with pytest.raises(ConfigValidationError):
        validate_document(document, "host-config.schema.json")


def test_canonical_json_is_stable() -> None:
    assert canonical_json({"b": 1, "a": [2, 3]}) == '{"a":[2,3],"b":1}'
    assert sha256_hex("abc") == "ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad"


def test_missing_config_raises(tmp_path: Path) -> None:
    with pytest.raises(ConfigValidationError):
        load_config(tmp_path / "missing.yaml", "host-config.schema.json")
