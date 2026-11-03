# Handoff Manifest — Phase 0
Commit: 0617e68f1cd935fa1496910df791d4b6e70cef6c   Generated: 2026-11-03T08:08:44+00:00   Bundle: dist/opspilot-cloud-0-0617e68f1cd9.zip

## Verified-Mac (evidence attached/linked)
- IMPL-001: Create repo structure per §1 — Verified-Mac — evidence: `pytest tests/unit`, `ruff check src tests`, `mypy src`, PSScriptAnalyzer, and this bundle's contents
- IMPL-002: Write `Makefile` (`lint`, `test`, `bundle`, `clean` targets) — Verified-Mac — evidence: `pytest tests/unit`, `ruff check src tests`, `mypy src`, PSScriptAnalyzer, and this bundle's contents
- IMPL-003: Write `.github/workflows/ci.yml` (windows-latest: ruff, mypy, PSScriptAnalyzer, pytest unit) — Verified-Mac — evidence: `pytest tests/unit`, `ruff check src tests`, `mypy src`, PSScriptAnalyzer, and this bundle's contents
- IMPL-004: Dependency audit doc seed in ENGINEERING_REFERENCE.md §2 — Verified-Mac — evidence: `pytest tests/unit`, `ruff check src tests`, `mypy src`, PSScriptAnalyzer, and this bundle's contents
- IMPL-005: Config schema skeleton (`config/schema/*.json`) — Verified-Mac — evidence: `pytest tests/unit`, `ruff check src tests`, `mypy src`, PSScriptAnalyzer, and this bundle's contents

## Pending Mac Tasks (excluded from the verified evidence above)
- None — every Mac-environment task in this phase is Verified-Mac or Done.

## Requires Windows Verification
- None recorded for this phase.

## Explicitly NOT Verified (cannot be verified off-Windows — do not assume pass)
- Every task in the Windows section above: real Windows Services, DPAPI, Caddy/ACME, Windows-container mode, live Terraform apply, and injected-failure timing are not executable on the Mac-side authoring host.
- No result here is a claim that Windows runtime behavior passed.

## Install/Verify Command
.\powershell\install\install.ps1 -Phase 0

## Known Open Items (ENGINEERING_REFERENCE.md §9 ADR references, if any)
- ADR-003: Linux-container workloads remain out of MVP scope; the Windows-side verification must confirm Docker is in Windows-container mode.
- ADR-004: DPAPI is the MVP secrets backend; Vault remains Post-MVP.
- Terraform reference module targets Azure; a different provider requires its own module against the same variable contract.
