# MASTER_PROMPT.md — OpsPilot Cloud Engineering Charter

**Audience:** every AI coding agent (and any human) that writes, reviews, tests, deploys, or operates OpsPilot Cloud.
**Status:** Living document. Authoritative over all other project documentation when a conflict exists, except that a more specific document (PRD.md, SAS.md, IMPLEMENTATION.md, ENGINEERING_REFERENCE.md, UI_SPEC.md) wins on matters squarely within its own scope, as long as it does not contradict a rule in this file.
**Related documents:** [PRD.md](./PRD.md) · [SAS.md](./SAS.md) · [IMPLEMENTATION.md](./IMPLEMENTATION.md) · [ENGINEERING_REFERENCE.md](./ENGINEERING_REFERENCE.md) · [UI_SPEC.md](./UI_SPEC.md)

---

## 0. The Two-Environment Development Model

OpsPilot Cloud is authored on macOS and operated on Windows. This is a structural fact every agent must internalize before touching the repository.

| | **Mac-side (authoring)** | **Windows-side (runtime/verification)** |
|---|---|---|
| Who works here | The development agent(s), using any macOS/Unix tooling | The operations/verification agent(s), on actual Windows 11 Pro or Windows Server |
| What's produced | Source code, documentation, tests, build scripts, a distributable bundle | Installed, running, verified OpsPilot Cloud instance |
| Tooling allowed | Unrestricted — bash, Makefiles, Unix CLIs, any language toolchain needed to write/test/package code | PowerShell 7+ only; no bash required for any runtime or install step |
| Constraint in force | None beyond normal engineering discipline | **Windows-only constraint (Section 1) applies in full** |

**The Windows-only constraint (Section 1) governs the shipped product and its runtime — not the authoring toolchain.** A Mac-side agent may use `make`, bash scripts, `jq`, Unix test runners, etc., to build, lint, and package the project, because none of that executes on a customer's Windows host. What that tooling *produces* — the installer payload, the services, the agent runtime, every script that runs on the target machine — must be Windows-native and must run correctly with no WSL, no Linux VM, no Linux container, and no bash dependency.

**The handoff contract.** The repository, hosted on a Git remote (GitHub), is the single source of truth and the only channel through which the Mac-side and Windows-side agents communicate. There is no other shared state. Concretely:

1. The Mac-side agent does its work, commits to the repository, and when a phase (see IMPLEMENTATION.md) is ready for Windows verification, runs the project's `make bundle` target (or equivalent) to produce a distributable build artifact, and writes a **Handoff Manifest** (`handoff/HANDOFF_<phase-id>.md`, see IMPLEMENTATION.md §6) describing exactly what was built, what must be verified on Windows, and what is explicitly NOT yet verified because it cannot be verified off-Windows.
2. The Mac-side agent pushes both the source commit and the bundle (as a release asset or tracked build artifact per IMPLEMENTATION.md) to the Git remote.
3. The Windows-side agent pulls from the same remote, reads the current Handoff Manifest, and performs the Windows-only installation, execution, and verification steps the manifest asks for, using PowerShell 7+ exclusively.
4. The Windows-side agent records results (pass/fail per verification item, with evidence) back into the repository — updating the manifest or appending a verification log — and commits/pushes that back to the remote.
5. Neither agent assumes the other's local filesystem, shell state, or in-memory context. All communication is through committed, reviewable artifacts in the repository. This makes the handoff resumable and auditable by design (Section 9).

**A rule of thumb:** if a task can be fully attempted and verified without a real Windows host (e.g., "does this Python module parse correctly," "does this PowerShell script pass a static analyzer," "is this Terraform plan syntactically valid"), a Mac-side agent should do it and say so plainly in the manifest. If a task requires Windows to actually execute and be truthfully verified (e.g., "does this script register a Windows Service," "does DPAPI encryption round-trip," "does the Windows container build"), it must be left as an open verification item for the Windows-side agent — never marked complete on the strength of Mac-side reasoning alone. See Section 7 on fabrication.

---

## 1. Windows-Only Enforcement (Runtime)

This section governs what OpsPilot Cloud **runs as** and **requires on the target host**. It does not restrict macOS-side authoring tooling (Section 0).

### 1.1 Allowed runtime platforms
- Windows 11 Pro (workstation/edge deployments) or Windows Server 2019+ (production).
- PowerShell 7+ (cross-version compatible; do not rely on Windows PowerShell 5.1-only cmdlets without a documented fallback).
- Python 3.11+ as the AI orchestration and tool-execution language.
- Native Windows Containers (`containerd` + Docker Engine on Windows Server, or Docker Desktop in Windows-container mode on Win 11 Pro/Enterprise) where containerization is required. See SAS.md §6 and ENGINEERING_REFERENCE.md for the documented Windows-container-vs-Linux-container-workload limitation (ADR-003).

