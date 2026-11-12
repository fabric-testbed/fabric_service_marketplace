import ipaddress
import json
import re
from datetime import datetime
from urllib.parse import urlparse

from flask import Blueprint, request, jsonify, session, redirect, render_template, current_app

from access import author_page, author_api
from models import Service, User, SERVICE_AUTH_MODES, db
from template_generator import generate_flask_template

registration_bp = Blueprint('registration', __name__)

RESERVED_NAMES = {
    'login', 'auth', 'register_service', 'available_services',
    'api', 'static', 'admin', 'my_services', 'favicon.ico'
}

SERVICE_NAME_RE = re.compile(r'^[a-z0-9][a-z0-9\-]{1,48}[a-z0-9]$')
# Widget endpoints are paths on the service node, e.g. /api/service_config or /v1/measure
ENDPOINT_RE = re.compile(r'^/[A-Za-z0-9_\-./]+$')


def _validate_projects(access_project_ids):
    if not isinstance(access_project_ids, list) or not access_project_ids or not all(
        isinstance(p, str) and p.strip() for p in access_project_ids
    ):
        return "Add at least one allowed FABRIC project UUID."
    return None


def _validate_nodes_and_widgets(nodes, widgets):
    if not widgets:
        return "At least one widget is required."
    for w in widgets:
        endpoint = w.get('endpoint', '')
        if not ENDPOINT_RE.match(endpoint) or '..' in endpoint or '//' in endpoint:
            return (f"Invalid endpoint '{endpoint}'. Use a path on your node like "
                    f"/api/service_config or /v1/measure (letters, digits, - _ . /).")
        if endpoint.rstrip('/') == '/health':
            return "/health is used by the app's health check; choose another endpoint."
        if w.get('method') not in ('GET', 'POST'):
            return "Widget method must be GET or POST."

    if not nodes or not isinstance(nodes, list) or not all(
        n.get('name') and n.get('ipv6') and n.get('port') for n in nodes
    ):
        return "Add at least one node; each node needs name, ipv6, and port."

    # Node addresses must be inside the service node network (FABnetv6)
    network = ipaddress.ip_network(current_app.config['SERVICE_NODE_NETWORK'], strict=False)
    for n in nodes:
        try:
            if ipaddress.ip_address(n['ipv6'].strip()) not in network:
                raise ValueError
        except ValueError:
            return f"Node IPv6 address must be inside {network}"
        try:
            port = int(n['port'])
            if port < 1 or port > 65535:
                raise ValueError
        except (ValueError, TypeError):
            return f"Invalid port for node {n['name']}"
    return None


def _validate_link_url(url):
    parsed = urlparse(url or '')
    if parsed.scheme not in ('http', 'https') or not parsed.netloc:
        return "Enter the service's web address, starting with https://"
    return None


def _apply_service_input(service, data):
    """Validate the form for the chosen auth mode and copy it onto `service`. Returns an error or None."""
    auth_mode = data.get('auth_mode', 'fabric_token')
    if auth_mode not in SERVICE_AUTH_MODES:
        return "Unknown access mode."
    if auth_mode == 'link':
        # Checked against the database, like the admin pages, not the session
        user = User.query.get(session['portal_user']['id'])
        if not user or user.role != 'admin':
            return "Only admins can add website links."

    if auth_mode == 'link':
        # Website link: no nodes, no widget, no project gate; the website has its own login
        url = (data.get('link_url') or '').strip()
        error = _validate_link_url(url)
        if error:
            return error
        service.link_url = url
        service.access_project_id = None
        service.access_project_ids_json = None
        service.node_ipv6 = None
        service.node_port = None
        service.nodes_json = None
        service.canvas_json = '[]'
    else:
        nodes = data.get('nodes', [])
        widgets = data.get('widgets', [])
        access_project_ids = data.get('access_project_ids', [])
        error = (_validate_projects(access_project_ids)
                 or _validate_nodes_and_widgets(nodes, widgets))
        if error:
            return error
        # First node / project also go in the legacy single-value columns
        service.link_url = None
        service.access_project_id = access_project_ids[0]
        service.access_project_ids_json = json.dumps(access_project_ids)
        service.node_ipv6 = nodes[0]['ipv6']
        service.node_port = int(nodes[0]['port'])
        service.nodes_json = json.dumps(nodes)
        service.canvas_json = json.dumps(widgets)

    service.auth_mode = auth_mode
    service.description = data.get('description', '')
    service.icon_color = data.get('icon_color', '#3B82F6')
    return None


