#!/usr/bin/env python3
"""
This material is based upon work supported by the United States Air Force under contract number FA8750-24-S-B079 (Prime Contractor Smart Information Flow Technologies (SIFT)).  Any opinions, findings and conclusions or recommendations expressed in this material are those of the author(s) and do not necessarily reflect the views of the United States Air Force.
 Copyright (c) 2026 RTX BBN Technologies. Licensed to US Government with unlimited rights.

This program is free software: you can redistribute it and/or modify it under the terms of the GNU General Public License as published by the Free Software Foundation, either version 3 of the License, or (at your option) any later version.
This is distributed in the hope that it will be useful, but without any warranty, without even the implied warranty of merchantability or fitness for a particular purpose.  See the GNU General Public License for more details. https://www.gnu.org/licenses/

test suite for TAK Video Restreamer

This module contains all tests for the application including:
- Health check endpoints
- Stream management
- Recording management
- Settings and configuration
- Utility functions
- Integration tests
- Web interface tests

Usage:
    python test_app.py              # Run all tests with runner
    python test_app.py -v           # Verbose output
    python test_app.py -k health    # Run tests matching 'health'
    python test_app.py --cov=app    # Run with coverage
    
Or use pytest directly:
    pytest test_app.py -v
"""
import sys
import pytest
import json
import os
from pathlib import Path
from unittest.mock import patch, MagicMock
from app import create_app


@pytest.fixture
def client():
    """Create test client, pre-authenticated with default credentials."""
    app = create_app()
    app.config['TESTING'] = True
    with app.test_client() as client:
        client.post(
            '/api/auth/login',
            json={'username': os.environ.get('ADMIN_USERNAME', 'admin'),
                  'password': os.environ.get('ADMIN_PASSWORD', 'changeme')},
            content_type='application/json',
        )
        yield client


@pytest.fixture
def temp_recording_dir(tmp_path):
    """Create temporary recording directory"""
    return tmp_path


def _write_fake_proc(root):
    """Minimal /proc tree for StreamUx CM5 hw tests (no real host /proc)."""
    root = Path(root)
    root.mkdir(parents=True, exist_ok=True)
    (root / 'stat').write_text(
        'cpu  100 0 50 850 0 0 0 0 0 0\n'
        'cpu0 0 0 0 0 0 0 0 0 0 0\n'
        'cpu1 0 0 0 0 0 0 0 0 0 0\n'
        'cpu2 0 0 0 0 0 0 0 0 0 0\n'
        'cpu3 0 0 0 0 0 0 0 0 0 0\n',
        encoding='utf-8',
    )
    (root / 'loadavg').write_text('0.42 0.50 0.55 1/120 99\n', encoding='utf-8')
    (root / 'uptime').write_text('109560.12 120000.00\n', encoding='utf-8')
    (root / 'meminfo').write_text(
        'MemTotal:        8126464 kB\n'
        'MemAvailable:    1458176 kB\n'
        'MemFree:          500000 kB\n',
        encoding='utf-8',
    )
    py = root / '1'
    py.mkdir()
    (py / 'comm').write_text('python3\n', encoding='utf-8')
    (py / 'cmdline').write_bytes(b'python3\x00-m\x00app')
    (py / 'stat').write_text(
        '1 (python3) S 0 0 0 0 0 0 0 0 0 0 10 5\n', encoding='utf-8',
    )
    (py / 'statm').write_text('2000 200 0 0 0 0 0\n', encoding='utf-8')
    ff = root / '42'
    ff.mkdir()
    (ff / 'comm').write_text('ffmpeg\n', encoding='utf-8')
    (ff / 'cmdline').write_bytes(
        b'ffmpeg\x00-i\x00rtsp://secret:pw@cam/stream\x00'
        b'srt://127.0.0.1:8890?streamid=publish:foot_traffic'
    )
    (ff / 'stat').write_text(
        '42 (ffmpeg) S 0 0 0 0 0 0 0 0 0 0 200 50\n', encoding='utf-8',
    )
    (ff / 'statm').write_text('8000 800 0 0 0 0 0\n', encoding='utf-8')
    kth = root / '2'
    kth.mkdir()
    (kth / 'comm').write_text('kthreadd\n', encoding='utf-8')
    (kth / 'cmdline').write_bytes(b'')
    (kth / 'stat').write_text(
        '2 (kthreadd) S 0 0 0 0 0 0 0 0 0 0 1 0\n', encoding='utf-8',
    )
    (kth / 'statm').write_text('1 1 0 0 0 0 0\n', encoding='utf-8')
    return root


def _write_fake_thermal(root, zones):
    """Minimal /sys/class/thermal tree. zones: [(name, type, milli_c), ...]."""
    base = Path(root) / 'class' / 'thermal'
    base.mkdir(parents=True, exist_ok=True)
    for name, ztype, milli in zones:
        zdir = base / name
        zdir.mkdir(parents=True, exist_ok=True)
        (zdir / 'type').write_text(ztype + '\n', encoding='utf-8')
        (zdir / 'temp').write_text(str(milli) + '\n', encoding='utf-8')
    return Path(root)


# =============================================================================
# Health Check Tests
# =============================================================================

class TestHealthEndpoint:
    """Test suite for /health endpoint"""
    
    def test_health_endpoint_exists(self, client):
        """Test that health endpoint is accessible"""
        response = client.get('/health')
        assert response.status_code == 200
    
    def test_health_endpoint_returns_json(self, client):
        """Test that health endpoint returns valid JSON"""
        response = client.get('/health')
        assert response.content_type == 'application/json'
        data = json.loads(response.data)
        assert isinstance(data, dict)
    
    def test_health_endpoint_has_status(self, client):
        """Test that health response includes status field"""
        response = client.get('/health')
        data = json.loads(response.data)
        assert 'status' in data
        assert data['status'] == 'healthy'
    
    def test_health_endpoint_has_timestamp(self, client):
        """Test that health response includes timestamp"""
        response = client.get('/health')
        data = json.loads(response.data)
        assert 'timestamp' in data
        assert isinstance(data['timestamp'], str)
    
    def test_health_endpoint_has_active_recordings(self, client):
        """Test that health response includes active recordings count"""
        response = client.get('/health')
        data = json.loads(response.data)
        assert 'activeRecordings' in data
        assert isinstance(data['activeRecordings'], int)
        assert data['activeRecordings'] >= 0
    
    def test_health_endpoint_has_klv_available(self, client):
        """Test that health response includes KLV availability status"""
        response = client.get('/health')
        data = json.loads(response.data)
        assert 'klvAvailable' in data
        assert isinstance(data['klvAvailable'], bool)
    
    def test_health_endpoint_has_srt_buffer_available(self, client):
        """Test that health response includes SRT buffer availability"""
        response = client.get('/health')
        data = json.loads(response.data)
        assert 'srtBufferAvailable' in data
        assert isinstance(data['srtBufferAvailable'], bool)


# =============================================================================
# Stream Management Tests
# =============================================================================

class TestStreamsEndpoint:
    """Test suite for /api/streams endpoints"""
    
    def test_list_streams_endpoint_exists(self, client):
        """Test that streams listing endpoint is accessible"""
        response = client.get('/api/streams')
        assert response.status_code == 200
    
    def test_list_streams_returns_json(self, client):
        """Test that streams endpoint returns valid JSON"""
        response = client.get('/api/streams')
        assert response.content_type == 'application/json'
        data = json.loads(response.data)
        assert isinstance(data, list)
    
    def test_list_streams_has_streams_array(self, client):
        """Test that streams response is an array"""
        response = client.get('/api/streams')
        data = json.loads(response.data)
        assert isinstance(data, list)
    
    @patch('app.api.streams.mediamtx.list_paths')
    def test_list_streams_with_active_streams(self, mock_list_paths, client):
        """Test streams listing with mock active streams"""
        mock_list_paths.return_value = {
            'items': {
                'test_stream': {
                    'name': 'test_stream',
                    'ready': True,
                    'tracks': ['H264'],
                    'bytesReceived': 12345,
                    'readers': [{'state': 'read'}]
                }
            }
        }
        
        response = client.get('/api/streams')
        data = json.loads(response.data)
        assert isinstance(data, list)
        assert len(data) > 0
    
    @staticmethod
    def _fake_ffmpeg_process():
        """MagicMock standing in for subprocess.Popen's ffmpeg process."""
        proc = MagicMock()
        proc.poll.return_value = None
        proc.pid = 12345
        return proc

    @staticmethod
    def _clear_pull_state(stream_name):
        """Remove any pull-stream state left behind by a test, including the
        persisted pull_sources.json entry (otherwise a leftover entry gets
        auto-restored - and re-launches a real ffmpeg process - the next time
        the app starts, e.g. in a later test run)."""
        from app.state import pull_stream_configs, active_pull_streams, pull_stream_lock
        import app.api.streams as streams_module
        with pull_stream_lock:
            pull_stream_configs.pop(stream_name, None)
            active_pull_streams.pop(stream_name, None)
        streams_module._remove_pull_source(stream_name)

    # NOTE: subprocess.Popen and threading.Thread are mocked so these tests
    # never spawn a real ffmpeg process or the background reconnect-monitor
    # thread. streamux_manager.start is mocked so it doesn't spin up its own
    # background thread waiting (up to 45s) for a MediaMTX source that will
    # never appear in this test environment.
    @patch('app.api.streams.streamux_manager.start')
    @patch('app.api.streams.threading.Thread')
    @patch('app.api.streams.subprocess.Popen')
    def test_start_pull_stream_endpoint_exists(self, mock_popen, mock_thread, mock_streamux_start, client):
        """Test that POST /api/streams/<name>/pull starts a pull stream"""
        mock_popen.return_value = self._fake_ffmpeg_process()
        stream_name = 'pull-test-exists'
        try:
            response = client.post(f'/api/streams/{stream_name}/pull',
                                  json={'sourceUrl': 'rtsp://example.com/stream'},
                                  content_type='application/json')
            assert response.status_code == 200
        finally:
            self._clear_pull_state(stream_name)

    @patch('app.api.streams.streamux_manager.start')
    @patch('app.api.streams.threading.Thread')
    @patch('app.api.streams.subprocess.Popen')
    def test_start_pull_stream_with_full_data(self, mock_popen, mock_thread, mock_streamux_start, client):
        """Test starting a pull stream returns success and echoes the source URL"""
        mock_popen.return_value = self._fake_ffmpeg_process()
        stream_name = 'pull-test-full'
        try:
            response = client.post(f'/api/streams/{stream_name}/pull',
                                  json={'sourceUrl': 'rtsp://example.com/test'},
                                  content_type='application/json')
            assert response.status_code == 200
            data = json.loads(response.data)
            assert data['success'] is True
            assert data['source'] == 'rtsp://example.com/test'
        finally:
            self._clear_pull_state(stream_name)

    def test_start_pull_stream_requires_body(self, client):
        """Test that starting a pull stream with an empty JSON body is rejected"""
        response = client.post('/api/streams/pull-test-nobody/pull',
                             json={},
                             content_type='application/json')
        assert response.status_code == 400

    def test_start_pull_stream_requires_source_url(self, client):
        """Test that starting a pull stream without a source URL is rejected"""
        response = client.post('/api/streams/pull-test-nourl/pull',
                             json={'username': 'someuser'},
                             content_type='application/json')
        assert response.status_code == 400

    def test_stop_pull_stream_not_active_returns_404(self, client):
        """Test that stopping a pull stream that isn't running returns 404"""
        response = client.post('/api/streams/nonexistent-pull/stop-pull')
        assert response.status_code == 404

    def test_delete_nonexistent_stream_is_idempotent(self, client):
        """Test that DELETE on a stream with no pull config still succeeds (idempotent cleanup)"""
        response = client.delete('/api/streams/nonexistent-stream')
        assert response.status_code in [200, 404]

    @patch('app.api.streams.streamux_manager.stop')
    @patch('app.api.streams.streamux_manager.start')
    @patch('app.api.streams.threading.Thread')
    @patch('app.api.streams.subprocess.Popen')
    def test_pull_stream_workflow(self, mock_popen, mock_thread, mock_streamux_start, mock_streamux_stop, client):
        """Test complete pull stream workflow: start, appear in pull-status, then stop"""
        mock_popen.return_value = self._fake_ffmpeg_process()
        stream_name = 'workflow-test'
        try:
            # Start pull stream
            start_response = client.post(f'/api/streams/{stream_name}/pull',
                                         json={'sourceUrl': 'rtsp://example.com/test'},
                                         content_type='application/json')
            assert start_response.status_code == 200

            # Verify it appears in the pull-status list
            list_response = client.get('/api/pull-status')
            assert list_response.status_code == 200
            names = [s['name'] for s in json.loads(list_response.data)]
            assert stream_name in names

            # Stop pull stream
            stop_response = client.post(f'/api/streams/{stream_name}/stop-pull')
            assert stop_response.status_code == 200
        finally:
            self._clear_pull_state(stream_name)


