"""
This material is based upon work supported by the United States Air Force under contract number FA8750-24-S-B079 (Prime Contractor Smart Information Flow Technologies (SIFT)).  Any opinions, findings and conclusions or recommendations expressed in this material are those of the author(s) and do not necessarily reflect the views of the United States Air Force.
 Copyright (c) 2026 RTX BBN Technologies. Licensed to US Government with unlimited rights.

This program is free software: you can redistribute it and/or modify it under the terms of the GNU General Public License as published by the Free Software Foundation, either version 3 of the License, or (at your option) any later version.
This is distributed in the hope that it will be useful, but without any warranty, without even the implied warranty of merchantability or fitness for a particular purpose.  See the GNU General Public License for more details. https://www.gnu.org/licenses/

WebSocket broadcast utilities
"""
import logging

logger = logging.getLogger(__name__)

# Will be set by __init__.py
socketio = None


def set_socketio(sio):
    """Set the socketio instance"""
    global socketio
    socketio = sio


def broadcast(event_type: str, data: dict):
    """Broadcast event to all WebSocket clients"""
    if not socketio:
        logger.warning("SocketIO not initialized, cannot broadcast")
        return
        
    try:
        socketio.emit('message', {'type': event_type, 'data': data})
    except Exception as e:
        logger.error(f"Error broadcasting message: {e}")
