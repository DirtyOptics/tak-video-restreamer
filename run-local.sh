#!/usr/bin/env bash
# This material is based upon work supported by the United States Air Force under contract number FA8750-24-S-B079 (Prime Contractor Smart Information Flow Technologies (SIFT)).  Any opinions, findings and conclusions or recommendations expressed in this material are those of the author(s) and do not necessarily reflect the views of the United States Air Force.
# Copyright (c) 2026 RTX BBN Technologies. Licensed to US Government with unlimited rights.

# This program is free software: you can redistribute it and/or modify it under the terms of the GNU General Public License as published by the Free Software Foundation, either version 3 of the License, or (at your option) any later version.
# This is distributed in the hope that it will be useful, but without any warranty, without even the implied warranty of merchantability or fitness for a particular purpose.  See the GNU General Public License for more details. https://www.gnu.org/licenses/
#
# Run TAK Video Restreamer locally on Linux without Docker.
#
# What this script does:
#   - Detects or creates a Python venv and installs requirements
#   - Downloads the mediamtx Linux binary if not present
#   - Patches mediaMTX.yml to use local data/ paths instead of /opt/app/...
#   - Generates a self-signed TLS cert if one is missing
#   - Sets all required environment variables
#   - Starts MediaMTX in the background and Flask in the foreground
#   - Cleans up both on Ctrl+C
#
# Usage: bash run-local.sh
# Run from the project root directory.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

# ── Colour helpers ─────────────────────────────────────────────────────────────
cyan()   { echo -e "\033[36m$*\033[0m"; }
green()  { echo -e "\033[32m$*\033[0m"; }
yellow() { echo -e "\033[33m$*\033[0m"; }
red()    { echo -e "\033[31m$*\033[0m"; }

# ── Paths ──────────────────────────────────────────────────────────────────────
DATA_DIR="$SCRIPT_DIR/data"
STREAMS_DIR="$DATA_DIR/streams"
LOGS_DIR="$DATA_DIR/logs"
HLS_DIR="$DATA_DIR/hls"
CERTS_DIR="$DATA_DIR/certs"
FFMPEG_LOG_DIR="$LOGS_DIR/ffmpeg"
MEDIAMTX_DIR="$SCRIPT_DIR/mediamtx-local"
MEDIAMTX_BIN="$MEDIAMTX_DIR/mediamtx"
MEDIAMTX_CFG="$MEDIAMTX_DIR/mediamtx.yml"

# ── Find Python venv ───────────────────────────────────────────────────────────
if [ -f "$SCRIPT_DIR/.venv/bin/python" ]; then
    VENV_PYTHON="$SCRIPT_DIR/.venv/bin/python"
    VENV_PIP="$SCRIPT_DIR/.venv/bin/pip"
elif [ -f "$SCRIPT_DIR/venv/bin/python" ]; then
    VENV_PYTHON="$SCRIPT_DIR/venv/bin/python"
    VENV_PIP="$SCRIPT_DIR/venv/bin/pip"
else
    cyan "No venv found — creating .venv ..."
    python3 -m venv "$SCRIPT_DIR/.venv"
    VENV_PYTHON="$SCRIPT_DIR/.venv/bin/python"
    VENV_PIP="$SCRIPT_DIR/.venv/bin/pip"
fi

# ── Install / verify Python dependencies ──────────────────────────────────────
cyan "Installing Python requirements ..."
"$VENV_PIP" install -q -r "$SCRIPT_DIR/requirements.txt"
green "Python requirements OK."

# ── Check FFmpeg ───────────────────────────────────────────────────────────────
if ! command -v ffmpeg &>/dev/null; then
    red "FFmpeg not found. Install it with: sudo apt install ffmpeg"
    exit 1
fi
green "FFmpeg: $(ffmpeg -version 2>&1 | head -1)"

# ── Create data directories ────────────────────────────────────────────────────
mkdir -p "$STREAMS_DIR" "$LOGS_DIR" "$HLS_DIR" "$CERTS_DIR" "$FFMPEG_LOG_DIR" "$MEDIAMTX_DIR"
green "Data directories ready."