# =============================================================================
# Recording Control Tests
# =============================================================================

class TestRecordingControl:
    """Test suite for recording start/stop operations"""
    
    def test_start_recording_endpoint_exists(self, client):
        """Test that start recording endpoint exists"""
        response = client.post('/api/recordings/start',
                              json={'streamName': 'test-stream'},
                              content_type='application/json')
        # Endpoint may be implemented or return 404
        assert response.status_code in [200, 201, 404, 400]
    
    def test_start_recording_requires_stream_name(self, client):
        """Test that starting recording validates stream name"""
        response = client.post('/api/recordings/start',
                              json={},
                              content_type='application/json')
        assert response.status_code in [200, 400, 404]
    
    def test_stop_recording_endpoint_exists(self, client):
        """Test that stop recording endpoint exists"""
        response = client.post('/api/recordings/stop',
                              json={'streamName': 'test-stream'},
                              content_type='application/json')
        assert response.status_code in [200, 404]
    
    def test_stop_recording_nonexistent_stream(self, client):
        """Test stopping recording for non-existent stream"""
        response = client.post('/api/recordings/stop',
                             json={'streamName': 'nonexistent'},
                             content_type='application/json')
        # Should handle gracefully
        assert response.status_code in [200, 404]
    
    def test_recording_workflow(self, client):
        """Test start and stop recording workflow"""
        stream_name = 'workflow-test-stream'
        
        # Start recording
        start_response = client.post('/api/recordings/start',
                                    json={'streamName': stream_name},
                                    content_type='application/json')
        
        if start_response.status_code in [200, 201]:
            # Stop recording
            stop_response = client.post('/api/recordings/stop',
                                       json={'streamName': stream_name},
                                       content_type='application/json')
            assert stop_response.status_code in [200, 204]


# =============================================================================
# Recordings Endpoint Tests
# =============================================================================

class TestRecordingsEndpoint:
    """Test suite for /api/recordings endpoints"""
    
    def test_list_recordings_endpoint_exists(self, client):
        """Test that recordings listing endpoint is accessible"""
        response = client.get('/api/recordings')
        assert response.status_code == 200
    
    def test_list_recordings_returns_json(self, client):
        """Test that recordings endpoint returns valid JSON"""
        response = client.get('/api/recordings')
        assert response.content_type == 'application/json'
        data = json.loads(response.data)
        assert isinstance(data, list)
    
    def test_list_recordings_returns_array(self, client):
        """Test that recordings response is an array"""
        response = client.get('/api/recordings')
        data = json.loads(response.data)
        assert isinstance(data, list)
    
    def test_recording_item_structure(self, client):
        """Test structure of recording items"""
        response = client.get('/api/recordings')
        data = json.loads(response.data)
        
        if len(data) > 0:
            recording = data[0]
            assert 'stream' in recording
            assert 'filename' in recording
            assert 'size' in recording
            assert 'created' in recording
    
    def test_recording_filters_video_files_only(self, client):
        """Test that recordings only returns video files"""
        response = client.get('/api/recordings')
        data = json.loads(response.data)
        
        # Check that no .bin or .json files are in the list
        for recording in data:
            filename = recording['filename']
            assert not filename.endswith('.bin')
            assert not filename.endswith('.json')
    
    def test_delete_nonexistent_recording(self, client):
        """Test deleting non-existent recording"""
        response = client.delete('/api/recordings/nonexistent/fakefile.mp4')
        assert response.status_code == 404
    
    def test_download_nonexistent_recording(self, client):
        """Test downloading non-existent recording"""
        response = client.get('/api/recordings/nonexistent/fakefile.mp4')
        assert response.status_code == 404


# =============================================================================
# Thumbnail Tests
# =============================================================================

class TestThumbnailGeneration:
    """Test suite for thumbnail generation"""
    
    def test_generate_thumbnail_endpoint_exists(self, client):
        """Test that thumbnail generation endpoint exists"""
        # Try to generate thumbnail (will fail for non-existent file)
        response = client.post('/api/recordings/test-stream/test-file.mp4/thumbnail')
        # Endpoint exists even if operation fails
        assert response.status_code in [200, 201, 404, 405, 500]
    
    def test_generate_thumbnail_nonexistent_recording(self, client):
        """Test generating thumbnail for non-existent recording"""
        response = client.post('/api/recordings/nonexistent/fakefile.mp4/thumbnail')
        assert response.status_code in [404, 405]
    
    def test_get_thumbnail_endpoint_exists(self, client):
        """Test that thumbnail retrieval endpoint exists"""
        response = client.get('/api/recordings/test-stream/test-file.mp4/thumbnail')
        # Endpoint exists even if thumbnail doesn't
        assert response.status_code in [200, 404]
    
    def test_get_thumbnail_nonexistent(self, client):
        """Test getting non-existent thumbnail"""
        response = client.get('/api/recordings/nonexistent/fakefile.mp4/thumbnail')
        assert response.status_code == 404
    
    def test_thumbnail_content_type(self, client):
        """Test that existing thumbnails return correct content type"""
        # This will fail for non-existent thumbnails, but tests the structure
        response = client.get('/api/recordings/test-stream/test-file.mp4/thumbnail')
        if response.status_code == 200:
            # Should be an image
            assert 'image/' in response.content_type


# =============================================================================
# Test Pattern Generator Tests
# =============================================================================

class TestPatternGenerator:
    """Test suite for test pattern generator endpoints"""
    
    def test_start_srt_test_pattern_endpoint_exists(self, client):
        """Test that SRT test pattern endpoint is accessible"""
        response = client.post('/api/test/srt',
                              json={'streamName': 'test-input'},
                              content_type='application/json')
        assert response.status_code in [200, 201, 500]  # 500 if FFmpeg not available
    
    def test_start_srt_test_pattern_returns_json(self, client):
        """Test that SRT test pattern returns valid JSON with testId"""
        response = client.post('/api/test/srt',
                              json={'streamName': 'test-srt'},
                              content_type='application/json')
        assert response.content_type == 'application/json'
        data = json.loads(response.data)
        assert isinstance(data, dict)
        if response.status_code in [200, 201]:
            assert 'testId' in data or 'message' in data
    
    def test_start_rtsp_test_pattern_tcp(self, client):
        """Test starting RTSP test pattern with TCP transport"""
        response = client.post('/api/test/rtsp',
                              json={'streamName': 'test-rtsp-tcp'},
                              content_type='application/json')
        assert response.status_code in [200, 201, 500]  # 500 if FFmpeg not available
        assert response.content_type == 'application/json'
    
    def test_start_rtsps_test_pattern(self, client):
        """Test starting RTSPS test pattern with TLS"""
        response = client.post('/api/test/rtsps',
                              json={'streamName': 'test-secure'},
                              content_type='application/json')
        assert response.status_code in [200, 201, 400, 500]  # May fail if certs not configured or FFmpeg not available
        assert response.content_type == 'application/json'
    
    def test_start_rtsp_udp_test_pattern(self, client):
        """Test starting RTSP test pattern with UDP transport"""
        response = client.post('/api/test/rtsp-udp',
                              json={'streamName': 'test-rtsp-udp'},
                              content_type='application/json')
        assert response.status_code in [200, 201, 500]  # 500 if FFmpeg not available
        assert response.content_type == 'application/json'
    
    def test_get_test_status_nonexistent(self, client):
        """Test getting status of non-existent test"""
        fake_id = 'nonexistent-test-id-12345'
        response = client.get(f'/api/test/{fake_id}')
        assert response.status_code in [404, 200]  # May return 200 with error message
    
    def test_stop_test_pattern_nonexistent(self, client):
        """Test stopping non-existent test pattern"""
        fake_id = 'nonexistent-test-id-12345'
        response = client.delete(f'/api/test/{fake_id}')
        assert response.status_code in [404, 200]  # May return 200 even if doesn't exist
    
    def test_test_pattern_workflow(self, client):
        """Test complete workflow: start, check status, stop"""
        # Start test pattern
        start_response = client.post('/api/test/srt',
                                    json={'streamName': 'workflow-test'},
                                    content_type='application/json')
        assert start_response.status_code in [200, 201, 500]  # 500 if FFmpeg not available
        
        if start_response.status_code in [200, 201]:
            data = json.loads(start_response.data)
            if 'testId' in data:
                test_id = data['testId']
                
                # Check status
                status_response = client.get(f'/api/test/{test_id}/status')
                assert status_response.status_code == 200
                
                # Stop test
                stop_response = client.post(f'/api/test/{test_id}/stop')
                assert stop_response.status_code in [200, 204]
    
    def test_test_pattern_requires_stream_name(self, client):
        """Test that test pattern requires streamName parameter"""
        response = client.post('/api/test/srt',
                              json={},
                              content_type='application/json')
        # May accept empty or return error
        assert response.status_code in [200, 201, 400]


