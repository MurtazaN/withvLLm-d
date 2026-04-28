# SOC-Claw Setup Guide

Complete setup for running the SOC-Claw three-agent incident response system.

## 1. Pre-requirements

### OS and base tooling
- Linux recommended (tested on Ubuntu 22.04)
- Python 3.10+
- `pip`, `git`, `curl`

### GPU and CUDA
- NVIDIA GPU required for local inference via vLLM
- Compatible NVIDIA driver + CUDA runtime
- CPU-only is not practical for interactive latency

### Network ports
| Port | Service |
|------|---------|
| `8000` | vLLM OpenAI-compatible endpoint |
| `7860` | UI server (FastAPI or Gradio) |
| `8001-8003` | Mock EDR/firewall/ITSM (NemoClaw policy) |

### Credentials
- `HF_TOKEN` — Hugging Face token for gated model downloads
- `NVIDIA_API_KEY` — optional, for cloud-route inference

Never commit secrets. Keep `.env` local.

## 2. Install Dependencies

```bash
cd SoC-Claw

python3 -m venv .venv
source .venv/bin/activate
pip install --upgrade pip wheel

# Root dependencies (vLLM)
pip install -r requirements.txt

# SOC-Claw app dependencies
pip install -r soc-claw/requirements.txt
```

### Installed packages

**Root** (`requirements.txt`): `vLLM`

**SOC-Claw** (`soc-claw/requirements.txt`):
- `openai>=1.30`
- `gradio>=4.0`
- `pyyaml`
- `fastapi`
- `uvicorn[standard]`
- `jinja2`

## 3. Models

| Model | Purpose | When to use |
|-------|---------|-------------|
| `nvidia/Nemotron-Mini-4B-Instruct` | Active runtime model | Dev/demo on 1-2 GPUs (24-48GB VRAM) |
| `nvidia/nemotron-3-super-120b-a12b` | PRD target model | Production with 8x80GB GPU server |

The active model is set in `soc-claw/utils.py` → `MODEL_NAME`. Change it if you serve a different model.

### Hardware sizing

| Tier | Model | GPU | RAM | Disk |
|------|-------|-----|-----|------|
| Dev minimum | Nemotron-Mini-4B | 1x 24GB VRAM | 16GB | 30GB |
| Comfortable dev | Nemotron-Mini-4B | 1x 48GB or 2x 24GB | 32GB | 60GB |
| PRD-scale | Nemotron 120B | 8x 80GB (A100/H100) | 128GB+ | 300GB+ |

## 4. Environment Variables

```bash
export HF_TOKEN="<your_hf_token>"
export NVIDIA_API_KEY="<your_nvidia_key>"   # optional, for cloud route
```

Note: The cloud client currently uses a placeholder API key in code. Set a real key and update `get_client()` in `utils.py` for working cloud inference.

## 5. Start vLLM (Terminal 1)

### Option A: Dev model (recommended)

```bash
source .venv/bin/activate
vllm serve nvidia/Nemotron-Mini-4B-Instruct --port 8000
```

### Option B: With 2 GPUs

```bash
vllm serve nvidia/Nemotron-Mini-4B-Instruct --port 8000 --tensor-parallel-size 2
```

### Option C: PRD model (requires multi-GPU server)

```bash
vllm serve nvidia/nemotron-3-super-120b-a12b --port 8000 --tensor-parallel-size 8
```

If using Option C, update `MODEL_NAME` in `soc-claw/utils.py` to match.

**Health check:**

```bash
curl http://localhost:8000/v1/models
```

Wait until you see `Uvicorn running on http://0.0.0.0:8000`.

## 6. Run SOC-Claw (Terminal 2)

```bash
cd SoC-Claw/soc-claw
source ../.venv/bin/activate
```

### Option A: FastAPI + HTML UI (recommended)

```bash
python ui/server.py
```

Open `http://<your-ip>:7860` in your browser.

Features: Red Hat-themed dashboard, alert table, 3-column analysis view, per-step approve/reject, benchmark view.

### Option B: Gradio UI (alternative)

```bash
python ui/app.py
```

Open `http://<your-ip>:7860` or use `share=True` for a public link.

**Only run one UI at a time** — both bind to port 7860.

## 7. Run Benchmark

```bash
cd SoC-Claw/soc-claw
source ../.venv/bin/activate

# All 30 alerts
python benchmark/harness.py

# Subset (e.g., first 5)
python benchmark/harness.py 5
```

Results saved to: `soc-claw/benchmark/results/run_<timestamp>.csv`

## 8. Data Integrity Check

```bash
cd SoC-Claw
python3 -c "
import json
alerts = json.load(open('soc-claw/data/alerts.json'))
assets = json.load(open('soc-claw/data/asset_inventory.json'))
threat = json.load(open('soc-claw/data/threat_intel.json'))
mitre = json.load(open('soc-claw/data/mitre_techniques.json'))
print(f'Alerts: {len(alerts)} (expect 30)')
print(f'Assets: {len(assets)} (expect 15)')
print(f'Threat Intel: {len(threat)} (expect 20)')
print(f'MITRE: {len(mitre)} (expect 20)')
asset_names = {a[\"hostname\"].upper() for a in assets}
missing = [a[\"id\"] for a in alerts if a[\"hostname\"].upper() not in asset_names]
print(f'Missing hostnames: {missing or \"NONE\"}')
"
```

Expected: all counts match, no missing hostnames.

## 9. NemoClaw Integration

Policy files:
- `soc-claw/config/nemoclaw_policy.yaml` — sandbox egress whitelist
- `soc-claw/config/privacy_routes.yaml` — local vs cloud routing rules

The privacy router checks prompts against regex patterns:
- Internal IPs (`10.x.x.x`), hostnames (`DC-`, `SRV-`), payloads → route to **local** inference
- Generic threat intel queries → route to **cloud** inference

Mock response tools (`isolate_host`, `block_ioc`, `create_ticket`, `escalate`) simulate EDR/firewall/ITSM actions. They are not standalone HTTP services.

## 10. Troubleshooting

| Problem | Fix |
|---------|-----|
| `Connection refused` on port 8000 | vLLM is not running. Start it first. |
| `CUDA out of memory` | Use a smaller model or add `--gpu-memory-utilization 0.85 --max-model-len 2048` |
| `401` from cloud route | Set `NVIDIA_API_KEY` and update `get_client()` in `utils.py` |
| Port 7860 already in use | `kill $(lsof -t -i:7860)` then restart |
| Import errors | Run from `soc-claw/` directory as shown above |
| Gradio share link not working | Use FastAPI server (`python ui/server.py`) instead |

## 11. Quick Test Sequence

```bash
# 1. Start vLLM (terminal 1)
vllm serve nvidia/Nemotron-Mini-4B-Instruct --port 8000

# 2. In terminal 2:
cd SoC-Claw/soc-claw
python -m tools.ip_reputation          # test tools
python benchmark/harness.py 3          # test 3 alerts
python ui/server.py                    # launch UI
```
