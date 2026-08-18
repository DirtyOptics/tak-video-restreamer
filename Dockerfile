FROM python:3.11-slim

# Install system dependencies
RUN apt-get update && apt-get install -y \
    ffmpeg \
    curl \
    wget \
    tar \
    openssl \
    certbot \
    && rm -rf /var/lib/apt/lists/*

# Set working directory
WORKDIR /opt/app

# Download and install MediaMTX
RUN MEDIAMTX_VERSION=v1.19.2 && \
    wget https://github.com/bluenviron/mediamtx/releases/download/${MEDIAMTX_VERSION}/mediamtx_${MEDIAMTX_VERSION}_linux_amd64.tar.gz && \
    tar -xzf mediamtx_${MEDIAMTX_VERSION}_linux_amd64.tar.gz && \
    rm mediamtx_${MEDIAMTX_VERSION}_linux_amd64.tar.gz && \
    chmod +x mediamtx

# Copy MediaMTX configuration
COPY mediaMTX.yml /opt/app/mediamtx.yml

# Copy Python requirements and install dependencies
COPY requirements.txt /opt/app/
RUN pip install --no-cache-dir -r requirements.txt

# Copy application files
COPY main.py /opt/app/
COPY app/ /opt/app/app/
COPY shared/ /opt/app/shared/
COPY utils/ /opt/app/utils/
COPY web/ /opt/app/web/

# Create necessary directories
RUN mkdir -p /opt/app/streams /opt/app/logs /opt/app/hls /opt/app/certs /opt/app/data

#Commented out certs directory creation to avoid redundancy - comment in if needed
# RUN mkdir -p/opt/app/certs

# Generate self-signed certificate for RTSPS (commented out by default)
# Uncomment the line below to auto-generate certificates, or mount your own in docker-compose.yml
# RUN openssl req -x509 -newkey rsa:4096 -keyout /opt/app/certs/server.key -out /opt/app/certs/server.crt -days 365 -nodes -subj "/CN=localhost"

# Expose ports
# 3000: Flask Web UI
# 8554: RTSP
# 8555: RTSPS (encrypted RTSP)
# 8890: SRT
# 8888: HLS (HTTP Live Streaming)
# 8889: MediaMTX API
EXPOSE 3000 8554 8555 8890 8888 8889

# Set environment variables
ENV PYTHONUNBUFFERED=1
ENV PORT=3000
ENV MEDIAMTX_API_URL=http://localhost:8889
ENV STREAMS_DIR=/opt/app/streams
ENV DATA_DIR=/opt/app/data
ENV ACTIVE_CERTS_DIR=/opt/app/certs
ENV ADMIN_USERNAME=admin
ENV ADMIN_PASSWORD=changeme

# Docker healthcheck
HEALTHCHECK --interval=30s --timeout=10s --start-period=15s --retries=3 \
    CMD curl -sf http://localhost:3000/health || exit 1

# Create startup script
RUN echo '#!/bin/bash\n\
set -e\n\
\n\
# Check for external certificates, copy if provided\n\
# If certs already exist in /opt/app/certs (mounted volume), use them as-is\n\
if [ -f "/opt/app/certs/server.crt" ] && [ -f "/opt/app/certs/server.key" ]; then\n\
    echo "Using existing TLS certificates from certs volume..."\n\
    chmod 600 /opt/app/certs/server.key 2>/dev/null || true\n\
fi\n\
\n\
# Only generate self-signed certs if AUTO_GENERATE_CERTS=true AND no certs exist.\n\
# Deployments using Let'\''s Encrypt or custom certs should leave this disabled.\n\
if [ ! -f "/opt/app/certs/server.crt" ] || [ ! -f "/opt/app/certs/server.key" ]; then\n\
    if [ "${AUTO_GENERATE_CERTS}" = "true" ]; then\n\
        echo "Generating self-signed certificate for RTSPS..."\n\
        openssl req -x509 -newkey rsa:2048 -keyout /opt/app/certs/server.key \\\n\
            -out /opt/app/certs/server.crt -days 3650 -nodes \\\n\
            -subj "/CN=tak-video-restreamer" 2>/dev/null\n\
        chmod 600 /opt/app/certs/server.key\n\
        echo "Self-signed certificate generated."\n\
    else\n\
        echo "No TLS certificates found. Disabling RTSPS. Set AUTO_GENERATE_CERTS=true to auto-generate, or mount your own."\n\
        MEDIAMTX_NO_TLS=true\n\
    fi\n\
fi\n\
\n\
# Copy config to writable location (source may be read-only volume mount)\n\
cp /opt/app/mediamtx.yml /tmp/mediamtx_runtime.yml\n\
\n\
# If no certs, patch the runtime config to disable RTSPS\n\
if [ "${MEDIAMTX_NO_TLS}" = "true" ]; then\n\
    sed -i "s|^rtspEncryption:.*|rtspEncryption: \"no\"|" /tmp/mediamtx_runtime.yml\n\
    sed -i "s|^rtspServerCert:.*|# rtspServerCert: (disabled - no certs)|" /tmp/mediamtx_runtime.yml\n\
    sed -i "s|^rtspServerKey:.*|# rtspServerKey: (disabled - no certs)|" /tmp/mediamtx_runtime.yml\n\
fi\n\
\n\
# Start MediaMTX in background\n\
echo "Starting MediaMTX..."\n\
/opt/app/mediamtx /tmp/mediamtx_runtime.yml &\n\
MEDIAMTX_PID=$!\n\
\n\
# Wait for MediaMTX to be ready\n\
sleep 3\n\
\n\
# Determine if HTTPS is enabled\n\
GUNICORN_BIND="--bind 0.0.0.0:3000"\n\
GUNICORN_EXTRA=""\n\
if [ "${HTTPS_ENABLED}" = "true" ] && [ -f "/opt/app/certs/server.crt" ] && [ -f "/opt/app/certs/server.key" ]; then\n\
    echo "HTTPS enabled for web UI"\n\
    GUNICORN_EXTRA="--certfile /opt/app/certs/server.crt --keyfile /opt/app/certs/server.key"\n\
fi\n\
\n\
# Start Flask app with Gunicorn (production WSGI server)\n\
echo "Starting TAK Video Restreamer with Gunicorn..."\n\
cd /opt/app\n\
gunicorn --worker-class eventlet -w 1 ${GUNICORN_BIND} ${GUNICORN_EXTRA} --access-logfile - --error-logfile - main:app &\n\
FLASK_PID=$!\n\
\n\
# Handle shutdown gracefully\n\
trap "echo Shutting down...; kill $MEDIAMTX_PID $FLASK_PID 2>/dev/null; wait $MEDIAMTX_PID $FLASK_PID 2>/dev/null" SIGTERM SIGINT\n\
\n\
# Wait for both processes\n\
wait $MEDIAMTX_PID $FLASK_PID\n\
' > /opt/app/start.sh && chmod +x /opt/app/start.sh

CMD ["/opt/app/start.sh"]