# =============================================================================
# Settings Tests
# =============================================================================

class TestSettingsEndpoint:
    """Test suite for /api/settings endpoints"""
    
    def test_get_settings_endpoint_exists(self, client):
        """Test that settings endpoint is accessible"""
        response = client.get('/api/settings')
        assert response.status_code == 200
    
    def test_get_settings_returns_json(self, client):
        """Test that settings endpoint returns valid JSON"""
        response = client.get('/api/settings')
        assert response.content_type == 'application/json'
        data = json.loads(response.data)
        assert isinstance(data, dict)
    
    def test_settings_has_auto_record(self, client):
        """Test that settings includes autoRecord field"""
        response = client.get('/api/settings')
        data = json.loads(response.data)
        assert 'autoRecord' in data
        assert isinstance(data['autoRecord'], bool)
    
    def test_settings_has_structure(self, client):
        """Test that settings has expected structure"""
        response = client.get('/api/settings')
        data = json.loads(response.data)
        assert 'autoRecord' in data
        assert 'disk' in data
        assert 'settings' in data
    
    def test_update_settings_requires_json(self, client):
        """Test that updating settings with PUT returns 405"""
        response = client.put('/api/settings')
        assert response.status_code == 405
    
    def test_update_auto_record_setting(self, client):
        """Test updating autoRecord setting with POST"""
        response = client.post('/api/settings',
                            json={'autoRecord': True},
                            content_type='application/json')
        assert response.status_code == 200


# =============================================================================
# Certificate Management Tests
# =============================================================================

class TestCertificateManagement:
    """Test suite for certificate management"""
    
    def test_get_certificate_info_endpoint_not_exists(self, client):
        """Test that certificate info endpoint returns 404"""
        response = client.get('/api/settings/certificates')
        assert response.status_code == 404


# =============================================================================
# Codec Detection Tests
# =============================================================================

class TestCodecDetection:
    """Test suite for codec detection utilities"""
    
    def test_codec_detection_module_exists(self):
        """Test that codec detection module exists"""
        from app.utils import codec_detection
        assert codec_detection is not None


# =============================================================================
# Thumbnail Utility Tests
# =============================================================================

class TestThumbnailUtils:
    """Test suite for thumbnail utilities"""
    
    def test_thumbnail_module_exists(self):
        """Test that thumbnail module exists"""
        from app.utils import thumbnail
        assert thumbnail is not None


# =============================================================================
# File Validation Tests
# =============================================================================

class TestFileValidation:
    """Test suite for file validation"""
    
    def test_valid_video_extensions(self):
        """Test that valid video extensions are accepted"""
        valid_extensions = ['.mp4', '.mov', '.ts', '.mxf', '.mpg', '.mpeg', '.mkv']
        
        for ext in valid_extensions:
            filename = f'test{ext}'
            # Video files should be valid
            assert any(filename.endswith(e) for e in valid_extensions)
    
    def test_invalid_file_extensions_filtered(self):
        """Test that non-video files are filtered"""
        invalid_files = ['test.bin', 'test.json', 'test.txt', 'test.log']
        video_extensions = ['.mp4', '.mov', '.ts', '.mxf', '.mpg', '.mpeg', '.mkv']
        
        for filename in invalid_files:
            # These should NOT match video extensions
            assert not any(filename.endswith(ext) for ext in video_extensions)


# =============================================================================
# Web Interface Tests
# =============================================================================

class TestWebInterface:
    """Test suite for web interface pages"""
    
    def test_index_page_loads(self, client):
        """Test that index page loads successfully"""
        response = client.get('/')
        assert response.status_code == 200
        assert b'TAK Video Restreamer' in response.data
    
    def test_recordings_page_loads(self, client):
        """Test that recordings page loads successfully"""
        response = client.get('/recordings')
        assert response.status_code == 200
        assert b'Recordings' in response.data
    
    def test_settings_page_loads(self, client):
        """Test that settings page loads successfully"""
        response = client.get('/settings')
        assert response.status_code == 200
        assert b'Settings' in response.data
    
    def test_utils_page_loads(self, client):
        """Test that utils page loads successfully"""
        response = client.get('/utils')
        assert response.status_code == 200
        assert b'Utils' in response.data
    
    def test_test_page_loads(self, client):
        """Test that test video page loads successfully"""
        response = client.get('/test')
        assert response.status_code == 200
        assert b'Test' in response.data

    def test_streamux_page_loads(self, client):
        """StreamUx page is /streamux (HTML file still overview.html)."""
        response = client.get('/streamux')
        assert response.status_code == 200
        assert b'StreamUx' in response.data
        assert b'/api/streamux' in response.data
        assert b'setProfile' in response.data
        assert b'/api/overview' not in response.data
        assert b'setRung' not in response.data
        assert b'yt_loop3' not in response.data
        assert b'lastError && published' not in response.data
        assert b'ENCODER LOG' in response.data
        assert b'/api/streamux/' in response.data
        assert b'/log?lines=' in response.data
        assert b'data-encoding' in response.data
        assert b'Turn encoding on to change profile' in response.data
        assert b'Viewers keep a copy of the source' not in response.data
        assert b'Turn encoding on to stop passthrough' in response.data
        assert b'setEncoding' in response.data
        assert b'encodingHold' in response.data
        assert b'id="streamux-hw"' in response.data
        assert b'Hardware Monitor' in response.data
        assert b"What's using CPU/RAM?" in response.data
        assert b'/api/streamux/hw' in response.data
        assert b'streamux-enc-passthrough-hint-20260827' in response.data
        assert b'streamux-switch' in response.data
        assert b'role="switch"' in response.data
        assert b'aria-label="Encoding"' in response.data
        assert b'streamux-hw-temp' in response.data
        assert b'>Temp<' in response.data
        assert b'streamux-hw-scope' not in response.data
        assert b'CM5 kernel' not in response.data
        assert b'streamux-settings' in response.data
        assert b'streamux-setting-box' in response.data
        assert b'streamux-setting-toggle' in response.data
        assert b'streamux-setting-btn' in response.data
        assert b'streamux-setting-dot' in response.data
        assert b'paintRoiDot' in response.data
        assert b'streamux-setting-pill' not in response.data
        assert b'streamux-settings-col' not in response.data
        assert b'streamux-setting-change' not in response.data
        assert b'Diagnostic overlay' in response.data
        assert b'Show Diagnostic Overlay' not in response.data
        assert b'data-overlay' in response.data
        assert b'Overlay off' not in response.data
        assert b'ROI off' not in response.data
        assert b'Region of interest' in response.data
        assert b'>Change</button>' not in response.data
        assert b'streamux-roi-modal' in response.data
        assert b'naturalWidth' in response.data
        assert b'/still' in response.data
        assert b'If the camera pans' in response.data
        html = response.data.decode('utf-8')
        # Overlay row: label left, switch right (mirrors ROI label / dot)
        overlay_label = html.find('streamux-setting-label">Diagnostic overlay')
        overlay_switch = html.find('class="streamux-switch"', overlay_label)
        assert 0 <= overlay_label < overlay_switch
        assert html.find('streamux-lead') < html.find('id="streamux-hw"') < html.find('>Profiles<')
        assert b'rung' not in response.data.lower()

    def test_videowall_page_loads(self, client):
        response = client.get('/videowall')
        assert response.status_code == 200
        assert b'Video Wall' in response.data

    def test_videowall_releases_wall_abr_on_leave(self, client):
        """Wall auto-starts ABR with source=wall and releases on pagehide / last tile."""
        body = client.get('/videowall').data.decode('utf-8')
        assert 'source=wall' in body
        assert 'pagehide' in body
        assert 'releaseAllWallAbr' in body
        assert 'releaseUnusedWallAbr' in body
        assert "method: 'POST'" in body
        assert "method: 'DELETE'" in body
        # Unscoped ABR POST would persist like Dashboard operator ABR.
        assert "/abr`, { method: 'POST' }" not in body
        assert '/abr?source=wall' in body


# =============================================================================
# StreamUx
# =============================================================================