def _template_for(service):
    """The Flask app the author runs on their nodes, or None for website links."""
    if service.auth_mode == 'link':
        return None
    nodes = json.loads(service.nodes_json) if service.nodes_json else []
    port = nodes[0]['port'] if nodes else service.node_port
    projects = (json.loads(service.access_project_ids_json)
                if service.access_project_ids_json else [service.access_project_id])
    return generate_flask_template(json.loads(service.canvas_json), port,
                                   allowed_projects=projects)


@registration_bp.route('/register_service')
@author_page
def register_service_page():
    return render_template('register_service.html')


@registration_bp.route('/api/register_service', methods=['POST'])
@author_api
def register_service():
    data = request.json or {}
    service_name = data.get('service_name', '').strip().lower()

    if not SERVICE_NAME_RE.match(service_name):
        return jsonify({
            "error": "Invalid service name. Use lowercase letters, numbers, and "
                     "hyphens (3-50 chars, must start/end with alphanumeric)."
        }), 400
    if service_name in RESERVED_NAMES:
        return jsonify({"error": "That name is reserved."}), 400
    if Service.query.filter_by(name=service_name).first():
        return jsonify({"error": "Service name already taken."}), 409

    service = Service(
        name=service_name,
        owner_id=session['portal_user']['id'],
    )
    error = _apply_service_input(service, data)
    if error:
        return jsonify({"error": error}), 400

    db.session.add(service)
    db.session.commit()

    return jsonify({
        "message": "Service registered successfully.",
        "url": f"/available_services/{service_name}",
        "flask_template": _template_for(service)
    }), 201


@registration_bp.route('/api/preview_template', methods=['POST'])
@author_api
def preview_template():
    """Generate a Flask template preview without saving anything."""
    data = request.json or {}
    if data.get('auth_mode', 'fabric_token') != 'fabric_token':
        return jsonify({"template": None}), 200
    try:
        port = int(data.get('port', 8080))
    except (TypeError, ValueError):
        port = 8080
    try:
        template = generate_flask_template(
            data.get('widgets', []), port,
            allowed_projects=data.get('access_project_ids') or [])
        return jsonify({"template": template}), 200
    except Exception:
        return jsonify({"error": "Failed to generate template"}), 500


@registration_bp.route('/edit_service/<service_name>')
@author_page
def edit_service_page(service_name):
    service = Service.query.filter_by(name=service_name).first_or_404()
    if service.owner_id != session['portal_user']['id']:
        return redirect(f'/available_services/{service_name}')

    # Build the service data for pre-filling the form
    if service.access_project_ids_json:
        projects = json.loads(service.access_project_ids_json)
    else:
        projects = [service.access_project_id] if service.access_project_id else []
    service_data = {
        'name': service.name,
        'description': service.description or '',
        'icon_color': service.icon_color or '#3B82F6',
        'auth_mode': service.auth_mode,
        'link_url': service.link_url or '',
        'nodes': json.loads(service.nodes_json) if service.nodes_json else [],
        'access_project_ids': projects,
        'widgets': json.loads(service.canvas_json),
    }
    return render_template('register_service.html',
                           edit_mode=True,
                           service_data=service_data)


@registration_bp.route('/api/update_service', methods=['POST'])
@author_api
def update_service():
    data = request.json or {}
    service_name = data.get('service_name', '').strip().lower()

    service = Service.query.filter_by(name=service_name).first()
    if not service:
        return jsonify({"error": "Service not found."}), 404
    if service.owner_id != session['portal_user']['id']:
        return jsonify({"error": "You don't own this service."}), 403

    error = _apply_service_input(service, data)
    if error:
        db.session.rollback()
        return jsonify({"error": error}), 400
    service.updated_at = datetime.utcnow()
    db.session.commit()

    return jsonify({"message": "Service updated.", "url": f"/available_services/{service_name}"}), 200


@registration_bp.route('/register_service/success/<service_name>')
@author_page
def register_success(service_name):
    service = Service.query.filter_by(name=service_name).first_or_404()
    nodes = json.loads(service.nodes_json) if service.nodes_json else []
    return render_template('register_success.html',
                           service=service, nodes=nodes,
                           flask_template=_template_for(service))
