#!/bin/sh
set -eu

SCRIPT="${1:-email_report.py}"
VENV_DIR=".venv"
ENV_FILE=".env"

cd "$(dirname "$0")" || exit 1

# .env robust laden: nur KEY=VALUE, Kommentare ignorieren, CRLF entfernen
load_env_file() {
  f="$1"
  [ -f "$f" ] || return 0

  # Nur Zeilen der Form KEY=... übernehmen (kein export, kein Bash-Kram)
  # CRLF -> LF: tr -d '\r'
  while IFS= read -r line; do
    line=$(printf "%s" "$line" | tr -d '\r')
    case "$line" in
      ""|\#*) continue ;;
      *=*)
        key=$(printf "%s" "$line" | sed 's/=.*$//')
        val=$(printf "%s" "$line" | sed 's/^[^=]*=//')
        # trim spaces um key
        key=$(printf "%s" "$key" | sed 's/^[[:space:]]*//; s/[[:space:]]*$//')
        # val NICHT trimmen, damit z.B. URLs mit :// ok sind
        # exportieren
        eval "$key=\$val"
        export "$key"
        ;;
      *)
        # unbekannte Zeile ignorieren
        ;;
    esac
  done < "$f"
}

load_env_file "$ENV_FILE"

# venv aktivieren
if [ -d "$VENV_DIR" ]; then
  . "$VENV_DIR/bin/activate"
else
  echo "ERROR: $VENV_DIR not found. Run ./bootstrap.sh first."
  exit 1
fi

: "${OLLAMA_URL:=http://localhost:11434/api/generate}"

BASE_URL="$OLLAMA_URL"
BASE_URL="${BASE_URL%/api/generate}"
BASE_URL="${BASE_URL%/api/chat}"
BASE_URL="${BASE_URL%/}"

have_curl() { command -v curl >/dev/null 2>&1; }

ollama_reachable() {
  if have_curl; then
    curl -fsS "${BASE_URL}/api/tags" >/dev/null 2>&1 && return 0
    curl -fsS "${BASE_URL}/api/version" >/dev/null 2>&1 && return 0
    return 1
  fi
  python - <<PY >/dev/null 2>&1
import urllib.request
base = "${BASE_URL}"
for path in ("/api/tags", "/api/version"):
    try:
        with urllib.request.urlopen(base + path, timeout=2) as r:
            if 200 <= r.status < 300:
                raise SystemExit(0)
    except Exception:
        pass
raise SystemExit(1)
PY
}

start_ollama() {
  echo "Ollama not reachable at ${BASE_URL}. Trying to start it..."

  if command -v systemctl >/dev/null 2>&1; then
    systemctl --user start ollama >/dev/null 2>&1 || true
  fi

  if command -v ollama >/dev/null 2>&1; then
    if command -v pgrep >/dev/null 2>&1; then
      pgrep ollama >/dev/null 2>&1 && return 0 || true
    fi
    nohup ollama serve > .ollama_serve.log 2>&1 &
    return 0
  fi

  if command -v open >/dev/null 2>&1 && [ -d "/Applications/Ollama.app" ]; then
    open -a "Ollama" >/dev/null 2>&1 || true
    return 0
  fi

  echo "ERROR: Could not start Ollama automatically."
  return 1
}

if ! ollama_reachable; then
  start_ollama || true
  i=1
  while [ "$i" -le 25 ]; do
    ollama_reachable && break
    sleep 0.4
    i=$((i + 1))
  done
fi

if ! ollama_reachable; then
  echo "ERROR: Ollama still not reachable at ${BASE_URL}."
  echo "       Check if Ollama is running and OLLAMA_URL is correct."
  exit 1
fi

if [ ! -f "$SCRIPT" ]; then
  echo "ERROR: Script not found: $SCRIPT"
  exit 1
fi

echo "==> Ollama OK at ${BASE_URL}"
echo "==> Running: python ${SCRIPT}"
python "$SCRIPT"