class TestStreamuxAPI:
    """Breaking StreamUx API — /api/overview and JSON `rung` are gone."""

    def test_list_profiles_catalog(self, client):
        response = client.get('/api/streamux')
        assert response.status_code == 200
        data = response.get_json()
        assert 'profiles' in data
        assert 'rungs' not in data
        assert [p['id'] for p in data['profiles']] == ['low', 'medium', 'high']
        assert data['streams'] == []

    def test_old_overview_routes_gone(self, client):
        assert client.get('/api/overview').status_code == 404
        assert client.post('/api/overview/restart', json={'name': 'x'}).status_code == 404
        assert client.get('/api/overview/unit/log').status_code == 404

    def test_log_unknown_stream_404(self, client):
        response = client.get('/api/streamux/missing/log')
        assert response.status_code == 404

    def test_log_tail_and_legacy(self, client, tmp_path, monkeypatch):
        import app.services.streamux as sx
        from app.state import pull_stream_configs, pull_stream_lock
        logdir = tmp_path / 'ffmpeg'
        logdir.mkdir()
        (logdir / 'streamux-unit.log').write_text(
            '\n'.join(f'line-{i}' for i in range(1, 121)) + '\n', encoding='utf-8'
        )
        monkeypatch.setattr(sx, 'FFMPEG_LOG_DIR', str(logdir))
        with pull_stream_lock:
            pull_stream_configs['unit'] = {'source_url': 'rtsp://x', 'stopped': False}
        try:
            response = client.get('/api/streamux/unit/log')
            assert response.status_code == 200
            data = response.get_json()
            assert data['name'] == 'unit'
            assert 'lastError' in data
            assert data['lines'][0] == 'line-21'
            assert data['lines'][-1] == 'line-120'
            assert len(data['lines']) == 100
            capped = client.get('/api/streamux/unit/log?lines=500')
            assert len(capped.get_json()['lines']) == 100
            few = client.get('/api/streamux/unit/log?lines=5')
            assert few.get_json()['lines'] == ['line-116', 'line-117', 'line-118', 'line-119', 'line-120']
        finally:
            with pull_stream_lock:
                pull_stream_configs.pop('unit', None)

    def test_log_falls_back_to_overview_filename(self, client, tmp_path, monkeypatch):
        import app.services.streamux as sx
        from app.state import pull_stream_configs, pull_stream_lock
        logdir = tmp_path / 'ffmpeg'
        logdir.mkdir()
        (logdir / 'overview-legacy.log').write_text('old-tail\n', encoding='utf-8')
        monkeypatch.setattr(sx, 'FFMPEG_LOG_DIR', str(logdir))
        with pull_stream_lock:
            pull_stream_configs['legacy'] = {'source_url': 'rtsp://x', 'stopped': False}
        try:
            data = client.get('/api/streamux/legacy/log').get_json()
            assert data['lines'] == ['old-tail']
        finally:
            with pull_stream_lock:
                pull_stream_configs.pop('legacy', None)

    def test_log_empty_when_missing_file(self, client, tmp_path, monkeypatch):
        import app.services.streamux as sx
        from app.state import pull_stream_configs, pull_stream_lock
        monkeypatch.setattr(sx, 'FFMPEG_LOG_DIR', str(tmp_path / 'ffmpeg'))
        with pull_stream_lock:
            pull_stream_configs['empty'] = {'source_url': 'rtsp://x', 'stopped': False}
        try:
            response = client.get('/api/streamux/empty/log')
            assert response.status_code == 200
            data = response.get_json()
            assert data['name'] == 'empty'
            assert data['lines'] == []
        finally:
            with pull_stream_lock:
                pull_stream_configs.pop('empty', None)

    def test_log_empty_file_200(self, client, tmp_path, monkeypatch):
        """0-byte log file is 200 + lines [], not 404 (UI would stick on empty)."""
        import app.services.streamux as sx
        from app.state import pull_stream_configs, pull_stream_lock
        logdir = tmp_path / 'ffmpeg'
        logdir.mkdir()
        (logdir / 'streamux-quiet.log').write_text('', encoding='utf-8')
        monkeypatch.setattr(sx, 'FFMPEG_LOG_DIR', str(logdir))
        with pull_stream_lock:
            pull_stream_configs['quiet'] = {'source_url': 'rtsp://x', 'stopped': False}
        try:
            response = client.get('/api/streamux/quiet/log')
            assert response.status_code == 200
            assert response.get_json()['lines'] == []
        finally:
            with pull_stream_lock:
                pull_stream_configs.pop('quiet', None)

    def test_open_encoder_log_appends(self, tmp_path, monkeypatch):
        import app.services.streamux as sx
        monkeypatch.setattr(sx, 'FFMPEG_LOG_DIR', str(tmp_path / 'ffmpeg'))
        path = tmp_path / 'ffmpeg' / 'streamux-traffic_loop.log'
        first = sx._open_encoder_log('traffic_loop')
        first.write('spawn-1\n')
        first.close()
        second = sx._open_encoder_log('traffic_loop')
        second.write('spawn-2\n')
        second.close()
        assert path.read_text(encoding='utf-8') == 'spawn-1\nspawn-2\n'

    def test_open_encoder_log_rotates_when_huge(self, tmp_path, monkeypatch):
        import app.services.streamux as sx
        monkeypatch.setattr(sx, 'FFMPEG_LOG_DIR', str(tmp_path / 'ffmpeg'))
        monkeypatch.setattr(sx, 'ENCODER_LOG_MAX_BYTES', 64)
        path = tmp_path / 'ffmpeg' / 'streamux-unit.log'
        path.parent.mkdir()
        path.write_text('keep-me\n' + ('x' * 200) + '\n', encoding='utf-8')
        fh = sx._open_encoder_log('unit')
        fh.write('after\n')
        fh.close()
        text = path.read_text(encoding='utf-8')
        assert 'keep-me' in text
        assert 'log rotated' in text
        assert 'after' in text
        assert len(text) < 400

    def test_encoder_log_file_no_traverse(self, tmp_path, monkeypatch):
        import app.services.streamux as sx
        logdir = tmp_path / 'ffmpeg'
        logdir.mkdir()
        (tmp_path / 'secret.log').write_text('pwned\n', encoding='utf-8')
        monkeypatch.setattr(sx, 'FFMPEG_LOG_DIR', str(logdir))
        assert sx.encoder_log_file('../secret') is None
        assert sx.encoder_log_file('..\\secret') is None
        (logdir / 'streamux-foot_traffic.log').write_text('ok\n', encoding='utf-8')
        path = sx.encoder_log_file('foot_traffic')
        assert path
        assert os.path.basename(path) == 'streamux-foot_traffic.log'
        assert os.path.realpath(path).startswith(os.path.realpath(str(logdir)))

    def test_put_missing_pull(self, client):
        response = client.put('/api/streamux/missing', json={'profile': 'medium'})
        assert response.status_code == 404

    def test_put_rejects_rung_field(self, client):
        from app.state import pull_stream_configs, pull_stream_lock
        with pull_stream_lock:
            pull_stream_configs['unit'] = {'source_url': 'rtsp://x', 'stopped': False}
        try:
            response = client.put('/api/streamux/unit', json={'rung': 'low'})
            assert response.status_code == 400
            err = (response.get_json() or {}).get('error', '')
            assert 'profile' in err
            assert 'rung' not in err.lower()
        finally:
            with pull_stream_lock:
                pull_stream_configs.pop('unit', None)

    def test_normalize_profile_aliases(self):
        from app.services.streamux import DEFAULT_PROFILE, PROFILES, normalize_profile
        assert DEFAULT_PROFILE == 'medium'
        assert normalize_profile('floor') == 'low'
        assert normalize_profile('mid') == 'medium'
        assert normalize_profile('g2g') == 'high'
        assert set(PROFILES) == {'low', 'medium', 'high'}

    def test_migrate_legacy_profiles_file(self, tmp_path, monkeypatch):
        import app.services.streamux as sx
        old = tmp_path / 'overview_rungs.json'
        new = tmp_path / 'streamux_profiles.json'
        old.write_text(json.dumps({'foot_traffic': 'floor'}), encoding='utf-8')
        monkeypatch.setattr(sx, 'DATA_DIR', str(tmp_path))
        monkeypatch.setattr(sx, 'STATE_FILE', str(new))
        monkeypatch.setattr(sx, 'LEGACY_STATE_FILE', str(old))
        monkeypatch.setattr(sx, 'OVERLAY_STATE_FILE', str(tmp_path / 'streamux_overlay.json'))
        monkeypatch.setattr(sx, 'LEGACY_OVERLAY_STATE_FILE', str(tmp_path / 'overview_overlay.json'))
        monkeypatch.setattr(sx, 'OVERLAY_DIR', str(tmp_path / 'streamux-overlay'))
        monkeypatch.setattr(sx, 'LEGACY_OVERLAY_DIR', str(tmp_path / 'overview-overlay'))
        monkeypatch.setattr(sx, 'ROI_STATE_FILE', str(tmp_path / 'streamux_roi.json'))
        mgr = sx.StreamuxManager()
        assert mgr.get_profile('foot_traffic') == 'low'
        assert new.is_file()
        saved = json.loads(new.read_text(encoding='utf-8'))
        assert saved == {'foot_traffic': {'profile': 'low', 'encoding': True}}
        assert mgr.get_encoding('foot_traffic') is True

    def test_load_encoding_false_and_missing_defaults_on(self, tmp_path, monkeypatch):
        import app.services.streamux as sx
        state = tmp_path / 'streamux_profiles.json'
        state.write_text(json.dumps({
            'MOHOC': {'profile': 'medium', 'encoding': False},
            'traffic_loop': {'profile': 'medium'},
            'foot_traffic': 'low',
        }), encoding='utf-8')
        monkeypatch.setattr(sx, 'DATA_DIR', str(tmp_path))
        monkeypatch.setattr(sx, 'STATE_FILE', str(state))
        monkeypatch.setattr(sx, 'LEGACY_STATE_FILE', str(tmp_path / 'overview_rungs.json'))
        monkeypatch.setattr(sx, 'OVERLAY_STATE_FILE', str(tmp_path / 'streamux_overlay.json'))
        monkeypatch.setattr(sx, 'LEGACY_OVERLAY_STATE_FILE', str(tmp_path / 'overview_overlay.json'))
        monkeypatch.setattr(sx, 'OVERLAY_DIR', str(tmp_path / 'streamux-overlay'))
        monkeypatch.setattr(sx, 'LEGACY_OVERLAY_DIR', str(tmp_path / 'overview-overlay'))
        monkeypatch.setattr(sx, 'ROI_STATE_FILE', str(tmp_path / 'streamux_roi.json'))
        mgr = sx.StreamuxManager()
        assert mgr.get_encoding('MOHOC') is False
        assert mgr.get_encoding('traffic_loop') is True
        assert mgr.get_encoding('foot_traffic') is True
        assert mgr.get_encoding('never_seen') is True
        saved = json.loads(state.read_text(encoding='utf-8'))
        assert saved['MOHOC'] == {'profile': 'medium', 'encoding': False}
        assert saved['traffic_loop']['encoding'] is True
        assert saved['foot_traffic'] == {'profile': 'low', 'encoding': True}

    def test_passthrough_cmd_is_copy_not_x264(self):
        from app.services.streamux import streamux_manager, source_name
        cmd = streamux_manager._build_passthrough_cmd('MOHOC')
        joined = ' '.join(cmd)
        assert 'libx264' not in cmd
        assert '-c' in cmd
        assert 'copy' in cmd
        assert 'crop=' not in joined
        assert '-vf' not in cmd
        assert 'publish:MOHOC' in joined
        assert 'publish:MOHOC__src' not in joined
        assert source_name('MOHOC') in joined

    def test_put_encoding_off_passthrough_and_409s(self, client, monkeypatch):
        import app.services.streamux as sx
        from app.state import pull_stream_configs, pull_stream_lock
        monkeypatch.setattr(sx.streamux_manager, '_restart', lambda *a, **k: None)
        monkeypatch.setattr(sx.streamux_manager, '_save', lambda: None)
        monkeypatch.setattr(sx.streamux_manager, '_save_overlays', lambda: None)
        monkeypatch.setattr(sx.streamux_manager, '_save_roi', lambda: None)
        with pull_stream_lock:
            pull_stream_configs['unit'] = {'source_url': 'rtsp://x', 'stopped': False}
        try:
            sx.streamux_manager._profiles['unit'] = 'medium'
            sx.streamux_manager._encoding.pop('unit', None)
            res = client.put('/api/streamux/unit', json={'encoding': False})
            assert res.status_code == 200
            data = res.get_json()
            assert data['encoding'] is False
            assert data['mode'] == 'passthrough'
            assert sx.streamux_manager.get_encoding('unit') is False
            restart = client.post('/api/streamux/restart', json={'name': 'unit'})
            assert restart.status_code == 409
            err = (restart.get_json() or {}).get('error', '').lower()
            assert 'encoding is off' in err
            prof = client.put('/api/streamux/unit', json={'profile': 'low'})
            assert prof.status_code == 409
            assert 'encoding on' in (prof.get_json() or {}).get('error', '').lower()
            on = client.put('/api/streamux/unit', json={'encoding': True, 'profile': 'low'})
            assert on.status_code == 200
            body = on.get_json()
            assert body['encoding'] is True
            assert body['mode'] == 'encode'
            assert body['profile'] == 'low'
        finally:
            with pull_stream_lock:
                pull_stream_configs.pop('unit', None)
            sx.streamux_manager._profiles.pop('unit', None)
            sx.streamux_manager._encoding.pop('unit', None)
            sx.streamux_manager._errors.pop('unit', None)

    def test_spawn_passthrough_when_encoding_off(self, tmp_path, monkeypatch):
        import app.services.streamux as sx
        monkeypatch.setattr(sx, 'DATA_DIR', str(tmp_path))
        monkeypatch.setattr(sx, 'STATE_FILE', str(tmp_path / 'streamux_profiles.json'))
        monkeypatch.setattr(sx, 'LEGACY_STATE_FILE', str(tmp_path / 'overview_rungs.json'))
        monkeypatch.setattr(sx, 'OVERLAY_STATE_FILE', str(tmp_path / 'streamux_overlay.json'))
        monkeypatch.setattr(sx, 'LEGACY_OVERLAY_STATE_FILE', str(tmp_path / 'overview_overlay.json'))
        monkeypatch.setattr(sx, 'OVERLAY_DIR', str(tmp_path / 'streamux-overlay'))
        monkeypatch.setattr(sx, 'LEGACY_OVERLAY_DIR', str(tmp_path / 'overview-overlay'))
        monkeypatch.setattr(sx, 'ROI_STATE_FILE', str(tmp_path / 'streamux_roi.json'))
        monkeypatch.setattr(sx, 'FFMPEG_LOG_DIR', str(tmp_path / 'ffmpeg'))
        mgr = sx.StreamuxManager()
        mgr._encoding['cam'] = False
        mgr._profiles['cam'] = 'medium'
        captured = {}

        class FakeProc:
            pid = 99
            stdout = None

            def poll(self):
                return None

        def fake_popen(cmd, **kwargs):
            captured['cmd'] = cmd
            return FakeProc()

        monkeypatch.setattr(sx.subprocess, 'Popen', fake_popen)
        monkeypatch.setattr(sx.mediamtx, 'add_path', lambda *a, **k: True)
        monkeypatch.setattr(mgr, '_watch', lambda *a, **k: None)
        mgr._spawn('cam')
        cmd = captured['cmd']
        assert 'libx264' not in cmd
        assert 'copy' in cmd
        assert any(isinstance(x, str) and x.endswith('publish:cam') for x in cmd)
        assert not any(isinstance(x, str) and 'publish:cam__src' in x for x in cmd)

    def _patch_streamux_files(self, tmp_path, monkeypatch):
        import app.services.streamux as sx
        monkeypatch.setattr(sx, 'DATA_DIR', str(tmp_path))
        monkeypatch.setattr(sx, 'STATE_FILE', str(tmp_path / 'streamux_profiles.json'))
        monkeypatch.setattr(sx, 'LEGACY_STATE_FILE', str(tmp_path / 'overview_rungs.json'))
        monkeypatch.setattr(sx, 'OVERLAY_STATE_FILE', str(tmp_path / 'streamux_overlay.json'))
        monkeypatch.setattr(sx, 'LEGACY_OVERLAY_STATE_FILE', str(tmp_path / 'overview_overlay.json'))
        monkeypatch.setattr(sx, 'OVERLAY_DIR', str(tmp_path / 'streamux-overlay'))
        monkeypatch.setattr(sx, 'LEGACY_OVERLAY_DIR', str(tmp_path / 'overview-overlay'))
        monkeypatch.setattr(sx, 'ROI_STATE_FILE', str(tmp_path / 'streamux_roi.json'))
        monkeypatch.setattr(sx, 'FFMPEG_LOG_DIR', str(tmp_path / 'ffmpeg'))
        return sx

    def test_overlay_put_while_encoding_off_does_not_restart(self, client, monkeypatch):
        import app.services.streamux as sx
        from app.state import pull_stream_configs, pull_stream_lock
        restarts = []
        monkeypatch.setattr(sx.streamux_manager, '_restart', lambda n: restarts.append(n))
        monkeypatch.setattr(sx.streamux_manager, '_save', lambda: None)
        monkeypatch.setattr(sx.streamux_manager, '_save_overlays', lambda: None)
        monkeypatch.setattr(sx.streamux_manager, '_save_roi', lambda: None)
        monkeypatch.setattr(sx.mediamtx, 'get_path', lambda *a, **k: {'ready': True})

        class Alive:
            def poll(self):
                return None

        with pull_stream_lock:
            pull_stream_configs['unit'] = {'source_url': 'rtsp://x', 'stopped': False}
        try:
            sx.streamux_manager._profiles['unit'] = 'high'
            sx.streamux_manager._encoding['unit'] = False
            sx.streamux_manager._overlays['unit'] = False
            sx.streamux_manager._procs['unit'] = Alive()
            res = client.put('/api/streamux/unit', json={'overlay': True})
            assert res.status_code == 200
            body = res.get_json()
            assert body['encoding'] is False
            assert body['mode'] == 'passthrough'
            assert body['overlay'] is True
            assert sx.streamux_manager.get_encoding('unit') is False
            assert restarts == []
        finally:
            with pull_stream_lock:
                pull_stream_configs.pop('unit', None)
            sx.streamux_manager._profiles.pop('unit', None)
            sx.streamux_manager._encoding.pop('unit', None)
            sx.streamux_manager._overlays.pop('unit', None)
            sx.streamux_manager._rois.pop('unit', None)
            sx.streamux_manager._procs.pop('unit', None)
            sx.streamux_manager._errors.pop('unit', None)

    def test_roi_parse_persist_and_crop_vf(self, tmp_path, monkeypatch):
        import app.services.streamux as sx
        self._patch_streamux_files(tmp_path, monkeypatch)
        box = {'enabled': True, 'x': 0.12, 'y': 0.45, 'w': 0.70, 'h': 0.40}
        parsed = sx.parse_roi(box)
        assert parsed['enabled'] is True
        assert parsed['x'] == 0.12
        path = tmp_path / 'streamux_roi.json'
        path.write_text(json.dumps({'foot_traffic': box}), encoding='utf-8')
        mgr = sx.StreamuxManager()
        monkeypatch.setattr(mgr, '_restart', lambda n: None)
        monkeypatch.setattr(sx.mediamtx, 'get_path', lambda *a, **k: {'ready': True})
        assert mgr.get_roi('foot_traffic')['w'] == 0.70
        st = mgr.status('foot_traffic')
        assert st['roi']['enabled'] is True
        assert st['roi']['x'] == 0.12
        cmd = mgr._build_cmd('foot_traffic', 'low')
        vf = cmd[cmd.index('-vf') + 1]
        assert vf.startswith('crop=floor(iw*0.700000/2)*2:floor(ih*0.400000/2)*2:')
        assert 'floor(iw*0.120000/2)*2:floor(ih*0.450000/2)*2' in vf
        assert 'scale=426:240:force_original_aspect_ratio=decrease' in vf
        assert 'pad=426:240:' in vf
        mgr.update('foot_traffic', roi=None)
        assert mgr.get_roi('foot_traffic') is None
        saved = json.loads(path.read_text(encoding='utf-8'))
        assert saved == {}
        vf_clear = mgr._build_cmd('foot_traffic', 'low')[cmd.index('-vf') + 1]
        assert not vf_clear.startswith('crop=')

    def test_roi_validation_rejects_tiny_and_oob(self):
        import app.services.streamux as sx
        import pytest
        with pytest.raises(ValueError):
            sx.parse_roi({'x': 0.0, 'y': 0.0, 'w': 0.05, 'h': 0.50})
        with pytest.raises(ValueError):
            sx.parse_roi({'x': 0.0, 'y': 0.0, 'w': 0.50, 'h': 0.05})
        with pytest.raises(ValueError):
            sx.parse_roi({'x': 0.8, 'y': 0.0, 'w': 0.3, 'h': 0.5})
        with pytest.raises(ValueError):
            sx.parse_roi({'x': -0.1, 'y': 0.0, 'w': 0.5, 'h': 0.5})
        with pytest.raises(ValueError):
            sx.parse_roi({'x': '0.1;crop', 'y': 0.0, 'w': 0.5, 'h': 0.5})
        assert sx.parse_roi(None) is None
        assert sx.parse_roi({'enabled': False, 'x': 0.1, 'y': 0.1, 'w': 0.5, 'h': 0.5}) is None

    def test_put_roi_and_encoding_off_does_not_crop_or_restart(self, client, monkeypatch):
        import app.services.streamux as sx
        from app.state import pull_stream_configs, pull_stream_lock
        restarts = []
        monkeypatch.setattr(sx.streamux_manager, '_restart', lambda n: restarts.append(n))
        monkeypatch.setattr(sx.streamux_manager, '_save', lambda: None)
        monkeypatch.setattr(sx.streamux_manager, '_save_overlays', lambda: None)
        monkeypatch.setattr(sx.streamux_manager, '_save_roi', lambda: None)
        monkeypatch.setattr(sx.mediamtx, 'get_path', lambda *a, **k: {'ready': True})

        class Alive:
            def poll(self):
                return None

        box = {'enabled': True, 'x': 0.12, 'y': 0.45, 'w': 0.70, 'h': 0.40}
        with pull_stream_lock:
            pull_stream_configs['unit'] = {'source_url': 'rtsp://x', 'stopped': False}
        try:
            sx.streamux_manager._profiles['unit'] = 'low'
            sx.streamux_manager._encoding['unit'] = True
            sx.streamux_manager._procs['unit'] = Alive()
            sx.streamux_manager._rois.pop('unit', None)
            res = client.put('/api/streamux/unit', json={'roi': box})
            assert res.status_code == 200
            body = res.get_json()
            assert body['roi']['enabled'] is True
            assert body['roi']['w'] == 0.70
            assert restarts == ['unit']
            vf = sx.streamux_manager._build_cmd('unit', 'low')
            assert vf[vf.index('-vf') + 1].startswith('crop=')
            restarts.clear()
            sx.streamux_manager._encoding['unit'] = False
            sx.streamux_manager._procs['unit'] = Alive()
            off = client.put('/api/streamux/unit', json={'roi': {
                'x': 0.20, 'y': 0.20, 'w': 0.50, 'h': 0.50,
            }})
            assert off.status_code == 200
            assert off.get_json()['encoding'] is False
            assert off.get_json()['mode'] == 'passthrough'
            assert sx.streamux_manager.get_roi('unit')['x'] == 0.20
            assert restarts == []
            pt = ' '.join(sx.streamux_manager._build_passthrough_cmd('unit'))
            assert 'crop=' not in pt
            tiny = client.put('/api/streamux/unit', json={'roi': {
                'x': 0.0, 'y': 0.0, 'w': 0.05, 'h': 0.5,
            }})
            assert tiny.status_code == 400
            cleared = client.put('/api/streamux/unit', json={'roi': None})
            assert cleared.status_code == 200
            assert cleared.get_json()['roi'] is None
            assert restarts == []
        finally:
            with pull_stream_lock:
                pull_stream_configs.pop('unit', None)
            sx.streamux_manager._profiles.pop('unit', None)
            sx.streamux_manager._encoding.pop('unit', None)
            sx.streamux_manager._rois.pop('unit', None)
            sx.streamux_manager._procs.pop('unit', None)
            sx.streamux_manager._errors.pop('unit', None)

    def test_still_from_src_not_published(self, client, monkeypatch):
        import app.services.streamux as sx
        from app.state import pull_stream_configs, pull_stream_lock
        jpeg = b'\xff\xd8\xff\xd9'
        captured = {}

        def fake_run(cmd, **kwargs):
            captured['cmd'] = cmd
            class Result:
                returncode = 0
                stdout = jpeg
                stderr = b''
            return Result()

        monkeypatch.setattr(sx.subprocess, 'run', fake_run)
        monkeypatch.setattr(sx.mediamtx, 'get_path', lambda name: {
            'ready': str(name).endswith('__src'),
        })
        with pull_stream_lock:
            pull_stream_configs['foot_traffic'] = {'source_url': 'rtsp://x', 'stopped': False}
        try:
            missing = client.get('/api/streamux/missing/still')
            assert missing.status_code == 404
            assert 'missing/still' not in (missing.get_json() or {}).get('error', '')
            res = client.get('/api/streamux/foot_traffic/still')
            assert res.status_code == 200
            assert res.mimetype == 'image/jpeg'
            assert res.data == jpeg
            joined = ' '.join(captured['cmd'])
            assert 'foot_traffic__src' in joined
            assert '-frames:v' in captured['cmd']
            pub = [p for p in captured['cmd'] if p.endswith('/foot_traffic') and '__src' not in p]
            assert pub == []
        finally:
            with pull_stream_lock:
                pull_stream_configs.pop('foot_traffic', None)
            sx.streamux_manager._still_cache.pop('foot_traffic', None)

    def test_still_409_when_ingest_down(self, client, monkeypatch):
        import app.services.streamux as sx
        from app.state import pull_stream_configs, pull_stream_lock
        monkeypatch.setattr(sx.mediamtx, 'get_path', lambda *_a, **_k: {'ready': False})
        with pull_stream_lock:
            pull_stream_configs['unit'] = {'source_url': 'rtsp://x', 'stopped': False}
        try:
            res = client.get('/api/streamux/unit/still')
            assert res.status_code == 409
            err = (res.get_json() or {}).get('error', '').lower()
            assert 'still' in err or 'source' in err
        finally:
            with pull_stream_lock:
                pull_stream_configs.pop('unit', None)

    def test_watch_does_not_respawn_encode_after_encoding_off(self, tmp_path, monkeypatch):
        import app.services.streamux as sx
        self._patch_streamux_files(tmp_path, monkeypatch)
        mgr = sx.StreamuxManager()
        mgr._encoding['cam'] = True
        mgr._profiles['cam'] = 'high'
        mgr._generations['cam'] = 1
        ensured = []

        def fake_ensure(name, gen=None):
            ensured.append((name, gen, mgr.get_encoding(name)))

        monkeypatch.setattr(mgr, '_ensure', fake_ensure)
        monkeypatch.setattr(sx.time, 'sleep', lambda _s: mgr._encoding.__setitem__('cam', False))

        class DeadProc:
            def wait(self):
                return 1

            def poll(self):
                return 1

        proc = DeadProc()
        mgr._procs['cam'] = proc
        mgr._watch('cam', proc, 'high', mode='encode', gen=1)
        assert ensured == []
        assert mgr.get_encoding('cam') is False

    def test_face_error_source_down_hides_ffmpeg_tail(self, tmp_path, monkeypatch):
        sx = self._patch_streamux_files(tmp_path, monkeypatch)
        mgr = sx.StreamuxManager()
        mgr._errors['cam'] = (
            'encoder exited 0.\nFailed reading RTSP data: End of file\n'
            'Output file is empty, nothing was encoded'
        )
        monkeypatch.setattr(sx.mediamtx, 'get_path', lambda *_a, **_k: {'ready': False})
        st = mgr.status('cam')
        assert st['sourceReady'] is False
        assert 'End of file' not in st['lastError']
        assert 'nothing was encoded' not in st['lastError']
        assert st['lastError'] == sx.SOURCE_DOWN_MSG

    def test_face_error_ingest_up_published_down(self, tmp_path, monkeypatch):
        sx = self._patch_streamux_files(tmp_path, monkeypatch)
        mgr = sx.StreamuxManager()
        mgr._encoding['cam'] = True
        mgr._errors['cam'] = 'passthrough exited 0\nFailed reading RTSP data: End of file'

        def get_path(name):
            if str(name).endswith('__src'):
                return {'ready': True}
            return {'ready': False}

        monkeypatch.setattr(sx.mediamtx, 'get_path', get_path)
        st = mgr.status('cam')
        assert st['sourceReady'] is True
        assert st['publishedReady'] is False
        assert st['lastError'] == ''
        assert 'ATAK' not in st['lastError']
        assert 'End of file' not in st['lastError']

    def test_stopped_pull_clears_card_error(self, client, monkeypatch):
        import app.services.streamux as sx
        from app.state import pull_stream_configs, pull_stream_lock
        monkeypatch.setattr(sx.mediamtx, 'get_path', lambda *_a, **_k: {'ready': False})
        sx.streamux_manager._errors['unit'] = 'passthrough exited 0\nEnd of file'
        with pull_stream_lock:
            pull_stream_configs['unit'] = {'source_url': 'rtsp://x', 'stopped': True}
        try:
            data = client.get('/api/streamux').get_json()
            row = next(s for s in data['streams'] if s['name'] == 'unit')
            assert row['stopped'] is True
            assert row['lastError'] == ''
        finally:
            with pull_stream_lock:
                pull_stream_configs.pop('unit', None)
            sx.streamux_manager._errors.pop('unit', None)

    def test_watch_stale_generation_does_not_retry(self, tmp_path, monkeypatch):
        import app.services.streamux as sx
        self._patch_streamux_files(tmp_path, monkeypatch)
        mgr = sx.StreamuxManager()
        mgr._encoding['cam'] = True
        mgr._profiles['cam'] = 'high'
        mgr._generations['cam'] = 2
        ensured = []
        monkeypatch.setattr(mgr, '_ensure', lambda *a, **k: ensured.append(1))
        monkeypatch.setattr(sx.time, 'sleep', lambda _s: None)

        class DeadProc:
            def wait(self):
                return 1

            def poll(self):
                return 1

        proc = DeadProc()
        mgr._procs['cam'] = proc
        mgr._watch('cam', proc, 'high', mode='encode', gen=1)
        assert ensured == []

    def test_spawn_skips_x264_if_encoding_flips_off(self, tmp_path, monkeypatch):
        import app.services.streamux as sx
        self._patch_streamux_files(tmp_path, monkeypatch)
        mgr = sx.StreamuxManager()
        mgr._encoding['cam'] = True
        mgr._profiles['cam'] = 'high'
        mgr._overlays['cam'] = True
        mgr._generations['cam'] = 5
        orig_n = {'n': 0}

        def get_enc(name):
            orig_n['n'] += 1
            return orig_n['n'] == 1

        monkeypatch.setattr(mgr, 'get_encoding', get_enc)
        monkeypatch.setattr(mgr, 'get_overlay', lambda n: True)
        monkeypatch.setattr(sx, 'find_font', lambda: '/tmp/fake.ttf')
        monkeypatch.setattr(mgr, '_seed_overlay_text', lambda *a, **k: None)
        monkeypatch.setattr(mgr, '_build_cmd', lambda *a, **k: ['ffmpeg', 'ENCODE'])
        captured = []
        monkeypatch.setattr(sx.subprocess, 'Popen', lambda *a, **k: captured.append(a) or None)
        mgr._spawn('cam', expected=5)
        assert captured == []

    def test_hw_api_schema(self, client):
        response = client.get('/api/streamux/hw')
        assert response.status_code == 200
        data = response.get_json()
        assert 'scope' in data
        assert 'cpu' in data
        assert 'memory' in data
        assert 'disk' in data
        assert 'temp' in data
        assert 'uptime' in data
        assert data.get('top_cpu') == []
        assert data.get('top_ram') == []
        assert 'rung' not in data
        assert data['scope']['processes'] in ('container', 'host', 'unavailable')
        assert 'celsius' in (data.get('temp') or {})

    def test_hw_api_procs_query(self, client, tmp_path, monkeypatch):
        import app.services.hoststats as hs
        from app.api import streamux as api
        root = _write_fake_proc(tmp_path)
        reader = hs.HostStats(
            proc_root=str(root),
            host_proc_candidates=(),
            disk_path=str(tmp_path),
            sys_root=str(tmp_path / 'nosys'),
            clk_tck=100,
            page_size=4096,
        )
        monkeypatch.setattr(api, 'read_hw', lambda include_procs=False: reader.snapshot(
            include_procs=include_procs, wait_s=0,
        ))
        empty = client.get('/api/streamux/hw').get_json()
        assert empty['top_cpu'] == []
        data = client.get('/api/streamux/hw?procs=1').get_json()
        assert data['scope']['processes'] == 'container'
        assert data['scope']['processes_label'] == 'this container (tvr-edge)'
        names = [r['name'] for r in data['top_cpu']]
        assert 'ffmpeg foot_traffic' in names
        assert not any('rtsp://secret' in (r['name'] or '') for r in data['top_cpu'])

    def test_hoststats_cpu_mem_disk_uptime(self, tmp_path):
        import app.services.hoststats as hs
        root = _write_fake_proc(tmp_path)
        reader = hs.HostStats(
            proc_root=str(root),
            host_proc_candidates=(),
            disk_path=str(tmp_path),
            sys_root=str(tmp_path / 'nosys'),
            clk_tck=100,
            page_size=4096,
        )
        first = reader.snapshot(include_procs=False, wait_s=0)
        assert first['scope']['stats'] == 'host_kernel'
        assert first['cpu']['percent'] is None
        assert first['cpu']['nproc'] == 4
        assert first['cpu']['load1'] == 0.42
        assert first['memory']['percent'] == 82.1
        assert first['uptime']['text'] == '1d 6h 26m'
        assert first['disk']['total_bytes']
        assert first['temp']['celsius'] is None
        (root / 'stat').write_text(
            'cpu  150 0 70 880 0 0 0 0 0 0\n'
            'cpu0 0 0 0 0 0 0 0 0 0 0\n'
            'cpu1 0 0 0 0 0 0 0 0 0 0\n'
            'cpu2 0 0 0 0 0 0 0 0 0 0\n'
            'cpu3 0 0 0 0 0 0 0 0 0 0\n',
            encoding='utf-8',
        )
        second = reader.snapshot(include_procs=False, wait_s=0)
        assert second['cpu']['percent'] == 70.0

    def test_hoststats_host_proc_scope(self, tmp_path):
        import app.services.hoststats as hs
        stats = _write_fake_proc(tmp_path / 'stats')
        host = _write_fake_proc(tmp_path / 'host')
        reader = hs.HostStats(
            proc_root=str(stats),
            host_proc_candidates=(str(host),),
            disk_path=str(tmp_path),
            sys_root=str(tmp_path / 'nosys'),
            clk_tck=100,
            page_size=4096,
        )
        data = reader.snapshot(include_procs=True, wait_s=0)
        assert data['scope']['processes'] == 'host'
        assert data['scope']['host_proc_mounted'] is True
        assert data['scope']['stats'] == 'host_kernel'

    def test_hoststats_cpu_thermal(self, tmp_path):
        import app.services.hoststats as hs
        root = _write_fake_proc(tmp_path / 'proc')
        sys_root = _write_fake_thermal(tmp_path / 'sys', [
            ('thermal_zone1', 'gpu-thermal', 41000),
            ('thermal_zone0', 'cpu-thermal', 72150),
        ])
        (Path(sys_root) / 'class' / 'thermal' / 'cooling_device0').mkdir()
        reader = hs.HostStats(
            proc_root=str(root),
            host_proc_candidates=(),
            disk_path=str(tmp_path),
            sys_root=str(sys_root),
            clk_tck=100,
            page_size=4096,
        )
        data = reader.snapshot(include_procs=False, wait_s=0)
        assert data['temp']['celsius'] == 72.2
        assert data['temp']['type'] == 'cpu-thermal'

    def test_static_css_loads(self, client):
        """Test that CSS file loads successfully"""
        response = client.get('/static/styles.css')
        assert response.status_code == 200
        assert 'text/css' in response.content_type
        assert b'.streamux-settings' in response.data
        assert b'--streamux-profile-cols' in response.data
        assert b'--streamux-settings-half-col' in response.data
        assert b'calc((100% - 1.2rem) / 6)' in response.data
        assert b'grid-template-columns: max-content' in response.data

    def test_static_js_loads(self, client):
        """Test that JavaScript file loads successfully"""
        response = client.get('/static/client.js')
        assert response.status_code == 200


