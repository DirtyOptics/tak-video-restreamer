// RTSP Restreamer Web Client
/**
  * This material is based upon work supported by the United States Air Force under contract number FA8750-24-S-B079 (Prime Contractor Smart Information Flow Technologies (SIFT)).  Any opinions, findings and conclusions or recommendations expressed in this material are those of the author(s) and do not necessarily reflect the views of the United States Air Force.
  * Copyright (c) 2026 RTX BBN Technologies. Licensed to US Government with unlimited rights.

  * This program is free software: you can redistribute it and/or modify it under the terms of the GNU General Public License as published by the Free Software Foundation, either version 3 of the License, or (at your option) any later version.
  * This is distributed in the hope that it will be useful, but without any warranty, without even the implied warranty of merchantability or fitness for a particular purpose.  See the GNU General Public License for more details. https://www.gnu.org/licenses/
  */
console.log('=== CLIENT.JS VERSION 2.1 LOADED ===');
class RTSPRestreamerClient {
    constructor() {
        this.ws = null;
        this.streams = new Map();
        this.recordingStreams = new Set(); // Track which streams are currently recording
        this.systemStartTime = null; // Will be fetched from server
        this.reconnectAttempts = 0;
        this.maxReconnectAttempts = 5;
        this.isLoadingStreams = true; // Track initial loading state
        
        this.init();
    }
    
    init() {
        this.setupWebSocket();
        
        // Ensure DOM is ready before setting up event listeners and initial data
        if (document.readyState === 'loading') {
            document.addEventListener('DOMContentLoaded', () => {
                this.setupEventListeners();
                this.fetchSystemStartTime();
                this.updateServerInfo();
                this.updateUASStatus();
                // Show loading state before fetching
                this.showLoadingState();
                this.refreshStreams();
                this.refreshRecordings();
            });
        } else {
            // DOM is already loaded - initialize immediately
            this.setupEventListeners();
            this.fetchSystemStartTime();
            this.updateServerInfo();
            this.updateUASStatus();
            // Show loading state before fetching
            this.showLoadingState();
            this.refreshStreams();
            this.refreshRecordings();
        }
        
        // Auto-refresh every 10 seconds as fallback (WebSocket handles real-time updates)
        setInterval(() => {
            this.refreshStreams();
            this.updateUptime();
            this.updateUASStatus();
        }, 10000);
        
        // Refresh when page becomes visible (e.g., switching tabs/navigating back)
        document.addEventListener('visibilitychange', () => {
            if (!document.hidden) {
                this.refreshStreams();
                this.updateUASStatus();
            }
        });
        
        // Also refresh on window focus
        window.addEventListener('focus', () => {
            this.refreshStreams();
            this.updateUASStatus();
        });
        

    }
    
    setupWebSocket() {
        // Use Socket.IO client if the library is loaded (added via CDN in index.html)
        if (typeof io === 'undefined') {
            console.log('Socket.IO client not loaded, falling back to API polling only');
            this.ws = null;
            this.updateServerStatus(true);
            return;
        }

        console.log('Connecting to Flask-SocketIO...');
        this.socket = io({
            transports: ['websocket', 'polling'],
            reconnectionAttempts: this.maxReconnectAttempts,
            reconnectionDelay: 2000
        });

        this.socket.on('connect', () => {
            console.log('Socket.IO connected');
            this.updateServerStatus(true);
            this.reconnectAttempts = 0;
        });

        this.socket.on('disconnect', () => {
            console.log('Socket.IO disconnected');
            this.updateServerStatus(false);
        });

        this.socket.on('connect_error', (error) => {
            console.warn('Socket.IO connection error:', error.message);
        });

        // Flask-SocketIO broadcasts on 'message' with {type, data} payload
        this.socket.on('message', (msg) => {
            try {
                this.handleWebSocketMessage(msg);
            } catch (error) {
                console.error('Error handling Socket.IO message:', error);
            }
        });
    }
    
    updateServerStatus(_isConnected) {
        // Connection status indicator was removed from UI
        // Method kept for compatibility
    }
    
    handleWebSocketMessage(data) {
        console.log('=== WEBSOCKET MESSAGE ===');
        console.log('Message type:', data.type);
        console.log('Document ready state:', document.readyState);
        console.log('streams-container exists:', !!document.getElementById('streams-container'));
        console.log('========================');
        
        switch (data.type) {
            case 'streams':
            case 'streams_update':
                this.updateStreamsDisplay(data.data);
                break;
            case 'stream_created':
                this.showNotification(`Stream "${data.data.name}" created`, 'success');
                this.refreshStreams();
                break;
            case 'stream_deleted':
                const deletedStreamName = data.data.name;
                this.showNotification(`Stream "${deletedStreamName}" deleted`, 'info');
                
                // Aggressively remove from client-side tracking
                this.streams.delete(deletedStreamName);
                this.recordingStreams.delete(deletedStreamName);
                
                // Force immediate UI update
                this.refreshStreams();
                break;

            case 'recording_started':
                this.recordingStreams.add(data.data.stream || data.data.name);
                const autoMsg = data.data.auto_started ? ' (auto-record)' : '';
                this.showNotification(`Recording started for "${data.data.stream || data.data.name}"${autoMsg}`, 'success');
                this.refreshStreams(); // Refresh to update button state
                break;
            case 'recording_stopped':
                this.recordingStreams.delete(data.data.name);
                this.showNotification(`Recording stopped for "${data.data.name}"`, 'info');
                this.refreshStreams(); // Refresh to update button state
                break;
            case 'auto_record_changed':
                const checkbox = document.getElementById('auto-record-checkbox');
                if (checkbox) {
                    checkbox.checked = data.data.enabled;
                }
                break;
            case 'pull_stream_started':
            case 'pull_stream_reconnected':
                this.refreshStreams();
                break;
            case 'pull_stream_stopped':
                // Self-terminated (not from an explicit stop/delete — those suppress this event)
                this.refreshStreams();
                break;
            case 'stream_stopped':
                this.showNotification(`Stream "${data.data.name}" stopped`, 'info');
                this.refreshStreams();
                break;
            case 'pull_stream_failed':
                this.showNotification(`Stream "${data.data.name}" failed to reconnect`, 'warning');
                this.refreshStreams();
                break;
            case 'pull_stream_retrying':
                // Don't spam toast notifications — the stream card shows reconnecting state
                this.refreshStreams();
                break;
            case 'codec_detecting':
                this.showNotification(`Detecting codec for "${data.data.name}"...`, 'info');
                break;
            default:
                console.log('Unknown WebSocket message type:', data.type);
        }
    }
    
