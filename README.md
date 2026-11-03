# OpsPilot Cloud

OpsPilot Cloud is a single-host operations platform for Windows: it provisions and
hardens a Windows host, deploys applications with blue/green rollback, installs a
Windows-native observability stack, detects/remediates incidents, and produces weekly
operational reports.

The product runtime is **Windows-only**. This repository is authored and statically
verified on macOS, then installed and runtime-verified on Windows. There is no WSL,
Linux-container, or bash dependency in the supported runtime path.

> **Verification status:** all Mac-side source, lint, type, unit-test, Terraform, and
> PowerShell-analysis checks pass (see `IMPLEMENTATION.md` §4.1). Windows-only runtime
> checks are **not** claimed as passing; they are tracked in `IMPLEMENTATION.md` §4.2
> and in `handoff/HANDOFF_<phase>.md` for the Windows-side agent.

## Platform model

| | Mac-side (authoring) | Windows-side (runtime/verification) |
|---|---|---|
| Purpose | Write source, docs, tests; run static analysis and unit tests | Install, run, and verify the product |
| Commands | `make lint`, `make test`, `make bundle PHASE=<n>` | `.\powershell\install\install.ps1 -Phase <n>` |
| Requirements | Python 3.11+, PowerShell 7+, Terraform, Git | Windows 11 Pro/Enterprise or Windows Server, PowerShell 7+, Python 3.11+, elevated shell, Terraform, Caddy; Docker in Windows-container mode only if deploying containers |
| Integration tests | `make test-integration` fails fast (by design) | `make test-integration` runs the real suite |

## Quick start (macOS authoring)

```bash
python3 -m venv .venv
. .venv/bin/activate
python -m pip install -r requirements.txt -r requirements-dev.txt

make lint                        # ruff + mypy + PSScriptAnalyzer
make test                        # 176 mocked unit tests
terraform -chdir=src/provisioning_engine/terraform fmt -check -recursive
terraform -chdir=src/provisioning_engine/terraform init -backend=false
terraform -chdir=src/provisioning_engine/terraform validate

make bundle PHASE=1              # dist/opspilot-cloud-1-<sha>.zip + handoff/HANDOFF_1.md
```

`make lint` needs `pwsh` and the pinned `PSScriptAnalyzer` module. The repository's
lint policy is in `powershell/PSScriptAnalyzerSettings.psd1`; the pinned module list is
in `powershell/modules/RequiredModules.psd1`.

## Windows installation

Run from an **elevated** PowerShell 7 session on the target host:

```powershell
git clone <repository-url> C:\OpsPilotCloud
Set-Location C:\OpsPilotCloud

# Preview: no services stopped, no files copied, no state written.
.\powershell\install\install.ps1 -Phase 1 -WhatIf

# Install and verify. Must be elevated.
.\powershell\install\install.ps1 -Phase 1
```

The installer:

1. Verifies Python 3.11+, PowerShell 7+, Terraform, and the phase's handoff manifest.
2. Copies the payload to `C:\OpsPilotCloud` and creates a virtual environment.
3. Registers `OpsPilotControlPlane` as a native Windows Service (default account
   `NT AUTHORITY\LocalService`, with data-directory ACLs granted to that SID).
4. Registers `OpsPilotDashboardUI` as a separate Windows Service on `127.0.0.1:8090`
   (Mission Control; its failure/restart does not affect the Control Plane).
5. Runs the phase integration suite unless `-SkipIntegrationTests` is supplied.
6. Writes the verification result into `handoff/HANDOFF_<phase>.md`.

Upgrade uses the documented checkpoint/stop/copy/start/health/rollback sequence:

```powershell
.\powershell\install\install.ps1 -Phase 2 -Upgrade
```

Blue/green application deployment requires an explicit cutover approval reference
(the reverse-proxy cutover is `externally_visible` under the approval policy):

```powershell
.\powershell\install\install.ps1 -Phase 2 -Deploy `
  -Domain app.example.com -Email ops@example.com -CutoverApprovalRef APR-2026-001
```

Do not run `install.ps1` off Windows; it throws immediately by design.

## Control-plane CLI

The CLI is available after installing the runtime dependencies. Set `PYTHONPATH=src`
when running directly from a checkout:

```bash
export PYTHONPATH=src
python -m orchestrator.cli check-config
python -m orchestrator.cli provision --host-config config/defaults/host.yaml
python -m orchestrator.cli provision --host-config config/defaults/host.yaml --apply
python -m orchestrator.cli deploy --deployment-config config/defaults/deployment.yaml \
  --domain app.example.com --email ops@example.com --cutover-approval-ref APR-2026-001
python -m orchestrator.cli status
python -m orchestrator.cli pending
python -m orchestrator.cli approve --request-id <request-id> --approver ops@example.com
python -m orchestrator.cli report --out dist/weekly-report.md
python -m orchestrator.cli serve
```

`python -m orchestrator.cli <command> --help` documents every flag. The polling
service entry point is `run_control_plane.py`; the install script registers it as the
`OpsPilotControlPlane` Windows Service.

## Mission Control dashboard

Mission Control (COMP-13) is a second, independent Windows Service,
`OpsPilotDashboardUI`, started from `run_dashboard.py`. It reads the durable event
journal, structured logs, and Prometheus; it never calls into the Control Plane, so a
dashboard crash or restart cannot take the platform down (NFR-014).

```powershell
# Installed automatically by install.ps1. Manual run for diagnostics:
python run_dashboard.py --data-dir C:\ProgramData\OpsPilot `
  --config config\defaults\dashboard.yaml --host 127.0.0.1 --port 8090
```

- Direct URL: `http://127.0.0.1:8090/`
- Through Caddy: `https://<hostname>/mission-control/` (SSE route uses
  `flush_interval -1`)
