"""
This material is based upon work supported by the United States Air Force under contract number FA8750-24-S-B079 (Prime Contractor Smart Information Flow Technologies (SIFT)).  Any opinions, findings and conclusions or recommendations expressed in this material are those of the author(s) and do not necessarily reflect the views of the United States Air Force.
 Copyright (c) 2026 RTX BBN Technologies. Licensed to US Government with unlimited rights.

This program is free software: you can redistribute it and/or modify it under the terms of the GNU General Public License as published by the Free Software Foundation, either version 3 of the License, or (at your option) any later version.
This is distributed in the hope that it will be useful, but without any warranty, without even the implied warranty of merchantability or fitness for a particular purpose.  See the GNU General Public License for more details. https://www.gnu.org/licenses/

Thumbnail generation utilities
"""
import subprocess
import logging
from pathlib import Path

logger = logging.getLogger(__name__)


def generate_thumbnail(video_path: str, stream_name: str) -> str:
    """Generate thumbnail for recorded video"""
    try:
        thumbnail_path = video_path.rsplit('.', 1)[0] + '_thumb.jpg'
        
        subprocess.run([
            'ffmpeg',
            '-i', video_path,
            '-ss', '00:00:01',
            '-vframes', '1',
            '-q:v', '2',
            '-y',
            thumbnail_path
        ], capture_output=True, timeout=10)
        
        logger.info(f"Generated thumbnail: {thumbnail_path}")
        return thumbnail_path
        
    except Exception as e:
        logger.error(f"Error generating thumbnail: {e}")
        return None
