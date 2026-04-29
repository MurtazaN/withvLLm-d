# Running SOC-Claw inside a NemoClaw sandbox

Standalone runbook for deploying SOC-Claw under NemoClaw, where the network egress whitelist and filesystem policy in [soc-claw/blueprint.yaml](soc-claw/blueprint.yaml) are enforced at the OS level.

For the host-only flow (no sandbox) see [SETUP.md](SETUP.md). For project background see [README.md](README.md).

## 1. Prerequisites

| Requirement | Notes |
|-------------|-------|
| Linux (Ubuntu 22.04+) | NemoClaw's OpenShell sandbox is Linux-only |
| Docker | Sandbox runtime — Podman is **not** supported |
| NVIDIA GPU + CUDA | for vLLM local inference |
| `HF_TOKEN` from HuggingFace | gated model downloads (set in step 3) |

`uv`, Python 3.11, the project venv, vLLM, app deps, Node 20+, and the `nemoclaw` CLI are all installed automatically by the scripts in step 4 / 6. `git` is required to clone the repo in step 2.

## 2. Clone the repo

```bash
git clone <your-fork-url> SoC-Claw
cd SoC-Claw
sudo mkdir -p /models    # NemoClaw mounts /models readonly; empty dir is fine
```

## 3. Configure `.env`

If `.env` doesn't exist yet, step 4 will create one from `.env.example` on first run and exit, asking you to populate it. You can also copy it ahead of time:

```bash
cp .env.example .env
```

Edit `.env` and set:
- `HF_TOKEN` — required, your HuggingFace access token
- `NVIDIA_API_KEY` — only required if any prompt routes to the cloud path

`.env` is gitignored. It is read by `python-dotenv` in [soc-claw/utils.py](soc-claw/utils.py).

## 4. Bootstrap the host

```bash
bash scripts/setup.sh
```

[scripts/setup.sh](scripts/setup.sh) calls [scripts/install-host.sh](scripts/install-host.sh) first — this installs `uv`, a managed Python 3.11, the project venv at `.venv/`, application dependencies, and vLLM (CUDA-aware via `uv pip install vllm --torch-backend=auto`).

If `.env` was just created from the template, the bootstrap exits with a message telling you to populate it. Edit `.env` (step 3), then re-run `bash scripts/setup.sh`.

## 5. Start vLLM (terminal 1)

```bash
set -a; source .env; set +a
source .venv/bin/activate
vllm serve "$SOC_CLAW_MODEL" --port 8000
```

`set -a` exports each var in `.env` so `vllm serve` can read `HF_TOKEN` (used by HuggingFace at startup) and `SOC_CLAW_MODEL`.

Verify it's up before continuing:

```bash
curl -s http://localhost:8000/v1/models | head -c 200
```

## 6. Onboard the sandbox (terminal 2)

```bash
bash scripts/setup.sh
```

`scripts/setup.sh` is idempotent — re-running it now skips the host bootstrap (already done) and proceeds with: Docker check, Node 20+ install, `nemoclaw` CLI install, vLLM reachability check, `nemoclaw onboard`, source-tree staging into `~/.nemoclaw/sandboxes/soc-claw/workspace/`, and generation of a sandbox-side `.env` containing:

- `SOC_CLAW_LOCAL_VLLM_URL=http://host.openshell.internal:8000/v1` — host-loopback alias so the sandboxed app can reach the host's vLLM.
- `BENCHMARK_OUTPUT_DIR=/sandbox/results` — `/workspace` is read-only inside the sandbox; the harness writes CSVs to a writable path declared in `blueprint.yaml`.

After any source change on the host, re-run this command to re-stage.

## 7. Connect and run

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

Env vars come from `/workspace/.env`; `load_dotenv()` in [soc-claw/utils.py](soc-claw/utils.py) picks them up automatically.

If you're on Brev, port 7860 may not be exposed by default — use `brev port-forward <instance> --port 7860:7860` from your laptop to tunnel.

## 8. Smoke tests (optional)

Run inside the sandbox to confirm `blueprint.yaml` is enforced, not just present.

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
| `scripts/install-host.sh` exits with "Created .env from .env.example" | Expected on first run. Edit `.env`, then re-run `bash scripts/setup.sh`. |
| `scripts/setup.sh` exits at "Docker is required" | Install Docker. Podman is not supported by NemoClaw. |
| `scripts/setup.sh` exits at "vLLM not reachable" | Step 5 didn't succeed. Check vLLM logs in terminal 1, then re-run `scripts/setup.sh`. |
| `nemoclaw: command not found` after install | Open a new shell so the install script's PATH update propagates. |
| `Connection refused` to `localhost:8000` from inside the sandbox | The sandbox `.env` is missing or stale. Re-run `bash scripts/setup.sh` from the host to regenerate `/workspace/.env`. |
| `Connection refused` to `host.openshell.internal:8000` | vLLM died on the host, or NemoClaw's host-loopback alias isn't set. Check `nemoclaw status` and re-onboard. |
| Cloud route 401 | `NVIDIA_API_KEY` was missing or expired when `scripts/setup.sh` ran. Update host `.env`, re-onboard. |
| Sandbox can't see new code | Re-run `bash scripts/setup.sh` from the host. |
| Port 7860 already in use | `kill $(lsof -t -i:7860)`, or change the port in [soc-claw/ui/server.py](soc-claw/ui/server.py). |
| Brev: can't reach `http://<vm-ip>:7860` from laptop | `brev port-forward <instance> --port 7860:7860`. |
