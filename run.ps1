# =========================================
# run.ps1 (PowerShell)
# - laedt .env
# - aktiviert .venv
# - prueft ob Ollama erreichbar ist (und versucht es zu starten)
# - fuehrt email_report.py aus
# =========================================

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

$Script   = if ($args.Count -gt 0) { $args[0] } else { "email_report.py" }
$VenvDir  = ".venv"
$EnvFile  = ".env"

# In das Verzeichnis des Skripts wechseln
Set-Location (Split-Path -Parent $MyInvocation.MyCommand.Definition)

# --- .env laden ---
function Load-EnvFile {
    param([string]$Path)
    if (-not (Test-Path $Path)) { return }
    foreach ($line in Get-Content $Path -Encoding UTF8) {
        $line = $line.Trim()
        if ($line -eq "" -or $line.StartsWith("#")) { continue }
        if ($line -match "^([^=]+)=(.*)$") {
            $key = $Matches[1].Trim()
            $val = $Matches[2]
            [Environment]::SetEnvironmentVariable($key, $val, "Process")
        }
    }
}

Load-EnvFile $EnvFile

# --- venv aktivieren ---
$VenvPython = Join-Path $VenvDir "Scripts\python.exe"
if (-not (Test-Path $VenvPython)) {
    Write-Host "ERROR: $VenvDir not found. Run .\bootstrap.ps1 first." -ForegroundColor Red
    exit 1
}

# --- Ollama-URL ermitteln ---
$OllamaUrl = [Environment]::GetEnvironmentVariable("OLLAMA_URL", "Process")
if (-not $OllamaUrl) { $OllamaUrl = "http://localhost:11434/api/generate" }

# Base-URL extrahieren (alles vor /api/...)
$BaseUrl = $OllamaUrl -replace "/api/(generate|chat)$", ""
$BaseUrl = $BaseUrl.TrimEnd("/")

# --- Ollama-Erreichbarkeit pruefen ---
function Test-Ollama {
    foreach ($endpoint in @("/api/tags", "/api/version")) {
        try {
            $null = Invoke-RestMethod -Uri "${BaseUrl}${endpoint}" -TimeoutSec 3 -ErrorAction Stop
            return $true
        } catch { }
    }
    return $false
}

function Start-OllamaIfNeeded {
    Write-Host "Ollama not reachable at $BaseUrl. Trying to start it..."

    # Versuch 1: ollama als Kommando starten
    $ollamaExe = Get-Command ollama -ErrorAction SilentlyContinue
    if ($ollamaExe) {
        # Pruefen ob schon ein Prozess laeuft
        $running = Get-Process -Name "ollama" -ErrorAction SilentlyContinue
        if ($running) { return $true }

        Start-Process -FilePath $ollamaExe.Source -ArgumentList "serve" `
                      -WindowStyle Hidden -RedirectStandardOutput ".ollama_serve.log" `
                      -RedirectStandardError ".ollama_serve_err.log"
        return $true
    }

    # Versuch 2: Typischer Installationspfad
    $defaultPath = Join-Path $env:LOCALAPPDATA "Programs\Ollama\ollama.exe"
    if (Test-Path $defaultPath) {
        Start-Process -FilePath $defaultPath -ArgumentList "serve" `
                      -WindowStyle Hidden -RedirectStandardOutput ".ollama_serve.log" `
                      -RedirectStandardError ".ollama_serve_err.log"
        return $true
    }

    Write-Host "ERROR: Could not start Ollama automatically." -ForegroundColor Red
    return $false
}

if (-not (Test-Ollama)) {
    $started = Start-OllamaIfNeeded
    if ($started) {
        $ok = $false
        for ($i = 1; $i -le 25; $i++) {
            if (Test-Ollama) { $ok = $true; break }
            Start-Sleep -Milliseconds 400
        }
        if (-not $ok) {
            Write-Host "ERROR: Ollama still not reachable at $BaseUrl." -ForegroundColor Red
            Write-Host "       Check if Ollama is running and OLLAMA_URL is correct."
            exit 1
        }
    } else {
        exit 1
    }
}

# --- Skript starten ---
if (-not (Test-Path $Script)) {
    Write-Host "ERROR: Script not found: $Script" -ForegroundColor Red
    exit 1
}

Write-Host "==> Ollama OK at $BaseUrl"
Write-Host "==> Running: $VenvPython $Script"
& $VenvPython $Script
exit $LASTEXITCODE
