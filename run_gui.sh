#!/usr/bin/env bash
# run_gui.sh -- Start the Inbox Sentinel GUI (FastAPI + Vue 3)
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$SCRIPT_DIR"

# --- Activate venv ---
if [ -d ".venv" ]; then
    source .venv/bin/activate
elif [ -d "venv" ]; then
    source venv/bin/activate
else
    echo "No virtual environment found. Run bootstrap.sh first."
    exit 1
fi

# --- Check Ollama reachability ---
OLLAMA_URL="${OLLAMA_URL:-http://localhost:11434}"
if curl -sf --max-time 3 "$OLLAMA_URL/api/tags" > /dev/null 2>&1; then
    echo "Ollama reachable at $OLLAMA_URL"
else
    echo "WARNING: Ollama not reachable at $OLLAMA_URL (LLM features will fail)"
fi

# --- Build frontend if dist/ missing ---
FRONTEND_DIR="$SCRIPT_DIR/gui/frontend"
if [ ! -d "$FRONTEND_DIR/dist" ]; then
    echo "Building frontend..."
    if command -v npm &> /dev/null; then
        cd "$FRONTEND_DIR"
        npm install
        npm run build
        cd "$SCRIPT_DIR"
    else
        echo "WARNING: npm not found. Frontend not built -- API-only mode."
    fi
fi

# --- Start server ---
PORT="${PORT:-8741}"
echo "Starting Inbox Sentinel GUI on http://127.0.0.1:$PORT"

# Open browser in background (best-effort)
(sleep 1 && xdg-open "http://127.0.0.1:$PORT" 2>/dev/null || true) &

python -m uvicorn gui.server:app --host 127.0.0.1 --port "$PORT"
