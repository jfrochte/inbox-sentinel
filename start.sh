#!/usr/bin/env bash
# start.sh -- One-click launcher for Inbox Sentinel
# Creates venv + installs deps on first run, then starts the GUI.
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$SCRIPT_DIR"

VENV_DIR=".venv"
REQ_FILE="requirements.txt"
PORT="${PORT:-8741}"
OLLAMA_URL="${OLLAMA_URL:-http://localhost:11434}"

# ── 1. Python ────────────────────────────────────────────────
PY=""
for candidate in python3 python; do
    if command -v "$candidate" &>/dev/null; then
        PY="$candidate"; break
    fi
done
if [ -z "$PY" ]; then
    echo "ERROR: Python 3 not found."
    echo "Please install Python 3.10+ from https://www.python.org/downloads/"
    exit 1
fi

# Verify minimum version 3.10
PY_OK=$("$PY" -c "import sys; print(int(sys.version_info >= (3, 10)))" 2>/dev/null || echo 0)
if [ "$PY_OK" != "1" ]; then
    echo "ERROR: Python 3.10+ required (found: $("$PY" --version 2>&1))"
    exit 1
fi

# ── 2. Virtual environment ───────────────────────────────────
if [ ! -d "$VENV_DIR" ]; then
    echo "First run -- setting up environment..."
    echo "Creating virtual environment..."
    "$PY" -m venv "$VENV_DIR"
fi
# shellcheck disable=SC1091
source "$VENV_DIR/bin/activate"

# ── 3. Dependencies ─────────────────────────────────────────
# Install/update when requirements.txt is newer than the stamp file
STAMP="$VENV_DIR/.deps_installed"
if [ ! -f "$STAMP" ] || [ "$REQ_FILE" -nt "$STAMP" ]; then
    echo "Installing Python dependencies..."
    python -m pip install --upgrade pip wheel -q
    pip install -r "$REQ_FILE" -q
    touch "$STAMP"
fi

# ── 4. Frontend build ───────────────────────────────────────
FRONTEND_DIR="$SCRIPT_DIR/gui/frontend"
if [ ! -d "$FRONTEND_DIR/dist" ]; then
    if command -v npm &>/dev/null; then
        echo "Building frontend (first run)..."
        (cd "$FRONTEND_DIR" && npm install --silent && npm run build)
    else
        echo "NOTE: npm not found -- running in API-only mode."
        echo "      Install Node.js 18+ for the full GUI."
    fi
fi

# ── 5. Ollama check ─────────────────────────────────────────
if curl -sf --max-time 3 "$OLLAMA_URL/api/tags" >/dev/null 2>&1; then
    echo "Ollama OK ($OLLAMA_URL)"
else
    echo "NOTE: Ollama not reachable at $OLLAMA_URL"
    echo "      Start Ollama for LLM features to work."
fi

# ── 6. Launch ────────────────────────────────────────────────
echo ""
echo "Starting Inbox Sentinel on http://127.0.0.1:$PORT"
echo "Press Ctrl+C to stop."
echo ""

# Open browser (best-effort, background)
(sleep 1 && xdg-open "http://127.0.0.1:$PORT" 2>/dev/null || open "http://127.0.0.1:$PORT" 2>/dev/null || true) &

python -m uvicorn gui.server:app --host 127.0.0.1 --port "$PORT"
