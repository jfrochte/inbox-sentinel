# run_gui.ps1 -- Start the Inbox Sentinel GUI (FastAPI + Vue 3) on Windows
$ErrorActionPreference = "Stop"
$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $ScriptDir

# --- Activate venv ---
if (Test-Path ".venv\Scripts\Activate.ps1") {
    & ".venv\Scripts\Activate.ps1"
} elseif (Test-Path "venv\Scripts\Activate.ps1") {
    & "venv\Scripts\Activate.ps1"
} else {
    Write-Host "No virtual environment found. Run bootstrap.ps1 first."
    exit 1
}

# --- Check Ollama reachability ---
$OllamaUrl = if ($env:OLLAMA_URL) { $env:OLLAMA_URL } else { "http://localhost:11434" }
try {
    $null = Invoke-WebRequest -Uri "$OllamaUrl/api/tags" -TimeoutSec 3 -UseBasicParsing
    Write-Host "Ollama reachable at $OllamaUrl"
} catch {
    Write-Host "WARNING: Ollama not reachable at $OllamaUrl (LLM features will fail)"
}

# --- Build frontend if dist/ missing ---
$FrontendDir = Join-Path $ScriptDir "gui\frontend"
if (-not (Test-Path (Join-Path $FrontendDir "dist"))) {
    Write-Host "Building frontend..."
    if (Get-Command npm -ErrorAction SilentlyContinue) {
        Push-Location $FrontendDir
        npm install
        npm run build
        Pop-Location
    } else {
        Write-Host "WARNING: npm not found. Frontend not built -- API-only mode."
    }
}

# --- Start server ---
$Port = if ($env:PORT) { $env:PORT } else { "8741" }
Write-Host "Starting Inbox Sentinel GUI on http://127.0.0.1:$Port"

# Open browser in background after a short delay
Start-Job -ScriptBlock {
    Start-Sleep -Seconds 2
    Start-Process $using:("http://127.0.0.1:$Port")
} | Out-Null

python -m uvicorn gui.server:app --host 127.0.0.1 --port $Port
