"""
This material is based upon work supported by the United States Air Force under contract number FA8750-24-S-B079 (Prime Contractor Smart Information Flow Technologies (SIFT)).  Any opinions, findings and conclusions or recommendations expressed in this material are those of the author(s) and do not necessarily reflect the views of the United States Air Force.
 Copyright (c) 2026 RTX BBN Technologies. Licensed to US Government with unlimited rights.

This program is free software: you can redistribute it and/or modify it under the terms of the GNU General Public License as published by the Free Software Foundation, either version 3 of the License, or (at your option) any later version.
This is distributed in the hope that it will be useful, but without any warranty, without even the implied warranty of merchantability or fitness for a particular purpose.  See the GNU General Public License for more details. https://www.gnu.org/licenses/

Utils API Blueprint - Utility endpoints (transcode, test patterns, etc.)
"""
from flask import Blueprint, request, jsonify
from pathlib import Path
import subprocess
import threading
import time
import os
import sys
import re
import logging

from app.config import KLV_AVAILABLE, STREAMS_DIR
from app.state import active_transcodes
from app.websocket.broadcast import broadcast

logger = logging.getLogger(__name__)

utils_bp = Blueprint('utils', __name__)


@utils_bp.route('/api/transcode/options', methods=['GET'])
def get_transcode_options():
    """
    Get available transcode options
    """
    options = [
        {
            "option": 1,
            "name": "MOV with corrected timecode",
            "description": "QuickTime MOV with H.264 video and corrected timecode track",
            "format": "mov",
            "klv": False
        },
        {
            "option": 2,
            "name": "MP4 + KLV backup",
            "description": "MP4 with H.264 video and separate binary KLV file",
            "format": "mp4",
            "klv": True,
            "klvFormat": "separate_file"
        },
        {
            "option": 3,
            "name": "MXF + KLV backup",
            "description": "Broadcast MXF with MPEG-2 video and separate binary KLV file",
            "format": "mxf",
            "klv": True,
            "klvFormat": "separate_file"
        }
    ]

    return jsonify({
        "options": options,
        "default": 1
    })


