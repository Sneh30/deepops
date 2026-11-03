# ENGINEERING_REFERENCE.md — OpsPilot Cloud Engineering Handbook

**Status:** v0.1, living document. Governed by [MASTER_PROMPT.md](./MASTER_PROMPT.md).
**Related documents:** [PRD.md](./PRD.md) · [SAS.md](./SAS.md) · [IMPLEMENTATION.md](./IMPLEMENTATION.md) · [UI_SPEC.md](./UI_SPEC.md)

**Legend used throughout this document:** 🟢 **Verified** (sourced from official documentation, URL given) · 🟡 **Assumption** (reasonable default, not independently verified — flagged per MASTER_PROMPT.md §8) · 🔴 **Unresolved** (open decision, needs resolution before the dependent task can be marked `Done`).

---

## 1. System Conventions and Terminology

| Term | Meaning |
|---|---|
| **Control Plane** | The OpsPilot Cloud agent orchestration layer (SAS.md §2–§3), runs on a Windows host |
| **Managed Host** | A Windows host whose applications/infra the Control Plane operates |
| **Mac-side / Windows-side** | The two-environment authoring/verification split (MASTER_PROMPT.md §0) |
| **Handoff Manifest** | The file-based synchronization artifact between Mac-side and Windows-side agents (IMPLEMENTATION.md §6) |
| **FR / NFR** | Functional / Non-Functional Requirement, IDs stable from PRD.md |
| **COMP-##** | Architectural component ID, stable from SAS.md §3 |
| **IMPL-###** | Implementation task ID, stable from IMPLEMENTATION.md §4 |
| **ADR-###** | Architectural Decision Record, logged in §9 of this document |
| **Verified-Mac / Verified-Windows / Done** | Task status values, defined in IMPLEMENTATION.md §2 |
| **In Progress / WIP note** | A task left incomplete by an ending session; see IMPLEMENTATION.md §12.1 for the same-environment handoff procedure (distinct from the Handoff Manifest, which is cross-environment only) |
| **Checkpoint** | A snapshot of config/state taken by COMP-05 before any mutating action, enabling rollback (AP-4, FR-070) |
| **Dry-run / `-WhatIf`** | A tool-call or PowerShell invocation mode that previews an action's effect without executing it (FR-091) |
| **Approval Gate** | COMP-04; the component every mutating call must pass through before execution, which blocks on human approval for destructive/irreversible/externally-visible/security-sensitive actions (MASTER_PROMPT.md §6) |
| **Blue/green (rollout)** | A deployment pattern where the new version runs alongside the old, is health-checked, and traffic cuts over only after verification passes, with the old version retained for fast rollback (SAS.md §7) |
| **Drift (configuration drift)** | A live host's actual configuration diverging from its declared config; detected by periodic reconciliation (FR-061) |
| **ACME** | Automatic Certificate Management Environment (RFC 8555) — the protocol Caddy uses for automatic TLS certificate issuance/renewal (FR-031) |
| **DPAPI** | Windows Data Protection API — native Windows local secret encryption tied to a service account, used for MVP secrets (COMP-11, ADR-004) |
| **Windows Container vs Linux Container** | Windows Containers run Windows-kernel-native processes; Linux Containers require a Linux kernel (via WSL2 or a Linux VM) — OpsPilot Cloud uses only the former in MVP (ADR-003, MASTER_PROMPT.md §1) |
| **ProactorEventLoop** | The default Windows `asyncio` event loop, required (not optional) for subprocess-based tool execution (§2 below); must never be overridden to `SelectorEventLoop` |
| **Escalation** | The Incident Engine (COMP-07) notifying the human owner after bounded automated remediation attempts fail (FR-052) |
| **Least privilege** | Every credential/execution context scoped to the minimum required for its task; no component runs as `LocalSystem`/admin by default (MASTER_PROMPT.md §3 principle 7, ENGINEERING_REFERENCE.md §5.2) |
| **Mission Control Dashboard** | The live "art-piece" web UI (COMP-13), full behavioral spec in UI_SPEC.md — distinct from the Grafana dashboard (COMP-06), which is metrics-only |
| **Event bus (internal)** | The in-process pub/sub (MVP) that COMP-13 subscribes to; every other component emits to it the same structured event it already logs, so the UI can never show activity that didn't really happen (SAS.md §5.1) |
| **SSE (Server-Sent Events)** | The one-directional HTTP push transport COMP-13 uses to stream live events to the dashboard's browser client (UI_SPEC.md §7) |