    setupEventListeners() {
        // Refresh buttons
        document.getElementById('refresh-streams').addEventListener('click', () => {
            this.refreshStreams();
        });
        
        // Auto-record checkbox
        const autoRecordCheckbox = document.getElementById('auto-record-checkbox');
        if (autoRecordCheckbox) {
            // Load initial state
            this.loadAutoRecordSetting();
            
            // Handle checkbox changes
            autoRecordCheckbox.addEventListener('change', (e) => {
                this.setAutoRecordSetting(e.target.checked);
            });
        }
        
        // Add event listener for the "Add First Stream" button
        document.addEventListener('click', (e) => {
            if (e.target.id === 'add-first-stream') {
                document.getElementById('add-stream').click();
            }
        });
        
        // Add stream modal
        document.getElementById('add-stream').addEventListener('click', () => {
            this.showAddStreamModal();
        });
        
        document.getElementById('cancel-add-stream').addEventListener('click', () => {
            this.hideAddStreamModal();
        });
        
        document.getElementById('add-stream-form').addEventListener('submit', (e) => {
            e.preventDefault();
            this.createStream();
        });
        
        // Modal close handlers
        document.querySelectorAll('.close').forEach(closeBtn => {
            closeBtn.addEventListener('click', (e) => {
                const modal = e.target.closest('.modal');
                modal.style.display = 'none';
            });
        });
        
        // Close modal when clicking outside
        window.addEventListener('click', (e) => {
            if (e.target.classList.contains('modal')) {
                e.target.style.display = 'none';
            }
        });
    }
    
    updateServerInfo() {
        // Update RTSP URLs with current hostname (defensive checks for elements)
        const hostname = window.location.hostname;
        
        const rtspServer = document.getElementById('rtsp-server');
        const publishUrl = document.getElementById('publish-url');
        const viewUrl = document.getElementById('view-url');
        
        if (rtspServer) rtspServer.textContent = `rtsp://${hostname}:8554/`;
        if (publishUrl) publishUrl.textContent = `rtsp://${hostname}:8554/[stream-name]`;
        if (viewUrl) viewUrl.textContent = `rtsp://${hostname}:8554/[stream-name]`;
        
        // Update SRT URLs if elements exist
        const srtServer = document.getElementById('srt-server');
        const srtPublishUrl = document.getElementById('srt-publish-url');
        const srtViewUrl = document.getElementById('srt-view-url');
        
        if (srtServer) srtServer.textContent = `srt://${hostname}:8890/`;
        if (srtPublishUrl) srtPublishUrl.textContent = `srt://${hostname}:8890?streamid=publish:[stream-name] (MediaMTX format - tested working)`;
        if (srtViewUrl) srtViewUrl.textContent = `srt://${hostname}:8890?streamid=read:[stream-name] (MediaMTX format - for VLC try both this and official format)`;

        // Update RTMP URL if element exists
        const rtmpServer = document.getElementById('rtmp-server');
        if (rtmpServer) rtmpServer.textContent = `rtmp://${hostname}:1935/`;
    }
    
    async updateUASStatus() {
        try {
            const response = await fetch('/api/status');
            if (response.ok) {
                const status = await response.json();
                
                // Update UAS mode badge
                const uasBadge = document.getElementById('uas-mode-badge');
                const uasLink = document.getElementById('uas-link');
                
                if (status.uasMode && status.uasMode.enabled) {
                    if (uasBadge) {
                        uasBadge.style.display = 'inline';
                        uasBadge.textContent = 'UAS MODE';
                        uasBadge.className = 'badge uas-enabled';
                    }
                    if (uasLink) {
                        uasLink.style.display = 'inline';
                    }
                } else {
                    if (uasBadge) {
                        uasBadge.style.display = 'none';
                    }
                    if (uasLink) {
                        uasLink.style.display = 'none';
                    }
                }
            }
        } catch (error) {
            console.warn('Failed to fetch UAS status:', error);
        }
    }
    
    showLoadingState() {
        const container = document.getElementById('streams-container');
        if (container) {
            container.innerHTML = `
                <div class="empty-state">
                    <h3>Loading...</h3>
                </div>
            `;
        }
    }
    
    async fetchSystemStartTime() {
        try {
            const response = await fetch('/health');
            if (response.ok) {
                const data = await response.json();
                if (data.systemStartTime) {
                    this.systemStartTime = new Date(data.systemStartTime).getTime();
                    this.updateUptime(); // Update immediately after fetching
                }
            }
        } catch (error) {
            console.error('Failed to fetch system start time:', error);
            // Fallback to page load time if server start time is unavailable
            this.systemStartTime = Date.now();
        }
    }
    
    updateUptime() {
        if (!this.systemStartTime) {
            // Display loading state until we have the start time
            const uptimeEl = document.getElementById('uptime');
            if (uptimeEl) {
                uptimeEl.textContent = '--:--:--';
            }
            return;
        }
        
        const uptime = Date.now() - this.systemStartTime;
        const hours = Math.floor(uptime / (1000 * 60 * 60));
        const minutes = Math.floor((uptime % (1000 * 60 * 60)) / (1000 * 60));
        const seconds = Math.floor((uptime % (1000 * 60)) / 1000);
        
        const uptimeEl = document.getElementById('uptime');
        if (uptimeEl) {
            uptimeEl.textContent = 
                `${hours.toString().padStart(2, '0')}:${minutes.toString().padStart(2, '0')}:${seconds.toString().padStart(2, '0')}`;
        }
    }
    
