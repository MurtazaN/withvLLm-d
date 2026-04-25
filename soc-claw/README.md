# SOC-Claw: Multi-Agent Incident Response Coordinator

**Track 5 — Agentic Edge powered by NemoClaw (Deep Tech lane)**

SOC analysts see 4,000 alerts per day. 95% are noise. Missing the 5% that matter costs $4.45M per breach. SOC-Claw solves this with a three-agent pipeline that triages, self-corrects, and plans response actions — with the human always in the loop.

## The Problem

Security Operations Centers are drowning in alerts. Manual triage is slow, error-prone, and leads to analyst burnout. Existing automation either auto-executes (dangerous) or just recommends (no verification). SOC-Claw does both: AI triages and verifies its own decisions, then the human approves before anything fires.

## Architecture

```
Raw Alert → Triage Agent (tools) → Verifier Agent (QA) → Response Agent (plan)
                                         ↓                       ↓
                                   Confirm/Adjust/Flag    Analyst approves steps
                                                                  ↓
                                                         Actions execute via UI
```

**Agent 1 — Triage (HAS tools):** Enriches raw SIEM alerts via IP reputation, MITRE ATT&CK lookup, and asset CMDB. Produces severity score (P1-P4) with confidence and reasoning. The only agent with tools.

**Agent 2 — Verifier (NO tools):** Senior analyst QA check. Receives raw alert + triage verdict. Runs a 4-point verification checklist (evidence alignment, reasoning completeness, logical consistency, bias check). Confirms, adjusts severity, or flags for human review. This is the self-correction loop that measurably improves accuracy.

**Agent 3 — Response (NO tools):** Produces prioritized response plans with specific next steps, reasoning for each action, and urgency levels. Analyst approves each step before execution. Because auto-isolating the wrong server causes an outage worse than the attack.

**Privacy routing:** Sensitive SOC data (internal IPs, hostnames, alert payloads) stays on local Nemotron inference via vLLM. Only generic threat intel queries route to cloud. Same model, different locations — the router controls where data goes, not which model runs.

---

## Judging Criteria Alignment (20 points)

### Innovation & Problem Significance (5 pts)

**The problem:** SOC alert overload is a $4.45M/breach problem. 95% of alerts are noise, but the 5% that slip through cause real damage.

**What's novel:**
- **Self-correcting pipeline** — The Verifier Agent acts as an automated senior analyst QA check. In our 30-alert benchmark, it improved triage accuracy from ~78% to ~88%. Most agentic systems don't verify their own decisions.
- **Human-in-the-loop response** — Unlike systems that auto-execute or just recommend, SOC-Claw does both: AI triages, verifies, and plans; the human approves and executes. This is how enterprise security teams actually want AI to work.
- **Three distinct agent roles** — Triage (enrichment + scoring), Verification (QA + self-correction), Response (action planning). Each agent has a focused role with clear system prompts.
- **Analyst steering** — A human can inject context (e.g., "this is a red team exercise") and all three agents re-evaluate. The pipeline adapts in real time.

### Technical Execution (5 pts)

**Deep Tech implementation:**
- **Three-agent pipeline** with distinct roles: tool-calling triage, reasoning-only verifier, reasoning-only response planner
- **Triage Agent** calls three enrichment tools (IP reputation, MITRE ATT&CK mapper, asset CMDB lookup), correlates results, and scores severity
- **Verifier Agent** runs a 4-point checklist (evidence-severity alignment, reasoning completeness, logical consistency, bias check) — catches under-scoring and over-scoring errors
- **Response Agent** produces severity-appropriate action plans (P1: 5 steps with isolation + blocking; P4: logging only) with per-step reasoning, urgency, and approval requirements
- **Pipeline orchestrator** with merge_verdict logic (confirmed/adjusted/flagged), timing instrumentation, and execute_approved_action mapping
- **Privacy router** with regex pattern matching on prompts — routes sensitive data to local vLLM, generic queries to cloud
- **NemoClaw sandbox** configuration with egress whitelist and audit logging
- **30 synthetic SIEM alerts** with ground truth labels, cross-referenced threat intel (20 IOCs), asset inventory (15 hosts), and MITRE techniques (20 ATT&CK entries)
- **Full benchmark harness** measuring latency (avg/p50/p95), accuracy (before/after verification), FP/FN rates, verification metrics, and response plan metrics

**Tech stack:** vLLM + Nemotron, OpenAI-compatible API, FastAPI backend, custom HTML/JS frontend (Red Hat design), Python async pipeline.

### Inference Efficiency Impact (4 pts)

**Measurable gains from the NemoClaw agentic loop:**

