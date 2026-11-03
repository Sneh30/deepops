"""Configuration loading and JSON Schema validation (AP-6, §4 of the handbook)."""

from __future__ import annotations

import hashlib
import json
import os
import tempfile
from pathlib import Path
from typing import Any

import yaml
from jsonschema import Draft202012Validator

from common.errors import ConfigValidationError

REPO_ROOT = Path(__file__).resolve().parents[2]
SCHEMA_DIR = REPO_ROOT / "config" / "schema"


def load_json(path: Path) -> Any:
    with path.open("r", encoding="utf-8") as handle:
        return json.load(handle)


def load_yaml(path: Path) -> Any:
    with path.open("r", encoding="utf-8") as handle:
        return yaml.safe_load(handle)


def load_schema(name_or_path: str | Path) -> dict[str, Any]:
    path = Path(name_or_path)
    if not path.is_absolute() and path.parent == Path("."):
        candidate = SCHEMA_DIR / path.name
        if candidate.exists():
            path = candidate
    document = load_json(path)
    if not isinstance(document, dict):
        raise ConfigValidationError(f"schema {path} must be a JSON object")
    return document


def validate_document(document: Any, schema: str | Path | dict[str, Any]) -> None:
    schema_document = schema if isinstance(schema, dict) else load_schema(schema)
    validator = Draft202012Validator(schema_document)
    errors = sorted(validator.iter_errors(document), key=lambda error: list(error.path))
    if not errors:
        return
    messages = []
    for error in errors:
        location = "/".join(str(part) for part in error.path) or "<root>"
        messages.append(f"{location}: {error.message}")
    raise ConfigValidationError(
        "configuration failed schema validation: " + "; ".join(messages),
        details={"errors": messages},
    )


def load_config(path: str | Path, schema_name: str | Path) -> dict[str, Any]:
    config_path = Path(path)
    if not config_path.exists():
        raise ConfigValidationError(f"config file not found: {config_path}")
    document = load_yaml(config_path)
    if not isinstance(document, dict):
        raise ConfigValidationError(f"config file {config_path} must contain a YAML mapping")
    validate_document(document, schema_name)
    return document


def canonical_json(value: Any) -> str:
    """Stable JSON used for digests and checkpoint checksums."""
    return json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
        default=str,
    )


def sha256_hex(value: str | bytes) -> str:
    payload = value.encode("utf-8") if isinstance(value, str) else value
    return hashlib.sha256(payload).hexdigest()


def digest(value: Any) -> str:
    return sha256_hex(canonical_json(value))


def atomic_write_text(path: str | Path, text: str, *, encoding: str = "utf-8") -> None:
    """Write a file atomically, never leaving a partially written artifact."""
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(
        dir=target.parent, prefix=f".{target.name}.", suffix=".tmp"
    )
    try:
        with os.fdopen(descriptor, "w", encoding=encoding, newline="\n") as handle:
            handle.write(text)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, target)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def atomic_write_json(path: str | Path, value: Any) -> None:
    atomic_write_text(path, json.dumps(value, indent=2, sort_keys=True) + "\n")
