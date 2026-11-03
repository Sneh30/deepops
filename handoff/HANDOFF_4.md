# Handoff Manifest — Phase 4
Commit: be6ef8f94928d29bb4e5a6604c461ab7a10f3ee8   Generated: 2026-11-03T08:07:55+00:00   Bundle: dist/opspilot-cloud-4-be6ef8f94928.zip

## Verified-Mac (evidence attached/linked)
- IMPL-401: Health-check polling loop (configurable interval, default ≤60s) — Verified-Mac — evidence: `pytest tests/unit`, `ruff check src tests`, `mypy src`, PSScriptAnalyzer, and this bundle's contents
- IMPL-402: Remediation playbook executor (bounded retries, default 2) calling Mutating Tool Registry — Verified-Mac — evidence: `pytest tests/unit`, `ruff check src tests`, `mypy src`, PSScriptAnalyzer, and this bundle's contents
- IMPL-403: Escalation notifier (email channel, MVP) with full diagnostic context payload — Verified-Mac — evidence: `pytest tests/unit`, `ruff check src tests`, `mypy src`, PSScriptAnalyzer, and this bundle's contents
- IMPL-404: Audit Log (COMP-12) — append-only JSON Lines writer + schema — Verified-Mac — evidence: `pytest tests/unit`, `ruff check src tests`, `mypy src`, PSScriptAnalyzer, and this bundle's contents
- IMPL-405: Approval Gate full policy engine (destructive/irreversible/external/security criteria from MASTER_PROMPT.md §6) — Verified-Mac — evidence: `pytest tests/unit`, `ruff check src tests`, `mypy src`, PSScriptAnalyzer, and this bundle's contents

## Pending Mac Tasks (excluded from the verified evidence above)
- None — every Mac-environment task in this phase is Verified-Mac or Done.

## Requires Windows Verification
- IMPL-410 (Windows verification: inject a real process crash on a test service; measure detection + remediation latency): run on the Windows-side host per IMPLEMENTATION.md §4.2; acceptance criterion is the mapped PRD.md §8 group. Current status: Blocked — Windows-only; see handoff/HANDOFF_4.md.
- IMPL-411 (Windows verification: negative test — attempt to bypass Approval Gate for a destructive action; confirm hard failure): run on the Windows-side host per IMPLEMENTATION.md §4.2; acceptance criterion is the mapped PRD.md §8 group. Current status: Blocked — Windows-only; see handoff/HANDOFF_4.md.

## Explicitly NOT Verified (cannot be verified off-Windows — do not assume pass)
- Every task in the Windows section above: real Windows Services, DPAPI, Caddy/ACME, Windows-container mode, live Terraform apply, and injected-failure timing are not executable on the Mac-side authoring host.
- No result here is a claim that Windows runtime behavior passed.

## Install/Verify Command
.\powershell\install\install.ps1 -Phase 4

## Known Open Items (ENGINEERING_REFERENCE.md §9 ADR references, if any)
- ADR-003: Linux-container workloads remain out of MVP scope; the Windows-side verification must confirm Docker is in Windows-container mode.
- ADR-004: DPAPI is the MVP secrets backend; Vault remains Post-MVP.
- Terraform reference module targets Azure; a different provider requires its own module against the same variable contract.