@utils_bp.route('/api/transcode', methods=['POST'])
def start_transcode():
    """
    Start transcoding a video file
    
    Request body:
        {
            "inputFile": "/path/to/video.mov",
            "option": 1-3,
            "streamName": "optional"
        }
    """
    try:
        data = request.get_json()
        if not data:
            return jsonify({'error': 'Request body required'}), 400

        input_file = data.get('inputFile')
        option = data.get('option', 1)  # Default to MOV with corrected timecode
        stream_name = data.get('streamName', 'transcoded')
        
        if not input_file:
            return jsonify({'error': 'inputFile required'}), 400
        
        # Resolve relative paths from STREAMS_DIR
        if not os.path.isabs(input_file):
            if '..' in input_file or '\x00' in input_file:
                return jsonify({'error': 'Invalid file path'}), 400
            input_file = os.path.join(STREAMS_DIR, input_file)
            resolved = Path(input_file).resolve()
            if not resolved.is_relative_to(Path(STREAMS_DIR).resolve()):
                return jsonify({'error': 'Access denied'}), 403
        
        if not os.path.exists(input_file):
            return jsonify({'error': 'Input file not found'}), 404
        
        if option not in [1, 2, 3]:
            return jsonify({'error': 'Invalid option (must be 1-3)'}), 400

        if option in [2, 3] and not KLV_AVAILABLE:
            return jsonify({'error': 'KLV module not available'}), 400

        # Generate unique transcode ID
        import random
        transcode_id = f"transcode_{int(time.time())}_{random.randint(1000, 9999)}"

        # Determine output file extension based on option
        output_extensions = {1: '.mov', 2: '.mp4', 3: '.mxf'}
        expected_ext = output_extensions.get(option, '.mov')
        base_name = os.path.splitext(input_file)[0]
        expected_output = f"{base_name}_transcoded{expected_ext}"
        
        # Store transcode job info
        active_transcodes[transcode_id] = {
            'id': transcode_id,
            'inputFile': input_file,
            'option': option,
            'streamName': stream_name,
            'status': 'starting',
            'progress': 0,
            'startTime': time.time(),
            'expectedOutput': expected_output,
            'error': None
        }
        
        # Run transcode in background thread
        def transcode_worker():
            try:
                active_transcodes[transcode_id]['status'] = 'running'
                logger.info(f"Starting transcode [{transcode_id}]: {input_file} (option {option})")
                
                # Get path to transcode script (cross-platform)
                script_dir = Path(__file__).parent.parent.parent / 'utils'
                transcode_script = script_dir / 'transcode_video.py'
                
                # Build command
                cmd = [
                    sys.executable,  # Use current Python interpreter
                    str(transcode_script),
                    input_file
                ]
                
                # Run with option selection via stdin
                process = subprocess.Popen(
                    cmd,
                    stdin=subprocess.PIPE,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    text=True,
                    bufsize=1  # Line buffered
                )
                
                active_transcodes[transcode_id]['pid'] = process.pid
                
                # Send option selection to stdin and close it
                process.stdin.write(str(option) + '\n')
                process.stdin.close()
                
                # Read stdout line by line to parse progress
                stdout_lines = []
                stderr_lines = []
                
                # Read both stdout and stderr
                while True:
                    # Check if process is still running
                    if process.poll() is not None:
                        # Process finished, read remaining output
                        remaining_stdout = process.stdout.read()
                        remaining_stderr = process.stderr.read()
                        if remaining_stdout:
                            stdout_lines.append(remaining_stdout)
                        if remaining_stderr:
                            stderr_lines.append(remaining_stderr)
                        break
                    
                    # Read stdout line
                    line = process.stdout.readline()
                    if line:
                        stdout_lines.append(line)
                        
                        # Parse progress from lines like: "  [████████░░░░] 45% (12.5/28.0s)"
                        if '%' in line and '[' in line:
                            try:
                                # Extract percentage
                                percent_match = re.search(r'(\d+)%', line)
                                if percent_match:
                                    progress = int(percent_match.group(1))
                                    active_transcodes[transcode_id]['progress'] = progress
                                    logger.debug(f"Transcode [{transcode_id}] progress: {progress}%")
                            except (ValueError, KeyError):
                                pass
                    
                    # Small delay to avoid busy waiting
                    time.sleep(0.1)
                
                stderr = ''.join(stderr_lines)
                
                duration = time.time() - active_transcodes[transcode_id]['startTime']
                
                if process.returncode == 0:
                    logger.info(f"Transcode completed [{transcode_id}]: {input_file} ({duration:.1f}s)")
                    active_transcodes[transcode_id]['status'] = 'completed'
                    active_transcodes[transcode_id]['progress'] = 100
                    active_transcodes[transcode_id]['completedTime'] = time.time()
                    active_transcodes[transcode_id]['duration'] = duration
                    
                    # Try to find actual output file
                    if os.path.exists(expected_output):
                        active_transcodes[transcode_id]['outputFile'] = expected_output
                        active_transcodes[transcode_id]['outputSize'] = os.path.getsize(expected_output)
                    
                    broadcast('transcode_complete', {
                        'id': transcode_id,
                        'inputFile': input_file,
                        'outputFile': expected_output,
                        'option': option,
                        'success': True,
                        'duration': duration
                    })
                else:
                    logger.error(f"Transcode failed [{transcode_id}]: {stderr}")
                    active_transcodes[transcode_id]['status'] = 'failed'
                    active_transcodes[transcode_id]['error'] = stderr[-500:] if stderr else 'Unknown error'
                    active_transcodes[transcode_id]['completedTime'] = time.time()
                    active_transcodes[transcode_id]['duration'] = duration
                    
                    broadcast('transcode_complete', {
                        'id': transcode_id,
                        'inputFile': input_file,
                        'option': option,
                        'success': False,
                        'error': stderr[-500:] if stderr else 'Unknown error'
                    })
                    
            except subprocess.TimeoutExpired:
                error_msg = 'Transcode timeout after 10 minutes'
                logger.error(f"Transcode timeout [{transcode_id}]")
                active_transcodes[transcode_id]['status'] = 'failed'
                active_transcodes[transcode_id]['error'] = error_msg
                active_transcodes[transcode_id]['completedTime'] = time.time()
                broadcast('transcode_complete', {
                    'id': transcode_id,
                    'inputFile': input_file,
                    'option': option,
                    'success': False,
                    'error': error_msg
                })
            except Exception as e:
                logger.error(f"Transcode error [{transcode_id}]: {e}")
                active_transcodes[transcode_id]['status'] = 'failed'
                active_transcodes[transcode_id]['error'] = str(e)
                active_transcodes[transcode_id]['completedTime'] = time.time()
                broadcast('transcode_complete', {
                    'id': transcode_id,
                    'inputFile': input_file,
                    'option': option,
                    'success': False,
                    'error': str(e)
                })
        
        threading.Thread(target=transcode_worker, daemon=True).start()
        
        return jsonify({
            'success': True,
            'message': 'Transcode started',
            'transcodeId': transcode_id,
            'inputFile': input_file,
            'option': option,
            'expectedOutput': expected_output
        })
        
    except Exception as e:
        logger.error(f"Error starting transcode: {e}")
        return jsonify({'error': 'Failed to start transcode. Please check the input file.'}), 500


