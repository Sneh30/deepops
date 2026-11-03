# UI_SPEC.md — OpsPilot Cloud "Mission Control" Dashboard

**Status:** v0.1. Governed by [MASTER_PROMPT.md](./MASTER_PROMPT.md). Implements new FR-100–FR-108 (added to [PRD.md](./PRD.md) §3.11) via [SAS.md](./SAS.md) COMP-13.
**Purpose of this document:** this is a visual/interaction spec, not just a requirements table — because the deliverable is explicitly meant to be looked at continuously, as ambient art, and prose precision about *how it behaves* matters as much as *what it shows*. An agent building this should treat this document the way IMPLEMENTATION.md treats code tasks: literal, not aspirational.

**The brief, in one sentence:** a single always-on screen, grid-styled, dark/techy, that makes the real, current state of the whole platform — every connected user, every database, every agent decision, every log line — visible and alive, so that even when the system is quiet it still reads as something clearly, continuously *working*.

---

## 1. Design Intent

- **This is an operations dashboard that is also meant to function as ambient art in a living room.** Every requirement below serves both purposes simultaneously — nothing is decorative-only, and nothing is func­tional-only-and-ugly. If a real signal exists, it should be rendered in a way that's genuinely satisfying to watch.
- **Aesthetic direction:** dark-mode "command center" / sci-fi ops-room techy — think glowing grid lines, monospace/terminal type for data, small persistent motion everywhere (scan lines, pulse animations, flowing log text) rather than a static dashboard that only updates on refresh. Nothing should ever look frozen. A moment of true silence on the backend should still read as "idle and alert," not "broken."
- **No fabrication principle applies here too (MASTER_PROMPT.md §7):** every animated/blinking element must be driven by a real event. The "looks like it's doing a lot" requirement is satisfied by *surfacing real low-level activity that's normally invisible* (heartbeats, polling ticks, scrape cycles), not by faking activity that isn't happening. See §6.
- **Must look finished and intentional even with only 1 user and 1 database connected** — the grid/layout must not look sparse or broken at small scale; see §3.4 for the minimum-viable-grid rule.

---

## 2. Screen Layout (Top-Level Grid)

```
┌─────────────────────────────────────────────────────────────────────────────┐
│  TOP BAR — Feature/Service Status Strip (§3)                                 │
│  [PROVISIONING●] [DEPLOY●] [PROXY/TLS●] [METRICS●] [INCIDENT●] [SECRETS●]... │
├───────────────────────────────┬─────────────────────────────────────────────┤
│                                │  RIGHT RAIL — Tabs (§5)                     │
│  MAIN GRID (§3.4)              │  [ Server Summary ] [ Observability ]        │
│  Users × Services access       │  [ Agent Thinking ]                          │
│  matrix, blink-on-activity     │                                              │
│                                │  (tab content fills this rail)               │
│                                │                                              │
├───────────────────────────────┴─────────────────────────────────────────────┤
│  BOTTOM STRIP — Live connected users table (§4) + Prometheus/log ticker (§6) │
└─────────────────────────────────────────────────────────────────────────────┘
```

Layout is CSS Grid (literal — this satisfies the "grid style pattern" requirement structurally, not just visually): a named-areas grid with `topbar`, `maingrid`, `rail`, `bottomstrip`, responsive down to a single ultrawide/TV display as the primary target (1920×1080 and 3440×1440 both required; this is a "screen on a wall," not a responsive web app — mobile/tablet layouts are explicitly out of scope, see PRD.md §6 additions).

---

## 3. Top Bar — Feature/Service Status Strip

One pill/tile per platform capability (maps 1:1 to SAS.md COMP-06 through COMP-12, plus COMP-09/10):

