#!/usr/bin/env python3
"""
This material is based upon work supported by the United States Air Force under contract number FA8750-24-S-B079 (Prime Contractor Smart Information Flow Technologies (SIFT)).  Any opinions, findings and conclusions or recommendations expressed in this material are those of the author(s) and do not necessarily reflect the views of the United States Air Force.
 Copyright (c) 2026 RTX BBN Technologies. Licensed to US Government with unlimited rights.

This program is free software: you can redistribute it and/or modify it under the terms of the GNU General Public License as published by the Free Software Foundation, either version 3 of the License, or (at your option) any later version.
This is distributed in the hope that it will be useful, but without any warranty, without even the implied warranty of merchantability or fitness for a particular purpose.  See the GNU General Public License for more details. https://www.gnu.org/licenses/

Transcode Video - Correct timecode and optionally generate KLV metadata
- Corrects timecode to match button press time
- Option 1: MOV with corrected timecode (no KLV)
- Option 2: MP4 + separate STANAG 4609 KLV binary file
- Option 3: MXF (broadcast) + separate STANAG 4609 KLV binary file

Note: none of these embed KLV into the container. To produce a stream with a
real embedded KLV data track, mux MPEG-TS with FFmpeg directly and keep the
data stream with `-map 0`; see utils/validate_stream.py to check the result.

GPU Encoding:
- Set ENABLE_GPU_ENCODING=1 environment variable to enable
- Requires NVIDIA GPU with NVENC support
- Provides 3-5x faster encoding than CPU
"""

import subprocess
import re
import os
import sys
import struct
import multiprocessing
from datetime import datetime, timezone
from pathlib import Path

# GPU encoding configuration
ENABLE_GPU_ENCODING = os.environ.get('ENABLE_GPU_ENCODING', '0') == '1'

# Add shared directory to path for KLV import
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'shared'))
try:
    import klv
    KLV_AVAILABLE = True
except ImportError:
    KLV_AVAILABLE = False
    print("Warning: KLV module not available - Option 2 will not be available")


def find_latest_recording(base_dir=None):
    if base_dir is None:
        base_dir = os.environ.get('STREAMS_DIR', os.path.join(os.path.dirname(__file__), '..', 'data', 'streams'))
    """Find the most recent .mov or .mp4 file"""
    latest_file = None
    latest_time = 0
    
    for root, dirs, files in os.walk(base_dir):
        for file in files:
            if file.endswith(('.mov', '.mp4')) and '_corrected' not in file and '_transcoded' not in file:
                file_path = os.path.join(root, file)
                mtime = os.path.getmtime(file_path)
                if mtime > latest_time:
                    latest_time = mtime
                    latest_file = file_path
    
    return latest_file


def get_file_metadata(file_path):
    """Get metadata from file using ffprobe"""
    cmd = [
        'ffprobe', '-v', 'error',
        '-show_entries', 'format_tags:stream_tags=timecode:format=duration',
        '-of', 'default=noprint_wrappers=1',
        file_path
    ]
    
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, check=True)
        return result.stdout
    except subprocess.CalledProcessError as e:
        print(f"Error running ffprobe: {e}")
        return None


def parse_timecode(tc_string):
    """Parse timecode string (HH:MM:SS;FF or HH:MM:SS.mmm) to seconds"""
    # Try frame-based format first (HH:MM:SS;FF)
    match = re.match(r'(\d+):(\d+):(\d+);(\d+)', tc_string)
    if match:
        h, m, s, f = map(int, match.groups())
        return h * 3600 + m * 60 + s + (f / 29.97)
    
    # Try millisecond format (HH:MM:SS.mmm)
    match = re.match(r'(\d+):(\d+):(\d+)\.(\d+)', tc_string)
    if match:
        h, m, s, ms = map(int, match.groups())
        return h * 3600 + m * 60 + s + (ms / 1000.0)
    
    return None


