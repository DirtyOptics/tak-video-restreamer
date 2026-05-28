<#
.SYNOPSIS
    Run TAK Video Restreamer locally without Docker, using the same settings as the container.
.DESCRIPTION
    - Downloads mediamtx Windows binary if not present
    - Patches mediaMTX.yml to use local data/ paths instead of /opt/app/...
    - Sets all environment variables from docker-compose.yml
    - Generates a self-signed cert if data/certs is empty
    - Starts MediaMTX in background, Flask app in foreground
    - Cleans up both on Ctrl+C
.NOTES
    Run from the tak-video-restreamer project root.
    Requires: Python venv at .\venv_media\  (run `pip install -r requirements.txt` first if needed)
#>

$ErrorActionPreference = "Stop"

# ─── Paths ────────────────────────────────────────────────────────────────────
$ScriptDir     = Split-Path -Parent $MyInvocation.MyCommand.Path
$VenvPython    = Join-Path $ScriptDir "venv_media\Scripts\python.exe"
$MediamtxExe   = Join-Path $ScriptDir "mediamtx-local\mediamtx.exe"
$FfmpegExe     = Join-Path $ScriptDir "ffmpeg-local\ffmpeg.exe"
$ConfigSrc     = Join-Path $ScriptDir "mediaMTX.yml"
$ConfigLocal   = Join-Path $ScriptDir "mediamtx-local\mediamtx.yml"
$DataDir       = Join-Path $ScriptDir "data"
$StreamsDir    = Join-Path $DataDir "streams"
$LogsDir       = Join-Path $DataDir "logs"
$HlsDir        = Join-Path $DataDir "hls"
$CertsDir      = Join-Path $DataDir "certs"

# ─── Env vars (mirrors docker-compose.yml) ─────────────────────────────────
$env:PORT                = "3000"
$env:MEDIAMTX_API_URL    = "http://localhost:8889"
$env:PYTHONUNBUFFERED    = "1"
$env:STREAMS_DIR         = $StreamsDir
$env:DATA_DIR            = $DataDir
$env:HLS_OUTPUT_DIR      = $HlsDir
$env:ACTIVE_CERTS_DIR    = $CertsDir
$env:ADMIN_USERNAME      = "admin"
$env:ADMIN_PASSWORD      = "changeme"
$env:SECRET_KEY          = "change-me-to-a-random-secret-key"
$env:LOGS_DIR            = $LogsDir
$env:FFMPEG_LOG_DIR      = Join-Path $LogsDir "ffmpeg"
$env:MEDIAMTX_RTSP_URL   = "rtsp://127.0.0.1:8554"

# ─── Sanity checks ────────────────────────────────────────────────────────────
if (-not (Test-Path $VenvPython)) {
    Write-Error "Python venv not found at $VenvPython`nRun: python -m venv venv_media && venv_media\Scripts\pip install -r requirements.txt"
}

if (-not (Test-Path $ConfigSrc)) {
    Write-Error "mediaMTX.yml not found at $ConfigSrc"
}

# Ensure ffmpeg-local dir exists
if (-not (Test-Path (Split-Path $FfmpegExe))) {
    New-Item -ItemType Directory -Path (Split-Path $FfmpegExe) -Force | Out-Null
}

# ─── Download ffmpeg Windows binary if missing ───────────────────────────────
if (-not (Test-Path $FfmpegExe)) {
    $ffmpegInPath = Get-Command ffmpeg -ErrorAction SilentlyContinue
    if ($ffmpegInPath) {
        # ffmpeg found in system PATH — copy it so we can add its directory to PATH reliably
        Write-Host "ffmpeg found in system PATH: $($ffmpegInPath.Source)" -ForegroundColor Green
        $FfmpegExe = $ffmpegInPath.Source
    } else {
        Write-Host "Downloading ffmpeg (this may take a minute ~80 MB)..." -ForegroundColor Cyan
        $ffmpegZip = Join-Path $env:TEMP "ffmpeg_windows.zip"
        # BtbN GPL essentials build — ffmpeg.exe, ffprobe.exe, ffplay.exe
        $ffmpegUrl = "https://github.com/BtbN/FFmpeg-Builds/releases/download/latest/ffmpeg-master-latest-win64-gpl.zip"
        Invoke-WebRequest -Uri $ffmpegUrl -OutFile $ffmpegZip
        Write-Host "Extracting ffmpeg..." -ForegroundColor Cyan
        Expand-Archive -Path $ffmpegZip -DestinationPath (Join-Path $ScriptDir "ffmpeg-local-tmp") -Force
        # The zip contains a versioned inner folder — find ffmpeg.exe inside it
        $ffmpegBin = Get-ChildItem -Path (Join-Path $ScriptDir "ffmpeg-local-tmp") -Recurse -Filter "ffmpeg.exe" | Select-Object -First 1
        if (-not $ffmpegBin) { Write-Error "Could not find ffmpeg.exe in downloaded archive" }
        Copy-Item $ffmpegBin.FullName (Split-Path $FfmpegExe) -Force
        # Also grab ffprobe.exe (used by codec detection)
        $ffprobeBin = Get-ChildItem -Path (Join-Path $ScriptDir "ffmpeg-local-tmp") -Recurse -Filter "ffprobe.exe" | Select-Object -First 1
        if ($ffprobeBin) { Copy-Item $ffprobeBin.FullName (Split-Path $FfmpegExe) -Force }
        Remove-Item (Join-Path $ScriptDir "ffmpeg-local-tmp") -Recurse -Force
        Remove-Item $ffmpegZip -Force
        Write-Host "ffmpeg downloaded to $(Split-Path $FfmpegExe)" -ForegroundColor Green
    }
}