    async refreshStreams() {
        // Early return if DOM is not ready or required elements don't exist
        if (document.readyState === 'loading' || !document.getElementById('streams-container')) {
            this.isLoadingStreams = false;
            return;
        }
        
        try {
            const response = await fetch('/api/streams');
            if (!response.ok) {
                throw new Error(`HTTP ${response.status}: ${response.statusText}`);
            }
            const streams = await response.json();
            
            // Also fetch recording status and ABR status for all streams
            try {
                const [recordingResponse, abrResponse] = await Promise.all([
                    fetch('/api/recording-status'),
                    fetch('/api/abr'),
                ]);
                if (recordingResponse.ok) {
                    const recordingStatus = await recordingResponse.json();
                    // Update recording status tracking
                    this.recordingStreams.clear();
                    Object.entries(recordingStatus).forEach(([streamName, isRecording]) => {
                        if (isRecording) {
                            this.recordingStreams.add(streamName);
                        }
                    });
                }
                // Update ABR status tracking
                this.abrStreams = this.abrStreams || new Set();
                this.abrStreams.clear();
                if (abrResponse.ok) {
                    const abrList = await abrResponse.json();
                    (abrList || []).forEach(entry => {
                        if (entry.running) this.abrStreams.add(entry.stream);
                    });
                }
            } catch (statusError) {
                console.warn('Failed to fetch recording/ABR status:', statusError);
            }
            
            // Ensure streams is an array
            const streamsArray = Array.isArray(streams) ? streams : [];
            this.updateStreamsDisplay(streamsArray);
        } catch (error) {
            console.error('=== REFRESH STREAMS ERROR ===');
            console.error('Error type:', typeof error);
            console.error('Error constructor:', error.constructor.name);
            console.error('Error message:', error.message);
            console.error('Error stack:', error.stack);
            console.error('=== END ERROR DEBUG ===');
            
            // Update server status to disconnected
            this.isLoadingStreams = false;
            this.updateServerStatus(false);
            
            // Show error in the streams container instead of blocking alert
            const container = document.getElementById('streams-container');
            if (container) {
                container.innerHTML = `
                    <div class="error-message" style="padding: 2rem; text-align: center; color: var(--error-color, #dc3545);">
                        <h3>Unable to connect to server</h3>
                        <p>The server appears to be offline or unreachable.</p>
                        <p style="font-size: 0.9em; color: var(--text-muted, #666);">Check the console for details.</p>
                    </div>
                `;
            }
        }
    }
    
    async loadAutoRecordSetting() {
        try {
            const response = await fetch('/api/settings/auto-record');
            if (response.ok) {
                const data = await response.json();
                const checkbox = document.getElementById('auto-record-checkbox');
                if (checkbox) {
                    checkbox.checked = data.enabled || false;
                }
            }
        } catch (error) {
            console.error('Error loading auto-record setting:', error);
        }
    }
    
    async setAutoRecordSetting(enabled) {
        try {
            const response = await fetch('/api/settings/auto-record', {
                method: 'POST',
                headers: {'Content-Type': 'application/json'},
                body: JSON.stringify({enabled})
            });
            
            if (response.ok) {
                const data = await response.json();
                this.showNotification(data.message, 'success');
            } else {
                const error = await response.json();
                this.showNotification(`Error: ${error.error}`, 'error');
                // Revert checkbox on error
                const checkbox = document.getElementById('auto-record-checkbox');
                if (checkbox) checkbox.checked = !enabled;
            }
        } catch (error) {
            console.error('Error setting auto-record:', error);
            this.showNotification('Failed to update auto-record setting', 'error');
            // Revert checkbox on error
            const checkbox = document.getElementById('auto-record-checkbox');
            if (checkbox) checkbox.checked = !enabled;
        }
    }
    
