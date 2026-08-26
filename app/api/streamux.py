"""
StreamUx API — ATAK published-path profiles (not ABR HLS).
"""
import logging
from flask import Blueprint, jsonify, request

from app.services.streamux import (
    streamux_manager, PROFILES, profile_catalog, normalize_profile, EncodingOff,
)
from app.services.hoststats import read_hw
from app.state import pull_stream_configs, pull_stream_lock

logger = logging.getLogger(__name__)


def _as_bool(value) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value)
    if isinstance(value, str):
        return value.strip().lower() in ('1', 'true', 'yes', 'on')
    return bool(value)

streamux_bp = Blueprint('streamux', __name__)


def _pull_names() -> list:
    with pull_stream_lock:
        return sorted(pull_stream_configs.keys())


def _attach_pull(st: dict, stream_name: str) -> dict:
    cfg = pull_stream_configs.get(stream_name) or {}
    st['sourceUrl'] = cfg.get('source_url', '')
    st['stopped'] = bool(cfg.get('stopped'))
    return st


def _require_running_pull(stream_name: str):
    with pull_stream_lock:
        if stream_name not in pull_stream_configs:
            return jsonify({'error': f'No pull stream named {stream_name}'}), 404
        if pull_stream_configs[stream_name].get('stopped'):
            return jsonify({
                'error': f'{stream_name} is stopped. Start it on the Dashboard first.'
            }), 409
    return None


@streamux_bp.route('/api/streamux', methods=['GET'])
def list_streamux():
    names = _pull_names()
    streams = []
    for name in names:
        st = streamux_manager.status(name)
        streams.append(_attach_pull(st, name))
    return jsonify({
        'profiles': profile_catalog(),
        'streams': streams,
    })


@streamux_bp.route('/api/streamux/hw', methods=['GET'])
def streamux_hw():
    """CM5 kernel stats + process table. Process list is this container unless /host/proc is mounted."""
    raw = (request.args.get('procs') or '').strip().lower()
    include_procs = raw in ('1', 'true', 'yes', 'on')
    return jsonify(read_hw(include_procs=include_procs))


@streamux_bp.route('/api/streamux/restart', methods=['POST'])
def restart_streamux():
    data = request.get_json(silent=True) or {}
    stream_name = (data.get('name') or '').strip()
    if not stream_name:
        return jsonify({'error': 'name required'}), 400
    err = _require_running_pull(stream_name)
    if err:
        return err
    try:
        st = streamux_manager.restart(stream_name)
    except EncodingOff as e:
        return jsonify({'error': str(e)}), 409
    except Exception as e:
        logger.error(f"streamux restart {stream_name}: {e}")
        return jsonify({'error': str(e)}), 500
    return jsonify(_attach_pull(st, stream_name))


@streamux_bp.route('/api/streamux/<stream_name>/log', methods=['GET'])
def get_streamux_log(stream_name):
    with pull_stream_lock:
        if stream_name not in pull_stream_configs:
            return jsonify({'error': f'No pull stream named {stream_name}'}), 404
    raw = request.args.get('lines', 100)
    try:
        n = int(raw)
    except (TypeError, ValueError):
        n = 100
    n = max(1, min(n, 100))
    data = streamux_manager.read_encoder_log(stream_name, lines=n)
    return jsonify(data)


@streamux_bp.route('/api/streamux/<path:stream_name>', methods=['GET'])
def get_streamux(stream_name):
    with pull_stream_lock:
        if stream_name not in pull_stream_configs:
            return jsonify({'error': f'No pull stream named {stream_name}'}), 404
    st = streamux_manager.status(stream_name)
    return jsonify(_attach_pull(st, stream_name))


@streamux_bp.route('/api/streamux/<path:stream_name>', methods=['PUT'])
def set_streamux(stream_name):
    err = _require_running_pull(stream_name)
    if err:
        return err
    data = request.get_json(silent=True) or {}
    has_profile = 'profile' in data and str(data.get('profile') or '').strip() != ''
    has_overlay = 'overlay' in data
    has_encoding = 'encoding' in data
    if not has_profile and not has_overlay and not has_encoding:
        return jsonify({'error': 'profile, overlay, or encoding required'}), 400
    profile = None
    overlay = None
    encoding = None
    if has_profile:
        profile = normalize_profile(str(data.get('profile') or '').strip().lower())
        if profile not in PROFILES:
            return jsonify({'error': f'profile must be one of: {", ".join(PROFILES)}'}), 400
        turning_on = has_encoding and _as_bool(data.get('encoding'))
        if not streamux_manager.get_encoding(stream_name) and not turning_on:
            return jsonify({
                'error': 'Turn encoding on to change profile',
            }), 409
    if has_overlay:
        overlay = _as_bool(data.get('overlay'))
    if has_encoding:
        encoding = _as_bool(data.get('encoding'))
    try:
        st = streamux_manager.update(
            stream_name, profile=profile, overlay=overlay, encoding=encoding,
        )
    except EncodingOff as e:
        return jsonify({'error': str(e)}), 409
    except ValueError as e:
        return jsonify({'error': str(e)}), 400
    except Exception as e:
        logger.error(f"streamux update {stream_name}: {e}")
        return jsonify({'error': str(e)}), 500
    return jsonify(_attach_pull(st, stream_name))
