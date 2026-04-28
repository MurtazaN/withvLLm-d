# SOC-Claw Setup Guide — NemoClaw / openclaw

How to run SOC-Claw **inside a NemoClaw sandbox** so the network/filesystem
policy and steering rules in [blueprint.yaml](blueprint.yaml) are actually
enforced, with the analyst driving the pipeline via `openclaw tui`.

For the host-only flow (no sandbox) see [SETUP.md](SETUP.md).

## 1. What this setup gives you

| Capability | Host-only ([SETUP.md](SETUP.md)) | NemoClaw (this guide) |
|------------|----------------------------------|------------------------|
| vLLM-served Nemotron | yes | yes (via `host.openshell.internal:8000`) |
| Three-agent pipeline | yes | yes |
| Network egress whitelist enforced | no — yaml is inert | yes — `blueprint.yaml` `network.allowed_hosts` |
| Filesystem read/write policy | no | yes — `/models` readonly, `/sandbox` + `/tmp` writable |
| Steering: structured output, max-tool-calls, schema enforcement | no | yes |
| `openclaw tui` analyst interface | n/a | yes |

`blueprint.yaml` is inert until NemoClaw mediates the process — that's the entire point of this guide.

## 2. Pre-requirements

Same as [SETUP.md §1](SETUP.md) plus:

| Requirement | Why |
|-------------|-----|
| Linux (Ubuntu 22.04 tested) | NemoClaw's OpenShell sandbox is Linux-only |
| Docker | OpenShell sandbox runtime — Podman is **not** supported |
| Node.js 20+ | NemoClaw CLI runtime |
| `nemoclaw` CLI | Installed by [scripts/setup.sh](scripts/setup.sh) on first run |
| Host vLLM on `:8000` | Sandboxed soc-claw reaches it via `host.openshell.internal` |

If you're on a Brev Tier-4 launchable that ran `vLLM-hackathon-guide/launchable-configs/tier4-nemoclaw/setup.sh`, Docker + Node + NemoClaw bootstrap are already done.

## 3. Configuration via `.env`

Config is read from the environment via `python-dotenv` in [soc-claw/utils.py](soc-claw/utils.py). You should already have a host `.env` populated from `.env.example` — that's all that's needed for the host vLLM, the cloud route, and the model name.

Sandbox vs host: the only variable that differs between contexts is `SOC_CLAW_LOCAL_VLLM_URL`. From inside the sandbox container, `localhost` is the container itself — NemoClaw exposes the host as `host.openshell.internal`. [scripts/setup.sh](scripts/setup.sh) handles this for you: it copies your host `.env` into the staged workspace, strips any host value of `SOC_CLAW_LOCAL_VLLM_URL`, and writes `SOC_CLAW_LOCAL_VLLM_URL=http://host.openshell.internal:8000/v1` as the sandbox override. `load_dotenv()` picks it up automatically — no manual `export` required.

## 4. Start vLLM on the host

Same as [SETUP.md §5](SETUP.md). vLLM stays on the host; NemoClaw routes to it.

```bash
source .venv/bin/activate
vllm serve nvidia/Nemotron-Mini-4B-Instruct --port 8000
```

Verify before continuing:

```bash
curl -s http://localhost:8000/v1/models | head -c 200
```

## 5. Onboard the sandbox

In a second terminal:

```bash
cd SoC-Claw
bash scripts/setup.sh
```

What [scripts/setup.sh](scripts/setup.sh) does:

1. Verifies Docker is installed (fails fast on Podman-only systems).
2. Installs Node 20 if missing.
3. Installs the `nemoclaw` CLI from `https://nvidia.com/nemoclaw.sh` if missing.
4. Confirms vLLM is reachable on `localhost:8000`.
5. Runs `nemoclaw onboard --non-interactive --name soc-claw` with the local-vLLM env vars.
6. Stages the soc-claw project tree into `~/.nemoclaw/sandboxes/soc-claw/workspace/` (excludes `__pycache__`, `.venv`, `.git`, benchmark CSVs).

Successful run ends with a banner listing the four ways to drive the pipeline (TUI, FastAPI UI, single-alert, benchmark).

## 6. Connect and run

