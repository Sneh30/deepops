"""Phase bundle + Handoff Manifest generator (IMPLEMENTATION.md §6)."""

from __future__ import annotations

import argparse
import os
import re
import subprocess
import zipfile
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from common.config import REPO_ROOT, atomic_write_text

_TASK_RE = re.compile(r"^\|\s*\*{0,2}(IMPL-[0-9]+)\*{0,2}\s*\|")
_PHASE_RE = re.compile(r"^### Phase ([0-9]+)")


@dataclass(frozen=True)
class TaskRow:
    task_id: str
    title: str
    environment: str
    status: str


def parse_tasks(implementation_path: Path) -> dict[str, list[TaskRow]]:
    phases: dict[str, list[TaskRow]] = {}
    current: str | None = None
    for line in implementation_path.read_text(encoding="utf-8").splitlines():
        phase_match = _PHASE_RE.match(line)
        if phase_match:
            current = phase_match.group(1)
            phases.setdefault(current, [])
            continue
        if line.startswith("## 5."):
            current = None
            continue
        if current is None:
            continue
        match = _TASK_RE.match(line)
        if not match:
            continue
        columns = [column.strip() for column in line.strip().strip("|").split("|")]
        if len(columns) < 5:
            continue
        task_id = match.group(1)
        title = columns[1].replace("**", "").strip()
        environment = columns[2] if len(columns) >= 6 else "Mac"
        status = columns[-1].replace("*", "").strip()
        phases[current].append(
            TaskRow(
                task_id=task_id,
                title=title,
                environment=environment,
                status=status,
            )
        )
    return phases


def git_sha(repo: Path) -> str:
    try:
        completed = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=str(repo),
            capture_output=True,
            text=True,
            check=False,
        )
    except FileNotFoundError:
        return "no-git"
    if completed.returncode != 0:
        return "uncommitted"
    return completed.stdout.strip()


def tracked_files(repo: Path) -> list[Path]:
    try:
        completed = subprocess.run(
            ["git", "ls-files", "--cached", "--others", "--exclude-standard", "-z"],
            cwd=str(repo),
            capture_output=True,
            check=False,
        )
    except FileNotFoundError:
        completed = None
    if completed is not None and completed.returncode == 0:
        names = [name for name in completed.stdout.decode("utf-8").split("\0") if name]
        return [repo / name for name in names if (repo / name).is_file()]
    excluded = {".git", ".venv", ".tools", ".tmp", ".pip-cache", "dist", "build"}
    files: list[Path] = []
    for root, directories, filenames in os.walk(repo):
        directories[:] = [
            directory
            for directory in directories
            if directory not in excluded and not directory.startswith("__pycache__")
        ]
        for filename in filenames:
            files.append(Path(root) / filename)
    return files