| Metric | Value |
|--------|-------|
| Pipeline stages using tools | 1 of 3 (Triage only) |
| Pure inference stages | 2 of 3 (Verifier + Response) |
| Verifier latency overhead | ~20-30% of triage time |
| Accuracy improvement from Verifier | +10% (worth the latency) |
| Privacy routing benefit | Sensitive data never leaves local inference — reduces cloud costs and compliance risk |

- **2/3 of the pipeline is pure inference** — Verifier and Response agents have zero tool calls, making them fast. Only the Triage Agent needs enrichment tools.
- **Verifier adds minimal latency but measurable accuracy** — The self-correction loop catches 2-3 triage errors per 30 alerts, improving accuracy by ~10%. This is a net positive: slightly more inference time for significantly better outcomes.
- **Privacy router reduces cloud inference costs** — SOC data containing internal IPs, hostnames, and payloads is routed to local vLLM. Only generic threat intel queries go to cloud. This cuts cloud API costs while maintaining data sovereignty.
- **Benchmark harness quantifies all of this** — Per-alert latency (triage/verify/response/total), throughput (alerts/minute), accuracy before vs after verification, all saved to CSV for analysis.

### Presentation & Demo (3 pts)

**Live demo flow (4 minutes):**

1. **The hook (30s):** "4,000 alerts/day. 95% noise. $4.45M per breach. How do you trust AI's judgment? And how do you stop it from auto-isolating a production server?"

2. **Live walkthrough (90s):** Feed ALT-001 (PowerShell on domain controller). Show triage enrichment in real time → P1 verdict → Verifier confirms → Response Agent produces 5-step plan → Analyst clicks "Approve All" → Actions execute.

3. **Verifier catch (60s):** Feed an alert where triage under-scores. Verifier catches it: "Confirmed C2 on critical asset — this is P1." Severity adjusts from P3 to P1. Response plan changes from "create ticket" to "isolate + block + escalate."

4. **Steering demo (45s):** Same alert, analyst types "this server is in our red team lab." All three agents re-run. P1 → P4. Plan shrinks from 5 steps to 1.

5. **Close (15s):** "Three agents. One verifies the other. Humans approve before anything fires."

**Honest limitations:**
- 4B model is less accurate than the 120B target; production deployment needs larger GPU infrastructure
- Synthetic alerts, not real SIEM data
- Response tools are simulations, not integrated with real EDR/firewall APIs

### Open-Source Contribution (3 pts)

**SOC Agent Starter Kit — reusable beyond security:**

1. **Three-agent verification pattern** — The Triage → Verifier → Response architecture is reusable for any domain where AI decisions need a QA step (medical triage, content moderation, loan underwriting). The verification checklist (evidence alignment, reasoning completeness, logical consistency, bias check) is domain-agnostic.

2. **Human-in-the-loop approval framework** — The response plan → per-step approve/reject → execute flow is a reusable pattern for any agentic system where automated actions are dangerous.

3. **Synthetic SOC dataset** — 30 alerts with ground truth labels, threat intel, asset inventory, and MITRE mappings. Useful for testing any SOC automation tool.

4. **Privacy routing implementation** — Regex-based prompt router that splits sensitive vs generic inference to local vs cloud endpoints. Reusable for any privacy-sensitive agentic workflow.

5. **Benchmark harness** — Measures accuracy before/after verification, latency breakdown, verification effectiveness. Adaptable to any multi-agent pipeline.

---

## Project Structure

```
soc-claw/
├── agents/
│   ├── triage_agent.py          # Triage Agent — calls tools, scores severity (HAS tools)
│   ├── verifier_agent.py        # Verifier Agent — QA check (NO tools)
│   └── response_agent.py        # Response Agent — action plans (NO tools)
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
│   ├── nemoclaw_policy.yaml     # NemoClaw sandbox egress whitelist
│   └── privacy_routes.yaml      # Privacy routing rules
├── pipeline.py                  # Orchestrator: Triage → Verifier → Response
├── utils.py                     # Shared: JSON extraction, privacy router, LLM client
├── requirements.txt
├── README.md
└── SETUP.md                     # Full setup guide
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

## Quick Start

```bash
# 1. Clone and install
git clone https://github.com/MurtazaN/withvLLm-d
cd withvLLm-d/soc-claw
pip install -r requirements.txt

# 2. Start vLLM (terminal 1)
vllm serve nvidia/Nemotron-Mini-4B-Instruct --port 8000

# 3. Run the UI (terminal 2)
python ui/server.py
# Open http://localhost:7860

# 4. Or run the benchmark
python benchmark/harness.py
```

See [SETUP.md](SETUP.md) for full setup guide including GPU requirements, model options, and troubleshooting.
