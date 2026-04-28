# Running SOC-Claw inside a NemoClaw sandbox

Runs SOC-Claw under NemoClaw so the network egress whitelist and filesystem policy in [soc-claw/blueprint.yaml](soc-claw/blueprint.yaml) are enforced at the OS level.

For the host-only flow (no sandbox) see [SETUP.md](SETUP.md). For project background see [README.md](README.md).

## 1. Prerequisites

| Requirement | Notes |
|-------------|-------|
| Linux (Ubuntu 22.04+) | NemoClaw's OpenShell sandbox is Linux-only |
| Docker | Sandbox runtime — Podman is **not** supported |
| Node.js 20+ | NemoClaw CLI runtime |
| Host GPU + CUDA | for vLLM local inference |
| `HF_TOKEN` | gated model downloads in step 3 |

If you're on a Brev Tier-4 launchable, Docker and Node are already installed.

## 2. Configure

```bash
cp .env.example .env
```

Edit `.env` and set `HF_TOKEN`. Set `NVIDIA_API_KEY` only if you plan to use the cloud route. `.env` is read by `python-dotenv` in [soc-claw/utils.py](soc-claw/utils.py) and is gitignored.

## 3. Start vLLM on the host (terminal 1)

```bash
set -a; source .env; set +a
vllm serve "$SOC_CLAW_MODEL" --port 8000
```

`set -a` exports the vars sourced from `.env` so `vllm serve` can read `HF_TOKEN` (model download) and `SOC_CLAW_MODEL` (which model to serve).

Verify it's up:

```bash
curl -s http://localhost:8000/v1/models | head -c 200
```

## 4. Onboard the sandbox (terminal 2)

```bash
cd SoC-Claw
bash scripts/setup.sh
```

This installs the `nemoclaw` CLI if missing, onboards a `soc-claw` sandbox profile against your host vLLM, stages the source tree into `~/.nemoclaw/sandboxes/soc-claw/workspace/`, and writes a sandbox-side `.env` containing `SOC_CLAW_LOCAL_VLLM_URL` (host-loopback alias for in-sandbox use) and `BENCHMARK_OUTPUT_DIR=/sandbox/results` (writable per the blueprint).

After any source change on the host, re-run `bash scripts/setup.sh` to re-stage. It is idempotent.

## 5. Connect and run

```bash
nemoclaw soc-claw connect
```

Inside the sandbox shell:

```bash
pip install -r requirements.txt
python3 ui/server.py            # FastAPI dashboard on :7860
# alternatives:
# python3 pipeline.py            # single-alert end-to-end
# python3 benchmark/harness.py   # 30-alert benchmark → /sandbox/results
```

Env vars come from `/workspace/.env`; `load_dotenv()` in [soc-claw/utils.py](soc-claw/utils.py) picks them up.

## 6. Smoke tests (optional)

Run these inside the sandbox to confirm `blueprint.yaml` is enforced, not just present.

```bash
# Whitelisted host reachable (expect: 200)
curl -s -o /dev/null -w '%{http_code}\n' http://host.openshell.internal:8000/v1/models

# Non-whitelisted host blocked (expect: BLOCKED)
curl -s --max-time 3 https://example.com >/dev/null && echo OPEN || echo BLOCKED

# Readonly path (expect: error)
touch /models/should-fail 2>&1 | head -1

# Writable path (expect: OK)
touch /tmp/ok && rm /tmp/ok && echo OK
```

## Troubleshooting

| Problem | Fix |
|---------|-----|
| `scripts/setup.sh` exits at "Docker is required" | Install Docker. Podman is not supported by NemoClaw. |
| `scripts/setup.sh` exits at "vLLM not reachable" | Step 3 didn't succeed. Check vLLM logs, then re-run. |
| `nemoclaw: command not found` after install | Open a new shell so the install script's PATH update propagates. |
| `Connection refused` to `localhost:8000` from inside the sandbox | The sandbox `.env` is missing or stale. Re-run `bash scripts/setup.sh` to regenerate `/workspace/.env`. |
| `Connection refused` to `host.openshell.internal:8000` | vLLM died on the host, or NemoClaw's host-loopback alias isn't set. Check `nemoclaw status` and re-onboard. |
| Cloud route 401 | `NVIDIA_API_KEY` was missing or expired when `scripts/setup.sh` ran. Update host `.env`, re-onboard. |
| Sandbox can't see new code | Re-run `bash scripts/setup.sh` from the host. |
| Port 7860 already in use | `kill $(lsof -t -i:7860)`, or change the port in [soc-claw/ui/server.py](soc-claw/ui/server.py). |
