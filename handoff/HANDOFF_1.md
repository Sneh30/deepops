# Handoff Manifest — Phase 1
Commit: be6ef8f94928d29bb4e5a6604c461ab7a10f3ee8   Generated: 2026-11-03T08:07:55+00:00   Bundle: dist/opspilot-cloud-1-be6ef8f94928.zip

## Verified-Mac (evidence attached/linked)
- IMPL-101: Terraform module skeleton for baseline host networking/firewall (`src/provisioning_engine/terraform/`) — Verified-Mac — evidence: `pytest tests/unit`, `ruff check src tests`, `mypy src`, PSScriptAnalyzer, and this bundle's contents
- IMPL-102: Python wrapper invoking `terraform plan`/`apply` with structured output capture — Verified-Mac — evidence: `pytest tests/unit`, `ruff check src tests`, `mypy src`, PSScriptAnalyzer, and this bundle's contents
- IMPL-103: PowerShell: register a native Windows Service from a packaged app (`New-Service` wrapper, `-WhatIf` support) — Verified-Mac — evidence: `pytest tests/unit`, `ruff check src tests`, `mypy src`, PSScriptAnalyzer, and this bundle's contents
- IMPL-104: Checkpoint/State Store (SQLite-backed) — schema + snapshot/restore API — Verified-Mac — evidence: `pytest tests/unit`, `ruff check src tests`, `mypy src`, PSScriptAnalyzer, and this bundle's contents
- IMPL-105: Mutating Tool Registry + Approval Gate skeleton (deploy_windows_service wired per SAS.md §4 contract) — Verified-Mac — evidence: `pytest tests/unit`, `ruff check src tests`, `mypy src`, PSScriptAnalyzer, and this bundle's contents
- IMPL-106: Caddy config generator + ACME staging-environment cert test — Verified-Mac — evidence: `pytest tests/unit`, `ruff check src tests`, `mypy src`, PSScriptAnalyzer, and this bundle's contents

## Pending Mac Tasks (excluded from the verified evidence above)
- None — every Mac-environment task in this phase is Verified-Mac or Done.

## Requires Windows Verification
- IMPL-110 (Windows verification: install Terraform, apply IMPL-101 module against a real/test cloud target; verify idempotent second apply): run on the Windows-side host per IMPLEMENTATION.md §4.2; acceptance criterion is the mapped PRD.md §8 group. Current status: Blocked — Windows-only; see handoff/HANDOFF_1.md.
- IMPL-111 (Windows verification: register + start a Windows Service from IMPL-103 on a real host; confirm `Get-Service` shows Running): run on the Windows-side host per IMPLEMENTATION.md §4.2; acceptance criterion is the mapped PRD.md §8 group. Current status: Blocked — Windows-only; see handoff/HANDOFF_1.md.
- IMPL-112 (Windows verification: DPAPI round-trip test (`ConvertTo-SecureString`/`ConvertFrom-SecureString`) on real Windows): run on the Windows-side host per IMPLEMENTATION.md §4.2; acceptance criterion is the mapped PRD.md §8 group. Current status: Blocked — Windows-only; see handoff/HANDOFF_1.md.
- IMPL-113 (Windows verification: Caddy issues a real cert against ACME staging for a test domain): run on the Windows-side host per IMPLEMENTATION.md §4.2; acceptance criterion is the mapped PRD.md §8 group. Current status: Blocked — Windows-only; see handoff/HANDOFF_1.md.

## Explicitly NOT Verified (cannot be verified off-Windows — do not assume pass)
- Every task in the Windows section above: real Windows Services, DPAPI, Caddy/ACME, Windows-container mode, live Terraform apply, and injected-failure timing are not executable on the Mac-side authoring host.
- No result here is a claim that Windows runtime behavior passed.

## Install/Verify Command
.\powershell\install\install.ps1 -Phase 1

## Known Open Items (ENGINEERING_REFERENCE.md §9 ADR references, if any)
- ADR-003: Linux-container workloads remain out of MVP scope; the Windows-side verification must confirm Docker is in Windows-container mode.
- ADR-004: DPAPI is the MVP secrets backend; Vault remains Post-MVP.
- Terraform reference module targets Azure; a different provider requires its own module against the same variable contract.
