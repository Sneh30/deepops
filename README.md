# DeepOps — Autonomous SRE Platform

## Explainer video

<video src="https://github.com/user-attachments/assets/ff8e9176-fe82-4576-ba73-72ea3df13d57" controls playsinline preload="metadata"></video>

---

## Explainer video

[▶ Watch the explainer video](explainer.mp4)

---

> An AI-powered autonomous cloud operations platform that behaves like an experienced SRE team.

**Status:** Early prototype — active development.

## Vision

Instead of merely monitoring infrastructure, DeepOps continuously:

- Deploys infrastructure from natural language
- Monitors health across metrics, logs, traces, and events
- Detects anomalies before users notice
- Finds root causes through causal chain analysis
- Repairs services automatically (with human approval for high-risk actions)
- Learns from every incident to improve future responses
- Optimizes cost across cloud resources
- Prevents future failures by recognizing known patterns

The long-term goal: **An operating system for cloud infrastructure.**

## Architecture

```
                         ┌──────────────────────┐
                         │    Planner Agent      │
                         │  (intent detection)   │
                         └──────────┬───────────┘
                                    │
                         ┌──────────▼───────────┐
                         │     Orchestrator      │
                         │  (agent coordinator)  │
                         └──┬──┬──┬──┬──┬──┬──┬─┘
                            │  │  │  │  │  │  │
              ┌─────────────┘  │  │  │  │  │  └──────────────┐
              │                │  │  │  │  └────────────────┐ │
         ┌────▼────┐    ┌─────▼──▼──▼──▼──┐    ┌───────────▼─▼──┐
         │Infra    │    │  Observability  │    │    Incidents   │
         │Provision│    │  Monitor        │    │    Detector    │
         │Scaler   │    │  Pipeline       │    │    Root Cause  │
         └─────────┘    └─────────────────┘    │    Remediator  │
                                               │    Responder   │
         ┌─────────┐    ┌─────────────────┐    └────────────────┘
         │Security │    │  Cost           │
         │Scanner  │    │  Optimizer      │    ┌────────────────┐
         └─────────┘    └─────────────────┘    │  Learning      │
                                               │  Engine        │
         ┌─────────┐    ┌─────────────────┐    │  (Memory)      │
         │Backup   │    │  Updates        │    └────────────────┘
         │Manager  │    │  Patcher        │
         └─────────┘    └─────────────────┘
```

## Current Modules

| Module | Status | Description |
|--------|--------|-------------|
| Core Orchestrator | ✅ Basic | Agent coordination and task dispatch |
| Planner Agent | ✅ Basic | Natural language intent detection |
| Learning Engine | ✅ Basic | Incident memory and pattern recall |
| Infrastructure | ⚡ WIP | Provisioner analyzes app stacks and recommends infra |
| Auto Scaler | ⚡ WIP | Predictive scaling decisions |
| Monitoring | ⚡ WIP | Metrics, health checks, observability |
| Pipeline | ⚡ WIP | Observability data ingestion |
| Anomaly Detector | ⚡ WIP | Signal analysis and alerting |
| Root Cause Analyzer | ✅ Basic | Causal chain construction |
| Remediator | ✅ Basic | Remediation action planning |
| Incident Responder | ⚡ WIP | Full incident lifecycle |
| Security Scanner | ⚡ WIP | Vulnerability, secret, and IAM scanning |
| Cost Optimizer | ⚡ WIP | Waste detection and optimization |
| Backup Manager | ⚡ WIP | Backup and disaster recovery |
| Patcher | ⚡ WIP | Automated update planning |

## Quick Start

```bash
python -m deepops
```

Or install and run:

```bash
pip install -e .
deepops
```

## Usage

Run in interactive mode:

```bash
python -m deepops
```

Then type natural language commands:

```
deepops> Deploy my MERN app
deepops> Why is latency increasing?
deepops> Fix production
deepops> Optimize my AWS bill
deepops> Scale the API
deepops> Scan for vulnerabilities
deepops> status
deepops> help
```

Or pass a command directly:

```bash
python -m deepops "Why is latency increasing?"
```

## Examples

### Root Cause Analysis

```
Input:  Why is latency increasing?
Output: Causal chain:
  1. API latency increased to 280ms p99
  2. Redis query timeout at 200ms
  3. Node.js event loop blocked
  4. High disk IOPS on EBS volume
  5. EBS gp2 volume IOPS exhausted
  6. Root cause: Recent deployment v2.3.1 changed cache TTL
```

### Cost Optimization

```
Input:  Optimize my AWS bill
Output: Identified $2,847/month waste:
  - Idle instances: $86/month
  - Oversized DB: $432/month
  - Unattached volumes: $50/month
  - Overprovisioned K8s nodes: $1,200/month
```

### Incident Response

```
Input:  Fix the memory leak
Output: Plan:
  1. Restart affected container (medium risk)
  2. Increase memory limit 512Mi → 1Gi
  3. Profile heap for root cause
  4. Redeploy with fix
```

## Project Status

This is a **rudimentary prototype** being built from the ground up. Current focus areas:

- [ ] Connect modules into a working pipeline
- [ ] Add persistent memory for incident learning
- [ ] Build the autonomous remediation workflow
- [ ] Add predictive scaling based on real patterns
- [ ] Integrate with actual cloud providers (AWS SDK)
- [ ] Real observability pipeline (OpenTelemetry)
- [ ] Web UI for dashboards and incident timelines
- [ ] Multi-agent coordination and arbitration

## Long-Term Vision

A cloud platform that continuously observes its own state, predicts failures before users notice them, repairs itself safely, remembers what worked, and improves after every incident — reducing the need for manual SRE intervention while keeping humans in control for high-risk operations.

---

*Built with the belief that infrastructure should adapt to applications, not the other way around.*
