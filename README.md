# SOC-Claw: Multi-Agent Incident Response Coordinator

A three-agent incident response system that automates security alert triage, verification, and response planning. Runs inside a NemoClaw sandbox with privacy-routed inference via vLLM + Nemotron.

## Architecture

```
Raw Alert → Triage Agent (tools) → Verifier Agent (QA) → Response Agent (plan)
                                         ↓                       ↓
                                   Confirm/Adjust/Flag    Analyst approves steps
                                                                  ↓
                                                         Actions execute via UI
```

**Agent 1 — Triage:** Enriches alerts via IP reputation, MITRE ATT&CK lookup, and asset CMDB. Produces a severity score (P1-P4) with confidence and reasoning. The only agent with tools.

**Agent 2 — Verifier:** Senior analyst QA check on triage decisions. Confirms, adjusts severity, or flags for human review. No tools — reasoning only.

**Agent 3 — Response:** Produces prioritized response plans with specific next steps. Analyst approves each step before execution. No tools — recommends only.

**Why the human stays in the loop:** Auto-isolating the wrong server can cause an outage worse than the attack. SOC-Claw keeps the human in control: AI triages, verifies, and plans; the human approves and executes.

## Key Features

- **Self-correcting pipeline** — Verifier catches triage errors, measurably improving accuracy (+10% in benchmarks)
- **Human-in-the-loop** — Response Agent recommends; analyst approves before any containment action fires
- **Privacy routing** — Sensitive SOC data (internal IPs, hostnames, payloads) stays on local Nemotron inference; only generic threat intel queries route to cloud
- **Analyst steering** — Inject context (e.g., "this is a red team exercise") and all three agents re-evaluate
- **Benchmark harness** — 30-alert benchmark measuring accuracy before/after verification, latency, throughput
- **Two UI options** — FastAPI + custom HTML (Red Hat design) or Gradio analyst interface

## Project Structure

```
soc-claw/
├── agents/
│   ├── triage_agent.py          # Triage Agent — calls tools, scores severity
│   ├── verifier_agent.py        # Verifier Agent — QA check, no tools
│   └── response_agent.py        # Response Agent — action plans, no tools
├── tools/
│   ├── ip_reputation.py         # IP threat intel lookup
│   ├── mitre_lookup.py          # MITRE ATT&CK technique mapper
│   ├── asset_lookup.py          # Asset inventory/CMDB lookup
│   └── response_tools.py        # EDR, firewall, ticketing simulations
├── data/
│   ├── alerts.json              # 30 synthetic SIEM alerts with ground truth
│   ├── threat_intel.json        # 20 known-bad IOCs
│   ├── asset_inventory.json     # 15 hosts with criticality tiers
│   └── mitre_techniques.json    # 20 ATT&CK techniques
├── benchmark/
│   ├── harness.py               # Runs all 30 alerts, measures metrics
│   └── results/                 # Output CSVs
├── ui/
│   ├── server.py                # FastAPI backend + API endpoints
│   ├── templates/index.html     # Red Hat-themed HTML interface
│   └── app.py                   # Gradio analyst interface (alternative)
├── config/
│   ├── nemoclaw_policy.yaml     # Sandbox egress whitelist
│   └── privacy_routes.yaml      # Privacy routing rules
├── pipeline.py                  # Orchestrator: Triage → Verifier → Response
├── utils.py                     # Shared: JSON extraction, privacy router, LLM client
└── requirements.txt
```

## Data Layer

| File | Count | Description |
|------|-------|-------------|
| `alerts.json` | 30 | 10 true positives (P1), 10 false positives (P4), 10 ambiguous (P2/P3) |
| `threat_intel.json` | 20 | IPs, domains, file hashes with threat scores and campaign tags |
| `asset_inventory.json` | 15 | 3 critical, 4 high, 5 medium, 3 low criticality hosts |
| `mitre_techniques.json` | 20 | ATT&CK techniques with keyword arrays for matching |

All data is cross-referenced: every alert hostname exists in asset inventory, every malicious IP in true-positive alerts exists in threat intel.

## Tool-Agent Mapping

| Tool | Called by | When |
|------|-----------|------|
| `ip_reputation` | Triage Agent | During enrichment (automatic) |
| `mitre_lookup` | Triage Agent | During enrichment (automatic) |
| `asset_lookup` | Triage Agent | During enrichment (automatic) |
| `isolate_host` | UI layer | After analyst approves the action |
| `block_ioc` | UI layer | After analyst approves the action |
| `create_ticket` | UI layer | After analyst approves the action |
| `escalate` | UI layer | After analyst approves the action |

## Scoring Alignment

| Criterion | Points | How SOC-Claw addresses it |
|-----------|--------|---------------------------|
| Innovation & problem significance | 5 | Three-agent pipeline with self-correction + human-in-the-loop approval |
| Technical execution | 5 | Tool-calling triage, reasoning-only verifier/response, NemoClaw sandbox, privacy routing |
| Inference efficiency impact | 4 | 2/3 of pipeline is pure inference (no tools). Verifier adds minimal latency but measurable accuracy gain |
| Presentation & demo | 3 | Live triage → verification → approve flow. Verifier catch demo. Steering demo |
| Open-source contribution | 3 | Reusable three-agent verification pattern, human-in-the-loop framework, synthetic dataset |
