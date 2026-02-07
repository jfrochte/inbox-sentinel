#!/bin/sh
set -eu

# =========================================
# bootstrap.sh (POSIX sh)
# - erstellt .venv
# - installiert Dependencies aus requirements.txt
# - legt optional .env (ohne Secrets) an
# =========================================

VENV_DIR=".venv"
REQ_FILE="requirements.txt"
ENV_FILE=".env"

echo "==> Checking Python..."
if command -v python3 >/dev/null 2>&1; then
  PY=python3
elif command -v python >/dev/null 2>&1; then
  PY=python
else
  echo "ERROR: Python not found. Please install Python 3."
  exit 1
fi

echo "==> Using: $PY"

echo "==> Creating venv in ${VENV_DIR} (if missing)..."
if [ ! -d "${VENV_DIR}" ]; then
  "$PY" -m venv "${VENV_DIR}"
fi

echo "==> Activating venv..."
. "${VENV_DIR}/bin/activate"

echo "==> Upgrading pip..."
python -m pip install --upgrade pip wheel

if [ ! -f "${REQ_FILE}" ]; then
  echo "ERROR: ${REQ_FILE} not found. Put it next to this script."
  exit 1
fi

echo "==> Installing requirements from ${REQ_FILE}..."
pip install -r "${REQ_FILE}"

if [ ! -f "${ENV_FILE}" ]; then
  echo "==> Creating ${ENV_FILE} (no secrets)..."
  cat > "${ENV_FILE}" <<'EOF'
# Debug: Dateien nach Versand behalten (1/true/yes/on)
EMAIL_REPORT_DEBUG=0

# Logging: INFO oder DEBUG
EMAIL_REPORT_LOGLEVEL=INFO

# Ollama URL (lokal)
OLLAMA_URL=http://localhost:11434/api/generate
EOF

  chmod 600 "${ENV_FILE}" 2>/dev/null || true
else
  echo "==> ${ENV_FILE} already exists, leaving it unchanged."
fi

echo ""
echo "==> Done."
echo "Next steps:"
echo "1) Activate venv:"
echo "   . ${VENV_DIR}/bin/activate"
echo ""
echo "2) Optional: load env vars:"
echo "   set -a; . ${ENV_FILE}; set +a"
echo ""
echo "3) Run:"
echo "   python your_script.py"
