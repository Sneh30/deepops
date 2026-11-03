# IMPLEMENTATION.md — OpsPilot Cloud Implementation Guide

**Status:** v0.1. Governed by [MASTER_PROMPT.md](./MASTER_PROMPT.md). Builds [SAS.md](./SAS.md) to satisfy [PRD.md](./PRD.md).
**Related documents:** [ENGINEERING_REFERENCE.md](./ENGINEERING_REFERENCE.md) holds schemas, commands, and the ADR log referenced below · [UI_SPEC.md](./UI_SPEC.md) holds the full behavioral spec for the Phase 7 Dashboard tasks.

**How to use this document (for any agent, Mac-side or Windows-side, resuming cold):**
1. Read MASTER_PROMPT.md first, in full, if you have not already this session.
2. Find the first task below with status `Not Started` or `Blocked` whose blockers are cleared (status legend in §2).
3. Confirm which environment it belongs to (§0) before starting — doing Windows-only work on Mac, or vice versa, produces either nothing verifiable or nothing at all.
4. Work the task, update its status, and if it's the last task in a phase, produce/consume the phase's Handoff Manifest (§6).

---

## 0. Environment Split at a Glance

| Phase activity | Environment | Why |
|---|---|---|
| Writing Python/PowerShell source, unit tests with mocks, static analysis, documentation | **Mac-side** | No real Windows host needed; macOS Unix tooling is unrestricted for authoring (MASTER_PROMPT.md §0) |
| Packaging (`make bundle`), producing the Handoff Manifest | **Mac-side** | Build orchestration, not runtime |
| Installing onto a real Windows host, registering Windows Services, running PowerShell against live Windows APIs, DPAPI round-trips, Windows Container builds, Caddy/ACME against a real network path, actual Terraform `apply` against cloud resources | **Windows-side** | These cannot be truthfully verified anywhere else (MASTER_PROMPT.md §7) |
| CI pipeline execution | **Either** — GitHub Actions `windows-latest` runners execute real Windows, usable from a trigger initiated on either side | |

---

## 1. Repository Structure

```
opspilot-cloud/
├── MASTER_PROMPT.md
├── PRD.md
├── SAS.md
├── IMPLEMENTATION.md
├── ENGINEERING_REFERENCE.md
├── UI_SPEC.md
├── README.md
├── Makefile                      # Mac-side entry point: make lint / make test / make bundle
├── requirements.txt              # pinned runtime deps (incl. FastAPI/uvicorn for COMP-13)
├── requirements-dev.txt          # pinned lint/test deps (incl. httpx ASGI test client)
├── pyproject.toml                # ruff/mypy/pytest configuration
├── run_control_plane.py          # OpsPilotControlPlane Windows Service entry point
├── run_dashboard.py              # OpsPilotDashboardUI Windows Service entry point (COMP-13)
├── .gitignore
├── .github/
│   └── workflows/
│       ├── ci.yml                # windows-latest: lint, unit test, static analysis
│       └── release.yml           # builds + attaches distributable bundle to a GitHub Release
├── config/
│   ├── schema/                   # JSON Schema for every config file (ENGINEERING_REFERENCE.md §4)
│   └── defaults/
├── src/
│   ├── orchestrator/              # COMP-01 — Python
│   ├── tools_readonly/            # COMP-02 — Python
│   ├── tools_mutating/            # COMP-03 — Python
│   ├── approval_gate/             # COMP-04 — Python
│   ├── checkpoint_store/          # COMP-05 — Python
│   ├── observability/             # COMP-06 — Python + PowerShell installers
│   ├── incident_engine/           # COMP-07 — Python
│   ├── deployment_engine/         # COMP-08 — Python + PowerShell
│   ├── provisioning_engine/       # COMP-09 — Python + Terraform (.tf)
│   ├── reverse_proxy/             # COMP-10 — Caddyfile templates + Python config generator
│   ├── secrets_manager/           # COMP-11 — Python + PowerShell (DPAPI)
│   ├── audit_log/                 # COMP-12 — Python
│   ├── event_bus/                 # COMP-13 transport — durable JSONL journal + asyncio fan-out
│   ├── dashboard_ui/              # COMP-13 — Python (FastAPI/ASGI backend, reducer, SSE, session/query ingest)
│   └── reporting/                 # FR-080/FR-082 weekly operational report
├── frontend/
│   └── dashboard/                  # COMP-13 static SPA source (vanilla HTML/CSS/JS served by dashboard_ui — UI_SPEC.md §8)
├── powershell/
│   ├── install/                   # Windows-side install scripts (entry point: install.ps1)
│   └── modules/                   # Shared PowerShell modules (PSScriptAnalyzer-clean)
├── tests/
│   ├── unit/                      # Mac-runnable, mocked Windows calls
│   └── integration/                # Windows-only; fails fast off-Windows
├── handoff/
│   └── HANDOFF_<phase-id>.md      # Generated per MASTER_PROMPT.md §0 / §6 below
└── docs/
    └── research/                  # Scratch/research notes, never referenced by the 6 canonical docs
```

No new top-level directory may be added without updating this section in the same commit (MASTER_PROMPT.md §4).

---

## 2. Status Legend

`Not Started` → `In Progress` → `Blocked` (with reason) → `Verified-Mac` (Mac-side checks pass) → `Verified-Windows` (Windows-side checks pass) → `Done` (both verified where applicable, docs updated).