### 1.2 Prohibited at runtime
The shipped product must never *require*, at install time or run time, on the target Windows host:
- Linux as a host OS.
- WSL or WSL2, in any capacity (including as an invisible dependency of a chosen tool — verify before adopting any dependency).
- Linux virtual machines.
- Linux containers (except as an explicitly out-of-scope, documented future option per ADR-003 — never silently).
- Bash as a required execution environment on the target host.
- Any Linux-only infrastructure tool with no Windows-native build.

### 1.3 Dependency vetting procedure
Before an agent adds any new runtime dependency (library, CLI tool, service), it must:
1. Confirm an official Windows build/binary exists (not a community fork of unknown maintenance status, unless no alternative exists and this is documented as an accepted risk).
2. Confirm the dependency does not itself silently require WSL, Docker Desktop's Linux-container mode, or a Linux subsystem to function.
3. Record the finding — tool, version, platform evidence, source URL — in ENGINEERING_REFERENCE.md §2 (Windows Platform Compatibility Reference) in the same commit that introduces the dependency.
4. If no Windows-native option exists, STOP. Do not substitute a Linux-dependent tool silently. Document the gap as an explicit limitation in the relevant document (PRD non-functional requirement, SAS architectural decision, or IMPLEMENTATION task note) and either propose a Windows-native alternative design or flag it as an unresolved decision per Section 6 of this document.

**Violating Section 1.2 is treated as a correctness bug, not a style issue.** Any agent that discovers an existing violation must raise it immediately (file it as a known limitation in ENGINEERING_REFERENCE.md §11 and surface it in the next status update) rather than leaving it undocumented.

---

## 2. Agent Roles and Responsibilities

OpsPilot Cloud's documentation and codebase are built and operated by AI agents acting in the following roles. One agent session may hold multiple roles sequentially; the roles describe responsibility, not separate processes.

| Role | Responsibility | Primarily active |
|---|---|---|
| **Systems Architect** | Owns SAS.md consistency, component boundaries, interface contracts, ADRs | Mac-side, design phases |
| **Implementation Engineer** | Writes source code and tests per IMPLEMENTATION.md tasks | Mac-side, build phases |
| **SRE / Platform Operator Agent** (the product itself, once built) | The AI agent OpsPilot Cloud ships to run on a customer's Windows host — provisions, deploys, monitors, remediates, per SAS.md §5 | Windows runtime, post-build |
| **DevSecOps Engineer** | Owns CI/CD pipeline definitions, security gates, secrets handling | Mac-side (pipeline authoring) + Windows-side (runtime enforcement) |
| **Verification Agent** | Executes Windows-only install/verify steps per the current Handoff Manifest; never the same agent session that wrote the code it's verifying, where feasible | Windows-side |
| **Technical Writer** | Keeps PRD/SAS/IMPLEMENTATION/ENGINEERING_REFERENCE synchronized when any one changes | Either side, as part of the task that caused the change |

**Human owner's role** is deliberately minimal: initial authorization, supplying credentials/secrets that cannot be generated by an agent, and approval gates for destructive or high-risk actions (Section 6, Section 8). The human is not expected to resolve routine ambiguity, review every commit, or manually bridge the Mac↔Windows handoff — the Handoff Manifest mechanism (Section 0) exists specifically so agents can do that themselves.

---

## 3. Engineering Principles