# =============================================================================
# Integration Tests
# =============================================================================

class TestAPIIntegration:
    """Test suite for API integration workflows"""
    
    def test_health_to_streams_flow(self, client):
        """Test workflow from health check to streams listing"""
        # Check health
        health_response = client.get('/health')
        assert health_response.status_code == 200
        
        # List streams
        streams_response = client.get('/api/streams')
        assert streams_response.status_code == 200
        
        # Get settings
        settings_response = client.get('/api/settings')
        assert settings_response.status_code == 200
    
    def test_recordings_workflow(self, client):
        """Test complete recordings workflow"""
        # List recordings
        list_response = client.get('/api/recordings')
        assert list_response.status_code == 200
        data = json.loads(list_response.data)
        assert isinstance(data, list)
    
    def test_settings_workflow(self, client):
        """Test settings get/update workflow"""
        # Get current settings
        get_response = client.get('/api/settings')
        assert get_response.status_code == 200
        
        # Update settings
        update_response = client.post('/api/settings',
                                    json={'autoRecord': False},
                                    content_type='application/json')
        assert update_response.status_code == 200


# =============================================================================
# Error Handling Tests
# =============================================================================

class TestErrorHandling:
    """Test suite for error handling"""
    
    def test_404_for_invalid_endpoint(self, client):
        """Test that invalid endpoints return 404"""
        response = client.get('/api/nonexistent')
        assert response.status_code == 404
    
    def test_405_for_wrong_method(self, client):
        """Test that wrong HTTP methods return 405"""
        response = client.post('/health')
        assert response.status_code == 405
    
    def test_400_for_invalid_json(self, client):
        """Test that invalid JSON with PUT returns 405"""
        response = client.put('/api/settings',
                            data='invalid json',
                            content_type='application/json')
        assert response.status_code == 405