A task that has no Windows-only component may go straight from `Verified-Mac` to `Done`. A task with any runtime behavior (MASTER_PROMPT.md §0 rule of thumb) must reach `Verified-Windows` before `Done`.

---

## 3. Development Environment Setup

### 3.1 Mac-side setup
```bash
# Prerequisites: Homebrew, Python 3.11+, Git
brew install python@3.11 git make
python3.11 -m venv .venv && source .venv/bin/activate
pip install -r requirements-dev.txt     # ruff, mypy, pytest, pytest-mock
pip install -r requirements.txt
make lint
make test        # runs tests/unit only — no Windows required
```
`requirements.txt`/`requirements-dev.txt` pin exact versions (MASTER_PROMPT.md §3 principle 2). PowerShell files can be lint-checked on Mac via `pwsh` (PowerShell 7+ is cross-platform for its *tooling*, even though the *product* targets Windows-only at runtime — this is purely a developer-convenience static-analysis step, not a runtime claim):
```bash
brew install --cask powershell
pwsh -NoProfile -Command "Install-Module PSScriptAnalyzer -Scope CurrentUser -Force; Invoke-ScriptAnalyzer -Path ./powershell -Recurse"
```

### 3.2 Windows-side setup
```powershell
# Prerequisites: Windows 11 Pro (22H2+) or Windows Server 2019+, PowerShell 7+, Git for Windows
# Install PowerShell 7 if needed: winget install --id Microsoft.PowerShell --source winget
git clone <repo-url> C:\OpsPilotCloud
cd C:\OpsPilotCloud
.\powershell\install\install.ps1 -Phase <phase-id> -WhatIf   # dry run first, per MASTER_PROMPT.md §4
.\powershell\install\install.ps1 -Phase <phase-id>            # real install
```
`install.ps1` reads the Handoff Manifest for `<phase-id>` (§6) to know exactly what to install and verify. It must support `-WhatIf` (MASTER_PROMPT.md §4) and must never require a bash shell or WSL at any step.

### 3.3 Prerequisite install commands (Windows-side, idempotent)
```powershell
winget install --id Hashicorp.Terraform
winget install --id Python.Python.3.11
winget install --id Docker.DockerDesktop   # Windows-container mode; see ENGINEERING_REFERENCE.md §2 for mode-switch instructions
winget install --id CaddyServer.Caddy
# windows_exporter, Prometheus, Grafana: versioned MSI/zip installers fetched and SHA256-verified
# by src/observability/install_stack.ps1 — never installed via an un-pinned "latest" URL.
```

---

## 4. Implementation Phases and Tasks

Phases mirror PRD.md §9. Each task: **ID**, title, environment, depends-on, satisfies (FR/NFR/COMP), status.

### Phase 0 — Scaffolding (Mac-side)
| ID | Task | Depends on | Satisfies | Status |
|---|---|---|---|---|
| IMPL-001 | Create repo structure per §1 | — | NFR-010 | Verified-Mac |
| IMPL-002 | Write `Makefile` (`lint`, `test`, `bundle`, `clean` targets) | IMPL-001 | MASTER_PROMPT.md §0 | Verified-Mac |
| IMPL-003 | Write `.github/workflows/ci.yml` (windows-latest: ruff, mypy, PSScriptAnalyzer, pytest unit) | IMPL-001 | NFR-001 audit | Verified-Mac |
| IMPL-004 | Dependency audit doc seed in ENGINEERING_REFERENCE.md §2 | — | NFR-001 | Verified-Mac |
| IMPL-005 | Config schema skeleton (`config/schema/*.json`) | IMPL-001 | AP-6 | Verified-Mac |

### Phase 1 — Core provisioning + Windows Service deployment
| ID | Task | Env | Depends on | Satisfies | Status |
|---|---|---|---|---|---|
| IMPL-101 | Terraform module skeleton for baseline host networking/firewall (`src/provisioning_engine/terraform/`) | Mac | IMPL-005 | FR-001 | Verified-Mac |
| IMPL-102 | Python wrapper invoking `terraform plan`/`apply` with structured output capture | Mac | IMPL-101 | FR-002, COMP-09 | Verified-Mac |
| IMPL-103 | PowerShell: register a native Windows Service from a packaged app (`New-Service` wrapper, `-WhatIf` support) | Mac (write) | IMPL-005 | FR-010, COMP-08 | Verified-Mac |
| IMPL-104 | Checkpoint/State Store (SQLite-backed) — schema + snapshot/restore API | Mac | IMPL-005 | FR-070, COMP-05 | Verified-Mac |
| IMPL-105 | Mutating Tool Registry + Approval Gate skeleton (deploy_windows_service wired per SAS.md §4 contract) | Mac | IMPL-103, IMPL-104 | FR-090–092, COMP-03/04 | Verified-Mac |
| IMPL-106 | Caddy config generator + ACME staging-environment cert test | Mac (generator) | IMPL-005 | FR-030/031, COMP-10 | Verified-Mac |
| **IMPL-110** | **Windows verification:** install Terraform, apply IMPL-101 module against a real/test cloud target; verify idempotent second apply | **Windows** | IMPL-102 | NFR-010 test (FR-001–002 group) | Blocked — Windows-only; see handoff/HANDOFF_1.md |
| **IMPL-111** | **Windows verification:** register + start a Windows Service from IMPL-103 on a real host; confirm `Get-Service` shows Running | **Windows** | IMPL-103 | PRD.md §8 acceptance test (FR-010–014 group) | Blocked — Windows-only; see handoff/HANDOFF_1.md |
| **IMPL-112** | **Windows verification:** DPAPI round-trip test (`ConvertTo-SecureString`/`ConvertFrom-SecureString`) on real Windows | **Windows** | IMPL-005 | NFR-009 | Blocked — Windows-only; see handoff/HANDOFF_1.md |
| **IMPL-113** | **Windows verification:** Caddy issues a real cert against ACME staging for a test domain | **Windows** | IMPL-106 | NFR-005, PRD.md §8 (FR-030–032 group) | Blocked — Windows-only; see handoff/HANDOFF_1.md |