1. **Small, modular components with explicit interfaces.** Every component in SAS.md has a documented interface contract (inputs, outputs, error modes) before implementation begins. No component may reach into another's internal state.
2. **Deterministic builds, reproducible environments.** Pin dependency versions (`requirements.txt` / `pyproject.toml` with locked versions; PowerShell module versions pinned in a manifest). A build run twice from the same commit must produce functionally identical artifacts.
3. **Machine-readable configuration over prose configuration.** Config lives in YAML/JSON/`.psd1` with a validated schema (ENGINEERING_REFERENCE.md §4), never in free-text instructions an agent must interpret at runtime.
4. **Structured logs and errors.** Every log line and error must be structured (see ENGINEERING_REFERENCE.md §7 logging conventions) — human-readable is a nice-to-have, machine-parseable is required, because agents (not just humans) read these logs to decide what to do next.
5. **Self-documenting code, but documentation is not optional.** Code clarity reduces the need for comments; it does not replace updating PRD/SAS/IMPLEMENTATION/ENGINEERING_REFERENCE when behavior changes (Section 5).
6. **Read-only vs mutating tools are always separate.** Any tool/function an agent can call that changes infrastructure state must be a distinct, explicitly named tool from any tool that only reads/diagnoses state. Never overload one tool to do both (SAS.md §5, Section 6 below).
7. **Least privilege by default.** Every credential, API token, and execution context is scoped to the minimum required for its task. No component runs as a Windows admin/SYSTEM account unless its documented function requires it.
8. **Everything is resumable.** Any multi-step process (a build phase, a deployment, an agent's own task queue) must be checkpointable so that an interruption (crash, context limit, human pause) loses no more than the current unit of work (Section 9).

---

## 4. Coding Standards and Repository Conventions

- **Languages:** Python 3.11+ (PEP 8, type-hinted, `ruff` + `mypy` clean) for orchestration/AI-agent code; PowerShell 7+ (`PSScriptAnalyzer` clean, approved verb-noun cmdlet naming) for all Windows system operations.
- **Repository layout:** defined authoritatively in IMPLEMENTATION.md §1. Do not create top-level directories outside that structure without updating IMPLEMENTATION.md in the same commit.
- **Commit messages:** imperative mood, reference the IMPLEMENTATION.md task ID they address (e.g., `IMPL-204: add windows_exporter install script`).
- **No secrets in source control, ever** — not in code, config defaults, test fixtures, or commit messages. See Section 4 privacy rules and ENGINEERING_REFERENCE.md §5.
- **Temporary/research artifacts** (scratch notes, exploratory scripts) live under `docs/research/` or `/tmp`-equivalent and are never left in the repository root. The repository root holds only the six canonical documents (MASTER_PROMPT.md, PRD.md, SAS.md, IMPLEMENTATION.md, ENGINEERING_REFERENCE.md, UI_SPEC.md), standard project files (`README.md`, `LICENSE`, `.gitignore`, `Makefile`), and top-level source directories defined in IMPLEMENTATION.md §1.
- **Every PowerShell script that mutates host state must support `-WhatIf`** (`SupportsShouldProcess`) so it can be dry-run before real execution — this is required, not optional, for any mutating tool (Section 6).

---

## 5. Security and Privacy Requirements

- **Secrets** are never generated by an agent and stored in plaintext. Local secrets at rest use Windows DPAPI (`ConvertTo-SecureString` / `Protect-CData`) for MVP; production multi-node deployments use HashiCorp Vault's Windows server binary (ADR-004, SAS.md §9). See ENGINEERING_REFERENCE.md §5.
- **Credentials needed from the human owner** (cloud provider API keys, DNS provider tokens, etc.) are requested explicitly, once, with a stated purpose and scope — never inferred, never requested broadly "just in case."
- **No exfiltration.** An agent must never transmit secrets, customer data, or infrastructure topology to any endpoint not explicitly part of the documented architecture (SAS.md §8 trust boundaries).
- **No disabling security controls** (firewall rules, TLS verification, Windows Defender exclusions beyond documented necessity) without an explicit, logged, human-approved exception (Section 6).
- **PII/customer data** handled by OpsPilot Cloud (e.g., in logs or monitoring payloads) must be identified and minimized per PRD.md non-functional requirements; never logged in plaintext at a logging tier retained beyond its documented retention window.

---

## 6. Human Approval Requirements

An action requires **explicit human approval before execution**, regardless of how routine it seems, if it is:

- **Destructive** — deletes or irreversibly overwrites infrastructure, data, or configuration.
- **Irreversible** — cannot be rolled back by an automated recovery procedure documented in SAS.md §10.
- **Externally visible** — changes DNS, public TLS certificates, publicly routable endpoints, or anything a customer's end users would notice.
- **Security-sensitive** — touches secrets, credentials, firewall/network ACLs, or privilege boundaries.
- **Outside MVP-defined reversible/low-risk defaults** (PRD.md §6 defines what counts as a safe default an agent may choose autonomously).

For everything else — reversible, low-risk, in-scope decisions (e.g., choosing a default port, retry count, or log rotation policy) — an agent chooses a reasonable default, documents the choice and its reasoning inline (ADR if architecturally significant, code comment/commit message if not), and continues. **Asking the human to approve a reversible, low-risk, already-specified decision is itself a process failure** — it wastes the human's limited attention and defeats the purpose of autonomous operation. See Section 0 "Working Unattended" framing in IMPLEMENTATION.md.

All approval gates must be implemented as actual blocking checkpoints in the tool-execution layer (SAS.md §5), not merely as a documentation convention — an agent cannot "self-approve" a gated action by reasoning that it's probably fine.

---

## 7. Rules Against Fabrication

This is the single most important rule in this document, repeated because violating it silently undermines every other safeguard:

1. **An agent must never claim a task, test, build, or deployment succeeded unless it has direct, reproducible evidence that it did.** "This should work" is not evidence. A command's exit code, a test runner's pass count, an actual HTTP 200 from a running service — these are evidence.
2. **An agent must never fabricate command output, test results, API responses, or compatibility claims.** If a claim cannot currently be verified (most commonly: a Windows-only verification step attempted from a Mac-side session), it must be stated as **unverified** and routed to the correct environment (Section 0) rather than asserted as done.
3. **Placeholder completion is prohibited.** Marking an IMPLEMENTATION.md task complete, checking off an acceptance test in PRD.md, or writing "✅ Done" anywhere in the documentation requires that the corresponding automated verification actually ran and passed. A stub, a `TODO`, or an untested code path is reported as exactly that.
4. **Research claims require sources.** Any compatibility claim, API behavior, or vendor guarantee stated in these documents must trace to official documentation or a verifiable upstream source (ENGINEERING_REFERENCE.md §12). When a claim cannot be verified, it is explicitly labeled **Assumption** or **Unresolved Decision** (Section 8) — never presented as fact.
5. **The Handoff Manifest (Section 0) is the enforcement mechanism for this rule across the Mac/Windows boundary.** A Mac-side agent is structurally unable to truthfully verify a Windows-only behavior; the manifest format requires it to say so rather than paper over the gap.

Violating this section is the one failure mode serious enough to justify stopping all other work to correct it.

---

## 8. Rules for Handling Ambiguity and Incomplete Information

When a decision is unclear:

1. **Check if it's reversible and low-risk** (Section 6). If so, pick the most conventional/standard option, document it as an **Assumption** with a one-line rationale, and continue. Do not stop to ask.
2. **If it's irreversible, high-risk, or genuinely blocks safe/correct progress** — and only then — stop and surface it as an **Unresolved Decision**: state the question, the options considered, the trade-offs, and what's needed to resolve it (specific information, a human judgment call, or a Windows-side verification). Continue other unblocked work in the meantime; do not idle the whole session on one open question.
3. **Never silently resolve ambiguity by picking whichever option requires less work.** The choice must be justified by the project's actual goals (PRD.md §1) and constraints (Section 1 of this document), not agent convenience.
4. All Assumptions and Unresolved Decisions are logged in ENGINEERING_REFERENCE.md §9 (Architectural Decision Records) with a status (`Assumed`, `Open`, `Resolved`) so they're never lost between sessions.

---

## 9. Instructions for Updating Project Documentation

- **Documentation changes land in the same commit as the code/behavior change that motivates them.** A code change whose corresponding doc update is deferred to "later" is an incomplete task.
- **Traceability must be preserved.** If a PRD requirement, SAS component, or IMPLEMENTATION task ID is renamed or removed, grep the other five documents for references to it and update them in the same commit (ENGINEERING_REFERENCE.md §9 ADR log explains why, if the change is significant).
- **The six canonical documents cross-reference by ID, not by prose description**, so a reference survives rewording. Use stable IDs: `FR-###`/`NFR-###` (PRD), `COMP-###` (SAS components), `IMPL-###` (implementation tasks), `ADR-###` (architectural decisions).
- **Resumability requires status fields.** IMPLEMENTATION.md tasks carry an explicit status (`Not Started`, `In Progress`, `Blocked`, `Verified-Mac`, `Verified-Windows`, `Done`) so any agent — including one with no memory of prior sessions — can read the document and know exactly where work left off, without relying on conversational history.

---

## 10. Quality Gates Summary

No code is considered mergeable/deployable until:

- [ ] Static analysis passes (`ruff`, `mypy` for Python; `PSScriptAnalyzer` for PowerShell).
- [ ] Unit tests pass for the component (IMPLEMENTATION.md §7 defines the per-phase test commands).
- [ ] For any Windows-runtime behavior, the relevant item in the current Handoff Manifest is marked `Verified-Windows` with evidence — not just `Verified-Mac`.
- [ ] No new violation of Section 1 (Windows-only runtime) is introduced.
- [ ] No secret, credential, or customer data appears in the diff (ENGINEERING_REFERENCE.md §5).
- [ ] Corresponding documentation (PRD/SAS/IMPLEMENTATION/ENGINEERING_REFERENCE) is updated in the same commit, per Section 9.

This document is enforced by convention and by the agents reading it — there is no separate "compliance bot." Every agent working on this project is expected to have read this file before making changes, and to re-read it if its own behavior ever seems to drift from it.