| Tile | Bound to | "On" condition | "Blinking/active" condition |
|---|---|---|---|
| Provisioning | COMP-09 | Provisioning Engine process healthy | A `terraform apply`/plan is actively running |
| Deploy | COMP-08 | Deployment Engine idle-healthy | A deployment/rollback is in progress |
| Proxy/TLS | COMP-10 | Caddy running, no cert errors | A request is being routed / a cert renewal is in flight |
| Metrics | COMP-06 | windows_exporter + Prometheus up | A scrape cycle just completed (pulses on every scrape interval — this alone guarantees near-constant low-level motion, §1) |
| Incident Engine | COMP-07 | No active incident | Health-check poll just ran (pulses every poll interval) / lit solid red + pulsing fast during an active incident |
| Secrets | COMP-11 | Available | A secret was just decrypted for a tool call (brief flash, never shows which secret or its value) |
| Audit | COMP-12 | Writer healthy | A new audit log line was just appended (flashes per write) |
| Approval Gate | COMP-04 | No pending approvals | Solid amber + pulsing while an approval is pending human action |

### 3.1 Visual states (applies to every tile, every grid cell in §4, consistent vocabulary across the whole UI)
- **Dim/steady glow** = present and healthy, currently idle.
- **Bright pulse (one-shot, ~400–600ms decay)** = a discrete event just happened (one scrape, one log line, one query).
- **Solid bright + continuous pulse** = a sustained operation in progress (a deployment running, an incident active).
- **Red** = error/failed state, always takes visual priority over everything else on that tile.
- **Grey/hollow outline** = component not yet connected/unreachable — never silently hidden; absence is itself a signal worth showing (MASTER_PROMPT.md §7 — don't let the UI imply health it can't verify).

### 3.2 Why this alone solves "looks like it's doing a lot even when idle"
Metrics scrape cycles (default interval, PRD.md NFR) and health-check polls (FR-050, default ≤60s) are real, continuous background activity on *any* healthy running system. Surfacing just those two as top-bar pulses means the bar is never fully static during normal operation — this is honest, not simulated (§1).

### 3.3 Service tiles are clickable
Clicking a tile opens that component's detail in the right rail (§5) — e.g., clicking "Metrics" jumps to the Observability tab filtered to that component.

### 3.4 Minimum-viable-grid rule
With only 1 service tile types present (MVP ships all 8 from day one per COMP-06–12/09/10, so this is somewhat moot at MVP, but stated for future-proofing): tiles never shrink to fill empty space in a way that looks broken. Use a fixed tile size with the grid simply ending — empty grid cells render as faint dot-grid background (part of the techy aesthetic, §1), never as visibly "missing" content.

---

## 4. Users × Services Access Matrix (the core "art piece" grid)

This is the centerpiece: a literal grid, **rows = connected users, columns = services/databases**, cell = that user's access state to that service.

### 4.1 Data model per cell
```
cell(user, service) ∈ {
  no_access        → cell not rendered / rendered as empty grid dot (user has no grant to this service)
  has_access_idle  → steady dim glow in the service's assigned color (user is authorized, not currently active)
  active_fetching  → bright blink/pulse, sustained for the duration of the actual query/request (user is this instant pulling data)
}
```

### 4.2 Rows — Connected Users
- One row appears the instant a new session/connection is observed on the server (login, API session, DB connection) — **real-time, not polled on a delay** (WebSocket/SSE push from the Control Plane, §7).
- Row label: username/account + a small IP badge.
- A new row's *first appearance* gets a brief "new connection" highlight animation (distinct from the activity blink) so a human glancing at the wall can tell "someone just showed up" — this doubles as a lightweight security-awareness feature (ties to FR-061 drift/unauthorized-access visibility) and as a satisfying visual event for the art-piece effect.
- Row disappears (with a brief fade, not an abrupt cut) a configurable grace period after the session/connection ends — never instantly vanishes, so a human watching can register the departure.

### 4.3 Columns — Services (including each individual database)
- Each service/database gets its own column **and its own assigned accent color**, consistent everywhere it appears in the UI (top bar tile, this grid, the ticker in §6) — e.g., if you're running 3 databases, each is visually distinct (not just "database" as one generic column), satisfying "that database which that user has access of should blink."
- Column header shows the service name + a small live request-rate sparkline (tiny, inline, part of the header row — reinforces "alive" even in the header).