### Phase 2 — Windows Container deployment + CI/CD trigger
| ID | Task | Env | Depends on | Satisfies | Status |
|---|---|---|---|---|---|
| IMPL-201 | Windows Container image build script (Dockerfile targeting `mcr.microsoft.com/windows/servercore` or `nanoserver` base) | Mac (write) | IMPL-005 | FR-011 | Verified-Mac |
| IMPL-202 | Blue/green rollout logic in Deployment Engine per SAS.md §7 | Mac | IMPL-105 | FR-012, FR-013 | Verified-Mac |
| IMPL-203 | GitHub Actions workflow: on release-branch merge, trigger deploy via repository-dispatch | Mac | IMPL-003 | FR-020 | Verified-Mac |
| **IMPL-210** | **Windows verification:** build + run IMPL-201 container image in Windows-container mode (no WSL/Linux-container fallback) | **Windows** | IMPL-201 | NFR-001, PRD.md §8 (FR-010–014 group) | Blocked — Windows-only; see handoff/HANDOFF_2.md |
| **IMPL-211** | **Windows verification:** full blue/green deploy + forced-failure rollback test (NFR-006 timing) | **Windows** | IMPL-202, IMPL-110/111 | FR-013, NFR-006 | Blocked — Windows-only; see handoff/HANDOFF_2.md |
| **IMPL-212** | **Windows verification:** CI trigger fires real deploy on a `windows-latest` runner | **Either (runner is Windows)** | IMPL-203 | FR-020, PRD.md §8 (FR-020–021 group) | Blocked — Windows-only; see handoff/HANDOFF_2.md |

### Phase 3 — Observability
| ID | Task | Env | Depends on | Satisfies | Status |
|---|---|---|---|---|---|
| IMPL-301 | `install_stack.ps1`: pinned, checksum-verified install of windows_exporter, Prometheus, Grafana | Mac (write) | IMPL-005 | FR-040, FR-043 | Verified-Mac |
| IMPL-302 | Structured log aggregator (Python, JSON Lines, rotation) | Mac | IMPL-001 | FR-041 | Verified-Mac |
| IMPL-303 | Grafana dashboard-as-code (provisioning YAML) for host + app health | Mac | IMPL-301 | FR-043 | Verified-Mac |
| **IMPL-310** | **Windows verification:** windows_exporter endpoint returns expected metric families; Prometheus scrapes successfully | **Windows** | IMPL-301 | PRD.md §8 (FR-040–044 group) | Blocked — Windows-only; see handoff/HANDOFF_3.md |
| **IMPL-311** | **Windows verification:** Grafana dashboard renders without error against live Prometheus data | **Windows** | IMPL-303, IMPL-310 | FR-043 | Blocked — Windows-only; see handoff/HANDOFF_3.md |

### Phase 4 — Incident handling, remediation, escalation, approval gates
| ID | Task | Env | Depends on | Satisfies | Status |
|---|---|---|---|---|---|
| IMPL-401 | Health-check polling loop (configurable interval, default ≤60s) | Mac | IMPL-105 | FR-050, NFR-003 | Verified-Mac |
| IMPL-402 | Remediation playbook executor (bounded retries, default 2) calling Mutating Tool Registry | Mac | IMPL-105, IMPL-401 | FR-051, NFR-004 | Verified-Mac |
| IMPL-403 | Escalation notifier (email channel, MVP) with full diagnostic context payload | Mac | IMPL-402 | FR-052 | Verified-Mac |
| IMPL-404 | Audit Log (COMP-12) — append-only JSON Lines writer + schema | Mac | IMPL-001 | FR-093, NFR-007 | Verified-Mac |
| IMPL-405 | Approval Gate full policy engine (destructive/irreversible/external/security criteria from MASTER_PROMPT.md §6) | Mac | IMPL-105 | FR-092, NFR-008 | Verified-Mac |
| **IMPL-410** | **Windows verification:** inject a real process crash on a test service; measure detection + remediation latency | **Windows** | IMPL-401, IMPL-402 | NFR-003, NFR-004, PRD.md §8 (FR-050–053 group) | Blocked — Windows-only; see handoff/HANDOFF_4.md |
| **IMPL-411** | **Windows verification:** negative test — attempt to bypass Approval Gate for a destructive action; confirm hard failure | **Windows** | IMPL-405 | NFR-008 | Blocked — Windows-only; see handoff/HANDOFF_4.md |