# ── Download mediamtx Linux binary if missing ─────────────────────────────────
if [ ! -f "$MEDIAMTX_BIN" ]; then
    cyan "Fetching latest mediamtx release info ..."
    RELEASE_JSON=$(curl -fsSL https://api.github.com/repos/bluenviron/mediamtx/releases/latest)
    VERSION=$(echo "$RELEASE_JSON" | grep '"tag_name"' | head -1 | sed 's/.*"tag_name": *"\([^"]*\)".*/\1/')
    ASSET_URL=$(echo "$RELEASE_JSON" | grep '"browser_download_url"' | grep 'linux_amd64.tar.gz' | head -1 | sed 's/.*"browser_download_url": *"\([^"]*\)".*/\1/')

    if [ -z "$ASSET_URL" ]; then
        red "Could not find a linux_amd64 asset in the latest mediamtx release."
        exit 1
    fi

    cyan "Downloading mediamtx $VERSION ..."
    TMP_TAR=$(mktemp /tmp/mediamtx_XXXXXX.tar.gz)
    curl -fsSL -o "$TMP_TAR" "$ASSET_URL"
    tar -xzf "$TMP_TAR" -C "$MEDIAMTX_DIR" mediamtx
    rm -f "$TMP_TAR"
    chmod +x "$MEDIAMTX_BIN"
    green "Downloaded mediamtx $VERSION to $MEDIAMTX_BIN"
else
    green "mediamtx binary already present."
fi

# ── Generate self-signed cert if missing ──────────────────────────────────────
CERT_FILE="$CERTS_DIR/server.crt"
KEY_FILE="$CERTS_DIR/server.key"
if [ ! -f "$CERT_FILE" ] || [ ! -f "$KEY_FILE" ]; then
    if command -v openssl &>/dev/null; then
        cyan "Generating self-signed certificate ..."
        openssl req -x509 -newkey rsa:2048 \
            -keyout "$KEY_FILE" -out "$CERT_FILE" \
            -days 3650 -nodes \
            -subj "/CN=tak-video-restreamer" 2>/dev/null
        green "Certificate generated."
    else
        yellow "openssl not found — skipping cert generation. RTSPS will not work."
    fi
else
    green "TLS certificate already present."
fi

# ── Patch mediaMTX.yml for local paths ────────────────────────────────────────
cyan "Writing patched mediaMTX config to $MEDIAMTX_CFG ..."
sed \
    -e "s|/opt/app/certs|$CERTS_DIR|g" \
    -e "s|/opt/app/streams|$STREAMS_DIR|g" \
    -e "s|/opt/app/data|$DATA_DIR|g" \
    "$SCRIPT_DIR/mediaMTX.yml" > "$MEDIAMTX_CFG"
green "Config written."

# ── Environment variables ──────────────────────────────────────────────────────
export PORT=3000
export MEDIAMTX_API_URL="http://127.0.0.1:8889"
export MEDIAMTX_RTSP_URL="rtsp://127.0.0.1:8554"
export STREAMS_DIR="$STREAMS_DIR"
export DATA_DIR="$DATA_DIR"
export LOGS_DIR="$LOGS_DIR"
export HLS_OUTPUT_DIR="$HLS_DIR"
export FFMPEG_LOG_DIR="$FFMPEG_LOG_DIR"
export ACTIVE_CERTS_DIR="$CERTS_DIR"
export ADMIN_USERNAME="${ADMIN_USERNAME:-admin}"
export ADMIN_PASSWORD="${ADMIN_PASSWORD:-changeme}"
export SECRET_KEY="${SECRET_KEY:-$(python3 -c 'import secrets; print(secrets.token_hex(32))')}"
export PYTHONUNBUFFERED=1
# Use threading async mode — required for Python 3.12+ (eventlet incompatible)
export SOCKETIO_ASYNC_MODE=threading

# ── Start MediaMTX in background ──────────────────────────────────────────────
cyan "Starting MediaMTX ..."
"$MEDIAMTX_BIN" "$MEDIAMTX_CFG" &
MEDIAMTX_PID=$!

# Give mediamtx a moment to initialise
sleep 2

if ! kill -0 "$MEDIAMTX_PID" 2>/dev/null; then
    red "MediaMTX exited immediately. Check $MEDIAMTX_CFG for errors."
    exit 1
fi
green "MediaMTX running (PID $MEDIAMTX_PID)"

# ── Cleanup trap ──────────────────────────────────────────────────────────────
cleanup() {
    echo ""
    yellow "Shutting down ..."
    if kill -0 "$MEDIAMTX_PID" 2>/dev/null; then
        kill "$MEDIAMTX_PID"
        wait "$MEDIAMTX_PID" 2>/dev/null || true
        green "MediaMTX stopped."
    fi
}
trap cleanup EXIT INT TERM

# ── Start Flask ───────────────────────────────────────────────────────────────
echo ""
green "============================================================"
green " TAK Video Restreamer"
green " Web UI : http://localhost:$PORT"
green " Login  : ${ADMIN_USERNAME} / ${ADMIN_PASSWORD}"
green " Press Ctrl+C to stop."
green "============================================================"
echo ""

"$VENV_PYTHON" "$SCRIPT_DIR/main.py"