# =============================================================================
# Stream Validation Tests
# =============================================================================

def _report(**overrides):
    """Minimal validator report, shaped like utils/validate_stream.py output."""
    report = {
        'target': 'rtsp://localhost:8554/drone1',
        'ok': True,
        'has_warnings': False,
        'checks': [
            {'id': 'klv_track', 'name': 'KLV track present', 'status': 'pass',
             'summary': 'KLV data track found (stream index 2).', 'hint': None, 'detail': {}},
        ],
        'streams': [],
    }
    report.update(overrides)
    return report


class TestStreamValidation:
    """Test suite for /api/stream/validate"""

    def test_requires_a_target(self, client):
        """Neither streamName nor videoFile is a 400"""
        response = client.post('/api/stream/validate', json={})
        assert response.status_code == 400

    def test_rejects_both_targets(self, client):
        """streamName and videoFile are mutually exclusive"""
        response = client.post('/api/stream/validate',
                               json={'streamName': 'drone1', 'videoFile': 'a/b.ts'})
        assert response.status_code == 400

    def test_rejects_invalid_stream_name(self, client):
        """Stream names are restricted to a safe character set"""
        response = client.post('/api/stream/validate',
                               json={'streamName': '../../etc/passwd'})
        assert response.status_code == 400

    def test_rejects_path_traversal(self, client):
        """Relative video paths may not escape STREAMS_DIR"""
        response = client.post('/api/stream/validate',
                               json={'videoFile': '../../../etc/passwd'})
        assert response.status_code == 400

    def test_rejects_out_of_range_window(self, client):
        """Sample window is bounded"""
        response = client.post('/api/stream/validate',
                               json={'streamName': 'drone1', 'window': 9999})
        assert response.status_code == 400

    def test_clean_stream_reports_ok(self, client):
        """A stream passing every check returns ok=True"""
        with patch('validate_stream.validate', return_value=_report()):
            response = client.post('/api/stream/validate', json={'streamName': 'drone1'})

        assert response.status_code == 200
        data = json.loads(response.data)
        assert data['success'] is True
        assert data['report']['ok'] is True

    def test_non_monotonic_klv_is_flagged(self, client):
        """The KLV timestamp defect surfaces as a failing check with a hint"""
        broken = _report(ok=False, checks=[
            {'id': 'klv_timestamps', 'name': 'KLV timestamps monotonic', 'status': 'fail',
             'summary': 'KLV PTS runs backwards on 75/217 steps (34.6%).',
             'hint': 'Repair with: -bsf:d "setts=pts=DTS:dts=DTS"',
             'detail': {'backward_steps': 75}},
        ])
        with patch('validate_stream.validate', return_value=broken):
            response = client.post('/api/stream/validate', json={'streamName': 'drone1'})

        assert response.status_code == 200
        data = json.loads(response.data)
        assert data['report']['ok'] is False
        failed = [c for c in data['report']['checks'] if c['status'] == 'fail']
        assert len(failed) == 1
        assert failed[0]['id'] == 'klv_timestamps'
        assert failed[0]['hint']

    def test_missing_klv_track_is_flagged(self, client):
        """A stream published over RTSP arrives with no data track at all"""
        broken = _report(ok=False, checks=[
            {'id': 'klv_track', 'name': 'KLV track present', 'status': 'fail',
             'summary': 'No data track — the stream carries no KLV at all.',
             'hint': 'Publish over SRT instead, and pass -map 0.', 'detail': {}},
        ])
        with patch('validate_stream.validate', return_value=broken):
            response = client.post('/api/stream/validate', json={'streamName': 'drone1'})

        data = json.loads(response.data)
        assert data['report']['ok'] is False
        assert data['report']['checks'][0]['id'] == 'klv_track'


