# PRD.md — OpsPilot Cloud Product Requirements

**Status:** v0.1 (MVP definition). Governed by [MASTER_PROMPT.md](./MASTER_PROMPT.md).
**Related documents:** [SAS.md](./SAS.md) implements these requirements · [IMPLEMENTATION.md](./IMPLEMENTATION.md) sequences the work · [ENGINEERING_REFERENCE.md](./ENGINEERING_REFERENCE.md) records policy detail.

---

## 1. Product Vision and Objectives

**Vision:** OpsPilot Cloud is an AI-agent-operated infrastructure and SRE platform that lets a startup or small/medium business run production applications on Windows infrastructure without a dedicated SRE team — an autonomous agent provisions, deploys, monitors, and heals the platform, escalating to a human only for genuinely high-stakes or ambiguous decisions.

**Objectives:**
- **O1.** Reduce the operational burden of running production infrastructure for teams with no dedicated SRE/DevOps headcount.
- **O2.** Provide a Windows-native alternative to Linux-centric SRE tooling, for organizations standardized on Windows Server or regulated to avoid Linux hosts.
- **O3.** Keep the human owner in control of risk — full autonomy for reversible/low-risk operations, hard approval gates for destructive/irreversible ones (MASTER_PROMPT.md §6).
- **O4.** Make the platform's own behavior auditable: every action taken, every decision an agent made, and the reasoning behind it is logged and reviewable.

**Out of scope for the vision (not just MVP):** OpsPilot Cloud is an operations platform, not an application development platform. It does not write application code, design application architecture, or replace a CI pipeline's build/test stages for the customer's own application — it packages, deploys, and operates what it's given.

---

## 2. Target Users and Use Cases

| Persona | Description | Primary needs |
|---|---|---|
| **Founder/technical generalist** at an early-stage startup | Writes product code, has no time/budget for dedicated ops | "Deploy this and keep it running without me thinking about it." |
| **Small IT/engineering team lead** at an SMB | 1–5 engineers, Windows-standardized environment (regulatory, licensing, or legacy reasons) | Reliable infra ops without hiring a Windows SRE specialist. |
| **Platform/DevOps engineer** evaluating OpsPilot Cloud for their org | Technically sophisticated, needs to trust the automation before delegating to it | Transparency, auditability, safe failure modes, no vendor lock-in surprises. |

**Representative use cases (UC):**
- **UC-1:** Deploy a new web application (container or native Windows service) to a fresh Windows host with TLS and a public domain, with zero manual server configuration.
- **UC-2:** Detect and remediate a service outage (crashed process, failed health check) automatically, without waking up the on-call human, within policy-defined bounds.
- **UC-3:** Roll out a new application version with automatic rollback if health checks fail post-deploy.
- **UC-4:** Receive a weekly operational report (uptime, incidents, resource utilization, cost/sizing recommendations) without requesting it.
- **UC-5:** Detect a security-relevant anomaly (unexpected inbound traffic pattern, unauthorized config change) and alert/contain it per policy.
- **UC-6:** A human owner reviews and approves a pending destructive action (e.g., database schema migration, DNS cutover) through an approval interface.

---

## 3. Functional Requirements

Each FR has a stable ID referenced from SAS.md (architecture) and IMPLEMENTATION.md (tasks). MVP-scope FRs are marked **[MVP]**; others are **[Post-MVP]**.

### 3.1 Provisioning & Infrastructure
- **FR-001 [MVP]** The system shall provision a target Windows host's baseline configuration (networking, firewall rules, required Windows features/roles) from a declarative configuration file.
- **FR-002 [MVP]** The system shall provision infrastructure resources (compute, storage, networking) via Terraform using Windows-native Terraform execution (MASTER_PROMPT.md §1).
- **FR-003 [Post-MVP]** The system shall support multi-host topologies (load-balanced application tiers across 2+ Windows hosts).

