"""
This material is based upon work supported by the United States Air Force under contract number FA8750-24-S-B079 (Prime Contractor Smart Information Flow Technologies (SIFT)).  Any opinions, findings and conclusions or recommendations expressed in this material are those of the author(s) and do not necessarily reflect the views of the United States Air Force.
 Copyright (c) 2026 RTX BBN Technologies. Licensed to US Government with unlimited rights.

This program is free software: you can redistribute it and/or modify it under the terms of the GNU General Public License as published by the Free Software Foundation, either version 3 of the License, or (at your option) any later version.
This is distributed in the hope that it will be useful, but without any warranty, without even the implied warranty of merchantability or fitness for a particular purpose.  See the GNU General Public License for more details. https://www.gnu.org/licenses/

Application state management
Global state that needs to be shared across modules
"""
import threading
from typing import Dict
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone

# System start time
system_start_time = datetime.now(timezone.utc)

# Global state dictionaries
active_recordings: Dict[str, dict] = {}
active_pull_streams: dict = {}
pull_stream_configs: Dict[str, dict] = {}
post_processing_queue: Dict[str, dict] = {}
active_transcodes: Dict[str, dict] = {}

# Locks
recording_lock = threading.Lock()
pull_stream_lock = threading.Lock()
hidden_streams_lock = threading.Lock()

# Flags
auto_record_enabled = False

# Stream tracking
known_streams: Dict[str, bool] = {}

# Streams hidden after deletion (regex-matched phantom paths in MediaMTX)
# Auto-unhidden when a publisher reconnects
hidden_streams: set = set()

# Thread pools
thumbnail_executor = ThreadPoolExecutor(max_workers=3, thread_name_prefix="thumbnail")
post_process_executor = ThreadPoolExecutor(max_workers=2, thread_name_prefix="postproc")


# SRT buffer manager singleton
_srt_buffer_manager = None

def get_srt_buffer_manager():
    """Get the SRT buffer manager singleton (lazy import to avoid circular deps)."""
    global _srt_buffer_manager
    try:
        from shared.srt_buffer import SRTBufferManager
        if _srt_buffer_manager is None:
            _srt_buffer_manager = SRTBufferManager()
        return _srt_buffer_manager
    except ImportError:
        return None