# Add ffmpeg directory to PATH so the app can find it
$ffmpegDir = Split-Path $FfmpegExe
if ($env:PATH -notlike "*$ffmpegDir*") {
    $env:PATH = "$ffmpegDir;$env:PATH"
    Write-Host "Added ffmpeg to PATH: $ffmpegDir" -ForegroundColor Green
}

# ─── Create data directories ─────────────────────────────────────────────────
foreach ($dir in @($StreamsDir, $LogsDir, $HlsDir, $CertsDir, (Split-Path $MediamtxExe))) {
    if (-not (Test-Path $dir)) {
        New-Item -ItemType Directory -Path $dir -Force | Out-Null
        Write-Host "Created: $dir"
    }
}

# ─── Download mediamtx Windows binary if missing ─────────────────────────────
if (-not (Test-Path $MediamtxExe)) {
    Write-Host "Fetching latest mediamtx version..." -ForegroundColor Cyan
    $release = Invoke-RestMethod "https://api.github.com/repos/bluenviron/mediamtx/releases/latest"
    $version = $release.tag_name           # e.g. "v1.16.1"
    $asset   = $release.assets | Where-Object { $_.name -like "*windows_amd64.zip" } | Select-Object -First 1
    if (-not $asset) { Write-Error "Could not find Windows amd64 asset in latest mediamtx release" }

    $zipPath = Join-Path $env:TEMP "mediamtx_windows.zip"
    Write-Host "Downloading mediamtx $version..." -ForegroundColor Cyan
    Invoke-WebRequest -Uri $asset.browser_download_url -OutFile $zipPath
    Expand-Archive -Path $zipPath -DestinationPath (Split-Path $MediamtxExe) -Force
    Remove-Item $zipPath
    Write-Host "Downloaded mediamtx $version to $(Split-Path $MediamtxExe)" -ForegroundColor Green
}

# ─── Generate self-signed cert if missing (enables RTSPS) ────────────────────
$CertFile = Join-Path $CertsDir "server.crt"
$KeyFile  = Join-Path $CertsDir "server.key"
if ((-not (Test-Path $CertFile)) -or (-not (Test-Path $KeyFile))) {
    $openssl = Get-Command openssl -ErrorAction SilentlyContinue
    if ($openssl) {
        Write-Host "Generating self-signed certificate..." -ForegroundColor Cyan
        & openssl req -x509 -newkey rsa:2048 -keyout $KeyFile -out $CertFile -days 3650 -nodes -subj "/CN=tak-video-restreamer" 2>$null
        Write-Host "Certificate generated." -ForegroundColor Green
    } else {
        Write-Host "openssl not found - skipping cert generation. RTSPS will not work." -ForegroundColor Yellow
    }
}

# ─── Patch mediaMTX.yml for local paths ──────────────────────────────────────
# Replace Docker container paths with local Windows paths (use forward slashes for mediamtx)
$localStreamsFwd = $StreamsDir -replace '\\', '/'
$localCertsFwd   = $CertsDir   -replace '\\', '/'

$config = Get-Content $ConfigSrc -Raw
$config = $config -replace '/opt/app/streams', $localStreamsFwd
$config = $config -replace '/opt/app/certs',   $localCertsFwd
$config = $config -replace '/opt/app/data',    ($DataDir -replace '\\', '/')
Set-Content -Path $ConfigLocal -Value $config -Encoding UTF8 -NoNewline
Write-Host "Config written: $ConfigLocal" -ForegroundColor Green

# ─── Start MediaMTX ──────────────────────────────────────────────────────────
Write-Host ""
Write-Host "Starting MediaMTX..." -ForegroundColor Cyan
$mtxProc = Start-Process -FilePath $MediamtxExe -ArgumentList $ConfigLocal `
    -NoNewWindow -PassThru

Start-Sleep -Seconds 2
if ($mtxProc.HasExited) {
    Write-Error "MediaMTX exited immediately. Check $ConfigLocal for errors."
}
Write-Host "MediaMTX running (PID $($mtxProc.Id))" -ForegroundColor Green

# ─── Start Flask / Web UI ────────────────────────────────────────────────────
Write-Host "Starting TAK Video Restreamer Web UI on http://localhost:3000 ..." -ForegroundColor Cyan
Write-Host "Login: $($env:ADMIN_USERNAME) / $($env:ADMIN_PASSWORD)" -ForegroundColor Yellow
Write-Host "Press Ctrl+C to stop both processes." -ForegroundColor Gray
Write-Host ""

try {
    & $VenvPython (Join-Path $ScriptDir "main.py")
} finally {
    # ─── Cleanup ─────────────────────────────────────────────────────────────
    Write-Host ""
    Write-Host "Stopping MediaMTX (PID $($mtxProc.Id))..." -ForegroundColor Yellow
    if (-not $mtxProc.HasExited) {
        $mtxProc.Kill()
        $mtxProc.WaitForExit(5000) | Out-Null
    }
    Write-Host "Stopped." -ForegroundColor Green
}