### 3.2 Application Packaging & Deployment
- **FR-010 [MVP]** The system shall deploy an application packaged as a native Windows Service.
- **FR-011 [MVP]** The system shall deploy an application packaged as a Windows Container image.
- **FR-012 [MVP]** The system shall support zero-downtime deployment for a new application version via a documented rollout strategy (SAS.md §7).
- **FR-013 [MVP]** The system shall automatically roll back a deployment if post-deploy health checks fail within a configurable verification window.
- **FR-014 [Post-MVP]** The system shall support deploying Linux-container-packaged applications via a documented, explicitly-opted-in compatibility path (ADR-003; out of MVP scope per the Windows-only constraint — see SAS.md §6 for the honest trade-off discussion).

### 3.3 CI/CD
- **FR-020 [MVP]** The system shall integrate with GitHub Actions (Windows runners) to trigger deployments on a defined pipeline event (e.g., merge to a release branch).
- **FR-021 [Post-MVP]** The system shall support Azure DevOps as an alternative CI/CD source.

### 3.4 Networking, Reverse Proxy, TLS
- **FR-030 [MVP]** The system shall configure a reverse proxy (Caddy, Windows-native) in front of deployed applications.
- **FR-031 [MVP]** The system shall automatically obtain and renew TLS certificates via ACME for any domain pointed at a managed host.
- **FR-032 [MVP]** The system shall support routing multiple applications/domains through a single reverse proxy instance.

### 3.5 Monitoring, Logging, Alerting, Observability
- **FR-040 [MVP]** The system shall collect host-level metrics (CPU, memory, disk, network) via windows_exporter and expose them to a Prometheus-compatible store.
- **FR-041 [MVP]** The system shall collect application-level logs in structured form and retain them per a configurable retention policy.
- **FR-042 [MVP]** The system shall evaluate alert rules against collected metrics/logs and notify the human owner through a configured channel (email at minimum for MVP).
- **FR-043 [MVP]** The system shall provide a dashboard (Grafana, Windows-native) visualizing host and application health.
- **FR-044 [Post-MVP]** The system shall support distributed tracing via OpenTelemetry for multi-service applications.

### 3.6 Incident Detection, Diagnosis, Remediation
- **FR-050 [MVP]** The system shall detect a failed health check or crashed process within a configurable polling interval (default ≤60s).
- **FR-051 [MVP]** The system shall attempt a documented, policy-bounded automated remediation (e.g., restart the service) before escalating to the human.
- **FR-052 [MVP]** The system shall escalate to the human owner if automated remediation fails after a configurable number of attempts (default: 2).
- **FR-053 [Post-MVP]** The system shall perform root-cause diagnosis across correlated logs/metrics and attach a diagnosis summary to any escalation.

### 3.7 Security Monitoring, Hardening, Incident Response
- **FR-060 [MVP]** The system shall apply a documented baseline security hardening profile to provisioned Windows hosts (ENGINEERING_REFERENCE.md §5).
- **FR-061 [MVP]** The system shall detect and alert on unauthorized changes to managed configuration (drift detection).
- **FR-062 [Post-MVP]** The system shall detect anomalous network traffic patterns and support automated containment (e.g., firewall rule insertion) within approval policy.

### 3.8 Rollback, Recovery, Self-Healing
- **FR-070 [MVP]** The system shall maintain a restorable configuration/state checkpoint before any mutating operation (MASTER_PROMPT.md §3 principle 8).
- **FR-071 [MVP]** The system shall support a one-command/one-call rollback to the last known-good checkpoint.
- **FR-072 [Post-MVP]** The system shall support automated capacity self-healing (e.g., restart on resource exhaustion with documented limits).

### 3.9 Reporting & Resource Optimization
- **FR-080 [MVP]** The system shall generate a periodic (default weekly) operational report summarizing uptime, incidents, and remediations taken.
- **FR-082 [MVP]** The system shall record per-host and per-application resource utilization (CPU, memory, disk) over time, retained per the logging policy (ENGINEERING_REFERENCE.md §7), and include a basic utilization summary (min/avg/max over the report period) in the periodic report (FR-080). This does not generate recommendations — it is the data foundation FR-081 consumes once built.
- **FR-081 [Post-MVP]** The system shall generate resource right-sizing recommendations based on the utilization history collected by FR-082.

