#!/bin/bash
# =============================================================================
# SOC-Claw — host bootstrap.
#
# Installs uv, a managed Python 3.11, a project venv, application deps,
# and vLLM. Provisions a .env from .env.example if one is not present
# (values are NOT auto-populated — the operator fills them in by hand).
#
# Idempotent: safe to re-run. Used by:
#   - SETUP.md       (host-only flow, run directly)
#   - scripts/setup.sh (NemoClaw onboarding, calls this first)
# =============================================================================

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
VENV_DIR="${REPO_ROOT}/.venv"

echo "============================================="
echo "  SOC-Claw — host bootstrap"
echo "============================================="

# --- 1. uv (one tool to manage Python, the venv, and pip installs) ---
if ! command -v uv &>/dev/null; then
    echo "[1/5] Installing uv..."
    curl -LsSf https://astral.sh/uv/install.sh | sh
    # The installer writes to ~/.local/bin; make it visible to this script.
    export PATH="$HOME/.local/bin:$PATH"
else
    echo "[1/5] uv detected: $(uv --version)"
fi

# --- 2. Managed Python 3.11 (uv downloads it if missing) ---
echo "[2/5] Ensuring Python 3.11..."
uv python install 3.11

# --- 3. Project venv (recreated only if missing) ---
if [ ! -d "${VENV_DIR}" ]; then
    echo "[3/5] Creating venv at ${VENV_DIR}..."
    uv venv "${VENV_DIR}" --python 3.11
else
    echo "[3/5] venv already present at ${VENV_DIR}."
fi
# shellcheck disable=SC1091
source "${VENV_DIR}/bin/activate"

# --- 4. Application deps + vLLM ---
echo "[4/5] Installing application dependencies..."
uv pip install -r "${REPO_ROOT}/requirements.txt"

if ! python -c "import vllm" &>/dev/null; then
    echo "       Installing vLLM 0.10.2 (cu126) + compatible transformers..."
    # Why these pins (verified working on Brev Instance A6000 48 GiB "VM Mode w/ Jupyter", driver 570.x):
    #   - vllm: latest (0.11+) ships only CUDA-13 wheels, but the driver caps
    #     at CUDA 12.8 → must pin to last cu126-compatible line.
    #   - transformers: 5.x renamed PreTrainedTokenizerBase → TokenizersBackend
    #     and removed all_special_tokens_extended, breaking vllm 0.10.2's
    #     tokenizer wrapper. 4.51+ has the qwen2_5_omni module vllm imports.
    # On a host with CUDA 13 driver (≥ 580), swap both lines for:
    #   uv pip install vllm --torch-backend=auto
    # which picks the cu13 wheels and current transformers.
    uv pip install "vllm==0.10.2" --torch-backend=cu126
    uv pip install "transformers>=4.51,<5.0"
else
    echo "       vLLM already importable in venv."
fi

# --- 5. .env provisioning (manual population required) ---
echo "[5/5] Checking .env..."
if [ ! -f "${REPO_ROOT}/.env" ]; then
    if [ ! -f "${REPO_ROOT}/.env.example" ]; then
        echo "  ✗ Neither .env nor .env.example found in ${REPO_ROOT}." >&2
        exit 1
    fi
    cp "${REPO_ROOT}/.env.example" "${REPO_ROOT}/.env"
    chmod 600 "${REPO_ROOT}/.env"
    echo ""
    echo "  ⚠ Created ${REPO_ROOT}/.env from .env.example."
    echo "    Open it and set:"
    echo "      HF_TOKEN          (required for vLLM to download model weights)"
    echo "      NVIDIA_API_KEY    (only if using the cloud route)"
    echo "    Then re-run this script."
    exit 1
fi
echo "       .env present at ${REPO_ROOT}/.env"

echo ""
echo "============================================="
echo "  ✅ Host bootstrap complete"
echo ""
echo "  Activate the venv in a new shell:"
echo "    source ${VENV_DIR}/bin/activate"
echo "============================================="
