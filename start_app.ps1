# ============================================================
# F&O Analyzer — Auto Startup Script
# Starts FastAPI on port 3000 + ngrok tunnel
# Registered in Task Scheduler to run at user login
# ============================================================

$AppDir   = "D:\Sensex-Nifty-FutureOptions"
$NgrokExe = "C:\Users\user\Downloads\ngrok-v3-stable-windows-amd64\ngrok.exe"
$Port     = 3000
$Domain   = "herbs-idiocy-constable.ngrok-free.dev"
$LogDir   = "$AppDir\logs"
$LogFile  = "$LogDir\startup_$(Get-Date -Format 'yyyy-MM-dd').log"

# Ensure log directory exists
if (-not (Test-Path $LogDir)) { New-Item -ItemType Directory -Path $LogDir | Out-Null }

function Log($msg) {
    $ts = Get-Date -Format "yyyy-MM-dd HH:mm:ss"
    "$ts  $msg" | Tee-Object -FilePath $LogFile -Append
}

Log "============================================================"
Log "F&O Analyzer startup initiated"

# ── Kill any stale processes on port 3000 ───────────────────
$stale = netstat -ano | findstr ":$Port" | findstr "LISTENING"
if ($stale) {
    $pid = ($stale -split '\s+')[-1]
    Log "Killing stale process on port $Port (PID $pid)"
    Stop-Process -Id $pid -Force -ErrorAction SilentlyContinue
    Start-Sleep -Seconds 1
}

# ── Kill any existing ngrok ──────────────────────────────────
Get-Process ngrok -ErrorAction SilentlyContinue | Stop-Process -Force -ErrorAction SilentlyContinue
Start-Sleep -Seconds 1

# ── Start FastAPI server ─────────────────────────────────────
Log "Starting FastAPI on port $Port ..."
$uvicorn = Start-Process -FilePath "python" `
    -ArgumentList "-m", "uvicorn", "main:app", "--host", "0.0.0.0", "--port", "$Port" `
    -WorkingDirectory $AppDir `
    -WindowStyle Hidden `
    -PassThru

Log "FastAPI started (PID $($uvicorn.Id))"

# Wait until port is open (max 30s)
$waited = 0
while ($waited -lt 30) {
    $listening = netstat -ano | findstr ":$Port" | findstr "LISTENING"
    if ($listening) { break }
    Start-Sleep -Seconds 1
    $waited++
}

if ($waited -ge 30) {
    Log "ERROR: FastAPI did not start within 30 seconds"
    exit 1
}
Log "FastAPI is listening on port $Port (waited ${waited}s)"

# ── Start ngrok with static domain ──────────────────────────
Log "Starting ngrok -> $Domain ..."
$ngrok = Start-Process -FilePath $NgrokExe `
    -ArgumentList "http", "--domain=$Domain", "$Port" `
    -WindowStyle Hidden `
    -PassThru

Log "ngrok started (PID $($ngrok.Id))"

# Wait for ngrok API
Start-Sleep -Seconds 5
try {
    $tunnels = Invoke-RestMethod "http://localhost:4040/api/tunnels" -ErrorAction Stop
    foreach ($t in $tunnels.tunnels) {
        Log "Tunnel active: $($t.public_url) -> $($t.config.addr)"
    }
} catch {
    Log "WARNING: Could not verify ngrok tunnel (it may still be starting)"
}

Log "Startup complete!"
Log "Local:  http://localhost:$Port"
Log "Public: https://$Domain"
Log "============================================================"
