# Handoff Manifest — Phase 3
Commit: 0617e68f1cd935fa1496910df791d4b6e70cef6c   Generated: 2026-11-03T08:08:45+00:00   Bundle: dist/opspilot-cloud-3-0617e68f1cd9.zip

## Verified-Mac (evidence attached/linked)
- IMPL-301: `install_stack.ps1`: pinned, checksum-verified install of windows_exporter, Prometheus, Grafana — Verified-Mac — evidence: `pytest tests/unit`, `ruff check src tests`, `mypy src`, PSScriptAnalyzer, and this bundle's contents
- IMPL-302: Structured log aggregator (Python, JSON Lines, rotation) — Verified-Mac — evidence: `pytest tests/unit`, `ruff check src tests`, `mypy src`, PSScriptAnalyzer, and this bundle's contents
- IMPL-303: Grafana dashboard-as-code (provisioning YAML) for host + app health — Verified-Mac — evidence: `pytest tests/unit`, `ruff check src tests`, `mypy src`, PSScriptAnalyzer, and this bundle's contents

## Pending Mac Tasks (excluded from the verified evidence above)
- None — every Mac-environment task in this phase is Verified-Mac or Done.

## Requires Windows Verification
- IMPL-310 (Windows verification: windows_exporter endpoint returns expected metric families; Prometheus scrapes successfully): run on the Windows-side host per IMPLEMENTATION.md §4.2; acceptance criterion is the mapped PRD.md §8 group. Current status: Blocked — Windows-only; see handoff/HANDOFF_3.md.
- IMPL-311 (Windows verification: Grafana dashboard renders without error against live Prometheus data): run on the Windows-side host per IMPLEMENTATION.md §4.2; acceptance criterion is the mapped PRD.md §8 group. Current status: Blocked — Windows-only; see handoff/HANDOFF_3.md.

## Explicitly NOT Verified (cannot be verified off-Windows — do not assume pass)
- Every task in the Windows section above: real Windows Services, DPAPI, Caddy/ACME, Windows-container mode, live Terraform apply, and injected-failure timing are not executable on the Mac-side authoring host.
- No result here is a claim that Windows runtime behavior passed.

## Install/Verify Command
.\powershell\install\install.ps1 -Phase 3

## Known Open Items (ENGINEERING_REFERENCE.md §9 ADR references, if any)
- ADR-003: Linux-container workloads remain out of MVP scope; the Windows-side verification must confirm Docker is in Windows-container mode.
- ADR-004: DPAPI is the MVP secrets backend; Vault remains Post-MVP.
- Terraform reference module targets Azure; a different provider requires its own module against the same variable contract.