    updateStreamsDisplay(streams) {
        // Always get fresh DOM elements - no variables that could go out of scope
        if (!document.getElementById('streams-container')) {
            console.warn('streams-container not found, skipping updateStreamsDisplay');
            return;
        }

        // Any call to update the display means we have a server response — no longer loading
        this.isLoadingStreams = false;
        
        // CLIENT-SIDE FILTER: Hide _hls transcoded streams (they're just technical derivatives)
        const activeStreams = (streams || []).filter(stream => !stream.name.endsWith('_hls'));
        
        // Handle empty streams
        if (!activeStreams || activeStreams.length === 0) {
            if (this.isLoadingStreams) {
                document.getElementById('streams-container').innerHTML = `
                    <div class="empty-state">
                        <h3>Loading...</h3>
                    </div>
                `;
            } else {
                document.getElementById('streams-container').innerHTML = `
                    <div class="empty-state">
                        <h3>No active streams</h3>
                    </div>
                `;
            }
            const activeElement = document.getElementById('active-streams');
            const viewersElement = document.getElementById('total-viewers');
            if (activeElement) activeElement.textContent = '0';
            if (viewersElement) viewersElement.textContent = '0';

            return;
        }
        
        // Process streams
        let totalViewers = 0;
        let activeCount = 0;
        
        const streamHTML = activeStreams.map(stream => {
            this.streams.set(stream.name, stream);
            const streamName = stream.name || 'Unknown';
            const safeName = this.escapeHtml(streamName);
            const safeAttrName = this.escapeAttr(streamName);
            const viewers = stream.numReaders || 0;
            const isActive = Boolean(stream.ready);
            const isReconnecting = stream.pullStatus === 'reconnecting';
            const retryCount = stream.retryCount || 0;
            const bytesReceived = stream.bytesReceived || 0;
            const bytesSent = stream.bytesSent || 0;
            const protocol = stream.protocol || '';
            const sourceAddress = stream.sourceAddress || '';
            const safeProtocol = this.escapeHtml(protocol);
            const safeSource = this.escapeHtml(sourceAddress);

            if (isActive) activeCount++;
            totalViewers += viewers;

            // Protocol badge color
            let protocolClass = 'protocol-default';
            if (protocol === 'SRT') protocolClass = 'protocol-srt';
            else if (protocol === 'RTSP' || protocol === 'RTSP Pull') protocolClass = 'protocol-rtsp';
            else if (protocol === 'RTMP') protocolClass = 'protocol-rtmp';
            else if (protocol === 'WebRTC') protocolClass = 'protocol-webrtc';

            return `
                <div class="stream-item">
                    <div class="stream-header">
                        <div class="stream-name">
                            ${safeName}
                            ${protocol ? `<span class="protocol-badge ${protocolClass}">${safeProtocol}</span>` : ''}
                        </div>
                        <div class="stream-status ${isActive ? 'live' : isReconnecting ? 'reconnecting' : 'offline'}">
                            ${this.recordingStreams.has(streamName) ? '<span class="rec-label">REC</span>' : ''}
                            <div class="status-dot ${isActive ? 'online' : isReconnecting ? 'reconnecting' : 'offline'}"></div>
                            ${isActive ? 'Live' : isReconnecting ? `Reconnecting${retryCount > 0 ? ` (${retryCount})` : ''}` : 'Offline'}
                        </div>
                    </div>
                    <div class="stream-details">
                        ${sourceAddress ? `
                        <div class="stream-detail">
                            <span class="stream-detail-label">Source</span>
                            <span class="stream-detail-value">${safeSource}</span>
                        </div>
                        ` : ''}
                        <div class="stream-detail">
                            <span class="stream-detail-label">Viewers</span>
                            <span class="stream-detail-value">
                                ${viewers > 0
                                    ? `<button class="viewers-count-btn" onclick="rtspClient.showViewers('${safeAttrName}')" title="View connected clients">${viewers}</button>`
                                    : `<span>0</span>`}
                            </span>
                        </div>
                        <div class="stream-detail">
                            <span class="stream-detail-label">Received</span>
                            <span class="stream-detail-value">${this.formatBytes(bytesReceived)}</span>
                        </div>
                        <div class="stream-detail">
                            <span class="stream-detail-label">Sent</span>
                            <span class="stream-detail-value">${this.formatBytes(bytesSent)}</span>
                        </div>
                    </div>
                    <div class="stream-actions">
                        <button class="btn btn-small btn-secondary" onclick="rtspClient.viewStreamDetails('${safeAttrName}')">
                            📊 Details
                        </button>
                        ${isActive ? `
                            ${this.recordingStreams.has(streamName) ? `
                                <button class="btn btn-small btn-stop-recording" onclick="rtspClient.stopRecording('${safeAttrName}')">
                                    ⏹️ Stop Recording
                                </button>
                            ` : `
                                <button class="btn btn-small btn-secondary" onclick="rtspClient.recordStream('${safeAttrName}')">
                                    🔴 Record
                                </button>
                            `}
                            ${(this.abrStreams && this.abrStreams.has(streamName)) ? `
                                <button class="btn btn-small btn-abr-active" onclick="rtspClient.toggleABR('${safeAttrName}')" id="abr-btn-${safeAttrName}" title="ABR is ON – click to stop">
                                    📶 ABR ON
                                </button>
                            ` : `
                                <button class="btn btn-small btn-abr-inactive" onclick="rtspClient.toggleABR('${safeAttrName}')" id="abr-btn-${safeAttrName}" title="ABR is OFF – click to start">
                                    📶 ABR
                                </button>
                            `}
                            <button class="btn btn-small btn-warning" onclick="rtspClient.stopStream('${safeAttrName}')">
                                ⏸️ Stop Stream
                            </button>
                        ` : ''}
                        <button class="btn btn-small btn-secondary" onclick="rtspClient.deleteStream('${safeAttrName}')">
                            🗑️ Delete
                        </button>
                    </div>
                </div>
            `;
        }).join('');
        
        // Update DOM - always get fresh elements, no variables
        const containerElement = document.getElementById('streams-container');
        if (!containerElement) {
            console.error('streams-container disappeared during processing');
            return;
        }
        
        containerElement.innerHTML = streamHTML;
        
        const activeElement = document.getElementById('active-streams');
        const viewersElement = document.getElementById('total-viewers');
        
        if (activeElement) {
            activeElement.textContent = activeCount.toString();
        }
        if (viewersElement) {
            viewersElement.textContent = totalViewers.toString();
        }
    }
    

    
    showAddStreamModal() {
        document.getElementById('add-stream-modal').style.display = 'block';
        document.getElementById('stream-name').focus();
    }
    
    hideAddStreamModal() {
        document.getElementById('add-stream-modal').style.display = 'none';
        document.getElementById('add-stream-form').reset();
    }
    
