# =========================================
# bootstrap.ps1 (PowerShell)
# - erstellt .venv
# - installiert Dependencies aus requirements.txt
# - legt optional .env (ohne Secrets) an
# =========================================

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

$VenvDir  = ".venv"
$ReqFile  = "requirements.txt"
$EnvFile  = ".env"

# --- Python finden ---
Write-Host "==> Checking Python..."

$PY = $null
foreach ($candidate in @("python3", "python")) {
    try {
        $ver = & $candidate --version 2>&1
        if ($LASTEXITCODE -eq 0) { $PY = $candidate; break }
    } catch { }
}
if (-not $PY) {
    Write-Host "ERROR: Python not found. Please install Python 3." -ForegroundColor Red
    exit 1
}
Write-Host "==> Using: $PY"

# --- venv anlegen ---
Write-Host "==> Creating venv in $VenvDir (if missing)..."
if (-not (Test-Path $VenvDir)) {
    & $PY -m venv $VenvDir
    if ($LASTEXITCODE -ne 0) {
        Write-Host "ERROR: Could not create venv." -ForegroundColor Red
        exit 1
    }
}

# --- pip/wheel upgraden ---
$VenvPython = Join-Path $VenvDir "Scripts\python.exe"
if (-not (Test-Path $VenvPython)) {
    Write-Host "ERROR: $VenvPython not found. venv seems broken." -ForegroundColor Red
    exit 1
}

Write-Host "==> Upgrading pip..."
& $VenvPython -m pip install --upgrade pip wheel
if ($LASTEXITCODE -ne 0) {
    Write-Host "WARNING: pip upgrade had issues, continuing anyway..." -ForegroundColor Yellow
}

# --- requirements installieren ---
if (-not (Test-Path $ReqFile)) {
    Write-Host "ERROR: $ReqFile not found. Put it next to this script." -ForegroundColor Red
    exit 1
}

Write-Host "==> Installing requirements from $ReqFile..."
& $VenvPython -m pip install -r $ReqFile
if ($LASTEXITCODE -ne 0) {
    Write-Host "ERROR: pip install failed." -ForegroundColor Red
    exit 1
}

# --- .env anlegen (falls nicht vorhanden) ---
if (-not (Test-Path $EnvFile)) {
    Write-Host "==> Creating $EnvFile (no secrets)..."
    @"
# Debug: Dateien nach Versand behalten (1/true/yes/on)
EMAIL_REPORT_DEBUG=0

# Logging: INFO oder DEBUG
EMAIL_REPORT_LOGLEVEL=INFO

# Ollama URL (lokal)
OLLAMA_URL=http://localhost:11434/api/generate
"@ | Set-Content -Path $EnvFile -Encoding UTF8
} else {
    Write-Host "==> $EnvFile already exists, leaving it unchanged."
}

Write-Host ""
Write-Host "==> Done."
Write-Host "Next steps:"
Write-Host "  1) Run the report:"
Write-Host "     .\run.ps1"
Write-Host ""
Write-Host "  2) Or run manually:"
Write-Host "     $VenvPython -m email_report"