### 4.4 Blink behavior — the literal requirement
> "that database which that user is has access off should blink or should stay on and blink when the user fetches data from it"

Implemented exactly as stated:
- **Has access, idle:** cell stays **on** (steady glow) — not off, not hidden. This is the "stay on" half of the requirement.
- **Actively fetching:** cell **blinks/pulses** for the real duration of that query/request (starts on query-start event, stops on query-complete event — never a fixed decorative duration divorced from the real call, §1/§6).
- **No access:** cell is not lit at all (and is visually distinct from "idle" — e.g. a faint grid-dot rather than a glow), so access scope is legible at a glance across the whole user base — a secondary, free security-visibility benefit.

### 4.5 Interaction
- Hovering/clicking a cell shows a tooltip/side-panel: user, service, access level (read/write/admin if the backend tracks it), last query timestamp, and (if the right rail's Observability tab is open) jumps the log view to that user+service's recent activity.
- The whole grid supports a "freeze frame" toggle (pauses live updates, lets you inspect a moment) — for the art-piece use case, nobody wants to lose a glance mid-thought; but it's never on by default.

---

## 5. Right Rail — Tabs

Three tabs, always visible, switchable without losing the main grid's live state (main grid keeps updating in the background regardless of which tab is active):

### 5.1 Server Summary tab
High-level health rollup: uptime, active incident count, total connected users right now, total services, last deployment, pending approvals count, weekly-report highlights (pulls from FR-080/FR-082). This is the "glanceable" tab — big numbers, minimal text, styled like a cockpit instrument cluster.

### 5.2 Observability tab
- Live Prometheus-backed metric charts (CPU/memory/disk/network per host, request rate per service) — small multiples, auto-scrolling time-series, techy monospace axis labels.
- Live log stream (structured JSON Lines from COMP-12/observability pipeline), color-coded by level (DEBUG/INFO/WARN/ERROR/CRITICAL per ENGINEERING_REFERENCE.md §7), auto-scrolling terminal-style feed — this is literally "logs of Prometheus" plus the structured application/audit logs together in one continuously flowing panel.
- Filterable by service/component (click-through from top bar tiles, §3.3, or grid columns, §4.3).

### 5.3 Agent Thinking tab — the LLM's reasoning, in depth
Two togglable sub-modes (per your decision — both, togglable), switch control top-right of this tab:

**Mode A — Structured Trace (default):**
A flowing, terminal-styled log of the agent's actual decision loop, one entry per real step:
```
[14:32:01] THINKING   evaluating health_check result: service X failed 2/3 polls
[14:32:01] DECISION   remediation candidate: restart_service (bounded retry 1/2)
[14:32:02] TOOL_CALL  restart_service(service=X, dry_run=false) → COMP-03
[14:32:02] GATE       approval_gate: auto-cleared (low-risk, reversible)
[14:32:03] CHECKPOINT snapshot taken: ckpt_8f3a1
[14:32:05] RESULT     status=success, attempt_count=1, evidence attached
[14:32:05] THINKING   verifying post-execution health before closing incident...
```
Every line here is a real entry from the Agent Orchestrator's (COMP-01) structured decision log — not prose generated for display. This is the direct visual surface of MASTER_PROMPT.md §7 (no fabrication) and the audit log schema (ENGINEERING_REFERENCE.md §4.3): what you're watching IS the audit trail, rendered live and styled, not a separate "for show" feed.

**Mode B — Raw Token Stream:**
When toggled, shows the actual raw model output tokens streaming character-by-character as the agent reasons (its literal chain-of-thought/response stream from the underlying LLM call), monospace, scrolling — maximalist, literal "watch it think." This requires the agent runtime to expose a token-stream hook (§7); if unavailable for a given call (e.g., a tool-only step with no LLM generation involved), Mode B falls back to showing that step's Mode A entry instead of showing nothing, so it never looks like it froze.

Both modes share the same color/pulse vocabulary as §3.1 (a THINKING line pulses once on arrival, a GATE-pending line stays solid amber until resolved, etc.) — visual consistency across the entire dashboard is a hard requirement, not a nice-to-have.

---

## 6. Bottom Strip — Connected Users Table + Live Ticker

- **Left: compact table form** of the same user data as the grid's rows (§4.2) — username, IP, connected-since timestamp, service count — for when a precise list (not just the visual grid) is needed; sortable by column.
- **Right: a continuously flowing ticker** of the rawest-granularity real events: every Prometheus scrape tick, every health-check poll, every DB connection open/close — deliberately the "boring" high-frequency events that prove the system is alive second-to-second, kept visually small/subordinate to the main grid/rail so it adds texture and motion without competing for attention.

---

## 7. Real-Time Data Architecture

- **Transport:** Server-Sent Events (SSE) from the Control Plane's web UI service to the browser — chosen over polling because every blink/pulse must reflect an event the instant it happens (your explicit requirement), and SSE is simpler and more Windows-friendly than WebSockets for a one-directional "server pushes events" model (no bidirectional need here). Fallback to short-interval polling only if SSE is blocked by a network policy, documented as a degraded mode, never silently.
- **Event sources feeding the UI, all real (§1 no-fabrication rule):**
  - COMP-01 Agent Orchestrator → decision/thinking log events (§5.3)
  - COMP-03 Mutating Tool Registry → tool-call/execution events
  - COMP-04 Approval Gate → pending/resolved approval events
  - COMP-05 Checkpoint Store → checkpoint-taken events
  - COMP-06 Observability Pipeline → metric scrape ticks, structured log lines
  - COMP-07 Incident Engine → health-check poll ticks, incident open/close
  - COMP-12 Audit Log → every append (mirrors into the ticker, §6)
  - **Database/service connection and query layer** → user-session open/close, per-query start/complete events, scoped per user+service — this is the feed that drives §4.4's blink behavior and must be wired per actual database technology in use (instrumented at the connection-pooling/driver layer; exact mechanism is an IMPLEMENTATION.md task, since it depends on which DB engines the deployed application actually uses — flagged as 🔴 Unresolved/environment-dependent in ENGINEERING_REFERENCE.md §2 until a target DB stack is named).
- **No event is invented client-side.** If a real feed for a visual element isn't wired yet, that element renders in its explicit "not connected" state (§3.1 grey/hollow) rather than a plausible-looking fake pulse — this is a hard rule, not a preference, consistent with MASTER_PROMPT.md §7 applied to the UI layer specifically.

---

## 8. Technology Choices (Windows-native, consistent with SAS.md §6)

| Concern | Choice | Rationale |
|---|---|---|
| UI service host | Python (FastAPI or equivalent ASGI app) running as its own Windows Service, alongside the Control Plane | Reuses the existing Python/Windows-Service pattern already established for COMP-01–12; no new runtime paradigm |
| Real-time push | Server-Sent Events over HTTP | Simple, one-directional, no extra Windows-side infra beyond what Caddy (COMP-10) already reverse-proxies |
| Frontend | A single-page app (vanilla/lightweight framework — exact choice left to IMPLEMENTATION.md, e.g., a small bundle via Vite), served as static assets by the same UI service | Keeps the "kiosk screen" simple to deploy — one URL, no separate hosting |
| Styling | CSS Grid (literal grid layout, §2) + CSS animations for pulse/blink/glow (no heavy animation library needed) | Matches the grid-pattern requirement structurally; keeps the Windows-side footprint light |
| Access | Reverse-proxied through Caddy (COMP-10) at a dedicated path/subdomain, TLS included automatically (FR-031) | Reuses existing infrastructure rather than standing up a parallel proxy |
| Kiosk display | Any browser in fullscreen/kiosk mode pointed at the UI service's URL | No native app needed — the "living room screen" is just a browser, per your preferred hosting model |

This dashboard is itself a read-only consumer of the platform's existing event/log/metric surfaces (§7) — it introduces **no new mutating tools** and therefore does not expand the Approval Gate's (COMP-04) surface area; it is purely observational, which keeps its own risk profile low per MASTER_PROMPT.md §6.