    async createStream() {
        console.log('createStream() called');
        const form = document.getElementById('add-stream-form');
        if (!form) {
            console.error('Form not found!');
            this.showNotification('Form error - please reload the page', 'error');
            return;
        }
        
        const formData = new FormData(form);
        
        const streamName = formData.get('streamName');
        console.log('Raw streamName:', streamName);
        const trimmedStreamName = streamName ? streamName.trim() : '';
        console.log('Trimmed streamName:', trimmedStreamName);
        
        const streamMode = formData.get('streamMode');
        const enableRecording = formData.get('recording') === 'on';
        const sourceUrl = formData.get('sourceUrl');
        const rtspUsername = formData.get('rtspUsername');
        const rtspPassword = formData.get('rtspPassword');
        
        // Validate stream name
        if (!trimmedStreamName || trimmedStreamName.length === 0) {
            console.error('Stream name is empty');
            this.showNotification('Stream name is required', 'error');
            return;
        }
        
        // Validate stream name format (alphanumeric, dash, underscore only)
        if (!/^[a-zA-Z0-9_-]+$/.test(trimmedStreamName)) {
            console.error('Invalid stream name format:', trimmedStreamName);
            this.showNotification('Stream name can only contain letters, numbers, dashes, and underscores', 'error');
            return;
        }
        
        console.log('Creating stream:', trimmedStreamName);
        
        try {
            // First, create the stream path
            const config = {
                source: "publisher"
            };
            
            const createResponse = await fetch(`/api/streams/${trimmedStreamName}`, {
                method: 'POST',
                headers: {
                    'Content-Type': 'application/json'
                },
                body: JSON.stringify(config)
            });
            
            console.log('API response status:', createResponse.status);
            
            if (!createResponse.ok) {
                const error = await createResponse.json();
                console.error('API error:', error);
                this.showNotification(`Failed to create stream: ${error.error || 'Unknown error'}`, 'error');
                return;
            }
            
            // If pull mode, start pulling the stream
            if (streamMode === 'pull' && sourceUrl) {
                const pullData = {
                    sourceUrl: sourceUrl.trim()
                };
                
                // Add credentials if provided
                if (rtspUsername && rtspPassword) {
                    pullData.username = rtspUsername.trim();
                    pullData.password = rtspPassword;
                }
                
                const pullResponse = await fetch(`/api/streams/${trimmedStreamName}/pull`, {
                    method: 'POST',
                    headers: {
                        'Content-Type': 'application/json'
                    },
                    body: JSON.stringify(pullData)
                });
                
                if (!pullResponse.ok) {
                    const error = await pullResponse.json();
                    this.showNotification(`Stream created but failed to start pull: ${error.error}`, 'warning');
                }
                // Success notification handled by WebSocket 'stream_created' event
            }
            // Success notification handled by WebSocket 'stream_created' event
            
            // Start recording if requested
            if (enableRecording) {
                setTimeout(async () => {
                    try {
                        await fetch(`/api/streams/${trimmedStreamName}/record`, { method: 'POST' });
                        // Notification handled by WebSocket 'recording_started' event
                    } catch (err) {
                        console.error('Failed to start recording:', err);
                    }
                }, 2000);
            }
            
            this.hideAddStreamModal();
            
            // Clear the form to prevent accidental resubmission
            form.reset();
            document.getElementById('pull-options').style.display = 'none';
            document.getElementById('auth-fields').style.display = 'none';
            // UI update handled by WebSocket 'stream_created' event
            
        } catch (error) {
            console.error('Error creating stream:', error);
            console.error('Error stack:', error.stack);
            this.showNotification(`Failed to create stream: ${error.message}`, 'error');
        }
    }
    
    async stopStream(streamName) {
        if (!confirm(`Stop all processes for stream "${streamName}"? (Stream path will remain)`)) {
            return;
        }
        
        try {
            const response = await fetch(`/api/streams/${streamName}/stop`, {
                method: 'POST'
            });
            
            const data = await response.json();
            
            if (data.success) {
                // UI update handled by WebSocket 'stream_stopped' event
            } else {
                this.showNotification(data.error || 'Failed to stop stream', 'error');
            }
        } catch (error) {
            console.error('Error stopping stream:', error);
            this.showNotification('Failed to stop stream', 'error');
        }
    }
    
    async deleteStream(streamName) {
        if (!confirm(`Are you sure you want to delete the stream "${streamName}"?`)) {
            return;
        }
        
        try {
            const response = await fetch(`/api/streams/${streamName}`, {
                method: 'DELETE'
            });
            
            if (response.ok) {
                // UI update handled by WebSocket 'stream_deleted' event
            } else {
                const error = await response.json();
                this.showNotification(`Failed to delete stream: ${error.error}`, 'error');
            }
        } catch (error) {
            console.error('Error deleting stream:', error);
            this.showNotification('Failed to delete stream', 'error');
        }
    }
    
    async showViewers(streamName) {
        const modal = document.getElementById('viewers-modal');
        const title = document.getElementById('viewers-modal-title');
        const content = document.getElementById('viewers-modal-content');
        title.textContent = `Viewers — ${streamName}`;
        content.innerHTML = '<p style="color:var(--text-secondary)">Loading...</p>';
        modal.style.display = 'block';
        try {
            const resp = await fetch(`/api/streams/${encodeURIComponent(streamName)}/viewers`);
            const viewers = await resp.json();
            if (!Array.isArray(viewers) || viewers.length === 0) {
                content.innerHTML = '<p style="color:var(--text-secondary)">No viewers connected.</p>';
                return;
            }
            content.innerHTML = `
                <table style="width:100%;border-collapse:collapse;font-size:0.875rem">
                    <thead>
                        <tr style="color:var(--text-secondary);text-align:left">
                            <th style="padding:6px 8px">IP Address</th>
                            <th style="padding:6px 8px">Protocol</th>
                            <th style="padding:6px 8px"></th>
                        </tr>
                    </thead>
                    <tbody>
                        ${viewers.map(v => `
                            <tr style="border-top:1px solid var(--border-color)">
                                <td style="padding:8px">${this.escapeHtml(v.ip)}${v.blocked ? ' <span style="color:var(--accent-danger);font-size:0.75rem">BLOCKED</span>' : ''}</td>
                                <td style="padding:8px">${this.escapeHtml(v.protocol)}</td>
                                <td style="padding:8px;text-align:right">
                                    <button class="btn btn-small btn-danger"
                                        onclick="rtspClient.blockViewer('${this.escapeAttr(streamName)}','${this.escapeAttr(v.connType)}','${this.escapeAttr(v.id)}',this)">
                                        Block
                                    </button>
                                </td>
                            </tr>
                        `).join('')}
                    </tbody>
                </table>
                <div style="margin-top:12px;text-align:right">
                    <button class="btn btn-small" onclick="rtspClient.showBlockedIPs()" style="font-size:0.8rem">
                        Manage Blocked IPs
                    </button>
                </div>
            `;
        } catch (e) {
            content.innerHTML = '<p style="color:var(--accent-danger)">Failed to load viewers.</p>';
        }
    }