@utils_bp.route('/api/transcode/<transcode_id>/status', methods=['GET'])
def get_transcode_status(transcode_id):
    """Get status of a specific transcode job"""
    try:
        if transcode_id not in active_transcodes:
            return jsonify({'error': 'Transcode job not found'}), 404
        
        job = active_transcodes[transcode_id]
        
        # Calculate elapsed time - use stored duration for completed/failed jobs
        if job['status'] in ['completed', 'failed']:
            elapsed = job.get('duration', time.time() - job['startTime'])
        else:
            elapsed = time.time() - job['startTime']
        
        response = {
            'id': job['id'],
            'inputFile': job['inputFile'],
            'option': job['option'],
            'streamName': job.get('streamName'),
            'status': job['status'],
            'progress': job.get('progress', 0),
            'startTime': job['startTime'],
            'elapsed': elapsed,
            'expectedOutput': job.get('expectedOutput'),
            'error': job.get('error')
        }
        
        # Add completion info if available
        if job['status'] in ['completed', 'failed']:
            response['completedTime'] = job.get('completedTime')
            response['duration'] = job.get('duration', elapsed)
            
        # Add output file info if available
        if job.get('outputFile'):
            response['outputFile'] = job['outputFile']
            response['outputSize'] = job.get('outputSize')
        
        return jsonify(response)
        
    except Exception as e:
        logger.error(f"Error listing transcode jobs: {e}")
        return jsonify({'error': 'Failed to list transcode jobs'}), 500


@utils_bp.route('/api/transcode/status', methods=['GET'])
def get_all_transcode_status():
    """Get status of all transcode jobs"""
    try:
        active = []
        completed = []
        failed = []
        
        for transcode_id, job in active_transcodes.items():
            # Use stored duration for completed/failed jobs, otherwise calculate current elapsed
            if job['status'] in ['completed', 'failed']:
                elapsed = job.get('duration', time.time() - job['startTime'])
            else:
                elapsed = time.time() - job['startTime']
            
            job_info = {
                'id': job['id'],
                'inputFile': Path(job['inputFile']).name,
                'option': job['option'],
                'status': job['status'],
                'progress': job.get('progress', 0),
                'startTime': job['startTime'],
                'elapsed': elapsed
            }
            
            if job['status'] == 'completed':
                job_info['duration'] = job.get('duration', elapsed)
                job_info['outputFile'] = Path(job.get('outputFile', '')).name if job.get('outputFile') else None
                job_info['outputSize'] = job.get('outputSize')
                completed.append(job_info)
            elif job['status'] == 'failed':
                job_info['duration'] = job.get('duration', elapsed)
                job_info['error'] = job.get('error')
                failed.append(job_info)
            else:
                active.append(job_info)
        
        return jsonify({
            'active': active,
            'completed': completed,
            'failed': failed,
            'total': len(active_transcodes)
        })
        
    except Exception as e:
        logger.error(f"Error getting all transcode status: {e}")
        return jsonify({'error': str(e)}), 500