- Config: `config/defaults/dashboard.yaml` (schema:
  `config/schema/dashboard.schema.json`)
- Events: `<data_dir>/logs/events.jsonl`, written by the Control Plane components and
  tailed by the dashboard; the browser receives SSE at
  `/mission-control/api/events/stream`.
- Every tile pulse, matrix cell, ticker line, and Agent Thinking entry is driven by a
  real journal event; there is no client-side animation that invents activity
  (NFR-015).
- The frontend is static HTML/CSS/JS in `frontend/dashboard/` — no Node.js, npm, or
  Vite build step at runtime or build time.

Per-database query blink is **not connected** in the MVP: the dashboard ships the
documented session/query ingest contract and shows `DB ADAPTER: NOT CONNECTED` until a
target database engine is named and an adapter is written (ADR-008). Ingest is disabled
by default; enabling it requires `OPSPILOT_DASHBOARD_INGEST_TOKEN`. Likewise, Mode B's
raw token stream has no producer in the six-document MVP (there is no LLM call site), so
the UI falls back to the structured trace as specified by `UI_SPEC.md` §5.3.

## Configuration

| File | Purpose | Schema |
|---|---|---|
| `config/defaults/host.yaml` | Baseline host intent and allowed ports | `config/schema/host-config.schema.json` |
| `config/defaults/deployment.yaml` | Application packaging, ports, health checks, rollout | `config/schema/deployment-config.schema.json` |
| `config/defaults/alert-rules.yaml` | Incident thresholds and notification channel | `config/schema/alert-rules.schema.json` |
| `config/defaults/approval-policy.yaml` | Categories that require human approval | `config/schema/approval-policy.schema.json` |
| `config/defaults/dashboard.yaml` | Mission Control bind address, journal, state, Prometheus, ingest, logs | `config/schema/dashboard.schema.json` |
| `config/schema/tool-contract.schema.json` | Shape of every tool contract | — |

`make check-config` validates all defaults against their schemas. Secrets are never
stored in these files: SMTP/API credentials are referenced by name and resolved through
DPAPI-backed local storage (`C:\ProgramData\OpsPilot\secrets`, ACL-restricted).

## Repository layout

```text
src/
  common/               shared config, clocks, process, logging, errors
  orchestrator/         control-plane CLI, polling loop, phase bundle generator
  tools_readonly/       COMP-02 read-only tools
  tools_mutating/       COMP-03 mutating tools (checkpointed, approval-gated)
  approval_gate/        COMP-04 approval policy and request store
  checkpoint_store/     COMP-05 SQLite checkpoints and verified rollback
  observability/        COMP-06 log aggregation, utilization, install_stack.ps1, Grafana
  incident_engine/      COMP-07 alerts, remediation, escalation, drift
  deployment_engine/    COMP-08 Windows image build + blue/green rollout
  provisioning_engine/  COMP-09 Terraform wrapper + reference Azure module
  reverse_proxy/        COMP-10 Caddyfile generator
  secrets_manager/      COMP-11 DPAPI-backed secrets
  audit_log/            COMP-12 JSON Lines audit trail
  event_bus/            COMP-13 durable event journal, follower, in-process bus
  dashboard_ui/         COMP-13 FastAPI backend, reducer, SSE, ingest contract
  reporting/            weekly operational report
frontend/
  dashboard/            COMP-13 static SPA (vanilla HTML/CSS/JS, no build step)
powershell/
  install/              Windows install/upgrade/deploy entry point
  modules/              Service, Firewall, Baseline, Proxy, Container, Secrets, Health, Install
tests/
  unit/                 mocked, Mac-runnable
  integration/          real Windows host; fails fast off-Windows
handoff/                generated per-phase Handoff Manifests
config/                 default YAML + JSON Schemas
```

## Testing

| Command | Host | What it proves |
|---|---|---|
| `make lint` | Mac or Windows | `ruff`, `mypy`, PSScriptAnalyzer clean |
| `make test` | Mac or Windows | 176 unit tests with mocked Windows calls |
| `make test-integration` | **Windows only** | Real services, DPAPI, Terraform apply, containers, blue/green timing |
| `make bundle PHASE=<n>` | Mac | Phase zip + `handoff/HANDOFF_<n>.md` |
| `make check-config` | Mac or Windows | Default configs validate against schemas |

Off Windows, `make test-integration` exits non-zero with
`Windows-only; run on the Windows-side agent (IMPLEMENTATION.md §7).` It never reports
a skip as a pass.

## Security model

- Mutating tools checkpoint before acting and return a rollback plan.
- `destructive`, `irreversible`, `externally_visible`, and `security_sensitive` actions
  require an approval reference (`config/defaults/approval-policy.yaml`).
- Reverse-proxy cutover during blue/green deployment is `externally_visible` and
  requires a one-time `--cutover-approval-ref`.
- Services run as `NT AUTHORITY\LocalService` by default; `LocalSystem` is not the
  default execution context (ADR-010).
- DPAPI secrets are tied to the service account and are intentionally non-portable.
- All actions are recorded as JSON Lines audit entries with inputs, outcome,
  checkpoint ID, approval reference, and evidence.

## Documentation

- `MASTER_PROMPT.md` — engineering charter, platform split, non-negotiable rules
- `PRD.md` — requirements (`FR-*`, `NFR-*`) and acceptance criteria
- `SAS.md` — architecture, components (`COMP-*`), deployment design
- `IMPLEMENTATION.md` — task phases (`IMPL-*`), statuses, handoff format, commands
- `ENGINEERING_REFERENCE.md` — dependency audit, schemas, security policy, ADR log
- `handoff/HANDOFF_<n>.md` — the Mac → Windows verification contract

## License

Proprietary. All rights reserved unless a `LICENSE` file is added.