Naming conventions: Python modules `snake_case`; PowerShell functions `Verb-OpsPilotNoun` (approved verbs only, per `Get-Verb`); config files `kebab-case.yaml`; tool IDs in the registries `snake_case` matching their Python function name.

---

## 2. Windows Platform Compatibility Reference (Dependency Audit)

Every runtime dependency must appear here before merge (MASTER_PROMPT.md §1.3). This table is the canonical "is this actually Windows-only-clean" record.

| Dependency | Role | Windows-native evidence | WSL/Linux dependency? | Status |
|---|---|---|---|---|
| Docker Engine on Windows Server (`containerd` backend) | Windows Containers | Official Microsoft/Docker install docs for Windows Server container host | No, in Windows-container mode | 🟢 |
| Docker Desktop (Windows-container mode, `--backend=windows`) | Dev/edge containers | docs.docker.com/desktop/setup/install/windows-install — requires Win 10/11 **Pro/Enterprise**, not Home; **not supported on Windows Server** | No, in this mode (default Linux-container mode does use WSL2 — must not be the mode OpsPilot relies on) | 🟢 (mode-specific) |
| Terraform (Windows amd64 binary) | IaC | Official HashiCorp Windows releases | No | 🟢 |
| Caddy (Windows binary) | Reverse proxy + ACME TLS | caddyserver.com/features — statically compiled Go binary, built-in automatic HTTPS | No | 🟢 |
| GitHub Actions `windows-latest` runner | CI/CD | GitHub-hosted runner images, Microsoft-maintained | No | 🟢 |
| windows_exporter | Metrics | github.com/prometheus-community/windows_exporter — MSI installer, native Windows service | No | 🟢 |
| Prometheus (Windows binary) | Metrics store | Official Prometheus Windows release | No | 🟢 |
| Grafana (Windows binary) | Dashboards | Official Grafana Windows release | No | 🟢 |
| Python 3.11+ (`asyncio`, ProactorEventLoop) | Agent runtime | CPython docs, `asyncio-platforms` — ProactorEventLoop is the Windows default and is required for `subprocess_exec`/`subprocess_shell` support | No | 🟢 — **constraint:** do not override the default event loop policy on Windows |
| PyYAML 6.0.3 | Config loading (all components) | Pure-Python wheel, cross-platform including Windows | No | 🟢 |
| jsonschema 4.26.0 | Config/schema validation (`config/schema/*.json`) | Pure-Python wheel, cross-platform including Windows | No | 🟢 |
| psutil 7.2.2 | Host utilization sampling (COMP-06, FR-082) | Cross-platform wheel with native Windows support (Windows API backend) | No | 🟢 |
| HashiCorp Vault (Windows server binary) | Production secrets (Post-MVP/ADR-004) | Official HashiCorp Windows releases | No | 🟢 |
| Windows DPAPI (`ConvertTo-SecureString`/`Protect-CData`) | MVP local secrets | Built into Windows | No | 🟢 |
| NSSM | Service wrapper fallback | nssm.cc — long-standing open-source Windows service wrapper | No | 🟡 (community-maintained, not Microsoft-official; acceptable per MASTER_PROMPT.md §1.3 rule 1 exception — no Microsoft-official general-purpose equivalent for wrapping arbitrary scripts) |
| SQLite (via Python `sqlite3` stdlib) | Checkpoint/State Store (MVP) | Part of CPython stdlib, cross-platform including Windows | No | 🟢 |
| win-acme / ACMESharp | IIS-fallback TLS automation (documented alternative, not primary) | Official Windows-targeted ACME clients | No | 🟢 (fallback path only; Caddy is primary) |
| FastAPI 0.162.0 | COMP-13 Dashboard/UI Service backend | Pure-Python wheel, cross-platform including Windows; runs under the same Python 3.11+/ProactorEventLoop constraint as the rest of the agent runtime (no Windows-specific gap) | No | 🟢 |
| uvicorn 0.54.0 | COMP-13 ASGI server (`run_dashboard.py`) | Pure-Python wheel, cross-platform including Windows; `asyncio` server only, no native extension | No | 🟢 |
| httpx 0.28.1 | **Test-only** ASGI client for dashboard API/SSE tests (`requirements-dev.txt`, never installed as a runtime dependency) | Pure-Python wheel, cross-platform | No | 🟢 (test-only) |
| Server-Sent Events (SSE, via ASGI framework's native support, no extra package) | COMP-13 live push transport | HTTP/1.1 standard feature, framework-native | No | 🟢 |
| Static frontend assets (vanilla HTML/CSS/JS, no build tool) | COMP-13 browser client | Plain files served by the Python backend; there is no Node.js/Vite runtime or build-time dependency | No | 🟢 |
| **Linux Container workloads (ADR-003)** | Explicitly out of MVP scope | — | **Yes, if ever enabled** — this is the one deliberate, documented exception, never silently introduced | 🔴 **Unresolved for Post-MVP; explicitly excluded from MVP runtime** |
| **Per-database query-event instrumentation (ADR-008)** | Drives UI_SPEC.md §4.4 blink behavior | — | Depends on target DB engine/driver, not yet named | 🔴 **Unresolved — deployment-dependent, see ADR-008** |

**Audit procedure:** run before every release — compare `requirements.txt`,
`requirements-dev.txt`, and `powershell/modules/RequiredModules.psd1` against this table,
and confirm every runtime dependency is pinned to an exact version. `make lint` does
**not** currently include an automated dependency-manifest check, so this comparison is
a required review step, not an assumed CI gate.

---

## 3. Dependency and Version Management Policy

- All Python dependencies pinned exactly in `requirements.txt` (`==`, not `>=`), with a separate `requirements-dev.txt` for tooling-only deps (MASTER_PROMPT.md §3).
- FastAPI/uvicorn are the pinned COMP-13 runtime deps. `httpx` is test-only (FastAPI `TestClient`) and stays in `requirements-dev.txt`.
- The Mission Control frontend is plain HTML/CSS/JS checked into `frontend/dashboard/` and served by the Python backend. There is no Node.js, npm, bundler, or build step on either host.
- All PowerShell module dependencies pinned by version in a module manifest (`powershell/modules/RequiredModules.psd1`).
- Windows binaries fetched by install scripts (Terraform, Caddy, windows_exporter, Prometheus, Grafana) are pinned to an exact version and SHA256-verified post-download — never fetched as "latest" (IMPLEMENTATION.md §3.3).
- Version bumps are their own commit, referencing which IMPL task/ADR motivated the bump, and update §2 of this document if the compatibility evidence changes.

---

## 4. API, Configuration, and Data Schema References

### 4.1 Tool interface contract (full shape, illustrated in SAS.md §4)
Every tool in COMP-02 (read-only) and COMP-03 (mutating) is declared in `config/schema/tool-contract.schema.json` and documented inline. Mutating tools additionally require `dry_run`, `checkpoint_id` in output, and `error_modes` including at minimum `PRECONDITION_FAILED` and `EXECUTION_FAILED`.

### 4.2 Config file schemas (`config/schema/`)
| Schema file | Governs | Validated by |
|---|---|---|
| `host-config.schema.json` | Baseline provisioning target (FR-001) | `provisioning_engine` on load |
| `deployment-config.schema.json` | App packaging metadata (service name, container image ref, health-check endpoint) | `deployment_engine` on load |
| `alert-rules.schema.json` | Incident Engine alert thresholds (FR-042) | `incident_engine` on load |
| `approval-policy.schema.json` | Which action categories require approval (MASTER_PROMPT.md §6 criteria, machine-readable) | `approval_gate` on load |
| `tool-contract.schema.json` | Shape in §4.1 | CI static check against every registered tool |
| `dashboard.schema.json` | COMP-13 dashboard bind address, event journal, state retention, Prometheus queries, ingest contract, log feed (UI_SPEC.md, ADR-008) | `orchestrator.cli check-config` and `DashboardConfig.load` on service start |

### 4.3 Audit log entry schema (COMP-12, JSON Lines)
```json
{
  "timestamp": "ISO-8601 UTC",
  "action": "tool_name",
  "inputs": {},
  "outcome": "success|failed|rolled_back",
  "checkpoint_id": "string|null",
  "approval_ref": "string|null",
  "evidence": {}
}
```
Every field is required; `approval_ref` is `null` only for actions the Approval Gate auto-cleared as low-risk/reversible (MASTER_PROMPT.md §6), never omitted.

### 4.4 Dashboard event journal and SSE contract (COMP-13)
The dashboard never calls the Control Plane. Each producer appends one JSON object per line to `<data_dir>/logs/events.jsonl`:

```json
{
  "id": "evt-...",
  "timestamp": "ISO-8601 UTC",
  "type": "deployment.completed",
  "component": "COMP-08",
  "level": "INFO",
  "message": "deployment completed",
  "data": {}
}
```

Every field is required. `Event.create` redacts credential-shaped keys in `data` and credential-shaped text in `message` before the journal append, so the dashboard cannot replay a secret that the source log also redacts. The journal rotates at `max_bytes` (default 20 MiB, `events.jsonl.1`…); the dashboard follows by byte offset plus file identity and saves its offset with its snapshot for restart backfill.

SSE is served at `<path_prefix>/api/events/stream` (default `/mission-control/api/events/stream`):

| Frame | Payload | When |
|---|---|---|
| `retry: 2000` | — | First line of every connection |
| `event: state` | Full dashboard state | Immediately after connect |
| `event: update` | `{"event": <event>, "state": <full state>}` | Structural events (sessions, queries, deployments, incidents, approvals, tiles) |
| `event: pulse` | `{"event": <event>}` | High-frequency events: `agent.token`, `agent.thinking`, `agent.decision`, `agent.result`, `health.poll`, `metrics.sample`, `system.heartbeat`, `tool.retry` |
| `: keep-alive` | — | After 15 s without an event |

`id:` is the event ID, so a browser `EventSource` can resume with `Last-Event-ID`. If SSE fails, the client switches to a visible **DEGRADED · POLLING 2S** mode against `/api/state`; it never silently pretends to be live.

---

## 5. Security and Operational Policies

### 5.1 Secrets handling
- Never commit secrets (MASTER_PROMPT.md §5). `.gitignore` excludes `*.secret`, `config/local/`, `*.pfx`, `*.pem` (private keys).
- Pre-commit/CI secrets scan (e.g., pattern-based scanner) blocks merges containing credential-shaped strings.
- DPAPI-encrypted local secrets (MVP) are tied to the Windows service account running the Control Plane — not portable off that host by design, which is a feature (reduces exfiltration blast radius), not a bug to work around.

### 5.2 Baseline hardening checklist (FR-060, verified by IMPL-510)
- [ ] Windows Firewall enabled, default-deny inbound, explicit allow rules only for required ports (reverse proxy 443/80, metrics scrape port restricted to Control Plane's own network path).
- [ ] Unnecessary Windows features/roles not installed (audit against a minimal-role baseline).
- [ ] Control Plane and managed services run under a dedicated least-privilege service account, never `LocalSystem` unless a specific task genuinely requires it (documented exception if so).
- [ ] Remote Desktop disabled by default on managed hosts unless explicitly required and approved.
- [ ] Windows Update policy documented (not silently disabled to avoid reboots — that would itself be a security control disabled without approval, MASTER_PROMPT.md §5).
- [ ] TLS 1.2+ enforced; weaker protocols disabled at the OS/Caddy level.

### 5.3 Approval-required action categories (machine-readable mirror of MASTER_PROMPT.md §6)
Encoded in `approval-policy.schema.json` (§4.2): `destructive`, `irreversible`, `externally_visible`, `security_sensitive`. Every mutating tool declares which categories it can fall into; the Approval Gate evaluates per-call, not per-tool-statically, because the same tool (e.g., "update DNS record") can be low-risk in a dry-run/staging context and externally-visible in production.

---

## 6. Testing and Debugging Procedures

- **Unit tests (Mac or Windows):** `pytest tests/unit`, all Windows API calls mocked (e.g., `unittest.mock` over `subprocess`/`winreg`/PowerShell invocation) — these tests check logic, never claim to verify real Windows behavior (MASTER_PROMPT.md §7).
- **Integration tests (Windows-only):** `pytest tests/integration`, real Windows Service registration, real DPAPI calls, real Terraform against a test/sandbox target. Must fail fast with a clear message if attempted on non-Windows (IMPLEMENTATION.md §7).
- **Debugging a failed Windows-side verification:** check the relevant Handoff Manifest's evidence fields first (IMPLEMENTATION.md §6) — they contain the actual command output, not a summary. Reproduce locally on the Windows host using the same PowerShell invocation before changing code.
- **Common Windows-specific pitfalls to check first:**
  - Event loop: confirm `asyncio.get_event_loop_policy()` is the default `WindowsProactorEventLoopPolicy` — a `SelectorEventLoop` override breaks all subprocess-based tools (§2 above).
  - Path separators: always use `pathlib.Path`, never hardcoded `/`.
  - Service account permissions: "access denied" on a mutating tool is very often a least-privilege scoping issue (§5.2), not a logic bug — check the service account's rights before debugging the Python/PowerShell logic itself.
  - Dashboard: `OpsPilotDashboardUI` is a separate service (ADR-008/NFR-014). A dashboard failure must not be "fixed" by folding it into the Control Plane; check its own service, config, and journal access first.

---

## 7. Logging, Telemetry, and Incident Management Conventions

- All application logs are structured JSON Lines, one event per line, fields: `timestamp`, `level`, `component` (COMP-## ID), `message`, `context` (object).
- Log levels: `DEBUG`, `INFO`, `WARN`, `ERROR`, `CRITICAL` (CRITICAL reserved for conditions that trigger FR-052 escalation).
- Retention: default 30 days hot (queryable in Grafana/log aggregator), configurable per NFR/privacy requirements (PRD.md §4 NFR table) — 🟡 Assumption, revisit if a customer compliance need demands longer/shorter.
- Incident lifecycle states: `Detected` → `Remediation-Attempted` → `Resolved` | `Escalated` → (`Escalated` →) `Human-Resolved`. Every transition is an audit log entry (§4.3).

---

## 8. Documentation Maintenance Policies

Per MASTER_PROMPT.md §9: documentation changes land in the same commit as the behavior change. This document specifically is updated whenever:
- A new dependency is adopted (§2, same commit).
- A schema changes (§4, same commit).
- A new ADR is recorded (§9, same commit as the decision).
- A hardening checklist item or logging convention changes (§5/§7).

Stale cross-references are treated as a bug: if grep for an ID (`FR-`, `NFR-`, `COMP-`, `IMPL-`, `ADR-`) across all six documents ever turns up a reference to an ID that no longer exists in its home document, that's a defect to fix in the same session it's noticed.

---

## 9. Architectural Decision Records

| ID | Decision | Status | Rationale (see also SAS.md for full discussion) |
|---|---|---|---|
| ADR-001 | Single-host MVP; interfaces shaped as if remote | Accepted | Avoids premature distributed-systems complexity (SAS.md §11) |
| ADR-002 | In-process Python tool registries, not per-tool microservices, for MVP | Accepted | Simpler to build/verify within MVP timeline |
| ADR-003 | Linux-container workloads excluded from MVP runtime scope | Accepted (MVP) / Open (Post-MVP approach) | Most SMB/startup OSS ships Linux-only images; Windows-only constraint is honored honestly rather than worked around via WSL (SAS.md §6) |
| ADR-004 | DPAPI for MVP secrets, Vault deferred to multi-host phase | Accepted | Avoids premature infra; DPAPI sufficient for single-host least-privilege model |
| ADR-005 | Alert evaluation inside Incident Engine, no separate Alertmanager for MVP | Accepted | Fewer moving Windows services; revisit if alert-rule complexity grows |
| ADR-006 | SQLite as Checkpoint/State Store backing for MVP | Accepted | Zero-install, stdlib, sufficient for single-host; migrate to shared store when NFR-011 (multi-host) is in scope |
| ADR-007 | Git remote (GitHub) is the sole Mac↔Windows synchronization channel; no other shared state assumed | Accepted | Makes the handoff auditable, resumable, and independent of any particular agent session's lifetime (MASTER_PROMPT.md §0) |
| ADR-008 | Dashboard/UI Service (COMP-13) is read-only, subscribes to an internal event bus rather than polling or inferring state; per-database query-level blink events are deployment-dependent and deferred to a named-adapter interface per target DB engine | Accepted (framework) / Open (per-DB adapter) | Structurally guarantees FR-107/NFR-015 (no fabricated UI state, UI_SPEC.md §1/§7); the DB-adapter gap is honest rather than papered over with a generic/fake implementation (SAS.md §5.1) |
| ADR-009 | A blue/green reverse-proxy cutover is classified `externally_visible`; every cutover requires a one-time approval reference supplied to the deploy call | Accepted | Traffic never flips to a new slot merely because a build succeeded; the externally visible step is gated by a human approval reference and recorded in the audit entry (MASTER_PROMPT.md §6, FR-013) |
| ADR-010 | Prometheus and Grafana are wrapped by NSSM because neither ships a native Windows Service shim; OpsPilot services run as `NT AUTHORITY\LocalService` by default, never `LocalSystem` unless a documented task requires it | Accepted | Keeps the observability stack Windows-native without a Linux/WSL dependency; least-privilege service accounts are the default (FR-060, ENGINEERING_REFERENCE.md §5.2) |
| ADR-011 | PowerShell analysis is enforced with a pinned PSScriptAnalyzer module and a repository settings file; the lint step runs on the Mac authoring host as well as Windows CI | Accepted | Static PowerShell defects are caught before the Windows handoff; this is a tooling-only cross-platform use, not a runtime claim (IMPLEMENTATION.md §3.1) |

New ADRs append here with status `Proposed` → `Accepted`/`Rejected`/`Superseded`. An ADR is never silently deleted; a superseded one links to its replacement.

---

## 10. Agent Onboarding and Handoff Procedures

**Any new agent session, Mac-side or Windows-side, on first contact with this repository:**
1. Read MASTER_PROMPT.md in full.
2. Read this document's §1 (terminology) and §2 (dependency audit) to understand current platform state.
3. Read IMPLEMENTATION.md §4, find current task status.
4. If resuming mid-phase, read the latest `handoff/HANDOFF_<phase-id>.md`.
5. Confirm own environment (Mac/Windows) before claiming any task (IMPLEMENTATION.md §0).

This sequence is also the correct order for a human engineer onboarding to the project, which is intentional (MASTER_PROMPT.md's documentation is written to be the only onboarding material needed).

---

## 11. Troubleshooting and Known Limitations

| Issue | Status | Notes |
|---|---|---|
| Linux-container-packaged applications cannot be deployed | **Known limitation (MVP)** | ADR-003. Not a bug — a documented scope boundary. Do not attempt a WSL workaround; escalate to Post-MVP planning instead. |
| Docker Desktop Windows-container mode unavailable on Windows Server | **Platform limitation** | Use `containerd` + Docker Engine directly on Windows Server (§2); Docker Desktop is dev/edge-only in this architecture. |
| `make test-integration` run on Mac | **Expected failure, by design** | Fails fast with an explicit message (IMPLEMENTATION.md §7) — this is correct behavior, not a defect. |
| DPAPI-encrypted secrets not portable between hosts | **By design (§5.1)** | Multi-host requires the Vault migration (ADR-004), not a DPAPI workaround. |
| Mission Control matrix shows "DB ADAPTER: NOT CONNECTED" | **Known limitation until a target DB engine is named (ADR-008)** | Not a bug — the per-DB query-event adapter is deployment-specific and genuinely not implementable generically. Session rows, tiles, ticker, summary, logs, and metrics still work. Do not fake the blink to make it "look done." |
| Mission Control Mode B shows no raw tokens | **Expected in the six-document MVP** | No LLM call site exists, so there is no `agent.token` producer. The toggle falls back to the structured trace and says so (UI_SPEC.md §5.3). Do not synthesize tokens. |
| Dashboard SSE connects but events arrive in bursts or stall behind Caddy | **Configuration issue** | Confirm the dashboard route is rendered as `handle_path /mission-control/*` with `flush_interval -1` (ADR-008 / IMPL-713). The browser shows `DEGRADED · POLLING 2S` if SSE fails rather than pretending to be live. |
| `OpsPilotDashboardUI` fails to start | **Check service account and paths** | Validate `config/defaults/dashboard.yaml` (`DashboardConfig` rejects out-of-range values), confirm port 8090 is free, and confirm the LocalService SID can read `<data_dir>/logs` and write `<data_dir>/dashboard`. Inspect `Get-Service OpsPilotDashboardUI` and the Windows Event Log. |
| Dashboard restart loses more than 5 s of state | **Open until IMPL-723** | The service restores its snapshot (`<data_dir>/dashboard/state.json`) and tails from the saved journal offset, but the ≤5 s NFR-014 guarantee is only proven by the Windows restart test. If it fails, check that the snapshot interval is ≤5 s and the journal was not rotated past the saved offset. |
| Observability tab shows metrics disconnected | **Real backend state** | The dashboard records `metrics.scrape_failed` when Prometheus is unreachable; it never displays a stale chart as live. Verify Prometheus on `127.0.0.1:9090` and the query names in `dashboard.yaml`. |

## 12. Links to Authoritative Upstream Documentation

- Docker Desktop Windows install / container modes: https://docs.docker.com/desktop/setup/install/windows-install/
- Caddy automatic HTTPS: https://caddyserver.com/features
- windows_exporter: https://github.com/prometheus-community/windows_exporter
- Python asyncio platform support (Windows event loop): https://docs.python.org/3/library/asyncio-platforms.html
- GitHub Actions `windows-latest` runner images: https://github.com/actions/runner-images
- HashiCorp Terraform downloads (Windows): https://developer.hashicorp.com/terraform/install
- HashiCorp Vault downloads (Windows): https://developer.hashicorp.com/vault/downloads
- Grafana downloads (Windows): https://grafana.com/grafana/download?platform=windows
- Prometheus downloads (Windows): https://prometheus.io/download/
- NSSM: https://nssm.cc/
- PSScriptAnalyzer: https://github.com/PowerShell/PSScriptAnalyzer
- FastAPI: https://fastapi.tiangolo.com/
- Uvicorn: https://www.uvicorn.org/
- WHATWG Server-Sent Events: https://html.spec.whatwg.org/multipage/server-sent-events.html

All claims in §2 of this document should be re-verified against these links at each major release, since vendor support policies (especially Docker Desktop's Windows-container mode and Windows Server support matrix) can change (MASTER_PROMPT.md §7 rule 4).
