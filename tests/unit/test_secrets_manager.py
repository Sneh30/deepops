from __future__ import annotations

import base64

import pytest

from common.errors import PreconditionFailed
from secrets_manager.dpapi import DpapiSecretsManager


class FakeRunner:
    def __init__(self, responses: list[object]) -> None:
        self.responses = list(responses)
        self.scripts: list[str] = []

    def run_json(self, script: str, *, timeout_seconds: float = 120, cwd=None) -> object:
        self.scripts.append(script)
        return self.responses.pop(0)


def test_get_decodes_base64_secret() -> None:
    encoded = base64.b64encode(b"s3cret").decode()
    manager = DpapiSecretsManager(
        runner=FakeRunner([{"found": True, "secret_base64": encoded}])  # type: ignore[arg-type]
    )
    assert manager.get("smtp-primary") == "s3cret"


def test_get_returns_none_when_absent() -> None:
    manager = DpapiSecretsManager(
        runner=FakeRunner([{"found": False, "secret_base64": None}])  # type: ignore[arg-type]
    )
    assert manager.get("missing") is None


def test_require_raises_when_absent() -> None:
    manager = DpapiSecretsManager(
        runner=FakeRunner([{"found": False}])  # type: ignore[arg-type]
    )
    with pytest.raises(PreconditionFailed):
        manager.require("missing")


def test_set_encodes_value_and_requires_stored() -> None:
    runner = FakeRunner([{"stored": True}])
    manager = DpapiSecretsManager(runner=runner)  # type: ignore[arg-type]
    manager.set("smtp-primary", "s3cret")
    assert "Set-OpsPilotSecret" in runner.scripts[0]
    assert "czNjcmV0" in runner.scripts[0]
    assert "s3cret" not in runner.scripts[0]


def test_set_rejects_empty_value() -> None:
    manager = DpapiSecretsManager(runner=FakeRunner([]))  # type: ignore[arg-type]
    with pytest.raises(PreconditionFailed):
        manager.set("smtp-primary", "")


def test_invalid_secret_names_are_rejected() -> None:
    manager = DpapiSecretsManager(runner=FakeRunner([]))  # type: ignore[arg-type]
    for name in ("", "../escape", "bad name", "a" * 129):
        with pytest.raises(PreconditionFailed):
            manager.get(name)


def test_delete_returns_boolean() -> None:
    runner = FakeRunner([{"removed": True}])
    manager = DpapiSecretsManager(runner=runner)  # type: ignore[arg-type]
    assert manager.delete("old-secret") is True


def test_list_names_is_sorted() -> None:
    manager = DpapiSecretsManager(
        runner=FakeRunner([{"names": ["b", "a"]}])  # type: ignore[arg-type]
    )
    assert manager.list_names() == ["a", "b"]
