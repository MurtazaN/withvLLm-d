#!/usr/bin/env bash
# Start vLLM on the host. The container reaches this via host.docker.internal:8000.
# Pre-req: scripts/install-host.sh has been run (creates .venv with vllm installed).

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"

if [ ! -f "${REPO_ROOT}/.venv/bin/activate" ]; then
    echo "✗ ${REPO_ROOT}/.venv not found. Run scripts/install-host.sh first." >&2
    exit 1
fi

# shellcheck disable=SC1091
source "${REPO_ROOT}/.venv/bin/activate"

if [ -f "${REPO_ROOT}/.env" ]; then
    set -a
    # shellcheck disable=SC1091
    source "${REPO_ROOT}/.env"
    set +a
fi

MODEL="${SOC_CLAW_MODEL:-nvidia/Nemotron-Mini-4B-Instruct}"

echo "Starting vLLM: ${MODEL} on 0.0.0.0:8000"
exec vllm serve "${MODEL}" --port 8000 --host 0.0.0.0
