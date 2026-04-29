#!/usr/bin/env bash
# =============================================================================
# SOC-Claw — one-command bootstrap.
#
#   1. Bootstraps the host (uv, Python 3.11, venv, vLLM, .env).
#   2. Builds the soc-claw container image.
#   3. Brings up docker-compose (app on :7860).
#
# vLLM runs on the host, not in a container. Start it in a separate
# terminal once this script completes:
#
#   scripts/run-host-vllm.sh
# =============================================================================

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"

if ! command -v docker &>/dev/null; then
    echo "✗ docker not found on PATH. Install Docker Engine / Desktop and re-run." >&2
    exit 1
fi
if ! docker compose version &>/dev/null; then
    echo "✗ docker compose v2 plugin not found. Install it and re-run." >&2
    exit 1
fi

echo "[1/3] Host bootstrap (idempotent)..."
bash "${SCRIPT_DIR}/install-host.sh"

echo "[2/3] Building soc-claw image..."
docker compose -f "${REPO_ROOT}/docker-compose.yml" build

echo "[3/3] Starting app..."
docker compose -f "${REPO_ROOT}/docker-compose.yml" up -d

cat <<EOF

✅ App: http://localhost:7860
   Logs:  docker compose logs -f app
   Stop:  docker compose down

ℹ️  vLLM runs on the host. Start it in another terminal:
     scripts/run-host-vllm.sh
EOF
