# Handoff Manifest — Phase 5
Commit: 64150577dba1fedc97d4596628ebb204e76f27d3   Generated: 2026-11-03T09:20:16+00:00   Bundle: dist/opspilot-cloud-5-64150577dba1.zip

## Verified-Mac (evidence attached/linked)
- IMPL-501: Baseline hardening profile script (firewall rules, disabled unnecessary services, per ENGINEERING_REFERENCE.md §5 checklist) — Verified-Mac — evidence: `pytest tests/unit`, `ruff check src tests`, `mypy src`, PSScriptAnalyzer, and this bundle's contents
- IMPL-502: Drift detector: periodic reconciliation of live config vs. declared config — Verified-Mac — evidence: `pytest tests/unit`, `ruff check src tests`, `mypy src`, PSScriptAnalyzer, and this bundle's contents

## Pending Mac Tasks (excluded from the verified evidence above)
- None — every Mac-environment task in this phase is Verified-Mac or Done.

## Requires Windows Verification
- IMPL-510 (Windows verification: apply hardening profile to a real host; verify against checklist): run on the Windows-side host per IMPLEMENTATION.md §4.2; acceptance criterion is the mapped PRD.md §8 group. Current status: Blocked — Windows-only; see handoff/HANDOFF_5.md.
- IMPL-511 (Windows verification: make an out-of-band config change; confirm drift detector alerts): run on the Windows-side host per IMPLEMENTATION.md §4.2; acceptance criterion is the mapped PRD.md §8 group. Current status: Blocked — Windows-only; see handoff/HANDOFF_5.md.

## Explicitly NOT Verified (cannot be verified off-Windows — do not assume pass)
- Every task in the Windows section above: real Windows Services, DPAPI, Caddy/ACME, Windows-container mode, live Terraform apply, and injected-failure timing are not executable on the Mac-side authoring host.
- No result here is a claim that Windows runtime behavior passed.

## Install/Verify Command
.\powershell\install\install.ps1 -Phase 5

## Known Open Items (ENGINEERING_REFERENCE.md §9 ADR references, if any)
- ADR-003: Linux-container workloads remain out of MVP scope; the Windows-side verification must confirm Docker is in Windows-container mode.
- ADR-004: DPAPI is the MVP secrets backend; Vault remains Post-MVP.
- Terraform reference module targets Azure; a different provider requires its own module against the same variable contract.
