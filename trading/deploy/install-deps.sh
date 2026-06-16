#!/usr/bin/env bash
# Install AlphaTrade Python deps into the droplet venv used by systemd services.
#
# Streamlit (dashboard) and FastAPI (alphatrade-api) share ONE venv:
#   /root/trading-bot-clean/venv
#
# Usage (on droplet):
#   /root/trading-bot-clean/trading/deploy/install-deps.sh
#
# After git pull, run this before restarting alphatrade-api / streamlit.
set -euo pipefail

REPO_ROOT="${REPO_ROOT:-/root/trading-bot-clean}"
VENV="${VENV:-${REPO_ROOT}/venv}"
TRADING="${TRADING:-${REPO_ROOT}/trading}"
PIP="${VENV}/bin/pip"
PYTHON="${VENV}/bin/python"

if [[ ! -x "${PIP}" ]]; then
  echo "ERROR: venv pip not found at ${PIP}" >&2
  echo "Create the venv first: python3 -m venv ${VENV}" >&2
  exit 1
fi

echo "[1/4] Upgrading pip in ${VENV}..."
"${PIP}" install --upgrade pip

echo "[2/4] Installing trading/requirements.txt (includes requirements-api.txt)..."
"${PIP}" install -r "${TRADING}/requirements.txt"

echo "[3/4] Validating API imports in ${VENV}..."
"${PYTHON}" -c "import fastapi; print('fastapi', fastapi.__version__)"
"${PYTHON}" -c "import uvicorn; print('uvicorn', uvicorn.__version__)"

echo "[4/4] Compiling api_server.py..."
cd "${TRADING}"
"${PYTHON}" -m py_compile api_server.py

echo "OK — deps installed into ${VENV} (same python systemd uses)."
