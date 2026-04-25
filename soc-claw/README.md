# SOC-Claw: Multi-Agent Incident Response Coordinator

Three-agent incident response system that automates security alert triage, verification, and response planning. Runs inside a NemoClaw sandbox with privacy-routed inference via vLLM + Nemotron.

## Architecture

```
Raw Alert → Triage Agent (tools) → Verifier Agent (QA) → Response Agent (plan)
                                         ↓                       ↓
                                   Confirm/Adjust/Flag    Analyst approves steps
                                                                  ↓
                                                         Actions execute via UI
```

**Agent 1 — Triage:** Enriches alerts via IP reputation, MITRE ATT&CK lookup, and asset CMDB. The only agent with tools.

**Agent 2 — Verifier:** QA check on triage decisions. Confirms, adjusts severity, or flags for human review. No tools.

**Agent 3 — Response:** Produces prioritized response plans. Analyst approves each step before execution. No tools.

## Setup

```bash
# Install dependencies
pip install -r requirements.txt

# Start vLLM with Nemotron (requires GPU)
vllm serve nvidia/nemotron-3-super-120b-a12b --port 8000

# Launch the analyst UI
python ui/app.py
# Opens at http://localhost:7860

# Run the 30-alert benchmark
python benchmark/harness.py
```

## Project Structure

```
soc-claw/
├── agents/            # Three agents (triage, verifier, response)
├── tools/             # Triage tools + response action tools
├── data/              # 30 synthetic alerts + threat intel + assets + MITRE
├── benchmark/         # Harness + results CSVs
├── ui/                # Gradio analyst interface
├── config/            # NemoClaw sandbox + privacy routing
├── pipeline.py        # Orchestrator: Triage → Verifier → Response
├── utils.py           # Shared: JSON extraction, privacy router, LLM client
└── requirements.txt
```

## Key Features

- **Self-correcting pipeline**: Verifier catches triage errors, measurably improving accuracy
- **Human-in-the-loop**: Response Agent recommends; analyst approves before execution
- **Privacy routing**: Sensitive SOC data stays on local inference; generic queries go to cloud
- **Analyst steering**: Inject context (e.g., "this is a red team exercise") — all agents re-evaluate
- **Benchmark**: 30-alert harness measuring accuracy before/after verification