    async blockViewer(streamName, connType, connId, btn) {
        btn.disabled = true;
        btn.textContent = 'Blocking…';
        try {
            const resp = await fetch(
                `/api/streams/${encodeURIComponent(streamName)}/viewers/${encodeURIComponent(connType)}/${encodeURIComponent(connId)}/block`,
                { method: 'POST' }
            );
            const data = await resp.json();
            if (data.success) {
                btn.closest('tr').remove();
                const tbody = document.querySelector('#viewers-modal-content tbody');
                if (tbody && tbody.rows.length === 0) {
                    document.getElementById('viewers-modal-content').innerHTML =
                        '<p style="color:var(--text-secondary)">No viewers connected.</p>';
                }
                if (data.blocked_ip) {
                    this.showNotification(`Blocked ${data.blocked_ip}`, 'warning');
                }
            } else {
                btn.disabled = false;
                btn.textContent = 'Block';
                this.showNotification('Failed to block viewer', 'error');
            }
        } catch (e) {
            btn.disabled = false;
            btn.textContent = 'Block';
            this.showNotification('Failed to block viewer', 'error');
        }
    }

    async showBlockedIPs() {
        const modal = document.getElementById('viewers-modal');
        const title = document.getElementById('viewers-modal-title');
        const content = document.getElementById('viewers-modal-content');
        title.textContent = 'Blocked IPs';
        content.innerHTML = '<p style="color:var(--text-secondary)">Loading...</p>';
        modal.style.display = 'block';
        try {
            const resp = await fetch('/api/blocked-ips');
            const ips = await resp.json();
            if (!Array.isArray(ips) || ips.length === 0) {
                content.innerHTML = '<p style="color:var(--text-secondary)">No blocked IPs.</p>';
                return;
            }
            content.innerHTML = `
                <table style="width:100%;border-collapse:collapse;font-size:0.875rem">
                    <thead>
                        <tr style="color:var(--text-secondary);text-align:left">
                            <th style="padding:6px 8px">IP Address</th>
                            <th style="padding:6px 8px"></th>
                        </tr>
                    </thead>
                    <tbody>
                        ${ips.map(ip => `
                            <tr style="border-top:1px solid var(--border-color)">
                                <td style="padding:8px">${this.escapeHtml(ip)}</td>
                                <td style="padding:8px;text-align:right">
                                    <button class="btn btn-small btn-success"
                                        onclick="rtspClient.unblockIP('${this.escapeAttr(ip)}',this)">
                                        Unblock
                                    </button>
                                </td>
                            </tr>
                        `).join('')}
                    </tbody>
                </table>
            `;
        } catch (e) {
            content.innerHTML = '<p style="color:var(--accent-danger)">Failed to load blocked IPs.</p>';
        }
    }

    async unblockIP(ip, btn) {
        btn.disabled = true;
        btn.textContent = 'Unblocking…';
        try {
            const resp = await fetch(`/api/blocked-ips/${encodeURIComponent(ip)}`, { method: 'DELETE' });
            const data = await resp.json();
            if (data.success) {
                btn.closest('tr').remove();
                const tbody = document.querySelector('#viewers-modal-content tbody');
                if (tbody && tbody.rows.length === 0) {
                    document.getElementById('viewers-modal-content').innerHTML =
                        '<p style="color:var(--text-secondary)">No blocked IPs.</p>';
                }
                this.showNotification(`Unblocked ${ip}`, 'success');
            } else {
                btn.disabled = false;
                btn.textContent = 'Unblock';
                this.showNotification('Failed to unblock IP', 'error');
            }
        } catch (e) {
            btn.disabled = false;
            btn.textContent = 'Unblock';
            this.showNotification('Failed to unblock IP', 'error');
        }
    }