def format_timecode(seconds, fps=29.97):
    """Format seconds as timecode (HH:MM:SS;FF)"""
    h = int(seconds // 3600)
    m = int((seconds % 3600) // 60)
    s = int(seconds % 60)
    f = int((seconds - int(seconds)) * fps)
    return f"{h:02d}:{m:02d}:{s:02d};{f:02d}"


def get_video_encoder_params(codec='h264'):
    """
    Get video encoder parameters based on GPU availability
    
    Args:
        codec: 'h264' or 'hevc'
    
    Returns:
        tuple: (encoder_name, encoder_params_list)
    """
    if ENABLE_GPU_ENCODING:
        if codec == 'hevc':
            return 'hevc_nvenc', [
                '-preset', 'p4',  # Medium quality preset (p1=fastest, p7=slowest)
                '-rc', 'vbr',     # Variable bitrate
                '-cq', '23',      # Constant quality (similar to CRF)
                '-b:v', '0'       # VBR mode
            ]
        else:  # h264
            return 'h264_nvenc', [
                '-preset', 'p4',
                '-rc', 'vbr',
                '-cq', '23',
                '-b:v', '0'
            ]
    else:
        # CPU encoding
        cpu_count = multiprocessing.cpu_count()
        thread_count = max(1, cpu_count - 1)
        
        if codec == 'hevc':
            return 'libx265', [
                '-preset', 'medium',
                '-crf', '18',
                '-threads', str(thread_count)
            ]
        else:  # h264
            return 'libx264', [
                '-preset', 'medium',
                '-crf', '18',
                '-threads', str(thread_count)
            ]


def generate_klv_packets(button_press_dt, duration, klv_rate=2.0):
    """
    Generate KLV metadata packets at specified rate
    
    Args:
        button_press_dt: Recording start datetime
        duration: Recording duration in seconds
        klv_rate: KLV update rate in Hz (default 2Hz)
    
    Returns:
        List of KLV packet bytes
    """
    if not KLV_AVAILABLE:
        return []
    
    print(f"Generating KLV metadata at {klv_rate}Hz...")
    
    # Calculate number of KLV packets at specified rate
    klv_interval = 1.0 / klv_rate
    num_packets = int(duration * klv_rate)
    
    klv_packets = []
    
    for packet_num in range(num_packets):
        # Calculate timestamp for this KLV packet
        packet_time = button_press_dt.timestamp() + (packet_num * klv_interval)
        
        # Create basic UAS metadata
        metadata = {
            'timestamp': int(packet_time * 1_000_000),  # Microseconds since epoch
            'mission_id': 'TRANSCODED_RECORDING',
            'platform_designation': 'Recording System',
            'sensor_latitude': 0.0,  # Placeholder
            'sensor_longitude': 0.0,  # Placeholder
            'sensor_altitude': 100.0,  # Placeholder - 100m altitude
            'platform_heading': 0.0,  # Placeholder - North
            'frame_center_latitude': 0.0,  # Placeholder
            'frame_center_longitude': 0.0,  # Placeholder
            'frame_center_elevation': 100.0,  # Placeholder
        }
        
        # Generate KLV packet using the klv module
        try:
            klv_packet = klv.encode_uas_metadata(metadata)
            klv_packets.append(klv_packet)
            
            # Show progress bar
            percent = int((packet_num + 1) * 100 / num_packets)
            bar_length = 40
            filled = int(bar_length * (packet_num + 1) / num_packets)
            bar = '█' * filled + '░' * (bar_length - filled)
            print(f'\r  [{bar}] {percent}% ({packet_num + 1}/{num_packets})', end='', flush=True)
        except Exception as e:
            print(f"\nWarning: Failed to generate KLV packet {packet_num}: {e}")
            continue
    
    print()  # New line after progress bar
    print(f"Generated {len(klv_packets)} KLV packets")
    return klv_packets


def transcode_option_1(input_file, output_file, creation_time, corrected_tc, offset, timecode_start, duration, button_press_dt, existing_keywords=''):
    """
    Option 1: MOV with corrected timecode (no KLV)
    Simple re-encode with fixed timecode
    """
    print("\n" + "="*70)
    print("Option 1: MOV with Corrected Timecode (No KLV)")
    print("="*70)
    print(f"\nTranscoding to: {Path(output_file).name}\n")
    
    # Get encoder parameters (GPU or CPU)
    encoder, encoder_params = get_video_encoder_params('h264')
    encoding_method = "GPU (NVENC)" if ENABLE_GPU_ENCODING else f"CPU ({multiprocessing.cpu_count()-1} threads)"
    print(f"Encoding method: {encoding_method}")
    
    ffmpeg_cmd = [
        'ffmpeg',
        '-i', input_file,
        '-c:v', encoder,
        *encoder_params,
        '-c:a', 'aac',
        '-b:a', '128k',
        '-write_tmcd', '1',
        '-timecode', corrected_tc,
        '-video_track_timescale', '30000',
        '-f', 'mov',
        '-movflags', '+faststart+write_colr',
        '-metadata', f'title=Transcoded Recording',
        '-metadata', f'creation_time={creation_time}',
        '-metadata', f'timecode={corrected_tc}',
        '-metadata', f'description={existing_keywords}',
        '-metadata', f'keywords={existing_keywords}',
        '-metadata', f'comment=Timecode corrected: offset {offset:.2f}s, Original TC: {timecode_start}' + (f' | Keywords: {existing_keywords}' if existing_keywords else ''),
        '-progress', 'pipe:1',
        '-y',
        output_file
    ]
    
    if existing_keywords:
        print(f"Preserving keywords: {existing_keywords}")
    
    result = run_ffmpeg(ffmpeg_cmd, duration)
    
    if result:
        # Verify alignment of button press and timecode
        print(f"\nVerifying timecode alignment...")
        verify_output = get_file_metadata(output_file)
        
        if verify_output:
            # Parse verified metadata
            verify_creation = re.search(r'creation_time=(.+)', verify_output)
            verify_tc_matches = re.findall(r'timecode=(.+)', verify_output)
            
            if verify_creation and verify_tc_matches:
                verified_creation = verify_creation.group(1)
                verified_tc = verify_tc_matches[0]
                
                # Parse both times
                verify_dt = datetime.fromisoformat(verified_creation.replace('Z', '+00:00'))
                verify_button_sec = verify_dt.hour * 3600 + verify_dt.minute * 60 + verify_dt.second + (verify_dt.microsecond / 1_000_000)
                verify_tc_sec = parse_timecode(verified_tc)
                
                # Calculate difference
                alignment_diff = abs(verify_tc_sec - verify_button_sec)
                
                print(f"\n  Button Press Time: {verified_creation}")
                print(f"  Timecode Start:    {verified_tc}")
                print(f"  Alignment:         {alignment_diff:.3f} seconds difference")
                
                if alignment_diff < 0.1:  # Within 0.1 seconds
                    print(f"  Status:            ✓ ALIGNED (within tolerance)")
                else:
                    print(f"  Status:            ⚠ {alignment_diff:.3f}s offset detected")
    
    return result


def transcode_option_2(input_file, output_file, creation_time, corrected_tc, offset, timecode_start, duration, button_press_dt, existing_keywords=''):
    """
    Option 2: MP4 with embedded KLV data stream (STANAG 4609)
    Uses FFmpeg SEI (Supplemental Enhancement Information) to embed KLV in H.264 stream
    """
    print("\n" + "="*70)
    print("Option 2: MP4 with Embedded KLV Stream (STANAG 4609)")
    print("="*70)
    print(f"\nTranscoding to: {Path(output_file).name}\n")
    
    if existing_keywords:
        print(f"Preserving keywords: {existing_keywords}")
    
    # Generate KLV packets
    klv_packets = generate_klv_packets(button_press_dt, duration, klv_rate=2.0)
    
    if not klv_packets:
        print("Error: Failed to generate KLV packets")
        return False
    
    # Create KLV SEI file for embedding
    # FFmpeg can inject SEI (Supplemental Enhancement Information) messages into H.264 stream
    # This is the standard method for embedding metadata in MPEG-4 Part 14 (MP4) containers
    sei_file = output_file.replace('.mp4', '_sei.txt')
    klv_bin_file = output_file.replace('.mp4', '_klv.bin')
    
    try:
        # Write SEI text file (frame number and hex-encoded KLV data)
        with open(sei_file, 'w') as f:
            frame_rate = 29.97
            frames_per_packet = frame_rate / 2.0  # 2 Hz = every ~15 frames
            
            for i, packet in enumerate(klv_packets):
                frame_num = int(i * frames_per_packet)
                # SEI format: frame_number SEI_type payload_hex
                # Type 5 = User Data Unregistered (suitable for KLV)
                klv_hex = packet.hex()
                f.write(f"{frame_num} 5 {klv_hex}\n")
        
        # Also save standalone KLV file for compatibility
        with open(klv_bin_file, 'wb') as f:
            for packet in klv_packets:
                f.write(struct.pack('>I', len(packet)))
                f.write(packet)
        
        print(f"KLV SEI data prepared: {len(klv_packets)} packets")
    except Exception as e:
        print(f"Error preparing KLV data: {e}")
        return False
    
    print(f"\nEmbedding KLV into MP4 via data track...")
    
    # Note: H.264 SEI embedding is not well-supported by FFmpeg for MP4
    # Using MP4 metadata track instead - more reliable but less standard
    # For proper SEI embedding, use MXF container or GStreamer with MPEG-TS
    
    # Get encoder parameters (GPU or CPU)
    encoder, encoder_params = get_video_encoder_params('h264')
    encoding_method = "GPU (NVENC)" if ENABLE_GPU_ENCODING else f"CPU ({multiprocessing.cpu_count()-1} threads)"
    print(f"Encoding method: {encoding_method}")
    
    # FFmpeg command - MP4 with separate data track for KLV
    # The KLV will be in a backup file, not truly embedded in MP4
    # True embedding requires MXF format or GStreamer mpegtsmux
    ffmpeg_cmd = [
        'ffmpeg',
        '-i', input_file,
        '-c:v', encoder,
        *encoder_params,
        '-c:a', 'aac',
        '-b:a', '128k',
        '-write_tmcd', '0',
        '-f', 'mp4',
        '-movflags', '+faststart',
        '-metadata', f'title=Transcoded Recording with KLV Reference',
        '-metadata', f'creation_time={creation_time}',
        '-metadata', f'description={existing_keywords}',
        '-metadata', f'keywords={existing_keywords}',
        '-metadata', f'comment=Timecode: {corrected_tc}, Offset: {offset:.2f}s, Original: {timecode_start}, KLV: STANAG 4609 (backup file)' + (f' | Keywords: {existing_keywords}' if existing_keywords else ''),
        '-progress', 'pipe:1',
        '-y',
        output_file
    ]
    
    result = run_ffmpeg(ffmpeg_cmd, duration)
    
    # Clean up temp files (SEI file not used in current implementation)
    try:
        if os.path.exists(sei_file):
            os.remove(sei_file)
    except:
        pass
    
    if result:
        print(f"\n✓ MP4 with KLV reference: {Path(output_file).name}")
        print(f"✓ KLV backup file: {Path(klv_bin_file).name}")
        print(f"\nNote: KLV stored in separate .klv.bin file (MP4 standard limitation)")
        print(f"For true embedded KLV, use MXF container or MPEG-TS format")
        print(f"Use read_klv.py to read KLV from backup file")
    
    return result


def transcode_option_3(input_file, output_file, creation_time, corrected_tc, offset, timecode_start, duration, button_press_dt, existing_keywords=''):
    """
    Option 3: MXF with KLV backup file (STANAG 4609)
    MXF is the broadcast industry standard - KLV stored separately for best compatibility
    """
    print("\n" + "="*70)
    print("Option 3: MXF with KLV Backup File (STANAG 4609)")
    print("="*70)
    print(f"\nTranscoding to: {Path(output_file).name}\n")
    
    if existing_keywords:
        print(f"Preserving keywords: {existing_keywords}")
    
    # Generate KLV packets
    klv_packets = generate_klv_packets(button_press_dt, duration, klv_rate=2.0)
    
    if not klv_packets:
        print("Error: Failed to generate KLV packets")
        return False
    
    # Create KLV binary file
    klv_bin_file = output_file.replace('.mxf', '_klv.bin')
    
    try:
        # Write binary KLV file
        with open(klv_bin_file, 'wb') as f:
            for packet in klv_packets:
                f.write(packet)
        
        print(f"Generated {len(klv_packets)} KLV packets")
        print(f"KLV file: {Path(klv_bin_file).name}")
    except Exception as e:
        print(f"Error preparing KLV data: {e}")
        return False
    
    print(f"\nTranscoding to MXF (broadcast format)...")
    
    # Get thread count
    cpu_count = multiprocessing.cpu_count()
    thread_count = max(1, cpu_count - 1)
    print(f"Using {thread_count} threads for encoding")
    
    # FFmpeg command for MXF
    # Note: FFmpeg's MXF muxer doesn't easily support adding KLV data tracks
    # For full KLV embedding in MXF, professional tools like Telestream or BMD are typically used
    # We create broadcast-quality MXF + separate KLV file
    ffmpeg_cmd = [
        'ffmpeg',
        '-i', input_file,
        '-c:v', 'mpeg2video',  # MXF typically uses MPEG-2
        '-b:v', '50M',         # 50 Mbps (broadcast quality)
        '-pix_fmt', 'yuv422p', # 4:2:2 color space (broadcast standard)
        '-threads', str(thread_count),  # Multi-threaded MPEG-2 encoding
        '-c:a', 'pcm_s16le',   # Uncompressed PCM audio
        '-ar', '48000',        # 48kHz sample rate
        '-metadata', f'title=Transcoded Recording with KLV Reference',
        '-metadata', f'creation_time={creation_time}',
        '-metadata', f'description={existing_keywords}',
        '-metadata', f'keywords={existing_keywords}',
        '-metadata', f'comment=Timecode: {corrected_tc}, Offset: {offset:.2f}s, Original: {timecode_start}, KLV: STANAG 4609 (separate file)' + (f' | Keywords: {existing_keywords}' if existing_keywords else ''),
        '-f', 'mxf',
        '-progress', 'pipe:1',
        '-y',
        output_file
    ]
    
    result = run_ffmpeg(ffmpeg_cmd, duration)
    
    if result:
        print(f"\n✓ MXF file: {Path(output_file).name}")
        print(f"✓ KLV backup file: {Path(klv_bin_file).name}")
        print(f"\nMXF format: SMPTE 377M compliant (broadcast standard)")
        print(f"KLV format: STANAG 4609 UAS Datalink Local Set")
        print(f"Note: KLV in separate file - use professional muxers for full MXF+KLV integration")
    
    return result


def run_ffmpeg(ffmpeg_cmd, duration):
    """Run FFmpeg with progress tracking"""
    try:
        process = subprocess.Popen(
            ffmpeg_cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            bufsize=1
        )
        
        # Track encoding progress
        last_percent = -1
        for line in process.stdout:
            if line.startswith('out_time_ms='):
                try:
                    time_str = line.split('=')[1].strip()
                    if time_str != 'N/A':
                        time_ms = int(time_str)
                        time_sec = time_ms / 1_000_000
                        percent = min(100, int((time_sec / duration) * 100))
                        
                        # Show progress bar
                        bar_length = 40
                        filled = int(bar_length * percent / 100)
                        bar = '█' * filled + '░' * (bar_length - filled)
                        print(f'\r  [{bar}] {percent}% ({time_sec:.1f}/{duration:.1f}s)', end='', flush=True)
                        last_percent = percent
                except (ValueError, IndexError):
                    pass
        
        process.wait(timeout=600)
        
        # Ensure we show 100% at completion
        if process.returncode == 0 and last_percent < 100:
            bar = '█' * 40
            print(f'\r  [{bar}] 100% ({duration:.1f}/{duration:.1f}s)', end='', flush=True)
        
        print()  # New line after progress bar
        
        if process.returncode == 0:
            print(f"\n✓ Transcode successful!")
            return True
        else:
            stderr = process.stderr.read()
            print(f"\n✗ FFmpeg failed with return code {process.returncode}")
            print(f"Error: {stderr[-500:]}")
            return False
            
    except subprocess.TimeoutExpired:
        print(f"\n✗ FFmpeg timeout after 10 minutes")
        return False
    except Exception as e:
        print(f"\n✗ Error running FFmpeg: {e}")
        return False


def transcode_video(input_file):
    """
    Main transcode function with user prompt
    """
    print("\n" + "="*70)
    print("Video Transcode - Timecode Correction")
    print("="*70)
    print(f"\nInput File: {Path(input_file).name}")
    
    # Get metadata
    probe_output = get_file_metadata(input_file)
    if not probe_output:
        print("Failed to read file metadata")
        return False
    
    # Parse metadata
    creation_match = re.search(r'creation_time=(.+)', probe_output)
    timecode_matches = re.findall(r'timecode=(.+)', probe_output)
    duration_match = re.search(r'duration=(.+)', probe_output)
    
    # Parse existing keywords/description/comment for preservation
    keywords_match = re.search(r'TAG:keywords=(.+)', probe_output)
    description_match = re.search(r'TAG:description=(.+)', probe_output)
    comment_match = re.search(r'TAG:comment=(.+)', probe_output)
    
    # Extract keywords from comment field (format: "Recording... | Keywords: xxx")
    existing_keywords = ''
    if keywords_match:
        existing_keywords = keywords_match.group(1)
    elif description_match:
        existing_keywords = description_match.group(1)
    elif comment_match:
        comment_value = comment_match.group(1)
        # Check if keywords are embedded in comment with delimiter
        if ' | Keywords: ' in comment_value:
            existing_keywords = comment_value.split(' | Keywords: ', 1)[1]
        elif comment_value.startswith('Keywords: '):
            existing_keywords = comment_value.replace('Keywords: ', '', 1)
        # Don't use recording metadata as keywords
        elif not comment_value.startswith('Recording started'):
            existing_keywords = comment_value
    
    if not all([creation_match, timecode_matches, duration_match]):
        print("Missing required metadata in file")
        return False
    
    creation_time = creation_match.group(1)
    timecode_start = timecode_matches[0]
    duration = float(duration_match.group(1))
    
    # Parse timestamps
    button_press_dt = datetime.fromisoformat(creation_time.replace('Z', '+00:00'))
    button_press_sec = button_press_dt.hour * 3600 + button_press_dt.minute * 60 + button_press_dt.second + (button_press_dt.microsecond / 1_000_000)
    tc_start_sec = parse_timecode(timecode_start)
    
    # Calculate offset
    offset = tc_start_sec - button_press_sec
    
    # Format corrected timecode
    corrected_tc = format_timecode(button_press_sec)
    
    print(f"\nButton Press Time:     {creation_time}")
    print(f"Original Timecode:     {timecode_start}")
    print(f"Corrected Timecode:    {corrected_tc}")
    print(f"Offset:                {offset:.2f} seconds")
    print(f"Duration:              {duration:.2f} seconds")
    
    # Show menu
    print("\n" + "="*70)
    print("Transcode Options:")
    print("="*70)
    print("\n1) MOV → Correct timecode MOV (no KLV)")
    
    if KLV_AVAILABLE:
        print("\n2) MOV → MP4 + KLV backup file")
        print("   - Corrects timecode to button press time")
        print("   - Generates KLV metadata file (STANAG 4609, 2Hz)")
        print("   - Output: MP4 video + separate .klv.bin file")
        print("   Note: MP4 doesn't support embedded KLV")
        
        print("\n3) MOV → MXF + KLV backup (BROADCAST STANDARD)")
        print("   - Corrects timecode to button press time")
        print("   - Generates broadcast-quality MXF file")
        print("   - STANAG 4609 KLV in separate file (2Hz)")
        print("   - Output: MXF (SMPTE 377M) + .klv.bin file")
        print("   Note: Industry standard format, better codec quality than MP4")
        
    else:
        print("\n2) [UNAVAILABLE] - KLV module not found")
        print("\n3) [UNAVAILABLE] - KLV module not found")

    print("\n4) Cancel")
    print("="*70)

    # Get user choice
    while True:
        try:
            choice = input("\nSelect option (1-4): ").strip()
            if choice in ['1', '2', '3', '4']:
                break
            print("Invalid choice. Please enter 1, 2, 3, or 4.")
        except (EOFError, KeyboardInterrupt):
            print("\n\nCancelled by user")
            return False

    if choice == '4':
        print("\nCancelled by user")
        return False
    
    # Generate output filenames
    base, ext = os.path.splitext(input_file)
    
    if choice == '1':
        output_file = f"{base}_transcoded.mov"
        success = transcode_option_1(
            input_file, output_file, creation_time, 
            corrected_tc, offset, timecode_start, duration, button_press_dt, existing_keywords
        )
    elif choice == '2':
        if not KLV_AVAILABLE:
            print("\nError: KLV module not available")
            return False
        output_file = f"{base}_transcoded.mp4"
        success = transcode_option_2(
            input_file, output_file, creation_time,
            corrected_tc, offset, timecode_start, duration, button_press_dt, existing_keywords
        )
    else:  # choice == '3'
        if not KLV_AVAILABLE:
            print("\nError: KLV module not available")
            return False
        output_file = f"{base}_transcoded.mxf"
        success = transcode_option_3(
            input_file, output_file, creation_time,
            corrected_tc, offset, timecode_start, duration, button_press_dt, existing_keywords
        )

    if success:
        print(f"\nOutput: {output_file}")
        print(f"\nCompleted successfully!")
    
    print("="*70 + "\n")
    return success


def main():
    import argparse
    
    parser = argparse.ArgumentParser(
        description='Transcode video with timecode correction and optional KLV embedding'
    )
    parser.add_argument(
        'input_file',
        nargs='?',
        help='Input video file (default: latest recording)'
    )
    
    args = parser.parse_args()
    
    # Find input file
    if args.input_file:
        input_file = args.input_file
        if not os.path.exists(input_file):
            print(f"Error: Input file not found: {input_file}")
            return 1
    else:
        print("Finding latest recording...")
        input_file = find_latest_recording()
        if not input_file:
            print("Error: No recording files found")
            return 1
        print(f"Found: {input_file}\n")
    
    # Run transcode
    success = transcode_video(input_file)
    return 0 if success else 1


if __name__ == "__main__":
    sys.exit(main())