### Phase 5 — Security hardening + drift detection
| ID | Task | Env | Depends on | Satisfies | Status |
|---|---|---|---|---|---|
| IMPL-501 | Baseline hardening profile script (firewall rules, disabled unnecessary services, per ENGINEERING_REFERENCE.md §5 checklist) | Mac (write) | IMPL-101 | FR-060 | Verified-Mac |
| IMPL-502 | Drift detector: periodic reconciliation of live config vs. declared config | Mac | IMPL-005 | FR-061 | Verified-Mac |
| **IMPL-510** | **Windows verification:** apply hardening profile to a real host; verify against checklist | **Windows** | IMPL-501 | PRD.md §8 (FR-060–062 group) | Blocked — Windows-only; see handoff/HANDOFF_5.md |
| **IMPL-511** | **Windows verification:** make an out-of-band config change; confirm drift detector alerts | **Windows** | IMPL-502 | FR-061 | Blocked — Windows-only; see handoff/HANDOFF_5.md |

### Phase 6 — Rollback/checkpoint completion + reporting
| ID | Task | Env | Depends on | Satisfies | Status |
|---|---|---|---|---|---|
| IMPL-601 | One-call rollback API (restore from checkpoint, full state equality verification) | Mac | IMPL-104 | FR-071 | Verified-Mac |
| IMPL-602 | Weekly report generator (uptime, incidents, remediations from Audit Log + Incident Engine history) | Mac | IMPL-404 | FR-080 | Verified-Mac |
| IMPL-603 | Resource utilization recorder: periodic sampling of CPU/memory/disk per host/app via COMP-06, written to the state store with min/avg/max rollup, surfaced in the weekly report | Mac | IMPL-301, IMPL-602 | FR-082 | Verified-Mac |
| IMPL-604 | `install.ps1 -Upgrade` flow: checkpoint → stop service → swap binaries/config → start service → health-check (timeout/retry per SAS.md §4) → auto-rollback on failure (IMPLEMENTATION.md §9 lettered sequence) | Mac (write) | IMPL-601, IMPL-105 | NFR-006, MASTER_PROMPT.md §3 principle 8 | Verified-Mac |
| **IMPL-610** | **Windows verification:** checkpoint → corrupt state → rollback → state-equality assertion; timing vs NFR-006 | **Windows** | IMPL-601 | NFR-006, PRD.md §8 (FR-070–072 group) | Blocked — Windows-only; see handoff/HANDOFF_6.md |
| **IMPL-611** | **Windows/Either verification:** generated report content-completeness test, including utilization summary | **Either** | IMPL-602, IMPL-603 | PRD.md §8 (FR-080–082 group) | Verified-Mac |
| **IMPL-612** | **Windows verification:** real upgrade run of IMPL-604 against a live installed instance; confirm each lettered sub-step (§9) executes and a forced post-upgrade health-check failure triggers automatic rollback | **Windows** | IMPL-604 | NFR-006, MASTER_PROMPT.md §10 | Blocked — Windows-only; see handoff/HANDOFF_6.md |

### Phase 7 — Mission Control Dashboard (UI_SPEC.md)
Depends on Phases 1–6: this phase visualizes real events from COMP-01, 03, 04, 05, 06, 07, 12, all of which must already exist and be emitting real events for the dashboard to have anything true to show (MASTER_PROMPT.md §7 — do not build this against mocked/fake event sources and call it done).

| ID | Task | Env | Depends on | Satisfies | Status |
|---|---|---|---|---|---|
| IMPL-701 | Internal event bus (in-process pub/sub, `asyncio.Queue`-backed) — SAS.md §5.1 | Mac | IMPL-001 | FR-100, COMP-13 | Verified-Mac |
| IMPL-702 | Wire event-bus emission into COMP-01, 03, 04, 05, 06, 07, 12 (each emits its existing structured event to the bus in addition to its own log/audit write — no new event types invented, same data) | Mac | IMPL-701, IMPL-105, IMPL-301, IMPL-404 | FR-107, NFR-015 | Verified-Mac |
| IMPL-703 | COMP-13 FastAPI/ASGI backend: SSE endpoint streaming bus events to connected clients | Mac | IMPL-701 | FR-100, COMP-13 | Verified-Mac |
| IMPL-704 | Frontend SPA scaffold: CSS Grid layout (top bar, main grid, right rail, bottom strip per UI_SPEC.md §2) | Mac | IMPL-703 | FR-100, FR-108 | Verified-Mac |
| IMPL-705 | Top bar service-status tiles (§3) wired to real component health + event pulses | Mac | IMPL-704, IMPL-702 | FR-101 | Verified-Mac |
| IMPL-706 | Users × Services access matrix grid (§4): rows from live session/connection events, columns per service, cell states per §4.1 | Mac | IMPL-704, IMPL-702 | FR-102 | Verified-Mac |
| IMPL-707 | Per-database query-event adapter interface (named per target DB engine, ADR-008) — ships as a documented extension point, not a generic/fake implementation | Mac | IMPL-706 | FR-102 (blink behavior), flagged 🔴 per ENGINEERING_REFERENCE.md §2 | Verified-Mac |
| IMPL-708 | Connected-users bottom-strip table + raw event ticker (§6) | Mac | IMPL-704, IMPL-702 | FR-103 | Verified-Mac |
| IMPL-709 | Right rail: Server Summary tab (§5.1) | Mac | IMPL-704 | FR-106 | Verified-Mac |
| IMPL-710 | Right rail: Observability tab — live metric charts + structured log stream (§5.2) | Mac | IMPL-704, IMPL-702 | FR-104 | Verified-Mac |
| IMPL-711 | Right rail: Agent Thinking tab, Structured Trace mode (§5.3 Mode A), sourced directly from COMP-01's decision log via the event bus | Mac | IMPL-704, IMPL-702 | FR-105 | Verified-Mac |
| IMPL-712 | Right rail: Agent Thinking tab, Raw Token Stream mode + toggle (§5.3 Mode B), requires a token-stream hook on the agent runtime's LLM calls | Mac | IMPL-711 | FR-105 | Verified-Mac |
| IMPL-713 | Reverse-proxy COMP-13 through Caddy (COMP-10) at a dedicated path, TLS included | Mac (config) | IMPL-703, IMPL-106 | FR-100 | Verified-Mac |
| **IMPL-720** | **Windows verification:** SSE stream delivers a real injected event to a connected browser client within NFR-013's ≤500ms budget | **Windows** | IMPL-703 | NFR-013, PRD.md §8 (FR-100–108 group) | Blocked — Windows-only; see handoff/HANDOFF_7.md |
| **IMPL-721** | **Windows verification:** access-matrix correctness — a user with no grant to a service never shows a lit cell, across a real multi-user/multi-service test scenario | **Windows** | IMPL-706 | PRD.md §8 (FR-100–108 group) | Blocked — Windows-only; see handoff/HANDOFF_7.md |
| **IMPL-722** | **Windows verification:** fabrication audit — compare UI-rendered events against the backend event log over a test session; zero unmatched UI events | **Windows** | IMPL-702, IMPL-705–712 | NFR-015 | Blocked — Windows-only; see handoff/HANDOFF_7.md |
| **IMPL-723** | **Windows verification:** kill and restart the dashboard UI service; confirm Control Plane operation is unaffected and dashboard reconnects/backfills within NFR-014's ≤5s | **Windows** | IMPL-703 | NFR-014 | Blocked — Windows-only; see handoff/HANDOFF_7.md |

