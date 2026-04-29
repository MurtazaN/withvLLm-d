#!/bin/bash
# =============================================================================
# SOC-Claw — NemoClaw onboarding.
#
# Composable scripts (Option B):
#   scripts/install-host.sh   — host bootstrap (uv, venv, vLLM, deps, .env)
#   scripts/setup.sh          — calls install-host.sh, then onboards NemoClaw
#
# This script:
#   1. Runs scripts/install-host.sh (idempotent; may exit asking you to fill .env)
#   2. Verifies Docker / Node 20+ / nemoclaw CLI
#   3. Verifies vLLM is reachable on :8000 (start it before re-running)
#   4. Onboards a NemoClaw sandbox profile and stages the source tree
#   5. Writes the sandbox-side .env with host-loopback URL + benchmark dir
# =============================================================================

set -euo pipefail

# Resolve paths regardless of where the user invokes the script from.
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
SOC_CLAW_DIR="${REPO_ROOT}/soc-claw"

# NemoClaw v0.0.7-17 (the version baked into Brev's NemoClaw launchable as of
# 2026-04-07) doesn't support `nemoclaw onboard --name <X>`; the default
# sandbox is always created as "nemoclaw". Newer NemoClaw (v0.0.30+ via the
# nvidia.com/nemoclaw.sh installer) adds --name. Until the launchable bumps
# its baked CLI, we use the default name to keep paths aligned with what the
# CLI actually produces on disk (~/.nemoclaw/sandboxes/nemoclaw/...).
SANDBOX_NAME="nemoclaw"
SANDBOX_WORKSPACE="$HOME/.nemoclaw/sandboxes/${SANDBOX_NAME}/workspace"

echo "============================================="
echo "  SOC-Claw — NemoClaw onboarding"
echo "============================================="

# --- Docker is required by NemoClaw's OpenShell sandbox ---
# Checked before host bootstrap so we fail fast on a system that can't
# eventually run a sandbox at all (no point installing vLLM etc. first).
if ! command -v docker &> /dev/null; then
    echo "✗ Docker is required for NemoClaw (OpenShell sandbox dependency)."
    echo ""
    if command -v podman &> /dev/null; then
        echo "  Podman is installed but NemoClaw does not support it."
    fi
    echo "  Install Docker:"
    echo "    https://docs.docker.com/engine/install/ubuntu/"
    exit 1
fi

# --- Run the host bootstrap (uv, venv, vLLM, app deps, .env) ---
# install-host.sh exits non-zero (and tells the user to edit .env) on the
# first run if .env doesn't already exist; that propagates here via -e.
bash "${SCRIPT_DIR}/install-host.sh"

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
# Build the sandbox image from Dockerfile.sandbox at the repo root. NemoClaw
# bakes our source + pinned deps into the image at build time, so the sandbox
# starts with everything in place — no runtime git clone or pip install dance.
#
# NEMOCLAW_NON_INTERACTIVE=1 suppresses the credential summary prompt. Other
# prompts (provider type / endpoint / model / policy preset) may still fire on
# first run; expected answers are listed below for paste-in convenience.
#
# NEMOCLAW_ACCEPT_THIRD_PARTY_SOFTWARE=1 acknowledges the third-party software
# notice. The notice fires unconditionally on first onboard and blocks under
# non-interactive mode (no TTY to type 'yes'); the env var is the documented
# scripted equivalent of `--yes-i-accept-third-party-software`.
DOCKERFILE_PATH="${REPO_ROOT}/Dockerfile.sandbox"
if [ ! -f "${DOCKERFILE_PATH}" ]; then
    echo "✗ Dockerfile.sandbox not found at ${DOCKERFILE_PATH}." >&2
    exit 1
fi

# Discover the host VM's primary LAN IP so the gateway container can reach
# the host's vLLM. `localhost` is wrong here — it would resolve to the
# gateway's own loopback, not the host VM. host.docker.internal works on
# some Docker setups but not all; the LAN IP is the safest cross-platform
# choice. Empty fallback prints a placeholder so the operator is aware.
HOST_IP="$(hostname -I 2>/dev/null | awk '{print $1}')"
HOST_IP="${HOST_IP:-<host-vm-ip>}"

cat <<EOF

  ----------------------------------------------------------------
  NemoClaw onboarding may prompt you. The answers are:

    Inference option:   3  (Other OpenAI-compatible endpoint)
    Endpoint URL:       http://${HOST_IP}:8000/v1
                        (or http://host.docker.internal:8000/v1 if the
                        gateway can resolve it — try the IP first)
    Model:              nvidia/Nemotron-Mini-4B-Instruct
    API key:            dummy   (vLLM does not check it)
    Policy preset:      balanced

  Why the host IP and not 'localhost': during onboarding NemoClaw stores
  this URL in the gateway container, which uses it to forward inference
  traffic. From inside the gateway, 'localhost' is the gateway itself —
  not the host VM where vLLM runs. Use the host VM's LAN IP instead.
  ----------------------------------------------------------------

EOF
echo "[4/4] Onboarding NemoClaw sandbox '${SANDBOX_NAME}' from Dockerfile.sandbox..."
# Note: --name is intentionally omitted (see SANDBOX_NAME comment above).
# The CLI defaults to "nemoclaw" and that's what we expect on disk.
# NEMOCLAW_NON_INTERACTIVE=1 \
NEMOCLAW_ACCEPT_THIRD_PARTY_SOFTWARE=1 \
COMPATIBLE_API_KEY=dummy \
    nemoclaw onboard --from "${DOCKERFILE_PATH}"

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
echo "    python3 ui/server.py             # FastAPI dashboard on :7860"
echo "    python3 pipeline.py              # single-alert run"
echo "    python3 benchmark/harness.py     # full 30-alert benchmark"
echo "============================================="