    async viewStreamDetails(streamName) {
        try {
            const modal   = document.getElementById('stream-details-modal');
            const title   = document.getElementById('stream-details-title');
            const content = document.getElementById('stream-details-content');

            const safeName  = this.escapeHtml(streamName);
            const hostname  = this.escapeHtml(window.location.hostname);
            const port      = window.location.port || '3000';

            // Current source info (from last stream update)
            const streamData    = this.streams.get(streamName);
            const protocol      = streamData ? (streamData.protocol      || '') : '';
            const sourceAddress = streamData ? (streamData.sourceAddress || '') : '';
            const sourceUrl     = streamData ? (streamData.sourceUrl     || '') : '';

            // "To" URL — the endpoint on THIS server the stream is arriving at,
            // matched to the ingest protocol
            let toUrl;
            if (protocol === 'SRT') {
                toUrl = `srt://${hostname}:8890?streamid=publish:${safeName}`;
            } else if (protocol === 'RTMP') {
                toUrl = `rtmp://${hostname}:1935/${safeName}`;
            } else if (protocol === 'WebRTC') {
                toUrl = `http://${hostname}:8889/${safeName}`;
            } else {
                // RTSP push, RTSP Pull, unknown — all land on RTSP
                toUrl = `rtsp://${hostname}:8554/${safeName}`;
            }

            // Watch endpoints
            const rtspWatchUrl = `rtsp://${hostname}:8554/${safeName}`;
            const srtWatchUrl  = `srt://${hostname}:8890?streamid=read:${safeName}`;
            const hlsUrl       = `http://${hostname}:8888/${safeName}/index.m3u8`;
            const hlsAbrUrl    = `http://${hostname}:${port}/hls/${safeName}/master.m3u8`;
            const playerUrl    = `http://${hostname}:${port}/hls/${safeName}/player`;

            const urlRow = (label, url, extra = '') => `
                <div class="url-row">
                    <span class="url-label">${label}</span>
                    <code class="url-code">${this.escapeHtml(url)}</code>
                    <button class="btn btn-tiny btn-secondary url-copy" data-url="${this.escapeAttr(url)}">Copy</button>
                    ${extra}
                </div>`;

            const textRow = (label, text) => `
                <div class="url-row">
                    <span class="url-label">${label}</span>
                    <span class="url-code">${this.escapeHtml(text)}</span>
                </div>`;

            // "From" row — pull streams get a copyable URL, push streams get text
            let fromRow = '';
            if (sourceUrl) {
                fromRow = urlRow('From', sourceUrl);
            } else if (sourceAddress) {
                fromRow = textRow('From', [protocol, sourceAddress].filter(Boolean).join(' · '));
            }

            title.textContent = `Stream: ${streamName}`;
            content.innerHTML = `
                <div class="url-section">
                    <div class="url-section-title">Source</div>
                    ${fromRow}
                    ${urlRow('To', toUrl)}
                </div>
                <div class="url-section">
                    <div class="url-section-title">Watch</div>
                    ${urlRow('RTSP', rtspWatchUrl)}
                    ${urlRow('SRT',  srtWatchUrl)}
                    ${urlRow('HLS', hlsUrl)}
                    ${urlRow('HLS ABR', hlsAbrUrl)}
                    ${urlRow('HLS Player', playerUrl, `<a href="${this.escapeAttr(playerUrl)}" target="_blank" class="btn btn-tiny btn-secondary">Open ↗</a>`)}
                </div>
            `;

            content.querySelectorAll('.url-copy').forEach(btn => {
                btn.addEventListener('click', () => {
                    navigator.clipboard.writeText(btn.dataset.url).then(() => {
                        const orig = btn.textContent;
                        btn.textContent = '✓';
                        setTimeout(() => { btn.textContent = orig; }, 1200);
                    }).catch(() => {});
                });
            });

            modal.style.display = 'block';
        } catch (error) {
            console.error('Error opening stream details:', error);
            this.showNotification('Failed to open stream details', 'error');
        }
    }

    async toggleABR(streamName) {
        // Optimistic UI: disable button immediately
        const btnId = `abr-btn-${streamName.replace(/[^a-zA-Z0-9_.-]/g, '_')}`;
        const btn = document.getElementById(btnId);
        if (btn) { btn.disabled = true; btn.style.opacity = '0.5'; }

        try {
            const isActive = this.abrStreams && this.abrStreams.has(streamName);

            if (isActive) {
                // Stop ABR
                const resp = await fetch(`/api/streams/${streamName}/abr`, { method: 'DELETE' });
                if (resp.ok) {
                    if (this.abrStreams) this.abrStreams.delete(streamName);
                    this.showNotification(`ABR stopped for "${streamName}"`, 'info');
                } else {
                    this.showNotification('Failed to stop ABR', 'error');
                }
            } else {
                // Start ABR
                const resp = await fetch(`/api/streams/${streamName}/abr`, { method: 'POST' });
                if (resp.ok) {
                    if (!this.abrStreams) this.abrStreams = new Set();
                    this.abrStreams.add(streamName);
                    this.showNotification(`ABR transcoding started for "${streamName}" (High + Low quality)`, 'success');
                } else {
                    const err = await resp.json().catch(() => ({}));
                    this.showNotification(`Failed to start ABR: ${err.error || resp.statusText}`, 'error');
                }
            }
            this.refreshStreams();
        } catch (error) {
            console.error('ABR toggle error:', error);
            this.showNotification('Failed to toggle ABR', 'error');
            this.refreshStreams();
        }
    }
    
    async recordStream(streamName) {
        try {
            const response = await fetch(`/api/streams/${streamName}/record`, {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' }
            });
            
            if (response.ok) {
                // UI update handled by WebSocket 'recording_started' event
            } else {
                const error = await response.json();
                this.showNotification(`Failed to start recording: ${error.error}`, 'error');
            }
        } catch (error) {
            console.error('Error starting recording:', error);
            this.showNotification('Failed to start recording', 'error');
        }
    }

    async stopRecording(streamName) {
        if (!confirm(`Stop recording for "${streamName}"?\n\nThe recording file will be finalized and saved.`)) {
            return;
        }
        try {
            const response = await fetch(`/api/streams/${streamName}/stop-record`, {
                method: 'POST'
            });
            
            if (response.ok) {
                // UI update handled by WebSocket 'recording_stopped' event
            } else {
                const error = await response.json();
                this.showNotification(`Failed to stop recording: ${error.error}`, 'error');
            }
        } catch (error) {
            console.error('Error stopping recording:', error);
            this.showNotification('Failed to stop recording', 'error');
        }
    }
    

    

    
    async refreshRecordings() {
        try {
            const response = await fetch('/api/recordings');
            const recordings = await response.json();
            this.displayRecordings(recordings);
        } catch (error) {
            console.error('Error refreshing recordings:', error);
            // Silently fail - don't show error notifications during initialization
        }
    }
    
    displayRecordings(recordings) {
        const container = document.getElementById('recordings-container');
        
        // Element doesn't exist on main page, only on recordings page
        if (!container) {
            return;
        }
        
        if (!recordings || recordings.length === 0) {
            container.innerHTML = `
                <div class="empty-state small">
                    <div class="empty-state-icon">🎬</div>
                    <p>No recordings yet</p>
                </div>
            `;
            return;
        }
        
        const recordingsHTML = recordings.map(recording => {
            const safeFilename = this.escapeHtml(recording.filename.replace('recording-', '').replace('.mp4', ''));
            const safeStream = this.escapeHtml(recording.stream);
            const safeAttrStream = this.escapeAttr(recording.stream);
            const safeAttrFilename = this.escapeAttr(recording.filename);
            return `
            <div class="recording-item">
                <div class="recording-info">
                    <div class="recording-name">${safeFilename}</div>
                    <div class="recording-details">
                        ${safeStream} • ${this.formatBytes(recording.size)} • ${new Date(recording.created).toISOString().replace('T', ' ').substring(0, 19)} UTC
                    </div>
                </div>
                <div class="recording-actions">
                    <button class="btn btn-small btn-secondary" onclick="rtspClient.downloadRecording('${safeAttrStream}', '${safeAttrFilename}')">
                        💾
                    </button>
                    <button class="btn btn-small btn-danger" onclick="rtspClient.deleteRecording('${safeAttrStream}', '${safeAttrFilename}')">
                        🗑️
                    </button>
                </div>
            </div>
        `}).join('');
        
        container.innerHTML = recordingsHTML;
    }
    
