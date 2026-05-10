# Configuration reference

All runtime config is env-driven. Copy `.env.example` to `.env` and edit.

## Network ports

| Port | Service |
| ---- | ------- |
| 8000 | vLLM OpenAI-compatible endpoint (host) |
| 7860 | Blue Lantern UI (container, published to host) |

## vLLM / model

| Var | Default | Purpose |
| --- | --- | --- |
| `HF_TOKEN` | — | HuggingFace token; consumed by `vllm serve` at startup. Not read by the blue-lantern runtime. |
| `BLUE_LANTERN_MODEL` | `nvidia/Nemotron-Mini-4B-Instruct` | Model name passed to both vLLM and the OpenAI client. |
| `BLUE_LANTERN_LOCAL_VLLM_URL` | `http://localhost:8000/v1` | Compose overrides to `http://host.docker.internal:8000/v1`. |

## Cloud route

| Var | Default | Purpose |
| --- | --- | --- |
| `VERTEX_API_KEY` | — | Required only when the privacy router routes a prompt to cloud. Bundled alerts never trigger this. |
| `BLUE_LANTERN_CLOUD_URL` | `https://openrouter.ai/api/v1` | Cloud LLM endpoint. |

## Authentication

| Var | Default | Purpose |
| --- | --- | --- |
| `BLUE_LANTERN_SECRET_KEY` | random per process (unstable) | Session-cookie + CSRF signing key. Generate with `python -c "import secrets; print(secrets.token_hex(32))"`. Must be stable across restarts. |
| `BLUE_LANTERN_USERS` | `analyst:analyst` (with startup warning) | `username:bcrypt_hash` pairs, comma-separated. Generate hashes with `python -m blue_lantern.backend.auth <password>`. |
| `BLUE_LANTERN_SESSION_MAX_AGE` | `28800` (8h) | Session lifetime in seconds. |

## Observability

| Var | Default | Purpose |
| --- | --- | --- |
| `OTEL_EXPORTER_OTLP_ENDPOINT` | — | OTLP gRPC endpoint (e.g., `http://localhost:4317`). Blank → no-op tracing. |
| `BLUE_LANTERN_LOG_LEVEL` | `INFO` (server) / `WARNING` (harness) | One of `DEBUG` / `INFO` / `WARNING` / `ERROR`. |
| `BLUE_LANTERN_LOG_FILE` | — | When set, JSON logs append to this path instead of stderr. |

## Network security (FastAPI Guard)

| Var | Default | Purpose |
| --- | --- | --- |
| `BLUE_LANTERN_RATE_LIMIT` | `200` | Per-IP request limit per window. |
| `BLUE_LANTERN_RATE_WINDOW` | `60` | Rate-limit window in seconds. |
| `BLUE_LANTERN_AUTO_BAN_THRESHOLD` | `20` | Consecutive 4xx / rejected requests before temporary ban. |
| `BLUE_LANTERN_AUTO_BAN_DURATION` | `3600` (1h) | Ban duration in seconds. |
| `BLUE_LANTERN_IP_WHITELIST` | `127.0.0.1,10.0.0.0/8,172.16.0.0/12,192.168.0.0/16` | Comma-separated IPs / CIDRs allowed past the firewall. Empty disables whitelist; rate-limit + auto-ban remain active. |
| `BLUE_LANTERN_CSP` | built-in default (`'self'` + inline script/style, `data:` images) | Overrides the Content-Security-Policy response header. |
| `BLUE_LANTERN_REDIS_URL` | — | Optional Redis backend for shared state (e.g., `redis://localhost:6379`). Blank → in-memory store, single-worker safe. Set when scaling to multi-worker uvicorn or multi-pod k8s. |

## Benchmark

| Var | Default | Purpose |
| --- | --- | --- |
| `BENCHMARK_OUTPUT_DIR` | `src/blue_lantern/benchmark/results/` (host) | Compose overrides to `/app/src/blue_lantern/benchmark/results`. |
| `BLUE_LANTERN_CONCURRENCY` | `5` | Alerts processed in parallel by the harness and `/api/run-all`. |

## Production target

For llm-d / k8s: same image ships unchanged. Secrets become a k8s `Secret`; config a `ConfigMap` mounted as env.