def build_bundle(repo: Path, phase: str, output_dir: Path | None = None) -> tuple[Path, Path]:
    phases = parse_tasks(repo / "IMPLEMENTATION.md")
    if phase not in phases:
        raise SystemExit(f"unknown phase {phase!r}; expected one of {sorted(phases)}")
    tasks = phases[phase]
    short_sha = git_sha(repo)[:12]
    generated = datetime.now(UTC).replace(microsecond=0)
    bundle_name = f"opspilot-cloud-{phase}-{short_sha}.zip"
    destination_dir = output_dir or repo / "dist"
    destination_dir.mkdir(parents=True, exist_ok=True)
    bundle_path = destination_dir / bundle_name

    with zipfile.ZipFile(bundle_path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for path in tracked_files(repo):
            relative = path.relative_to(repo)
            if relative.parts[0] in {"dist", ".git"}:
                continue
            archive.write(path, arcname=str(relative))

    manifest = render_manifest(
        phase=phase,
        tasks=tasks,
        sha=git_sha(repo),
        bundle=bundle_path.relative_to(repo)
        if bundle_path.is_relative_to(repo)
        else bundle_path,
        generated=generated,
    )
    manifest_path = repo / "handoff" / f"HANDOFF_{phase}.md"
    atomic_write_text(manifest_path, manifest)
    return bundle_path, manifest_path


def _is_mac_environment(environment: str) -> bool:
    """True for authoring-environment tasks, including Either tasks.

    An environment such as ``Either (runner is Windows)`` mentions both words
    but is still a Windows-side task; the Windows marker wins.
    """
    if "Windows" in environment:
        return False
    return "Mac" in environment or "Either" in environment


def _is_windows_environment(environment: str) -> bool:
    return "Windows" in environment


def _mac_verified(status: str) -> bool:
    return status in {"Verified-Mac", "Done"}


def _windows_verified(status: str) -> bool:
    return status in {"Verified-Windows", "Done"}


def _verified_evidence(task: TaskRow) -> str:
    suffix = (
        " (Either task, verified on the Mac authoring host)"
        if "Either" in task.environment and "Windows" not in task.environment
        else ""
    )
    return (
        f"- {task.task_id}: {task.title} — {task.status}{suffix} — "
        "evidence: `pytest tests/unit`, `ruff check src tests`, `mypy src`, "
        "PSScriptAnalyzer, and this bundle's contents"
    )


def render_manifest(
    *,
    phase: str,
    tasks: list[TaskRow],
    sha: str,
    bundle: Path,
    generated: datetime,
) -> str:
    # A handoff may only claim what was actually verified. Mac-environment tasks
    # appear as verified evidence only at Verified-Mac/Done; incomplete Mac work
    # is listed separately instead of being reported as passing.
    verified_mac = [
        task
        for task in tasks
        if _is_mac_environment(task.environment) and _mac_verified(task.status)
    ]
    pending_mac = [
        task
        for task in tasks
        if _is_mac_environment(task.environment) and not _mac_verified(task.status)
    ]
    windows = [
        task
        for task in tasks
        if _is_windows_environment(task.environment) and not _windows_verified(task.status)
    ]
    verified_windows = [
        task
        for task in tasks
        if _is_windows_environment(task.environment) and _windows_verified(task.status)
    ]
    lines = [
        f"# Handoff Manifest — Phase {phase}",
        f"Commit: {sha}   Generated: {generated.isoformat()}   Bundle: {bundle}",
        "",
        "## Verified-Mac (evidence attached/linked)",
    ]
    if verified_mac:
        lines.extend(_verified_evidence(task) for task in verified_mac)
    else:
        lines.append("- None — no Mac-environment task in this phase is Verified-Mac or Done yet.")
    lines.extend(["", "## Pending Mac Tasks (excluded from the verified evidence above)"])
    if pending_mac:
        for task in pending_mac:
            lines.append(
                f"- {task.task_id}: {task.title} — {task.status} — Mac work is incomplete; "
                "do not treat this task as verified in this handoff."
            )
    else:
        lines.append("- None — every Mac-environment task in this phase is Verified-Mac or Done.")
    lines.extend(["", "## Requires Windows Verification"])
    if windows:
        for task in windows:
            lines.append(
                f"- {task.task_id} ({task.title}): run on the Windows-side host per "
                f"IMPLEMENTATION.md §4.2; acceptance criterion is the mapped PRD.md §8 group. "
                f"Current status: {task.status}."
            )
    else:
        lines.append("- None recorded for this phase.")
    if verified_windows:
        lines.extend(["", "## Verified-Windows (recorded on the Windows host)"])
        for task in verified_windows:
            lines.append(f"- {task.task_id}: {task.title} — {task.status}")
    lines.extend(
        [
            "",
            "## Explicitly NOT Verified (cannot be verified off-Windows — do not assume pass)",
            "- Every task in the Windows section above: real Windows Services, DPAPI, Caddy/ACME, "
            "Windows-container mode, live Terraform apply, and injected-failure timing are "
            "not executable on the Mac-side authoring host.",
            "- No result here is a claim that Windows runtime behavior passed.",
        ]
    )
    if phase == "7":
        lines.extend(
            [
                "- IMPL-707 (per-database query blink): only the documented ingest contract and "
                "the explicit 'DB ADAPTER: NOT CONNECTED' UI state are implemented. No per-DB "
                "adapter exists until a target database engine is named (ADR-008), so query "
                "blink behavior is not claimed.",
                "- IMPL-712 (Mode B raw token stream): the toggle and the UI_SPEC.md §5.3 fallback "
                "to the structured trace are implemented, but the six-document MVP defines no "
                "LLM call site, so no live `agent.token` producer or token stream is claimed.",
                "- IMPL-713 (Caddy route): Caddyfile generation and the SSE flush directive are "
                "verified statically; live TLS issuance and SSE delivery through Caddy remain "
                "IMPL-720/IMPL-723 on the Windows host.",
            ]
        )
    lines.extend(
        [
            "",
            "## Install/Verify Command",
            f".\\powershell\\install\\install.ps1 -Phase {phase}",
            "",
            "## Known Open Items (ENGINEERING_REFERENCE.md §9 ADR references, if any)",
            "- ADR-003: Linux-container workloads remain out of MVP scope; the Windows-side "
            "verification must confirm Docker is in Windows-container mode.",
            "- ADR-004: DPAPI is the MVP secrets backend; Vault remains Post-MVP.",
            "- Terraform reference module targets Azure; a different provider requires its own "
            "module against the same variable contract.",
            "",
        ]
    )
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Build an OpsPilot phase bundle")
    parser.add_argument("--phase", required=True)
    parser.add_argument("--repo", default=str(REPO_ROOT))
    parser.add_argument("--output-dir", default=None)
    args = parser.parse_args(argv)
    bundle, manifest = build_bundle(
        Path(args.repo), args.phase, Path(args.output_dir) if args.output_dir else None
    )
    print(f"bundle: {bundle}")
    print(f"manifest: {manifest}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