    downloadRecording(stream, filename) {
        // Create a download link (would need server-side support)
        const url = `/api/recordings/${stream}/${filename}`;
        const a = document.createElement('a');
        a.href = url;
        a.download = filename;
        document.body.appendChild(a);
        a.click();
        document.body.removeChild(a);
    }
    
    async deleteRecording(stream, filename) {
        // Confirm deletion with user
        if (!confirm(`Are you sure you want to delete the recording "${filename}"?\n\nThis action cannot be undone.`)) {
            return;
        }
        
        try {
            const response = await fetch(`/api/recordings/${stream}/${filename}`, {
                method: 'DELETE'
            });
            
            if (response.ok) {
                await response.json();
                this.showNotification(`Recording "${filename}" deleted successfully`, 'success');
                // Refresh the recordings list to show the updated list
                this.refreshRecordings();
            } else {
                const error = await response.json();
                this.showNotification(`Failed to delete recording: ${error.error}`, 'error');
            }
        } catch (error) {
            console.error('Error deleting recording:', error);
            this.showNotification('Failed to delete recording', 'error');
        }
    }
    
    escapeHtml(text) {
        const div = document.createElement('div');
        div.textContent = text;
        return div.innerHTML;
    }

    escapeAttr(text) {
        return text.replace(/[&"'<>]/g, c => ({
            '&': '&amp;', '"': '&quot;', "'": '&#39;', '<': '&lt;', '>': '&gt;'
        }[c]));
    }

    formatBytes(bytes) {
        if (bytes === 0) return '0 B';
        const k = 1024;
        const sizes = ['B', 'KB', 'MB', 'GB', 'TB'];
        const i = Math.floor(Math.log(bytes) / Math.log(k));
        return parseFloat((bytes / Math.pow(k, i)).toFixed(2)) + ' ' + sizes[i];
    }
    
    showNotification(message, type = 'info') {
        try {
            const notification = document.createElement('div');
            const safeType = (type && typeof type === 'string') ? type : 'info';
            const safeMessage = (message && typeof message === 'string') ? message : 'Unknown error';

            notification.className = `notification notification-${safeType}`;
            notification.textContent = safeMessage;

            // Stack notifications vertically based on existing ones
            const existing = document.querySelectorAll('.notification');
            const offset = existing.length * 60;

            notification.style.cssText = `
                position: fixed;
                top: ${20 + offset}px;
                right: 20px;
                padding: 15px 20px;
                border-radius: 8px;
                color: white;
                font-weight: 600;
                z-index: 10000;
                max-width: 400px;
                opacity: 0;
                transform: translateX(100%);
                transition: all 0.3s ease;
            `;

            switch (safeType) {
                case 'success':
                    notification.style.backgroundColor = '#10b981';
                    break;
                case 'error':
                    notification.style.backgroundColor = '#ef4444';
                    break;
                case 'warning':
                    notification.style.backgroundColor = '#f59e0b';
                    break;
                default:
                    notification.style.backgroundColor = '#3b82f6';
            }

            document.body.appendChild(notification);

            // Animate in
            setTimeout(() => {
                notification.style.opacity = '1';
                notification.style.transform = 'translateX(0)';
            }, 100);

            // Remove after 5 seconds and reflow remaining notifications
            setTimeout(() => {
                notification.style.opacity = '0';
                notification.style.transform = 'translateX(100%)';
                setTimeout(() => {
                    if (notification.parentNode) {
                        notification.parentNode.removeChild(notification);
                    }
                    // Reflow remaining notifications so they slide up
                    document.querySelectorAll('.notification').forEach((n, i) => {
                        n.style.top = `${20 + i * 60}px`;
                    });
                }, 300);
            }, 5000);
        } catch (notificationError) {
            console.error('Error showing notification:', notificationError);
        }
    }
}

// Copy to clipboard function
function copyToClipboard(elementId) {
    const element = document.getElementById(elementId);
    const text = element.textContent;
    
    navigator.clipboard.writeText(text).then(() => {
        rtspClient.showNotification('Copied to clipboard!', 'success');
    }).catch(err => {
        console.error('Failed to copy: ', err);
        rtspClient.showNotification('Failed to copy to clipboard', 'error');
    });
}

// Initialize the client when the page loads
let rtspClient;
document.addEventListener('DOMContentLoaded', () => {
    console.log('DOMContentLoaded event fired');
    console.log('Document ready state:', document.readyState);
    console.log('streams-container exists:', !!document.getElementById('streams-container'));
    
    try {
        rtspClient = new RTSPRestreamerClient();
        console.log('RTSPRestreamerClient initialized successfully');
    } catch (error) {
        console.error('Error initializing RTSPRestreamerClient:', error);
        console.error('Error stack:', error.stack);
    }
});

// Fallback initialization in case DOMContentLoaded already fired
if (document.readyState === 'loading') {
    console.log('Document still loading, waiting for DOMContentLoaded');
} else {
    console.log('Document already loaded, initializing immediately');
    setTimeout(() => {
        if (!rtspClient) {
            console.log('Fallback initialization triggered');
            try {
                rtspClient = new RTSPRestreamerClient();
                console.log('RTSPRestreamerClient initialized via fallback');
            } catch (error) {
                console.error('Error in fallback initialization:', error);
            }
        }
    }, 100);
}
