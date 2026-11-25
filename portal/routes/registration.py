import ipaddress
import json
import re
from datetime import datetime
from urllib.parse import urlparse

from sqlalchemy.orm.attributes import flag_modified
from flask import Blueprint, request, jsonify, session, redirect, render_template, current_app, url_for

from access import author_page, author_api
from models import Service, User, SERVICE_AUTH_MODES, db
from template_generator import generate_flask_template

registration_bp = Blueprint('registration', __name__)

RESERVED_NAMES = {
    'login', 'auth', 'register_service', 'available_services',
    'api', 'static', 'admin', 'my_services', 'favicon.ico'
}

SERVICE_NAME_RE = re.compile(r'^[a-z0-9][a-z0-9\-]{1,48}[a-z0-9]$')
MAX_TITLE_LENGTH = 80
# Widget endpoints are paths on the service node, e.g. /api/service_config or /v1/measure
ENDPOINT_RE = re.compile(r'^/[A-Za-z0-9_\-./]+$')


def _owned_service_projects():
    """The service projects the logged-in user owns (or created): [{"uuid", "name", "owner"}]."""
    return [p for p in session['portal_user'].get('service_projects', []) if p.get('owner')]


def _current_projects(service):
    """The projects a saved service allows. One, except for services registered
    before the one-project rule, which may have several."""
    if service.access_project_ids_json:
        return json.loads(service.access_project_ids_json)
    return [service.access_project_id] if service.access_project_id else []


def _validate_projects(access_project_ids, service):
    """A service belongs to exactly one service project, and only an owner of that
    project may register it. (An edit may keep the project the service has.)"""
    if (not isinstance(access_project_ids, list) or len(access_project_ids) != 1
            or not isinstance(access_project_ids[0], str) or not access_project_ids[0].strip()):
        return "Choose the service project this service belongs to."
    project = access_project_ids[0]
    owned = {p['uuid'] for p in _owned_service_projects()}
    if project not in owned and project != service.access_project_id:
        return "Choose a service project that you own."
    return None


def _set_project(service, access_project_ids):
    """Store the (validated, single) project. A service from before the one-project
    rule keeps all its projects for as long as its first project is left unchanged:
    an edit of something else must not take access away from the others."""
    if access_project_ids[0] != service.access_project_id or not _current_projects(service):
        # First project also goes in the legacy single-value column
        service.access_project_id = access_project_ids[0]
        service.access_project_ids_json = json.dumps(access_project_ids)
        service.access_project_name = None
    # Keep the project's name, to show to people who are not members of it (they
    # cannot look it up themselves). Refreshed whenever an owner saves the service.
    names = {p['uuid']: p['name'] for p in _owned_service_projects()}
    if service.access_project_id in names:
        service.access_project_name = names[service.access_project_id]


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
        return "Add at least one node; each node needs a name, an address and a port."

    # Node addresses must be inside a service node network (FABnetv6 or FABNetv4)
    networks = [ipaddress.ip_network(v.strip(), strict=False)
                for v in current_app.config['SERVICE_NODE_NETWORK'].split(',') if v.strip()]
    for n in nodes:
        try:
            address = ipaddress.ip_address(str(n['ipv6']).strip())
        except ValueError:
            return f"Node {n['name']}: '{n['ipv6']}' is not an IP address."
        # Same version only: an IPv4 address is never "in" an IPv6 network
        if not any(address.version == net.version and address in net for net in networks):
            allowed = ' or '.join(str(net) for net in networks)
            return (f"Node {n['name']}: {address} is not a FABnet address. "
                    f"It must be inside {allowed}.")
        n['ipv6'] = str(address)
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
    # The name people see; the short name (service.name) is the one in the address
    title = data.get('title') or ''
    if not isinstance(title, str) or len(title.strip()) > MAX_TITLE_LENGTH:
        return f"The service name can be at most {MAX_TITLE_LENGTH} characters."
    title = title.strip()
    if auth_mode == 'link':
        # Website link: no nodes and no widget; the website has its own login.
        # An owner of a service project may ask for one under that project; it is
        # listed once an admin has approved the web address. An admin's own link
        # needs a project and that approval too.
        url = (data.get('link_url') or '').strip()
        error = _validate_link_url(url)
        if error:
            return error
        # Like every service it belongs to a service project the author owns
        access_project_ids = data.get('access_project_ids') or []
        error = _validate_projects(access_project_ids, service)
        if error:
            return error
        _set_project(service, access_project_ids)
        # Every link is approved explicitly on the Admin page, an admin's own too
        if not (service.auth_mode == 'link' and service.status == 'active' and service.link_url == url):
            # New, or its web address changed: an admin has to look at it (again)
            service.status = 'pending'
            # Always written, even if it was already 'pending' when read: an admin
            # may be approving the old address at this very moment, and that
            # approval must not carry over to the new one.
            if service.id is not None:
                flag_modified(service, 'status')
        service.link_url = url
        service.node_ipv6 = None
        service.node_port = None
        service.nodes_json = None
        service.canvas_json = '[]'
    else:
        nodes = data.get('nodes', [])
        widgets = data.get('widgets', [])
        access_project_ids = data.get('access_project_ids', [])
        error = (_validate_projects(access_project_ids, service)
                 or _validate_nodes_and_widgets(nodes, widgets))
        if error:
            return error
        # First node / project also go in the legacy single-value columns
        service.link_url = None
        _set_project(service, access_project_ids)
        service.node_ipv6 = nodes[0]['ipv6']
        service.node_port = int(nodes[0]['port'])
        service.nodes_json = json.dumps(nodes)
        service.canvas_json = json.dumps(widgets)
        # Apps on FABRIC nodes need no approval
        service.status = 'active'

    service.auth_mode = auth_mode
    service.title = title or None
    service.allow_owner_email = data.get('allow_owner_email') is True
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
            "field": "service_name",
            "error": "The address can only have lowercase letters, numbers and hyphens: "
                     "3 to 50 characters, starting and ending with a letter or number."
        }), 400
    if service_name in RESERVED_NAMES:
        return jsonify({"field": "service_name",
                        "error": "That address is reserved. Please choose another."}), 400
    if Service.query.filter_by(name=service_name).first():
        return jsonify({"field": "service_name",
                        "error": "Another service already uses that address. Please choose another."}), 409

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
        "status": service.status,
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
        return redirect(url_for('services.service_page', service_name=service.name))

    # Build the service data for pre-filling the form
    if service.access_project_ids_json:
        projects = json.loads(service.access_project_ids_json)
    else:
        projects = [service.access_project_id] if service.access_project_id else []
    service_data = {
        'name': service.name,
        'title': service.display_name,
        'allow_owner_email': bool(service.allow_owner_email),
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

    return jsonify({"message": "Service updated.", "status": service.status,
                    "url": f"/available_services/{service_name}"}), 200


@registration_bp.route('/register_service/success/<service_name>')
@author_page
def register_success(service_name):
    service = Service.query.filter_by(name=service_name).first_or_404()
    nodes = json.loads(service.nodes_json) if service.nodes_json else []
    return render_template('register_success.html',
                           service=service, nodes=nodes,
                           flask_template=_template_for(service))
