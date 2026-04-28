#!/bin/bash
# =============================================================================
# SOC-Claw — NemoClaw onboarding script (Track 5: Agentic Edge)
# Installs NemoClaw if needed, onboards a sandbox named "soc-claw" against
# the local vLLM server, and stages the soc-claw project tree into the
# sandbox workspace so it can be driven via `openclaw tui`.
#
# Assumes the Brev launchable (vLLM-hackathon-guide/launchable-configs/
# tier4-nemoclaw/setup.sh) already ran: Docker installed, Node 20, vLLM
# pip deps, model weights at /models/, and start_vllm_server.sh staged.
# =============================================================================

set -euo pipefail

# Resolve paths regardless of where the user invokes the script from.
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
SOC_CLAW_DIR="${REPO_ROOT}/soc-claw"

SANDBOX_NAME="soc-claw"
SANDBOX_WORKSPACE="$HOME/.nemoclaw/sandboxes/${SANDBOX_NAME}/workspace"

echo "============================================="
echo "  SOC-Claw — NemoClaw onboarding"
echo "============================================="

# --- Docker is required by NemoClaw's OpenShell sandbox ---
if ! command -v docker &> /dev/null; then
    echo "✗ Docker is required for NemoClaw (OpenShell sandbox dependency)."
    echo ""
    if command -v podman &> /dev/null; then
        echo "  Podman is installed but NemoClaw does not support it."
        echo "  See vLLM-hackathon-guide/docs/PODMAN-NOTES.md (NemoClaw section)."
    fi
    echo "  Install Docker:"
    echo "    https://docs.docker.com/engine/install/ubuntu/"
    exit 1
fi

# --- Node 20+ is required by NemoClaw ---
if ! command -v node &> /dev/null; then
    echo "[1/4] Installing Node.js 20..."
    curl -fsSL https://deb.nodesource.com/setup_20.x | sudo -E bash -
    sudo apt-get install -y nodejs
else
    NODE_MAJOR="$(node -v | sed 's/^v//' | cut -d. -f1)"
    if [ "${NODE_MAJOR}" -lt 20 ]; then
        echo "[1/4] Upgrading Node.js to v20 (current: $(node -v))..."
        curl -fsSL https://deb.nodesource.com/setup_20.x | sudo -E bash -
        sudo apt-get install -y nodejs
    else
        echo "[1/4] Node.js $(node -v) detected."
    fi
fi

# --- Install NemoClaw ---
echo "[2/4] Installing NemoClaw..."
if ! command -v nemoclaw &> /dev/null; then
    curl -fsSL https://nvidia.com/nemoclaw.sh | bash
else
    echo "  NemoClaw already installed: $(nemoclaw --version 2>/dev/null || echo 'present')"
fi

# --- Verify local vLLM is reachable ---
echo "[3/4] Checking local vLLM server..."
if ! curl -s --max-time 3 http://localhost:8000/v1/models > /dev/null 2>&1; then
    echo "✗ vLLM not reachable at http://localhost:8000/v1/models."
    echo "  Start it first (in a separate tmux pane):"
    echo "    bash /workspace/start_vllm_server.sh"
    echo "  Or any vLLM launch script that serves an OpenAI-compatible API on :8000."
    exit 1
fi
echo "  vLLM detected at http://localhost:8000/v1"

# --- Onboard NemoClaw with the soc-claw profile ---
echo "[4/4] Onboarding NemoClaw sandbox '${SANDBOX_NAME}'..."
NEMOCLAW_PROVIDER=custom \
NEMOCLAW_ENDPOINT_URL=http://localhost:8000/v1 \
NEMOCLAW_MODEL=nvidia/Nemotron-Mini-4B-Instruct \
COMPATIBLE_API_KEY=dummy \
NEMOCLAW_PREFERRED_API=openai-completions \
nemoclaw onboard --non-interactive --name "${SANDBOX_NAME}"

# --- Stage soc-claw into the sandbox workspace ---
echo "Staging soc-claw project into ${SANDBOX_WORKSPACE}..."
mkdir -p "${SANDBOX_WORKSPACE}"