class TestKLVTimestampRepairSetting:
    """The repair_klv_timestamps setting must reach the pull-stream FFmpeg args"""

    def test_setting_defaults_on(self):
        from app.config import SERVER_SETTINGS
        assert SERVER_SETTINGS['repair_klv_timestamps'] is True

    def test_setting_is_in_schema(self):
        from app.config import SERVER_SETTINGS_SCHEMA
        assert SERVER_SETTINGS_SCHEMA['repair_klv_timestamps'] == (bool,)

    def test_bsf_present_when_enabled(self):
        from app.api.streams import _build_pull_ffmpeg_args
        from app.api.settings import server_settings

        with patch.dict(server_settings, {'repair_klv_timestamps': True}):
            args = _build_pull_ffmpeg_args('srt://example:9000', 'drone1')

        assert '-bsf:d' in args
        assert args[args.index('-bsf:d') + 1] == 'setts=pts=DTS:dts=DTS'

    def test_bsf_absent_when_disabled(self):
        from app.api.streams import _build_pull_ffmpeg_args
        from app.api.settings import server_settings

        with patch.dict(server_settings, {'repair_klv_timestamps': False}):
            args = _build_pull_ffmpeg_args('srt://example:9000', 'drone1')

        assert '-bsf:d' not in args

    def test_map_all_streams_still_present(self):
        """-map 0 is what keeps the KLV track; guard against regression"""
        from app.api.streams import _build_pull_ffmpeg_args

        args = _build_pull_ffmpeg_args('srt://example:9000', 'drone1')
        assert '-map' in args
        assert args[args.index('-map') + 1] == '0'
        # Republished as MPEG-TS over SRT, never RTSP - RTP cannot carry KLV.
        assert 'mpegts' in args