### Phase 8 (Post-MVP) — placeholder, re-scoped after MVP retrospective
FR-003, FR-014 (ADR-003), FR-021, FR-044 extended, FR-081, NFR-011. Not broken into tasks yet; do not start until MVP phases 0–7 reach `Done`.

### 4.1 Mac-Side Verification Evidence

Recorded on the macOS authoring host. These are authoring/static checks only: they
prove the source, schemas, unit-level behavior, and generated configuration, and they
never assert Windows runtime behavior (MASTER_PROMPT.md §7). Re-run them with the
commands in §7 after any change.

| Check | Command | Last observed result |
|---|---|---|
| Python lint | `ruff check src tests` | clean |
| Python types | `mypy src` | success, 81 source files |
| Unit tests | `pytest tests/unit` | 176 passed |
| PowerShell analysis | `pwsh -NoProfile -File powershell/lint.ps1` via `make lint-powershell` | PSScriptAnalyzer: clean |
| Terraform formatting | `terraform -chdir=src/provisioning_engine/terraform fmt -check -recursive` | pass |
| Terraform validation | `terraform -chdir=src/provisioning_engine/terraform validate` | pass (no backend, no apply) |
| Config validation | `python -m orchestrator.cli check-config` | all five default configs valid (host, deployment, alerts, approval, dashboard) |
| Windows CI | GitHub Actions `.github/workflows/ci.yml` on `windows-latest` | authoring and integration jobs passed in run [41610940276](https://github.com/Sneh30/deepops/actions/runs/41610940276) at code commit `f2c685a`; Terraform/container cases skipped when those binaries are absent |

Per-phase Mac evidence:

| Phase | Mac tasks recorded `Verified-Mac` | Evidence |
|---|---|---|
| 0 | IMPL-001–005 | Repository structure, `Makefile`, CI workflow definitions (Windows authoring workflow executed on `windows-latest`; deploy/release workflows still unexecuted), initial config schemas, and dependency-audit seed are present; lint/type/unit checks pass. |
| 1 | IMPL-101–106 | Terraform module validates; provisioning wrapper, Windows Service registration logic, DPAPI wrapper, reverse-proxy/ACME configuration generation, and unit tests pass with Windows calls mocked. |
| 2 | IMPL-201–203 | Windows-image build definitions, blue/green deployment engine, and CI trigger logic are covered by unit tests; the deploy/release workflows are authored but **not executed**, and the container build itself remains IMPL-210. |
| 3 | IMPL-301–303 | Log aggregation, utilization sampling, metrics configuration, pinned stack installer, and Grafana dashboard JSON are covered by unit/static checks. |
| 4 | IMPL-401–405 | Incident detection/remediation/escalation and approval-gate behavior are covered by unit tests, including negative approval cases. |
| 5 | IMPL-501–502 | Hardening and drift-detection logic is covered by unit/static checks; the hardening checklist itself is Windows-only (IMPL-510/511). |
| 6 | IMPL-601–604, 611 | Checkpoint/rollback equality, weekly report, utilization rollup, upgrade sequencing, and report completeness are covered by unit tests. |
| 7 | IMPL-701–713 | See the Phase 7 evidence block below. |

#### Phase 7 evidence (COMP-13 Mission Control)

New tests for this phase:

| Test file | Tests | What it proves |
|---|---|---|
| `tests/unit/test_event_bus.py` | 11 | Event schema/redaction, durable JSONL journal append/rotation/replay offsets, journal follower rotation handling, in-process bus fan-out. |
| `tests/unit/test_dashboard.py` | 34 | Config bounds and schema-backed defaults, reducer session/query/incident/deployment/approval state, snapshot restore, ingest authentication, REST surfaces, SSE frame types, and static-frontend contract (including no `Math.random`). |
| `tests/unit/test_dashboard_observability.py` | 8 | Prometheus response parsing/failure surfacing, bounded metric series, log/audit feed merge and filtering. |
| `tests/unit/test_reverse_proxy.py` | 15 total | Dashboard path route, SSE `flush_interval -1`, path-prefix validation and traversal rejection, default Caddy cutover route. |
| `tests/unit/test_install_scripts.py` | 7 total | Separate `OpsPilotDashboardUI` service installation, upgrade ordering, entry point/config wiring. |

Observed Phase 7 checks:

- `pytest tests/unit` — **176 passed** (one third-party `StarletteDeprecationWarning` from FastAPI's `TestClient` about `httpx`; no test failure).
- `ruff check src tests` — clean.
- `mypy src` — success, 81 source files.
- PSScriptAnalyzer — clean for the dashboard installer changes.
- No timer or random source drives a tile pulse or matrix cell: pulses are emitted only
  for real journal events, and the matrix cell state changes only on matching
  `query.started`/`query.completed`/`query.denied` events.
- The frontend is static vanilla HTML/CSS/JS served by the dashboard backend; there is
  no Node.js/Vite build step at runtime or build time.

Explicit Phase 7 non-claims (important):

- **IMPL-707** is `Verified-Mac` for the documented ingest contract and the explicit
  "DB ADAPTER: NOT CONNECTED" state only. No per-database adapter exists until a target
  DB engine is named (ADR-008); per-query blink behavior is not implemented and is not
  claimed.
- **IMPL-712** is `Verified-Mac` for the Mode B toggle, bounded token display, and the
  UI_SPEC.md §5.3 fallback to the structured trace. The six-document MVP defines no LLM
  call site, so there is no live `agent.token` producer and no live token stream is
  claimed.
- **IMPL-713** is `Verified-Mac` for Caddyfile generation, path routing, and the SSE
  flush directive. Live TLS issuance and SSE delivery through Caddy remain IMPL-720/723
  on the Windows host.
- The dashboard is a separate `OpsPilotDashboardUI` Windows Service (NFR-014); its
  failure/restart isolation and ≤5s backfill are implemented and unit-tested, but the
  real service restart test remains IMPL-723.

### 4.2 Windows Verification Queue

No Windows host has executed this repository's runtime in the current session. Every
task below remains **Blocked — Windows-only**; Mac-side evidence must not be promoted to
`Verified-Windows` or `Done` without the listed Windows run and captured command output.
Each phase's generated `handoff/HANDOFF_<n>.md` is the authoritative handoff contract.

| Task | Phase | Windows verification required | Acceptance evidence |
|---|---|---|---|
| IMPL-110 | 1 | Apply the Terraform module to a real/test cloud target; apply twice | First apply succeeds; second apply reports no changes (idempotent) |
| IMPL-111 | 1 | Register and start the Control Plane Windows Service | `Get-Service OpsPilotControlPlane` shows `Running`; service starts after reboot |
| IMPL-112 | 1 | DPAPI protect/unprotect round-trip under the service account | Ciphertext round-trips; another account cannot decrypt it |
| IMPL-113 | 1 | Request a real certificate from ACME staging for a test domain | Certificate issued and served by Caddy; renewal path recorded |
| IMPL-210 | 2 | Build/run the Windows-container image in Windows-container mode | Container starts and serves traffic; no WSL/Linux-container fallback |
| IMPL-211 | 2 | Full blue/green deploy plus forced-failure rollback | Cutover succeeds; injected failure rolls back within NFR-006 timing |
| IMPL-212 | 2 | Trigger CI deploy on a `windows-latest` runner | Workflow run deploys and records the result |
| IMPL-310 | 3 | windows_exporter endpoint scraped by Prometheus | Expected metric families present in Prometheus |
| IMPL-311 | 3 | Render the Grafana dashboard against live Prometheus data | Dashboard renders without errors; panels show live series |
| IMPL-410 | 4 | Inject a real process crash on a test service; measure latency | Detection/remediation meet NFR-003/NFR-004 |
| IMPL-411 | 4 | Attempt to bypass the Approval Gate for a destructive action | Call fails hard; no action executes; audit entry records the denial |
| IMPL-510 | 5 | Apply the hardening profile to a real host | Every ENGINEERING_REFERENCE.md §5.2 checklist item passes |
| IMPL-511 | 5 | Make an out-of-band configuration change | Drift detector raises an alert with the changed setting |
| IMPL-610 | 6 | Checkpoint → corrupt state → rollback → state equality | State equality assertion passes; timing recorded against NFR-006 |
| IMPL-612 | 6 | Real upgrade with a forced post-upgrade health failure | All lettered §9 sub-steps execute; automatic rollback occurs |
| IMPL-720 | 7 | Inject a real event and measure SSE delivery to a browser | Delivered within NFR-013's ≤500 ms budget |
| IMPL-721 | 7 | Multi-user/multi-service access-matrix scenario | A user without a grant never shows a lit cell |
| IMPL-722 | 7 | Compare rendered UI events with the backend event journal | Zero unmatched UI events (NFR-015 fabrication audit) |
| IMPL-723 | 7 | Kill/restart `OpsPilotDashboardUI` while the Control Plane runs | Control Plane unaffected; dashboard reconnects/backfills within NFR-014's ≤5 s |

Windows-side procedure: obtain the phase commit and bundle, run
`.\powershell\install\install.ps1 -Phase <n>` from an elevated PowerShell 7 session, run
`make test-integration`, execute each row above, and record the actual command output in
`handoff/HANDOFF_<n>.md`. Only then change the status to `Verified-Windows`, and to `Done`
after the documentation update in the same commit.

---

## 5. Interfaces, Schemas, and Configuration Contracts

Authoritative schemas live in ENGINEERING_REFERENCE.md §4 and are versioned alongside `config/schema/*.json`. Every task above that introduces a new config surface or tool interface must add/update the corresponding schema entry in the same commit (MASTER_PROMPT.md §9). The tool-interface contract format is defined in SAS.md §4 (illustrated with `deploy_windows_service`) — every COMP-02/COMP-03 tool follows that same YAML contract shape.

---

## 6. The Handoff Manifest (Mac → Windows)

At the end of each phase's Mac-side work (all non-Windows-verification tasks in that phase at `Verified-Mac`), the Mac-side agent runs:

```bash
make bundle PHASE=<phase-id>
```

This produces:
1. A distributable build artifact (`dist/opspilot-cloud-<phase-id>-<commit-sha>.zip`) containing the installable payload for that phase.
2. `handoff/HANDOFF_<phase-id>.md`, generated from this template:

```markdown
# Handoff Manifest — Phase <phase-id>
Commit: <sha>   Generated: <UTC timestamp>   Bundle: dist/opspilot-cloud-<phase-id>-<sha>.zip

## Verified-Mac (evidence attached/linked)
- IMPL-xxx: <what was checked> — <pass/fail> — <evidence: test output, log path>

## Requires Windows Verification
- IMPL-1xx (Windows task ID): <exact steps to run> — <acceptance criterion from PRD.md §8>
- ...

## Explicitly NOT Verified (cannot be verified off-Windows — do not assume pass)
- <item> — <why>

## Install/Verify Command
.\powershell\install\install.ps1 -Phase <phase-id>

## Known Open Items (ENGINEERING_REFERENCE.md §9 ADR references, if any)
- ...
```

The Windows-side agent, on receiving this manifest:
1. Pulls the commit and bundle from the Git remote.
2. Runs the Install/Verify command.
3. For each "Requires Windows Verification" item, executes it for real and records pass/fail + evidence (command output, not a restatement — MASTER_PROMPT.md §7) directly into the manifest file, changing task statuses in §4 of this document from `Verified-Mac` to `Verified-Windows` (or to `Blocked` with a reason if it fails).
4. Commits and pushes the updated manifest and any status changes back to the remote.
5. If every task in the phase reaches `Verified-Windows` (or `Verified-Mac`-only where no runtime behavior applies), the Windows-side agent marks the phase tasks `Done` in §4 and the next phase's Mac-side tasks become unblocked.

This manifest is the entire synchronization mechanism between the two environments (MASTER_PROMPT.md §0) — no other channel is assumed to exist.

---

## 7. Automated Testing and Validation Commands

| Command | Runs where | What it checks |
|---|---|---|
| `make lint` | Mac or Windows | `ruff`, `mypy`, `PSScriptAnalyzer` |
| `make test` | Mac or Windows | `pytest tests/unit` (mocked Windows calls — never asserts real Windows behavior) |
| `make test-integration` | **Windows only** (errors clearly if run elsewhere) | `pytest tests/integration` — real Windows Service registration, real DPAPI, real Terraform apply against a test target, etc. |
| `make bundle PHASE=<id>` | Mac | Produces dist artifact + Handoff Manifest (§6) |
| `.\powershell\install\install.ps1 -Phase <id> -WhatIf` | Windows | Dry-run install preview |
| `.\powershell\install\install.ps1 -Phase <id>` | Windows | Real install + runs that phase's integration tests, writing results into the Handoff Manifest |

`make test-integration` run on non-Windows must fail fast with a clear message ("Windows-only; run on the Windows-side agent") rather than silently skip and risk being mistaken for a pass (MASTER_PROMPT.md §7).

---

## 8. CI/CD Setup

`.github/workflows/ci.yml` (windows-latest runner, triggered on every PR):
```yaml
name: CI
on: [pull_request, push]
jobs:
  build:
    runs-on: windows-latest
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with: { python-version: '3.11' }
      - run: pip install -r requirements.txt -r requirements-dev.txt
      - run: make lint
      - run: make test
      - run: make test-integration   # real Windows runner — this job genuinely verifies Windows behavior
```
Because `windows-latest` runners are real Windows, CI itself satisfies a meaningful slice of "Windows verification" automatically for anything not requiring a persistent/stateful host — reducing (not eliminating) what must wait for the Windows-side agent's manual phase verification. `.github/workflows/release.yml` builds the bundle and attaches it to a GitHub Release on tag push, mirroring `make bundle` so the artifact a human downloads matches what the Windows-side agent would produce locally.

---

## 9. Deployment and Upgrade Procedures

1. Windows-side agent/operator pulls the target release tag.
2. Runs `install.ps1 -Phase <id> -WhatIf`, reviews the plan.
3. Runs `install.ps1 -Phase <id>` for real.
4. Control plane starts (Windows Service, per IMPL-103) and begins health-check polling (IMPL-401) automatically.

**Upgrade (exact command sequence, not conceptual):**
```powershell
git -C C:\OpsPilotCloud fetch --tags
git -C C:\OpsPilotCloud checkout <new-release-tag>
.\powershell\install\install.ps1 -Phase <id> -Upgrade -WhatIf    # 1. preview the diff against the running install
.\powershell\install\install.ps1 -Phase <id> -Upgrade            # 2. real upgrade run, internally:
#    a. Invoke-OpsPilotCheckpoint -Tag "pre-upgrade-<new-release-tag>"      (COMP-05 snapshot, blocking, AP-4)
#    b. Stop-Service OpsPilotControlPlane -ErrorAction Stop
#    c. copies new binaries/config into place (blue/green at the service-binary level, SAS.md §7 pattern)
#    d. Start-Service OpsPilotControlPlane
#    e. Invoke-OpsPilotHealthCheck -TimeoutSeconds 120 -MaxRetries 2        (same timeout/retry fields as SAS.md §4 execution_policy)
#    f. on health-check failure: Invoke-OpsPilotRollback -CheckpointId <id from step a> automatically, then exit non-zero
#    g. on success: write result to the current Handoff Manifest (§6) and exit zero
```
Step 2's sub-steps (a–g) are the literal control flow `install.ps1 -Upgrade` implements — not aspirational; IMPL-604 (below) implements this exact sequence and its Windows verification confirms each lettered step actually runs in order.

---

## 10. Rollback and Recovery Procedures

- **Application rollback:** `Invoke-OpsPilotRollback -CheckpointId <id>` (wraps IMPL-601's API) — restores the last-known-good checkpoint, target ≤2 minutes (NFR-006).
- **Control-plane self-rollback:** if an upgrade (§9) fails its own post-upgrade health check, the installer automatically restores the pre-upgrade checkpoint before exiting — the control plane must never be left in a partially-upgraded, non-functional state.
- **Full disaster recovery:** documented runbook in ENGINEERING_REFERENCE.md §6 (troubleshooting), referencing the Checkpoint/State Store's backup export.

---

## 11. Definition of Done (per phase)

A phase is `Done` only when, per MASTER_PROMPT.md §7 and §10:
- [ ] Every task ID in the phase shows `Verified-Windows` (or `Verified-Mac`-only if explicitly no runtime behavior) in §4 above.
- [ ] The phase's Handoff Manifest (§6) has zero items left in "Requires Windows Verification."
- [ ] All PRD.md §8 acceptance tests mapped to this phase's FR groups pass.
- [ ] PRD.md, SAS.md, ENGINEERING_REFERENCE.md updated for anything that changed from the original design during implementation.
- [ ] No new Section 1 (Windows-only) violation introduced, per the dependency audit (ENGINEERING_REFERENCE.md §2).

## 12. Resuming After Interruption

Any agent — with zero memory of prior sessions — can resume correctly by:
1. Reading MASTER_PROMPT.md.
2. Reading this document's §4 task table, finding the first non-`Done` task.
3. Reading the most recent `handoff/HANDOFF_*.md` file (by phase number) to see what's pending Windows verification.
4. Checking its own environment (Mac or Windows) against §0 of this document to confirm it can actually work the next task; if not, it says so and stops rather than guessing or fabricating progress on the wrong side (MASTER_PROMPT.md §7).

No task's status or the Handoff Manifest's content may be inferred from conversational memory — both are files in the repository specifically so this resumption works across completely disconnected sessions.

### 12.1 Mid-task handoff (same environment, different agent session)

The Handoff Manifest (§6) covers the Mac→Windows boundary. It does **not** cover the more common case: a Mac-side (or Windows-side) agent's session ends partway through a single task — e.g., IMPL-104 is half-written when the session is interrupted. The next agent on the same side picks this up differently from a phase-boundary handoff:

1. **Before stopping** (whenever an agent knows its session is ending mid-task — context limit approaching, explicit pause, etc.), it must, in its last available turns:
   - Commit whatever compiles/passes lint, even if incomplete (never leave uncommitted work as the only copy).
   - Set the task's status in §4 to `In Progress` (not `Not Started`, not falsely `Verified-Mac`) with a one-line **"Left off at:"** note appended to the task's row or in a `handoff/WIP_<task-id>.md` scratch note if more detail is needed than fits the table — e.g., "Left off at: snapshot schema defined, restore() stubbed but untested."
   - Never mark a partially-implemented task's dependents as unblocked.
2. **The next agent**, on finding a task already `In Progress`:
   - Reads the "Left off at" note (table row or `handoff/WIP_<task-id>.md`) before writing any code — do not assume a clean start.
   - Runs the existing tests for that task's module first to establish real current state (MASTER_PROMPT.md §7 — verify, don't assume from the note alone).
   - Continues from there; deletes the `WIP_` note once the task reaches `Verified-Mac`/`Verified-Windows`.
3. This mechanism is same-environment by construction — it never substitutes for the Handoff Manifest when work crosses the Mac↔Windows boundary (§6 still applies there), and a task should never be marked `Verified-*` based on a WIP note's claims alone.