# Use rsync if available (excludes pycache + benchmark CSVs cleanly);
# fall back to cp -r with a post-prune.
if command -v rsync &> /dev/null; then
    rsync -a \
        --exclude='__pycache__' \
        --exclude='*.pyc' \
        --exclude='benchmark/results/*.csv' \
        --exclude='.venv' \
        --exclude='.git' \
        "${SOC_CLAW_DIR}/agents" \
        "${SOC_CLAW_DIR}/tools" \
        "${SOC_CLAW_DIR}/data" \
        "${SOC_CLAW_DIR}/ui" \
        "${SOC_CLAW_DIR}/benchmark" \
        "${SOC_CLAW_DIR}/config" \
        "${SANDBOX_WORKSPACE}/"
    cp "${SOC_CLAW_DIR}/pipeline.py" \
       "${SOC_CLAW_DIR}/utils.py" \
       "${SOC_CLAW_DIR}/blueprint.yaml" \
       "${REPO_ROOT}/requirements.txt" \
       "${SANDBOX_WORKSPACE}/"
else
    cp -r "${SOC_CLAW_DIR}/agents" \
          "${SOC_CLAW_DIR}/tools" \
          "${SOC_CLAW_DIR}/data" \
          "${SOC_CLAW_DIR}/ui" \
          "${SOC_CLAW_DIR}/benchmark" \
          "${SOC_CLAW_DIR}/config" \
          "${SANDBOX_WORKSPACE}/"
    cp "${SOC_CLAW_DIR}/pipeline.py" \
       "${SOC_CLAW_DIR}/utils.py" \
       "${SOC_CLAW_DIR}/blueprint.yaml" \
       "${REPO_ROOT}/requirements.txt" \
       "${SANDBOX_WORKSPACE}/"
    find "${SANDBOX_WORKSPACE}" -type d -name '__pycache__' -prune -exec rm -rf {} +
    find "${SANDBOX_WORKSPACE}" -type f -name '*.pyc' -delete
    rm -f "${SANDBOX_WORKSPACE}/benchmark/results/"*.csv 2>/dev/null || true
fi

# --- Generate sandbox-side .env ---
# Inherit secrets from the host .env, then override SOC_CLAW_LOCAL_VLLM_URL
# so soc-claw running inside the sandbox reaches the host's vLLM via
# NemoClaw's host-loopback alias (host.openshell.internal). load_dotenv()
# in utils.py picks this up automatically — no manual export required.
echo "Generating sandbox-side .env..."
SANDBOX_ENV="${SANDBOX_WORKSPACE}/.env"
if [ -f "${REPO_ROOT}/.env" ]; then
    grep -vE '^[[:space:]]*(SOC_CLAW_LOCAL_VLLM_URL|BENCHMARK_OUTPUT_DIR)=' \
        "${REPO_ROOT}/.env" > "${SANDBOX_ENV}" || true
else
    : > "${SANDBOX_ENV}"
fi
{
    echo ""
    echo "# --- generated by scripts/setup.sh: sandbox-only overrides ---"
    echo "SOC_CLAW_LOCAL_VLLM_URL=http://host.openshell.internal:8000/v1"
    # /workspace is intentionally readonly inside the sandbox; benchmark
    # output goes to /sandbox/results (a writable path per blueprint.yaml).
    echo "BENCHMARK_OUTPUT_DIR=/sandbox/results"
} >> "${SANDBOX_ENV}"
chmod 600 "${SANDBOX_ENV}"
echo "  → ${SANDBOX_ENV}"

echo ""
echo "============================================="
echo "  ✅ SOC-Claw onboarded into NemoClaw"
echo ""
echo "  Connect to the sandbox:"
echo "    nemoclaw ${SANDBOX_NAME} connect"
echo ""
echo "  Inside the sandbox (one-time):"
echo "    pip install -r requirements.txt"
echo ""
echo "  Drive the pipeline:"
echo "    openclaw tui                     # interactive TUI"
echo "    python3 ui/server.py             # FastAPI dashboard on :7860"
echo "    python3 pipeline.py              # single-alert run"
echo "    python3 benchmark/harness.py     # full 30-alert benchmark"
echo "============================================="
