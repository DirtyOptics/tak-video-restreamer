"""
This material is based upon work supported by the United States Air Force under contract number FA8750-24-S-B079 (Prime Contractor Smart Information Flow Technologies (SIFT)).  Any opinions, findings and conclusions or recommendations expressed in this material are those of the author(s) and do not necessarily reflect the views of the United States Air Force.
 Copyright (c) 2026 RTX BBN Technologies. Licensed to US Government with unlimited rights.

This program is free software: you can redistribute it and/or modify it under the terms of the GNU General Public License as published by the Free Software Foundation, either version 3 of the License, or (at your option) any later version.
This is distributed in the hope that it will be useful, but without any warranty, without even the implied warranty of merchantability or fitness for a particular purpose.  See the GNU General Public License for more details. https://www.gnu.org/licenses/

TLS / Certificate API Blueprint

Manages RTSPS and HTTPS certificate configuration.
"""
import logging
from flask import Blueprint, request, jsonify
from werkzeug.utils import secure_filename

from app.auth import auth_required, audit_log
from app.services.tls import (
    get_tls_settings, update_tls_settings, get_cert_status,
    generate_self_signed, request_letsencrypt, renew_letsencrypt,
    CERTS_DIR,
)
import os

logger = logging.getLogger(__name__)

tls_bp = Blueprint('tls', __name__)


@tls_bp.route('/api/tls/settings', methods=['GET'])
@auth_required
def get_settings():
    """Get TLS settings and certificate status."""
    return jsonify({
        'settings': get_tls_settings(),
        'cert': get_cert_status(),
    })


@tls_bp.route('/api/tls/settings', methods=['POST'])
@auth_required
def save_settings():
    """Update TLS settings (rtsps_enabled, https_enabled, etc.)."""
    data = request.get_json() or {}
    settings = update_tls_settings(data)
    audit_log('tls_settings_updated', str(data))
    return jsonify({
        'success': True,
        'settings': settings,
        'message': 'TLS settings saved. Restart container to apply RTSPS/HTTPS changes.',
    })


@tls_bp.route('/api/tls/self-signed', methods=['POST'])
@auth_required
def gen_self_signed():
    """Generate a self-signed certificate."""
    data = request.get_json() or {}
    cn = data.get('common_name', 'localhost')
    result = generate_self_signed(common_name=cn)
    if result.get('success'):
        audit_log('cert_generated', f'Self-signed CN={cn}')
    return jsonify(result), 200 if result.get('success') else 500


@tls_bp.route('/api/tls/letsencrypt', methods=['POST'])
@auth_required
def get_letsencrypt():
    """Request a Let's Encrypt certificate."""
    data = request.get_json() or {}
    domain = data.get('domain', '')
    email = data.get('email', '')
    result = request_letsencrypt(domain, email)
    if result.get('success'):
        audit_log('cert_letsencrypt', f'LE cert for {domain}')
    return jsonify(result), 200 if result.get('success') else 400


@tls_bp.route('/api/tls/renew', methods=['POST'])
@auth_required
def renew_le():
    """Renew Let's Encrypt certificates."""
    result = renew_letsencrypt()
    if result.get('success'):
        audit_log('cert_renewed', 'LE renewal')
    return jsonify(result), 200 if result.get('success') else 500


@tls_bp.route('/api/tls/upload', methods=['POST'])
@auth_required
def upload_cert():
    """Upload custom certificate and key files."""
    if 'certificate' not in request.files or 'key' not in request.files:
        return jsonify({'error': 'Both certificate and key files are required'}), 400

    cert_file = request.files['certificate']
    key_file = request.files['key']

    if cert_file.filename == '' or key_file.filename == '':
        return jsonify({'error': 'No files selected'}), 400

    allowed_cert_ext = {'.crt', '.pem', '.cer'}
    allowed_key_ext = {'.key', '.pem'}

    cert_ext = os.path.splitext(secure_filename(cert_file.filename))[1].lower()
    key_ext = os.path.splitext(secure_filename(key_file.filename))[1].lower()

    if cert_ext not in allowed_cert_ext:
        return jsonify({'error': f'Certificate must be: {", ".join(allowed_cert_ext)}'}), 400
    if key_ext not in allowed_key_ext:
        return jsonify({'error': f'Key must be: {", ".join(allowed_key_ext)}'}), 400

    os.makedirs(CERTS_DIR, exist_ok=True)
    cert_path = os.path.join(CERTS_DIR, 'server.crt')
    key_path = os.path.join(CERTS_DIR, 'server.key')

    cert_file.save(cert_path)
    key_file.save(key_path)
    os.chmod(cert_path, 0o644)
    os.chmod(key_path, 0o600)

    settings = update_tls_settings({'cert_type': 'custom'})
    audit_log('cert_uploaded', 'Custom certificate uploaded')

    return jsonify({
        'success': True,
        'message': 'Certificates uploaded. Restart container to apply.',
        'settings': settings,
    })


@tls_bp.route('/api/tls/cert-status', methods=['GET'])
@auth_required
def cert_status():
    """Get certificate details."""
    return jsonify(get_cert_status())
