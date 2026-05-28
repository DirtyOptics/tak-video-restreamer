"""
This material is based upon work supported by the United States Air Force under contract number FA8750-24-S-B079 (Prime Contractor Smart Information Flow Technologies (SIFT)).  Any opinions, findings and conclusions or recommendations expressed in this material are those of the author(s) and do not necessarily reflect the views of the United States Air Force.
 Copyright (c) 2026 RTX BBN Technologies. Licensed to US Government with unlimited rights.

This program is free software: you can redistribute it and/or modify it under the terms of the GNU General Public License as published by the Free Software Foundation, either version 3 of the License, or (at your option) any later version.
This is distributed in the hope that it will be useful, but without any warranty, without even the implied warranty of merchantability or fitness for a particular purpose.  See the GNU General Public License for more details. https://www.gnu.org/licenses/

Auto-cleanup service for old recordings and disk space management.
Controlled by server_settings: auto_cleanup_enabled, cleanup_days, min_free_space_gb.
"""
import os
import shutil
import threading
import time
import logging
from pathlib import Path

from app.config import STREAMS_DIR

logger = logging.getLogger(__name__)

# Check interval: every 5 minutes
CLEANUP_INTERVAL = 300

_VIDEO_EXTENSIONS = {'.mov', '.mp4', '.ts', '.mxf', '.mpg', '.mpeg', '.mkv'}


def _is_recording_file(path: Path) -> bool:
    """Check if a file is a recording (by extension)."""
    return path.suffix.lower() in _VIDEO_EXTENSIONS


def _cleanup_old_files(max_age_days: int):
    """Delete recording files older than max_age_days."""
    cutoff = time.time() - (max_age_days * 86400)
    streams_path = Path(STREAMS_DIR)
    if not streams_path.is_dir():
        return

    deleted = 0
    for file in streams_path.rglob('*'):
        if not file.is_file() or not _is_recording_file(file):
            continue
        try:
            if file.stat().st_mtime < cutoff:
                file.unlink()
                deleted += 1
                logger.info(f"Auto-cleanup: deleted old recording {file.name}")
        except OSError as e:
            logger.warning(f"Auto-cleanup: could not delete {file}: {e}")

    if deleted:
        logger.info(f"Auto-cleanup: removed {deleted} file(s) older than {max_age_days} days")


def _cleanup_for_space(min_free_gb: int):
    """Delete oldest recording files until min_free_gb is available."""
    streams_path = Path(STREAMS_DIR)
    if not streams_path.is_dir():
        return

    try:
        usage = shutil.disk_usage(STREAMS_DIR)
        free_gb = usage.free / (1024 ** 3)
    except OSError:
        return

    if free_gb >= min_free_gb:
        return

    # Collect all recording files sorted by mtime (oldest first)
    files = []
    for f in streams_path.rglob('*'):
        if f.is_file() and _is_recording_file(f):
            try:
                files.append((f.stat().st_mtime, f.stat().st_size, f))
            except OSError:
                continue
    files.sort()

    deleted = 0
    for mtime, size, file in files:
        try:
            usage = shutil.disk_usage(STREAMS_DIR)
            if usage.free / (1024 ** 3) >= min_free_gb:
                break
        except OSError:
            break

        try:
            file.unlink()
            deleted += 1
            logger.info(f"Auto-cleanup (space): deleted {file.name} ({size / (1024**3):.2f}GB)")
        except OSError as e:
            logger.warning(f"Auto-cleanup (space): could not delete {file}: {e}")

    if deleted:
        logger.info(f"Auto-cleanup: freed space by removing {deleted} file(s)")


def cleanup_loop():
    """Background loop that runs cleanup checks periodically."""
    logger.info("Auto-cleanup service started")
    while True:
        time.sleep(CLEANUP_INTERVAL)
        try:
            from app.api.settings import server_settings
            if not server_settings.get('auto_cleanup_enabled', False):
                continue

            cleanup_days = server_settings.get('cleanup_days', 30)
            min_free_gb = server_settings.get('min_free_space_gb', 10)

            _cleanup_old_files(cleanup_days)
            _cleanup_for_space(min_free_gb)
        except Exception as e:
            logger.error(f"Auto-cleanup error: {e}")


def start_cleanup_service():
    """Start the cleanup background thread."""
    t = threading.Thread(target=cleanup_loop, daemon=True, name='auto-cleanup')
    t.start()
    return t
