"""
Overview ladder API — ATAK published-path rungs (not ABR HLS).
"""
import logging
from flask import Blueprint, jsonify, request

from app.services.overview import overview_manager, RUNGS, rung_catalog, normalize_rung
from app.state import pull_stream_configs, pull_stream_lock

logger = logging.getLogger(__name__)

overview_bp = Blueprint('overview', __name__)


def _pull_names() -> list:
    with pull_stream_lock:
        return sorted(pull_stream_configs.keys())


@overview_bp.route('/api/overview', methods=['GET'])
def list_overview():
    names = _pull_names()
    streams = []
    for name in names:
        st = overview_manager.status(name)
        cfg = pull_stream_configs.get(name) or {}
        st['sourceUrl'] = cfg.get('source_url', '')
        streams.append(st)
    return jsonify({
        'rungs': rung_catalog(),
        'streams': streams,
    })


@overview_bp.route('/api/overview/<path:stream_name>', methods=['GET'])
def get_overview(stream_name):
    with pull_stream_lock:
        if stream_name not in pull_stream_configs:
            return jsonify({'error': f'No pull stream named {stream_name}'}), 404
    st = overview_manager.status(stream_name)
    st['sourceUrl'] = pull_stream_configs[stream_name].get('source_url', '')
    return jsonify(st)


@overview_bp.route('/api/overview/<path:stream_name>', methods=['PUT'])
def set_overview(stream_name):
    with pull_stream_lock:
        if stream_name not in pull_stream_configs:
            return jsonify({'error': f'No pull stream named {stream_name}'}), 404
    data = request.get_json(silent=True) or {}
    rung = normalize_rung((data.get('rung') or '').strip().lower())
    if rung not in RUNGS:
        return jsonify({'error': f'rung must be one of: {", ".join(RUNGS)}'}), 400
    try:
        st = overview_manager.set_rung(stream_name, rung)
    except ValueError as e:
        return jsonify({'error': str(e)}), 400
    except Exception as e:
        logger.error(f"overview set_rung {stream_name}: {e}")
        return jsonify({'error': str(e)}), 500
    st['sourceUrl'] = pull_stream_configs[stream_name].get('source_url', '')
    return jsonify(st)


@overview_bp.route('/api/overview/restart', methods=['POST'])
def restart_overview():
    data = request.get_json(silent=True) or {}
    stream_name = (data.get('name') or '').strip()
    if not stream_name:
        return jsonify({'error': 'name required'}), 400
    with pull_stream_lock:
        if stream_name not in pull_stream_configs:
            return jsonify({'error': f'No pull stream named {stream_name}'}), 404
    try:
        st = overview_manager.restart(stream_name)
    except Exception as e:
        logger.error(f"overview restart {stream_name}: {e}")
        return jsonify({'error': str(e)}), 500
    st['sourceUrl'] = pull_stream_configs[stream_name].get('source_url', '')
    return jsonify(st)
