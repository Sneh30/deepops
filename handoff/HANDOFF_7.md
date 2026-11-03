# Handoff Manifest — Phase 7
Commit: be6ef8f94928d29bb4e5a6604c461ab7a10f3ee8   Generated: 2026-11-03T08:07:56+00:00   Bundle: dist/opspilot-cloud-7-be6ef8f94928.zip

## Verified-Mac (evidence attached/linked)
- IMPL-701: Internal event bus (in-process pub/sub, `asyncio.Queue`-backed) — SAS.md §5.1 — Verified-Mac — evidence: `pytest tests/unit`, `ruff check src tests`, `mypy src`, PSScriptAnalyzer, and this bundle's contents
- IMPL-702: Wire event-bus emission into COMP-01, 03, 04, 05, 06, 07, 12 (each emits its existing structured event to the bus in addition to its own log/audit write — no new event types invented, same data) — Verified-Mac — evidence: `pytest tests/unit`, `ruff check src tests`, `mypy src`, PSScriptAnalyzer, and this bundle's contents
- IMPL-703: COMP-13 FastAPI/ASGI backend: SSE endpoint streaming bus events to connected clients — Verified-Mac — evidence: `pytest tests/unit`, `ruff check src tests`, `mypy src`, PSScriptAnalyzer, and this bundle's contents
- IMPL-704: Frontend SPA scaffold: CSS Grid layout (top bar, main grid, right rail, bottom strip per UI_SPEC.md §2) — Verified-Mac — evidence: `pytest tests/unit`, `ruff check src tests`, `mypy src`, PSScriptAnalyzer, and this bundle's contents
- IMPL-705: Top bar service-status tiles (§3) wired to real component health + event pulses — Verified-Mac — evidence: `pytest tests/unit`, `ruff check src tests`, `mypy src`, PSScriptAnalyzer, and this bundle's contents
- IMPL-706: Users × Services access matrix grid (§4): rows from live session/connection events, columns per service, cell states per §4.1 — Verified-Mac — evidence: `pytest tests/unit`, `ruff check src tests`, `mypy src`, PSScriptAnalyzer, and this bundle's contents
- IMPL-707: Per-database query-event adapter interface (named per target DB engine, ADR-008) — ships as a documented extension point, not a generic/fake implementation — Verified-Mac — evidence: `pytest tests/unit`, `ruff check src tests`, `mypy src`, PSScriptAnalyzer, and this bundle's contents
- IMPL-708: Connected-users bottom-strip table + raw event ticker (§6) — Verified-Mac — evidence: `pytest tests/unit`, `ruff check src tests`, `mypy src`, PSScriptAnalyzer, and this bundle's contents
- IMPL-709: Right rail: Server Summary tab (§5.1) — Verified-Mac — evidence: `pytest tests/unit`, `ruff check src tests`, `mypy src`, PSScriptAnalyzer, and this bundle's contents
- IMPL-710: Right rail: Observability tab — live metric charts + structured log stream (§5.2) — Verified-Mac — evidence: `pytest tests/unit`, `ruff check src tests`, `mypy src`, PSScriptAnalyzer, and this bundle's contents
- IMPL-711: Right rail: Agent Thinking tab, Structured Trace mode (§5.3 Mode A), sourced directly from COMP-01's decision log via the event bus — Verified-Mac — evidence: `pytest tests/unit`, `ruff check src tests`, `mypy src`, PSScriptAnalyzer, and this bundle's contents
- IMPL-712: Right rail: Agent Thinking tab, Raw Token Stream mode + toggle (§5.3 Mode B), requires a token-stream hook on the agent runtime's LLM calls — Verified-Mac — evidence: `pytest tests/unit`, `ruff check src tests`, `mypy src`, PSScriptAnalyzer, and this bundle's contents
- IMPL-713: Reverse-proxy COMP-13 through Caddy (COMP-10) at a dedicated path, TLS included — Verified-Mac — evidence: `pytest tests/unit`, `ruff check src tests`, `mypy src`, PSScriptAnalyzer, and this bundle's contents

## Pending Mac Tasks (excluded from the verified evidence above)
- None — every Mac-environment task in this phase is Verified-Mac or Done.

## Requires Windows Verification
- IMPL-720 (Windows verification: SSE stream delivers a real injected event to a connected browser client within NFR-013's ≤500ms budget): run on the Windows-side host per IMPLEMENTATION.md §4.2; acceptance criterion is the mapped PRD.md §8 group. Current status: Blocked — Windows-only; see handoff/HANDOFF_7.md.
- IMPL-721 (Windows verification: access-matrix correctness — a user with no grant to a service never shows a lit cell, across a real multi-user/multi-service test scenario): run on the Windows-side host per IMPLEMENTATION.md §4.2; acceptance criterion is the mapped PRD.md §8 group. Current status: Blocked — Windows-only; see handoff/HANDOFF_7.md.
- IMPL-722 (Windows verification: fabrication audit — compare UI-rendered events against the backend event log over a test session; zero unmatched UI events): run on the Windows-side host per IMPLEMENTATION.md §4.2; acceptance criterion is the mapped PRD.md §8 group. Current status: Blocked — Windows-only; see handoff/HANDOFF_7.md.
- IMPL-723 (Windows verification: kill and restart the dashboard UI service; confirm Control Plane operation is unaffected and dashboard reconnects/backfills within NFR-014's ≤5s): run on the Windows-side host per IMPLEMENTATION.md §4.2; acceptance criterion is the mapped PRD.md §8 group. Current status: Blocked — Windows-only; see handoff/HANDOFF_7.md.

## Explicitly NOT Verified (cannot be verified off-Windows — do not assume pass)
- Every task in the Windows section above: real Windows Services, DPAPI, Caddy/ACME, Windows-container mode, live Terraform apply, and injected-failure timing are not executable on the Mac-side authoring host.
- No result here is a claim that Windows runtime behavior passed.
- IMPL-707 (per-database query blink): only the documented ingest contract and the explicit 'DB ADAPTER: NOT CONNECTED' UI state are implemented. No per-DB adapter exists until a target database engine is named (ADR-008), so query blink behavior is not claimed.
- IMPL-712 (Mode B raw token stream): the toggle and the UI_SPEC.md §5.3 fallback to the structured trace are implemented, but the six-document MVP defines no LLM call site, so no live `agent.token` producer or token stream is claimed.
- IMPL-713 (Caddy route): Caddyfile generation and the SSE flush directive are verified statically; live TLS issuance and SSE delivery through Caddy remain IMPL-720/IMPL-723 on the Windows host.

## Install/Verify Command
.\powershell\install\install.ps1 -Phase 7

## Known Open Items (ENGINEERING_REFERENCE.md §9 ADR references, if any)
- ADR-003: Linux-container workloads remain out of MVP scope; the Windows-side verification must confirm Docker is in Windows-container mode.
- ADR-004: DPAPI is the MVP secrets backend; Vault remains Post-MVP.
- Terraform reference module targets Azure; a different provider requires its own module against the same variable contract.
