# Handoff Manifest — Phase 2
Commit: be6ef8f94928d29bb4e5a6604c461ab7a10f3ee8   Generated: 2026-11-03T08:07:55+00:00   Bundle: dist/opspilot-cloud-2-be6ef8f94928.zip

## Verified-Mac (evidence attached/linked)
- IMPL-201: Windows Container image build script (Dockerfile targeting `mcr.microsoft.com/windows/servercore` or `nanoserver` base) — Verified-Mac — evidence: `pytest tests/unit`, `ruff check src tests`, `mypy src`, PSScriptAnalyzer, and this bundle's contents
- IMPL-202: Blue/green rollout logic in Deployment Engine per SAS.md §7 — Verified-Mac — evidence: `pytest tests/unit`, `ruff check src tests`, `mypy src`, PSScriptAnalyzer, and this bundle's contents
- IMPL-203: GitHub Actions workflow: on release-branch merge, trigger deploy via repository-dispatch — Verified-Mac — evidence: `pytest tests/unit`, `ruff check src tests`, `mypy src`, PSScriptAnalyzer, and this bundle's contents

## Pending Mac Tasks (excluded from the verified evidence above)
- None — every Mac-environment task in this phase is Verified-Mac or Done.

## Requires Windows Verification
- IMPL-210 (Windows verification: build + run IMPL-201 container image in Windows-container mode (no WSL/Linux-container fallback)): run on the Windows-side host per IMPLEMENTATION.md §4.2; acceptance criterion is the mapped PRD.md §8 group. Current status: Blocked — Windows-only; see handoff/HANDOFF_2.md.
- IMPL-211 (Windows verification: full blue/green deploy + forced-failure rollback test (NFR-006 timing)): run on the Windows-side host per IMPLEMENTATION.md §4.2; acceptance criterion is the mapped PRD.md §8 group. Current status: Blocked — Windows-only; see handoff/HANDOFF_2.md.
- IMPL-212 (Windows verification: CI trigger fires real deploy on a `windows-latest` runner): run on the Windows-side host per IMPLEMENTATION.md §4.2; acceptance criterion is the mapped PRD.md §8 group. Current status: Blocked — Windows-only; see handoff/HANDOFF_2.md.

## Explicitly NOT Verified (cannot be verified off-Windows — do not assume pass)
- Every task in the Windows section above: real Windows Services, DPAPI, Caddy/ACME, Windows-container mode, live Terraform apply, and injected-failure timing are not executable on the Mac-side authoring host.
- No result here is a claim that Windows runtime behavior passed.

## Install/Verify Command
.\powershell\install\install.ps1 -Phase 2

## Known Open Items (ENGINEERING_REFERENCE.md §9 ADR references, if any)
- ADR-003: Linux-container workloads remain out of MVP scope; the Windows-side verification must confirm Docker is in Windows-container mode.
- ADR-004: DPAPI is the MVP secrets backend; Vault remains Post-MVP.
- Terraform reference module targets Azure; a different provider requires its own module against the same variable contract.