```bash
# Drop into the sandbox shell — NemoClaw enforces blueprint.yaml from here on
nemoclaw soc-claw connect

# Inside the sandbox (one-time)
pip install -r requirements.txt
# Note: SOC_CLAW_LOCAL_VLLM_URL is already in /workspace/.env, written by
# scripts/setup.sh during onboarding. load_dotenv() in utils.py picks it up.
```

Then pick one:

| Command | What you get |
|---------|--------------|
| `openclaw tui` | Interactive TUI bound to the soc-claw inference profile |
| `python3 ui/server.py` | Full FastAPI dashboard on `:7860` (open `http://<brev-ip>:7860` from the host) |
| `python3 pipeline.py` | Single-alert end-to-end run (Triage → Verifier → Response) |
| `python3 benchmark/harness.py` | Full 30-alert benchmark with timing + accuracy metrics |

## 7. Verify the policy is actually enforced

Run these inside the sandbox shell. They confirm `blueprint.yaml` is live, not just a yaml on disk.

**Network policy — whitelisted host succeeds:**

```bash
curl -s -o /dev/null -w '%{http_code}\n' http://host.openshell.internal:8000/v1/models
# Expected: 200
```

**Network policy — non-whitelisted host blocked:**

```bash
curl -s --max-time 3 https://example.com >/dev/null && echo OPEN || echo BLOCKED
# Expected: BLOCKED
```

**Filesystem policy — readonly path:**

```bash
touch /models/should-fail 2>&1 | head -1
# Expected: "Read-only file system" or permission error
```

**Filesystem policy — writable path:**

```bash
touch /tmp/ok && rm /tmp/ok && echo OK
# Expected: OK
```

**Steering — schema enforcement:**

Run `python3 pipeline.py` on a single alert and confirm the Triage Agent's response is valid JSON with the expected fields. With `fail_on_schema_violation: true` in `blueprint.yaml`, malformed model output is rejected at the boundary.

## 8. Re-onboarding after code changes

`scripts/setup.sh` copies your working tree into the sandbox workspace. Edits to `agents/`, `tools/`, `pipeline.py`, `utils.py`, etc. on the host **do not** propagate automatically. After any change:

```bash
# From the host, in SoC-Claw/
bash scripts/setup.sh   # re-runs onboarding (idempotent) and re-stages the tree
```

Inside an already-connected sandbox shell, exit and reconnect with `nemoclaw soc-claw connect`.

If you want a tighter dev loop, consider mounting the host directory into the sandbox via `~/.nemoclaw/sandboxes/soc-claw/workspace` as a bind mount — but that bypasses the filesystem policy and is not recommended for the demo.

## 9. Troubleshooting

| Problem | Fix |
|---------|-----|
| `scripts/setup.sh` exits at "Docker is required" | Install Docker. Podman is detected but not supported by NemoClaw. |
| `scripts/setup.sh` exits at "vLLM not reachable" | Start vLLM on the host first (§4). |
| `nemoclaw: command not found` after install | Open a new shell or `source ~/.bashrc` so the install script's PATH update is picked up. |
| `Connection refused` from inside the sandbox to `localhost:8000` | The sandbox `.env` is missing or stale. Re-run `bash scripts/setup.sh` from the host to regenerate `/workspace/.env` with `SOC_CLAW_LOCAL_VLLM_URL=http://host.openshell.internal:8000/v1`. |
| `Connection refused` to `host.openshell.internal:8000` | vLLM died on the host, or NemoClaw's host-loopback alias isn't set. Check `nemoclaw status` and re-onboard. |
| Cloud route 401 | Set `NVIDIA_API_KEY` on the host before `bash scripts/setup.sh` so it propagates into the sandbox env. |
| Sandbox can't see new code | Re-run `bash scripts/setup.sh` (§8). |
| `openclaw tui` hangs | Confirm the local profile resolves: `openshell inference set --provider vllm` then re-launch the TUI. |
| Port 7860 already in use | `kill $(lsof -t -i:7860)` on the host, or pick another port in [ui/server.py](ui/server.py). |

## 10. Quick reference — the whole flow

```bash
# Host
vllm serve nvidia/Nemotron-Mini-4B-Instruct --port 8000   # terminal 1
cd SoC-Claw
bash scripts/setup.sh                                      # terminal 2
nemoclaw soc-claw connect

# Inside sandbox (env vars already in /workspace/.env via setup.sh)
pip install -r requirements.txt
python3 ui/server.py          # or pipeline.py / benchmark/harness.py
```
