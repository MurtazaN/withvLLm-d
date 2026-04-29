# SOC-Claw Setup

End-to-end setup for running the three-agent pipeline. vLLM runs on the host (GPU-bound); the SOC-Claw app + benchmark run in a Docker container. Two terminals.

## 1. Prerequisites

- **Linux host with NVIDIA GPU + driver** — required to run vLLM. Tested on Ubuntu 22.04, A6000 / L4. Driver ≥ 570.x for the pinned vLLM 0.10.2 path; ≥ 580 to use latest vLLM. CPU-only is not practical for interactive latency.
- **Docker Engine + `docker compose` v2 plugin** — verified with `docker compose version`.
- **`git`, `curl`, `bash`** — standard.

Mac dev (no GPU): you can run the container locally and point it at a remote host's vLLM via SSH port-forward (`ssh -L 8000:localhost:8000 brev-host`). The container reaches it through `host.docker.internal:8000`.

### Network ports

| Port | Service |
|------|---------|
| 8000 | vLLM OpenAI-compatible endpoint (host) |
| 7860 | SOC-Claw UI (container, published to host) |

### Credentials

- `HF_TOKEN` — HuggingFace token, required for vLLM to download model weights.
- `NVIDIA_API_KEY` — optional, only needed if the privacy router routes a prompt to cloud. SOC-Claw's bundled alerts never trigger the cloud route.

## 2. Clone and configure

```bash
git clone https://github.com/MurtazaN/SoC-Claw
cd SoC-Claw

cp .env.example .env
$EDITOR .env   # set HF_TOKEN
```

`.env` is gitignored. Don't commit it.

## 3. Host bootstrap (once per host)

```bash
bash scripts/install-host.sh
```

Idempotent. Installs `uv`, a managed Python 3.11, the project venv at `.venv/`, application deps, and vLLM. On first run, if `.env` is missing it copies from `.env.example` and exits — populate it then re-run.

vLLM pin notes (driver-dependent): see comments in [scripts/install-host.sh](scripts/install-host.sh) lines 52-65. On a host with driver ≥ 580, replace the pinned install with `uv pip install vllm --torch-backend=auto`.

## 4. Start vLLM (terminal 1)

```bash
bash scripts/run-host-vllm.sh
```

Wait for `Uvicorn running on http://0.0.0.0:8000`. Sanity check from another shell:

```bash
curl http://localhost:8000/v1/models
```

The script reads `SOC_CLAW_MODEL` from `.env` (default: `nvidia/Nemotron-Mini-4B-Instruct`). To use a different model, set the env var and re-run.

## 5. Build and start the app (terminal 2)

One command:

```bash
bash scripts/setup.sh
```

This re-runs the host bootstrap (idempotent), builds the `soc-claw:dev` image, and runs `docker compose up -d`.

Equivalent manual flow:

```bash
docker compose build
docker compose up -d
docker compose logs -f app
```

Open **http://localhost:7860** (or `http://<host-ip>:7860` if running remotely).

The app reaches vLLM via `host.docker.internal:8000`. On Linux this is mapped to the host gateway by `extra_hosts:` in [docker-compose.yml](docker-compose.yml); on Mac/Windows Docker Desktop resolves it natively.

## 6. Run the benchmark

```bash
# All 30 alerts
docker compose --profile benchmark run --rm benchmark 30

# Subset
docker compose --profile benchmark run --rm benchmark 5
```

Results are written to `soc-claw/benchmark/results/run_<timestamp>.csv` on the host (mounted into the container at `/app/benchmark/results`).

## 7. Stop and clean up

```bash
docker compose down            # stop the app
docker compose down --rmi all  # also delete the image
# Stop vLLM in terminal 1 with Ctrl+C
```

## 8. Troubleshooting

| Problem | Fix |
|---------|-----|
| `Connection refused` to vLLM from the container | Confirm vLLM is up: `curl http://localhost:8000/v1/models` from the host. Confirm `extra_hosts:` is in `docker-compose.yml` (it is by default). |
| `host.docker.internal` not resolving on Linux | The compose file maps it via `host-gateway`. If your Docker is older than 20.10, upgrade — or set `SOC_CLAW_LOCAL_VLLM_URL=http://172.17.0.1:8000/v1` in `.env`. |
| `CUDA out of memory` | Smaller model, or pass `--gpu-memory-utilization 0.85 --max-model-len 2048` to `vllm serve`. |
| `libcudart.so.13: not found` | vLLM 0.11+ needs CUDA 13. Either upgrade the driver to ≥ 580 or pin `vllm==0.10.2 --torch-backend=cu126` (already done by `install-host.sh`). |
| Port 7860 already in use | `kill $(lsof -t -i:7860)` then `docker compose up -d`. |
| `docker compose: command not found` | Install the Compose v2 plugin (separate from legacy `docker-compose`). |
| 401 from cloud route | Set `NVIDIA_API_KEY` in `.env` and restart the container. |

## 9. Quick verification sequence

```bash
# Terminal 1: host vLLM
bash scripts/run-host-vllm.sh

# Terminal 2: build + run + smoke
bash scripts/setup.sh
curl -fsS http://localhost:7860/        # should be 200
docker compose --profile benchmark run --rm benchmark 3
ls soc-claw/benchmark/results/run_*.csv  # CSV present on host
```

## 10. Configuration reference

All runtime config is env-driven (see [.env.example](.env.example)):

- `HF_TOKEN` — used only by `vllm serve` at startup
- `SOC_CLAW_LOCAL_VLLM_URL` — default `http://localhost:8000/v1`; overridden by compose to `http://host.docker.internal:8000/v1`
- `SOC_CLAW_CLOUD_URL` — default `https://integrate.api.nvidia.com/v1`
- `NVIDIA_API_KEY` — required only for the cloud route
- `SOC_CLAW_MODEL` — model name passed to both vLLM and the OpenAI client
- `BENCHMARK_OUTPUT_DIR` — leave blank for host dev (`soc-claw/benchmark/results/`); compose overrides to `/app/benchmark/results`

For the production target (llm-d / k8s), the same image ships unchanged; secrets become a k8s `Secret` and config a `ConfigMap` mounted as env.