### 3.10 Agent Tooling & Safety (governs how FRs above are executed)
- **FR-090 [MVP]** Every infrastructure-mutating action shall be exposed to the AI agent as a distinct tool, separate from any read-only/diagnostic tool (MASTER_PROMPT.md §3 principle 6).
- **FR-091 [MVP]** Every mutating tool shall support a dry-run/preview mode before execution.
- **FR-092 [MVP]** Destructive, irreversible, externally-visible, or security-sensitive actions shall require explicit human approval before execution (MASTER_PROMPT.md §6).
- **FR-093 [MVP]** All agent actions shall be logged with: timestamp, action, inputs, outcome, and (if applicable) the human approval reference.

### 3.11 Live Observability Dashboard ("Mission Control" UI)
Full behavioral spec in [UI_SPEC.md](./UI_SPEC.md); FRs here are the requirement-level summary for traceability. All **[MVP]** — the owner specified this as a core deliverable, not a nice-to-have.
- **FR-100 [MVP]** The system shall provide a web-based live dashboard, served by the Control Plane and reverse-proxied through COMP-10, showing real-time platform state with no manual refresh required (UI_SPEC.md §7, SSE push).
- **FR-101 [MVP]** The dashboard shall display a persistent status strip of every platform capability/component (provisioning, deploy, proxy/TLS, metrics, incidents, secrets, audit, approval gate), each reflecting real current state (idle/active/error/unreachable) — never a static or simulated indicator (UI_SPEC.md §3).
- **FR-102 [MVP]** The dashboard shall display a live grid of every currently connected user (with IP address) against every service/database they have access to, where: a cell with no access is unlit, a cell with access is lit and steady, and a cell is lit and pulsing for the real duration of an active data fetch (UI_SPEC.md §4).
- **FR-103 [MVP]** The dashboard shall display a live table of connected users (username, IP, connected-since, service count), updating in real time as sessions open and close (UI_SPEC.md §4.2, §6).
- **FR-104 [MVP]** The dashboard shall provide an Observability tab showing live Prometheus-backed metrics and a live structured-log stream (UI_SPEC.md §5.2).
- **FR-105 [MVP]** The dashboard shall provide an Agent Thinking tab showing the AI agent's real decision/tool-call trace live, with a togglable mode to view raw model reasoning-token output instead of the structured trace (UI_SPEC.md §5.3).
- **FR-106 [MVP]** The dashboard shall provide a Server Summary tab with glanceable high-level health/activity rollup (UI_SPEC.md §5.1).
- **FR-107 [MVP]** Every animated/live element on the dashboard shall be driven by a real backend event; no element may simulate activity that is not occurring (UI_SPEC.md §1, §7 — direct extension of MASTER_PROMPT.md §7's no-fabrication rule to the UI layer).
- **FR-108 [MVP]** The dashboard shall remain visually legible and intentional (not sparse or broken-looking) at minimum scale (one user, one service) as well as at full platform scale (UI_SPEC.md §3.4).

---

## 4. Non-Functional Requirements

Each NFR has measurable acceptance criteria. All are **[MVP]** unless marked.

| ID | Requirement | Acceptance Criterion |
|---|---|---|
| **NFR-001** | Platform runs entirely on Windows-native components | Zero WSL/Linux-VM/Linux-container/bash-required dependencies in the production runtime path (MASTER_PROMPT.md §1); verified by the dependency audit in ENGINEERING_REFERENCE.md §2 at every release. |
| **NFR-002** | Deployment speed | A standard application deployment (FR-010/FR-011) completes in ≤5 minutes from trigger to health-check-passed, for an application under 500MB packaged size. |
| **NFR-003** | Incident detection latency | A crashed process or failed health check is detected within the configured polling interval, default ≤60 seconds (FR-050), verified by injected-failure test. |
| **NFR-004** | Automated remediation success rate | ≥80% of single-process-crash incidents are resolved by automated remediation (FR-051) without human escalation, measured over a 30-day operational window in staging. |
| **NFR-005** | TLS certificate uptime | No managed domain serves an expired TLS certificate; renewal attempted automatically starting ≥30 days before expiry (FR-031). |
| **NFR-006** | Rollback time | A rollback to last-known-good (FR-071) completes in ≤2 minutes for a single-host deployment. |
| **NFR-007** | Audit completeness | 100% of mutating agent actions produce a log entry per FR-093; verified by log-coverage test against the tool-call interface. |
| **NFR-008** | Approval gate integrity | 0 destructive/irreversible actions execute without a recorded human approval reference, verified by a negative test (attempted bypass must fail). |
| **NFR-009** | Secrets never in plaintext at rest | 0 instances of plaintext secrets found in config files, logs, or source control, verified by a secrets-scanning gate in CI (MASTER_PROMPT.md §5). |
| **NFR-010** | Documentation/implementation traceability | 100% of MVP FRs trace to at least one SAS.md component and at least one IMPLEMENTATION.md task with a passing automated test (Section 8 of this document; MASTER_PROMPT.md §9). |
| **NFR-011 [Post-MVP]** | Multi-host scalability | Platform supports ≥10 managed hosts from a single control plane without architectural redesign. |
| **NFR-012** | Usability — time to first deployment | A new user with a packaged application and a Windows host completes UC-1 (first deployment) in ≤30 minutes following IMPLEMENTATION.md setup docs, without vendor support contact. |
| **NFR-013** | Dashboard live-update latency | A real backend event (tool call, scrape tick, query start/stop, connection open/close) is reflected on the dashboard (UI_SPEC.md) within ≤500ms, measured end-to-end from event emission to DOM update, verified by an injected-event timing test. |
| **NFR-014** | Dashboard uptime independence | A dashboard UI-service failure or restart shall not affect Control Plane operation (FR-090–093 continue unaffected); dashboard reconnects and backfills recent state within ≤5 seconds of recovery. |
| **NFR-015** | No fabricated UI state | 0 instances of a dashboard element animating/indicating activity without a corresponding real backend event in the same time window, verified by an automated audit comparing UI event log to backend event log over a test session (UI_SPEC.md §1/§7, MASTER_PROMPT.md §7 applied to the UI layer). |

---

## 5. User Journeys

### Journey A — First deployment (UC-1)
1. Owner provides: a Windows host (or cloud credentials for provisioning one), a packaged application, a domain name.
2. Agent provisions baseline host config (FR-001), requests any missing credentials once, with stated purpose (MASTER_PROMPT.md §5).
3. Agent deploys the application (FR-010/FR-011), configures reverse proxy + TLS (FR-030/FR-031).
4. Agent runs post-deploy health verification; reports success with the live URL, or reports failure with diagnosis and does not leave the host in a half-configured state (auto-rollback, FR-013).
5. Agent begins monitoring (FR-040–FR-043) without further instruction.

### Journey B — Autonomous incident handling (UC-2)
1. Health check fails (FR-050). Agent checkpoints current state is already in place (FR-070).
2. Agent attempts documented remediation (FR-051) — e.g., service restart — within policy bounds, logging each attempt (FR-093).
3. If resolved: agent logs resolution, no human interruption, item appears in the next periodic report (FR-080).
4. If not resolved after the configured retry count: agent escalates (FR-052) with full context (what failed, what was tried, current state) so the human's first message is actionable, not "something's wrong, please investigate."

### Journey C — Human approval flow (UC-6)
1. Agent determines an action requires approval (MASTER_PROMPT.md §6 criteria).
2. Agent prepares the action as a reviewable proposal: what will change, why, rollback plan, blast radius.
3. Action is held in a pending state; agent continues other unblocked work (MASTER_PROMPT.md §8).
4. Human approves/rejects via the approval interface (SAS.md §5 defines the interface). Agent executes only on explicit approval, logging the approval reference (FR-093).

---

## 6. MVP Scope and Out-of-Scope

### MVP includes
All FRs marked **[MVP]** above: single-host and simple multi-app deployment, Windows Service + Windows Container packaging, GitHub Actions CI/CD trigger, Caddy reverse proxy with automatic TLS, windows_exporter + Prometheus + Grafana observability stack, policy-bounded automated remediation with human escalation, checkpoint/rollback, weekly reporting, full approval-gate and audit-logging tooling, and the live Mission Control dashboard (FR-100–108, UI_SPEC.md) — the dashboard was specified by the owner as a core MVP deliverable, not an optional add-on, and is treated with the same rigor (no fabricated state, NFR-013–015) as every other MVP requirement.

### Explicitly out of scope for MVP
- Multi-host/load-balanced topologies (FR-003) — single-host only for MVP.
- Linux-container-packaged application support (FR-014) — the single most significant scope boundary; see ADR-003 in SAS.md/ENGINEERING_REFERENCE.md for the honest discussion of why most SMB/startup OSS software ships Linux-only images and how this is deliberately deferred rather than silently worked around with WSL.
- Azure DevOps CI/CD integration (FR-021) — GitHub Actions only for MVP.
- Distributed tracing (FR-044).
- Automated root-cause diagnosis beyond basic correlation (FR-053).
- Automated network containment (FR-062) — detection and alerting only for MVP; containment stays human-approved.
- Resource right-sizing *recommendations* (FR-081) — note that the underlying utilization-data collection (FR-082) IS in MVP scope, since it's low-cost to capture alongside existing metrics collection (FR-040) and is the prerequisite for FR-081 later; only the recommendation-generation logic is deferred.
- Multi-host scalability target (NFR-011).
- **Per-database query-level event wiring for FR-102's blink behavior is dependent on the specific database engine(s) a given deployment uses** — the connection/query instrumentation mechanism differs per driver. This is 🔴 Unresolved (ENGINEERING_REFERENCE.md §2) until a target DB stack is named for a given deployment; the dashboard still functions fully for all other event sources (§7 of UI_SPEC.md) in the interim, with the affected grid cells correctly showing "not connected" (UI_SPEC.md §3.1) rather than a fabricated blink — never silently skipped or faked.

### Reversible/low-risk defaults an agent may choose autonomously (MASTER_PROMPT.md §6)
Health-check polling interval, log retention window (within a privacy-compliant range), default retry counts, default port assignments, non-production TLS cert lifetime, dashboard refresh interval. All such defaults must still be documented (not silently chosen) per MASTER_PROMPT.md §8.

---

## 7. Failure Scenarios and Expected Behavior

| Scenario | Expected behavior |
|---|---|
| Application crashes repeatedly past retry limit | Escalate per FR-052 with full diagnostic context; do not retry indefinitely (bounded retries, MASTER_PROMPT.md §5). |
| TLS renewal fails (ACME challenge blocked) | Alert before expiry (≥14 days out), do not silently serve an expired cert, do not disable TLS verification elsewhere as a workaround (MASTER_PROMPT.md §5). |
| Deployment health check fails post-rollout | Automatic rollback (FR-013) to last checkpoint (FR-070); human notified of the rollback and the failure reason. |
| Host becomes unreachable (network partition / host down) | Agent cannot act; this is reported, not silently retried forever; a maximum backoff/retry policy applies, then escalation. |
| Required credential missing/expired | Agent requests it once, explicitly, with purpose; does not proceed with a workaround that weakens security (e.g., skipping auth). |
| Agent proposes an action later found unsafe/incorrect before execution | Action is blocked by the approval gate (FR-092) or dry-run preview (FR-091) catches it before any state changes. |
| Verification cannot be performed from the current (Mac-side) environment | Per MASTER_PROMPT.md §7, the task is marked unverified and routed to Windows-side verification — never marked done on the strength of reasoning alone. |

---

## 8. Acceptance Tests

Acceptance tests are defined per-FR and executed as part of IMPLEMENTATION.md's test suite; this section states the acceptance test *type* for each FR group so IMPLEMENTATION.md can assign concrete automated tests against it.

| FR group | Acceptance test type | Verified in environment |
|---|---|---|
| FR-001–003 (Provisioning) | `terraform plan`/`apply` against a test target; idempotency check (second `apply` produces zero changes) | Windows-side (real host) |
| FR-010–014 (Deployment) | End-to-end deploy of a reference test app; health check assertion | Windows-side |
| FR-020–021 (CI/CD) | Pipeline trigger test using a GitHub Actions workflow against a test repo | Either (GitHub-hosted Windows runner) |
| FR-030–032 (Proxy/TLS) | Automated cert issuance against a test domain (ACME staging environment); HTTPS reachability assertion | Windows-side |
| FR-040–044 (Observability) | Metrics scrape assertion (`windows_exporter` endpoint returns expected metric families); Grafana dashboard renders without error | Windows-side |
| FR-050–053 (Incident handling) | Injected failure (kill a test process); time-to-detect and time-to-remediate measured against NFR-003/NFR-004 | Windows-side |
| FR-060–062 (Security) | Hardening baseline checked against a documented checklist (ENGINEERING_REFERENCE.md §5); drift-detection test via unauthorized out-of-band config change | Windows-side |
| FR-070–072 (Rollback) | Checkpoint-then-corrupt-then-rollback test; state equality assertion pre/post | Windows-side |
| FR-080–082 (Reporting) | Scheduled report generation test; content-completeness assertion (including utilization summary, FR-082) | Either |
| FR-090–093 (Agent safety) | Unit tests on the tool-execution layer: mutating tools require approval reference where policy demands it; negative test for bypass attempts | Mac-side (logic) + Windows-side (real execution confirmation) |
| FR-100–108 (Mission Control Dashboard) | SSE event-to-render latency test (NFR-013); injected-event-vs-UI-state audit for zero fabricated activity (NFR-015); grid access-matrix correctness test (a user with no grant never shows a lit cell); dashboard-restart independence test (NFR-014) | Windows-side (real SSE stream + live events required) |

Every acceptance test referenced here must exist as a runnable, named test in IMPLEMENTATION.md §7 before the corresponding FR may be marked `Done` (MASTER_PROMPT.md §9/§10).

---

## 9. Prioritized Development Roadmap

| Phase | Focus | Key FRs | Environment split |
|---|---|---|---|
| **Phase 0** | Repo scaffolding, documentation, CI skeleton, dependency audit | NFR-001, NFR-010 | Mac-side |
| **Phase 1** | Core provisioning + single-app deployment (Windows Service path) | FR-001, FR-002, FR-010, FR-030, FR-031 | Mac-side build → Windows-side verify |
| **Phase 2** | Windows Container deployment path, CI/CD trigger | FR-011, FR-012, FR-013, FR-020 | Mac-side build → Windows-side verify |
| **Phase 3** | Observability stack | FR-040–FR-044 | Mac-side build → Windows-side verify |
| **Phase 4** | Incident detection, remediation, escalation, approval gates | FR-050–FR-053, FR-090–FR-093 | Mac-side build → Windows-side verify |
| **Phase 5** | Security hardening + drift detection | FR-060, FR-061 | Mac-side build → Windows-side verify |
| **Phase 6** | Rollback/checkpoint, reporting | FR-070–FR-072, FR-080, FR-082 | Mac-side build → Windows-side verify |
| **Phase 7** | Mission Control Dashboard (UI_SPEC.md) — depends on Phases 1–6's components existing to have real events to visualize | FR-100–FR-108, NFR-013–015 | Mac-side build → Windows-side verify (real SSE stream + live events required) |
| **Phase 8 (Post-MVP)** | Multi-host, Linux-container compatibility path, Azure DevOps, tracing, right-sizing | FR-003, FR-014, FR-021, FR-044 (extended), FR-081, NFR-011 | TBD, re-scoped after MVP retrospective |

Full task breakdown with IDs is in IMPLEMENTATION.md §3–§6, which this roadmap maps onto directly (Phase N here ↔ IMPLEMENTATION.md Phase N).
