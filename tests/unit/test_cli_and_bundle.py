from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

from orchestrator.bundle import TaskRow, build_bundle, parse_tasks, render_manifest
from orchestrator.cli import build_parser, main

IMPLEMENTATION = """# Test implementation

### Phase 1 — Foundation

| ID | Task | Environment | Depends on | Requirement | Status |
|---|---|---|---|---|---|
| IMPL-101 | Sample task | Mac | — | FR-001 | Not Started |
| IMPL-102 | Windows task | Windows | IMPL-101 | FR-002 | Blocked |
| IMPL-103 | Either task | Either | — | FR-003 | Not Started |

### Phase 2 — Next

| ID | Task | Environment | Depends on | Requirement | Status |
|---|---|---|---|---|---|
| IMPL-201 | Other task | Mac | — | FR-004 | Not Started |

## 5. Other
"""


def test_parse_tasks_groups_by_phase(tmp_path) -> None:
    path = tmp_path / "IMPLEMENTATION.md"
    path.write_text(IMPLEMENTATION, encoding="utf-8")
    phases = parse_tasks(path)
    assert list(phases) == ["1", "2"]
    assert [task.task_id for task in phases["1"]] == ["IMPL-101", "IMPL-102", "IMPL-103"]
    assert phases["1"][1].environment == "Windows"


def test_build_bundle_creates_zip_and_manifest(tmp_path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "IMPLEMENTATION.md").write_text(IMPLEMENTATION, encoding="utf-8")
    (repo / "README.md").write_text("# demo\n", encoding="utf-8")
    bundle, manifest = build_bundle(repo, "1", output_dir=tmp_path / "dist")
    assert bundle.exists()
    assert bundle.suffix == ".zip"
    assert manifest.exists()
    text = manifest.read_text(encoding="utf-8")
    assert "Handoff Manifest — Phase 1" in text
    assert "IMPL-102" in text
    assert "Requires Windows Verification" in text
    assert "Explicitly NOT Verified" in text


def test_unknown_phase_fails(tmp_path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "IMPLEMENTATION.md").write_text(IMPLEMENTATION, encoding="utf-8")
    import pytest

    with pytest.raises(SystemExit):
        build_bundle(repo, "99", output_dir=tmp_path / "dist")


def _manifest(tasks: list[TaskRow], phase: str = "7") -> str:
    return render_manifest(
        phase=phase,
        tasks=tasks,
        sha="abc123",
        bundle=Path("dist/opspilot-cloud-7-abc123.zip"),
        generated=datetime(2026, 1, 1, tzinfo=UTC),
    )


def test_manifest_separates_verified_pending_and_windows_tasks() -> None:
    text = _manifest(
        [
            TaskRow("IMPL-701", "Verified Mac task", "Mac", "Verified-Mac"),
            TaskRow("IMPL-702", "Incomplete Mac task", "Mac", "In Progress"),
            TaskRow("IMPL-710", "Done Mac task", "Mac", "Done"),
            TaskRow("IMPL-611", "Either task", "Either", "Verified-Mac"),
            TaskRow("IMPL-720", "Windows task", "Windows", "Blocked — Windows-only"),
            TaskRow(
                "IMPL-721",
                "Verified Windows task",
                "Windows",
                "Verified-Windows",
            ),
            TaskRow(
                "IMPL-722",
                "Either runner task",
                "Either (runner is Windows)",
                "Blocked — Windows-only",
            ),
        ]
    )
    verified, rest = text.split("## Pending Mac Tasks", 1)
    pending, rest = rest.split("## Requires Windows Verification", 1)
    windows, rest = rest.split("## Verified-Windows", 1)
    assert "IMPL-701" in verified
    assert "IMPL-710" in verified
    assert "IMPL-611" in verified
    assert "IMPL-702" not in verified
    assert "IMPL-720" not in verified
    assert "IMPL-702" in pending
    assert "IMPL-701" not in pending
    assert "IMPL-720" in windows
    assert "IMPL-722" in windows
    assert "IMPL-721" not in windows
    assert "IMPL-722" not in pending
    assert "IMPL-721" in rest


def test_manifest_never_presents_unverified_mac_work_as_passing() -> None:
    text = _manifest([TaskRow("IMPL-703", "Pending backend", "Mac", "Not Started")])
    assert "None — no Mac-environment task" in text
    assert "Pending backend — Not Started" in text
    assert "do not treat this task as verified" in text


def test_phase_7_manifest_records_adapter_and_mode_b_caveats() -> None:
    text = _manifest([TaskRow("IMPL-707", "Adapter", "Mac", "Verified-Mac")])
    assert "IMPL-707" in text
    assert "ADR-008" in text
    assert "not claimed" in text
    assert "IMPL-712" in text
    assert "Mode B" in text
    assert "IMPL-713" in text
    phase_one = _manifest([TaskRow("IMPL-101", "Foundation", "Mac", "Verified-Mac")], phase="1")
    assert "IMPL-707" not in phase_one
    assert "Mode B" not in phase_one


def test_cli_check_config_validates_default_configs() -> None:
    assert main(["check-config"]) == 0


def test_cli_status_reports_empty_state(tmp_path) -> None:
    assert main(["--data-dir", str(tmp_path), "status"]) == 0


def test_parser_requires_a_command() -> None:
    import pytest

    with pytest.raises(SystemExit):
        build_parser().parse_args([])


def test_cli_rejects_malformed_override(tmp_path) -> None:
    assert (
        main(
            [
                "--data-dir",
                str(tmp_path),
                "provision",
                "--override",
                "not-a-pair",
            ]
        )
        == 1
    )