@utils_bp.route('/api/transcode/<transcode_id>', methods=['DELETE'])
def cancel_transcode(transcode_id):
    """Cancel a running transcode job"""
    try:
        if transcode_id not in active_transcodes:
            return jsonify({'error': 'Transcode job not found'}), 404
        
        job = active_transcodes[transcode_id]
        
        if job['status'] not in ['starting', 'running']:
            return jsonify({'error': f'Cannot cancel transcode with status: {job["status"]}'}), 400
        
        # Try to kill the process
        pid = job.get('pid')
        if pid:
            try:
                import signal
                os.kill(pid, signal.SIGTERM)
                logger.info(f"Sent SIGTERM to transcode process [{transcode_id}] pid={pid}")
            except ProcessLookupError:
                logger.warning(f"Process {pid} not found for transcode [{transcode_id}]")
            except Exception as e:
                logger.error(f"Error killing transcode process: {e}")
        
        job['status'] = 'cancelled'
        job['completedTime'] = time.time()
        job['duration'] = time.time() - job['startTime']
        
        broadcast('transcode_cancelled', {'id': transcode_id})
        
        return jsonify({
            'success': True,
            'message': f'Transcode {transcode_id} cancelled'
        })
        
    except Exception as e:
        logger.error(f"Error cancelling transcode: {e}")
        return jsonify({'error': str(e)}), 500


_VALIDATE_STREAM_NAME_RE = re.compile(r'^[a-zA-Z0-9_-]+$')

# utils/ holds the standalone CLI tools; put it on the path once so the stream
# validator can be imported by name from the request handler below.
_UTILS_DIR = str(Path(__file__).resolve().parent.parent.parent / 'utils')
if _UTILS_DIR not in sys.path:
    sys.path.insert(0, _UTILS_DIR)


@utils_bp.route('/api/stream/validate', methods=['POST'])
def validate_stream():
    """
    Preflight-validate a live stream or recording for KLV and TAK-client compatibility.

    Request body (exactly one of):
        {"streamName": "drone1"}              // validates rtsp://localhost:8554/drone1
        {"videoFile": "drone1/recording.ts"}  // relative to STREAMS_DIR, or absolute

    Optional:
        {"window": 10}  // seconds of stream to sample (1-60)

    Response: {"success": true, "report": {...}} where report.checks[] each carry
    a status of pass/warn/fail/skip plus a remediation hint.
    """
    try:
        data = request.get_json() or {}
        stream_name = data.get('streamName')
        video_file = data.get('videoFile')
        window = data.get('window', 10)

        if bool(stream_name) == bool(video_file):
            return jsonify({'error': 'Provide exactly one of streamName or videoFile'}), 400

        if not isinstance(window, int) or not 1 <= window <= 60:
            return jsonify({'error': 'window must be an integer between 1 and 60'}), 400

        if stream_name:
            if len(stream_name) > 64 or not _VALIDATE_STREAM_NAME_RE.match(stream_name):
                return jsonify({'error': 'Invalid stream name'}), 400
            target = f'rtsp://localhost:8554/{stream_name}'
        else:
            target = video_file
            # Same containment rules as the transcode endpoint.
            if not os.path.isabs(target):
                if '..' in target or '\x00' in target:
                    return jsonify({'error': 'Invalid file path'}), 400
                target = os.path.join(STREAMS_DIR, target)
                resolved = Path(target).resolve()
                if not resolved.is_relative_to(Path(STREAMS_DIR).resolve()):
                    return jsonify({'error': 'Access denied'}), 403
            if not os.path.exists(target):
                return jsonify({'error': 'Video file not found'}), 404

        # Imported per-request so a missing/broken validator degrades to a 500 on
        # this endpoint alone rather than breaking app startup. sys.path is set up
        # once at module import (see _UTILS_DIR above).
        from validate_stream import validate

        report = validate(target, window=window, timeout=max(30, window * 4))

        # Don't leak absolute server paths back to the browser.
        if video_file:
            report['target'] = video_file

        return jsonify({'success': True, 'report': report})

    except ImportError as e:
        logger.error(f"Stream validator unavailable: {e}")
        return jsonify({'error': 'Stream validator unavailable'}), 500
    except Exception as e:
        logger.error(f"Error validating stream: {e}")
        return jsonify({'error': 'An error occurred during stream validation.'}), 500
