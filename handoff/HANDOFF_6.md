# Handoff Manifest — Phase 6
Commit: be6ef8f94928d29bb4e5a6604c461ab7a10f3ee8   Generated: 2026-11-03T08:07:56+00:00   Bundle: dist/opspilot-cloud-6-be6ef8f94928.zip

## Verified-Mac (evidence attached/linked)
- IMPL-601: One-call rollback API (restore from checkpoint, full state equality verification) — Verified-Mac — evidence: `pytest tests/unit`, `ruff check src tests`, `mypy src`, PSScriptAnalyzer, and this bundle's contents
- IMPL-602: Weekly report generator (uptime, incidents, remediations from Audit Log + Incident Engine history) — Verified-Mac — evidence: `pytest tests/unit`, `ruff check src tests`, `mypy src`, PSScriptAnalyzer, and this bundle's contents
- IMPL-603: Resource utilization recorder: periodic sampling of CPU/memory/disk per host/app via COMP-06, written to the state store with min/avg/max rollup, surfaced in the weekly report — Verified-Mac — evidence: `pytest tests/unit`, `ruff check src tests`, `mypy src`, PSScriptAnalyzer, and this bundle's contents
- IMPL-604: `install.ps1 -Upgrade` flow: checkpoint → stop service → swap binaries/config → start service → health-check (timeout/retry per SAS.md §4) → auto-rollback on failure (IMPLEMENTATION.md §9 lettered sequence) — Verified-Mac — evidence: `pytest tests/unit`, `ruff check src tests`, `mypy src`, PSScriptAnalyzer, and this bundle's contents
- IMPL-611: Windows/Either verification: generated report content-completeness test, including utilization summary — Verified-Mac (Either task, verified on the Mac authoring host) — evidence: `pytest tests/unit`, `ruff check src tests`, `mypy src`, PSScriptAnalyzer, and this bundle's contents

## Pending Mac Tasks (excluded from the verified evidence above)
- None — every Mac-environment task in this phase is Verified-Mac or Done.

## Requires Windows Verification
- IMPL-610 (Windows verification: checkpoint → corrupt state → rollback → state-equality assertion; timing vs NFR-006): run on the Windows-side host per IMPLEMENTATION.md §4.2; acceptance criterion is the mapped PRD.md §8 group. Current status: Blocked — Windows-only; see handoff/HANDOFF_6.md.
- IMPL-612 (Windows verification: real upgrade run of IMPL-604 against a live installed instance; confirm each lettered sub-step (§9) executes and a forced post-upgrade health-check failure triggers automatic rollback): run on the Windows-side host per IMPLEMENTATION.md §4.2; acceptance criterion is the mapped PRD.md §8 group. Current status: Blocked — Windows-only; see handoff/HANDOFF_6.md.

## Explicitly NOT Verified (cannot be verified off-Windows — do not assume pass)
- Every task in the Windows section above: real Windows Services, DPAPI, Caddy/ACME, Windows-container mode, live Terraform apply, and injected-failure timing are not executable on the Mac-side authoring host.
- No result here is a claim that Windows runtime behavior passed.

## Install/Verify Command
.\powershell\install\install.ps1 -Phase 6

## Known Open Items (ENGINEERING_REFERENCE.md §9 ADR references, if any)
- ADR-003: Linux-container workloads remain out of MVP scope; the Windows-side verification must confirm Docker is in Windows-container mode.
- ADR-004: DPAPI is the MVP secrets backend; Vault remains Post-MVP.
- Terraform reference module targets Azure; a different provider requires its own module against the same variable contract.