class TestTranscodeOptions:
    """Option list must not advertise anything the backend cannot run"""

    def test_options_are_1_to_3(self, client):
        response = client.get('/api/transcode/options')
        assert response.status_code == 200
        data = json.loads(response.data)
        assert [o['option'] for o in data['options']] == [1, 2, 3]

    def test_default_option_is_offered(self, client):
        """Default must be one of the advertised options"""
        response = client.get('/api/transcode/options')
        data = json.loads(response.data)
        assert data['default'] in [o['option'] for o in data['options']]

    def test_option_4_is_rejected(self, client):
        """Option 4 depended on a script that never existed.

        The file-existence check runs first, so stub it out to reach the
        option validation.
        """
        with patch('app.api.utils.os.path.exists', return_value=True):
            response = client.post('/api/transcode',
                                   json={'inputFile': 'anything.mov', 'option': 4})

        assert response.status_code == 400
        assert '1-3' in json.loads(response.data)['error']

    def test_valid_option_passes_validation(self, client):
        """Guard the above: option 1 must get past the same validation gate"""
        with patch('app.api.utils.os.path.exists', return_value=True), \
                patch('app.api.utils.subprocess.Popen'):
            response = client.post('/api/transcode',
                                   json={'inputFile': 'anything.mov', 'option': 1})

        assert response.status_code != 400

    def test_extract_endpoint_is_gone(self, client):
        """/api/klv/extract was backed by a missing script; it should 404"""
        response = client.post('/api/klv/extract', json={'videoFile': 'a/b.ts'})
        assert response.status_code == 404


class TestAbrHolders:
    """Video Wall ABR must not leak after the wall is unused; Dashboard ABR stays on."""

    def _manager(self, tmp_path, monkeypatch):
        import app.services.abr as abr_mod
        monkeypatch.setattr(abr_mod, 'HLS_OUTPUT_DIR', str(tmp_path / 'hls'))
        monkeypatch.setattr(abr_mod, 'ABR_STATE_FILE', str(tmp_path / 'abr_state.json'))
        mgr = abr_mod.ABRManager()
        monkeypatch.setattr(mgr, '_start_monitor_thread', lambda name: None)
        return mgr, abr_mod

    def _persisted(self, abr_mod):
        path = abr_mod.ABR_STATE_FILE
        if not os.path.exists(path):
            return None
        with open(path, encoding='utf-8') as f:
            return json.load(f)

    def test_wall_stop_kills_wall_only_abr(self, tmp_path, monkeypatch):
        mgr, abr_mod = self._manager(tmp_path, monkeypatch)
        started = mgr.start('MOHOC', source='wall')
        assert started['status'] == 'started'
        assert started['operator'] is False
        assert started['wall'] == 1
        assert mgr.status('MOHOC')['running'] is True
        assert self._persisted(abr_mod) == {'streams': []}

        stopped = mgr.stop('MOHOC', source='wall')
        assert stopped['status'] == 'stopped'
        assert stopped['running'] is False
        assert mgr.status('MOHOC')['running'] is False

    def test_wall_stop_preserves_operator_abr(self, tmp_path, monkeypatch):
        mgr, abr_mod = self._manager(tmp_path, monkeypatch)
        mgr.start('yt_plates', source='operator')
        mgr.start('yt_plates', source='wall')
        assert mgr.status('yt_plates')['operator'] is True
        assert mgr.status('yt_plates')['wall'] == 1
        assert self._persisted(abr_mod) == {'streams': ['yt_plates']}

        released = mgr.stop('yt_plates', source='wall')
        assert released['status'] == 'released'
        assert released['running'] is True
        assert mgr.status('yt_plates')['running'] is True
        assert mgr.status('yt_plates')['operator'] is True
        assert mgr.status('yt_plates')['wall'] == 0
        assert self._persisted(abr_mod) == {'streams': ['yt_plates']}

    def test_operator_off_stops_even_if_wall_is_watching(self, tmp_path, monkeypatch):
        mgr, _abr_mod = self._manager(tmp_path, monkeypatch)
        mgr.start('MOHOC', source='operator')
        mgr.start('MOHOC', source='wall')
        stopped = mgr.stop('MOHOC', source='operator')
        assert stopped['status'] == 'stopped'
        assert mgr.status('MOHOC')['running'] is False

    def test_two_wall_viewers_last_release_stops(self, tmp_path, monkeypatch):
        mgr, _abr_mod = self._manager(tmp_path, monkeypatch)
        mgr.start('cam1', source='wall')
        mgr.start('cam1', source='wall')
        first = mgr.stop('cam1', source='wall')
        assert first['status'] == 'released'
        assert first['wall'] == 1
        assert mgr.status('cam1')['running'] is True
        second = mgr.stop('cam1', source='wall')
        assert second['status'] == 'stopped'
        assert mgr.status('cam1')['running'] is False

    def test_operator_upgrade_persists_after_wall_start(self, tmp_path, monkeypatch):
        mgr, abr_mod = self._manager(tmp_path, monkeypatch)
        mgr.start('MOHOC', source='wall')
        assert self._persisted(abr_mod) == {'streams': []}
        upgraded = mgr.start('MOHOC', source='operator')
        assert upgraded['status'] == 'already_running'
        assert upgraded['operator'] is True
        assert self._persisted(abr_mod) == {'streams': ['MOHOC']}
        mgr.stop('MOHOC', source='wall')
        assert mgr.status('MOHOC')['running'] is True

    def test_api_wall_query_param(self, client, tmp_path, monkeypatch):
        import app.services.abr as abr_mod
        import app.api.hls as hls_mod
        mgr, _ = self._manager(tmp_path, monkeypatch)
        monkeypatch.setattr(hls_mod, 'abr_manager', mgr)
        monkeypatch.setattr(abr_mod, 'abr_manager', mgr)

        start = client.post('/api/streams/MOHOC/abr?source=wall')
        assert start.status_code == 200
        body = json.loads(start.data)
        assert body['status'] == 'started'
        assert body['operator'] is False
        assert body['wall'] == 1

        stop = client.delete('/api/streams/MOHOC/abr?source=wall')
        assert stop.status_code == 200
        stopped = json.loads(stop.data)
        assert stopped['status'] == 'stopped'
        assert json.loads(client.get('/api/streams/MOHOC/abr/status').data)['running'] is False

    def test_api_default_is_operator_and_survives_wall_release(self, client, tmp_path, monkeypatch):
        import app.api.hls as hls_mod
        mgr, _ = self._manager(tmp_path, monkeypatch)
        monkeypatch.setattr(hls_mod, 'abr_manager', mgr)

        assert client.post('/api/streams/yt_plates/abr').status_code == 200
        assert client.post('/api/streams/yt_plates/abr?source=wall').status_code == 200
        released = json.loads(client.delete('/api/streams/yt_plates/abr?source=wall').data)
        assert released['status'] == 'released'
        assert released['running'] is True
        status = json.loads(client.get('/api/streams/yt_plates/abr/status').data)
        assert status['running'] is True
        assert status['operator'] is True

    def test_api_rejects_unknown_source(self, client):
        response = client.post('/api/streams/MOHOC/abr?source=sneaky')
        assert response.status_code == 400


# =============================================================================
# Test Runner (when executed directly)
# =============================================================================

def main():
    """Run pytest with the provided arguments"""
    try:
        import pytest
    except ImportError:
        print("ERROR: pytest is not installed")
        print("Install it with: pip install pytest")
        return 1
    
    args = sys.argv[1:]
    
    # Default pytest arguments - run this file
    pytest_args = [__file__]
    
    # Add user arguments
    if args:
        pytest_args.extend(args)
    else:
        # Default: run with verbose output
        pytest_args.append('-v')
    
    # Run pytest
    print("=" * 70)
    print("TAK Video Restreamer - Test Suite")
    print("=" * 70)
    print(f"Running: pytest {' '.join(pytest_args)}\n")
    
    result = pytest.main(pytest_args)
    
    print("\n" + "=" * 70)
    if result == 0:
        print("✓ All tests passed!")
    else:
        print("✗ Some tests failed")
    print("=" * 70)
    
    return result


if __name__ == '__main__':
    sys.exit(main())
