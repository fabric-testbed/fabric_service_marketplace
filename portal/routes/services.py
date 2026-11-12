import json

from flask import Blueprint, request, session, render_template

from access import current_user, can_use_service
from models import Service
from fabric_auth import log_auth_attempt

services_bp = Blueprint('services', __name__)


def _allowed_projects(service):
    # Use the list of allowed project IDs, falling back to legacy single ID
    if service.access_project_ids_json:
        return json.loads(service.access_project_ids_json)
    return [service.access_project_id]


def _grant_service(service, user_id, name, uuid):
    log_auth_attempt(
        fabric_uuid=uuid, username=name,
        auth_type='service', service_name=service.name,
        success=True, error_message=None,
        ip_address=request.remote_addr, user_id=user_id
    )
    session[f'auth_{service.name}'] = {'id': user_id, 'name': name, 'uuid': uuid}


@services_bp.route('/available_services')
def available_services():
    services = Service.query.order_by(Service.created_at.desc()).all()
    return render_template('available_services.html', services=services)


@services_bp.route('/available_services/<service_name>')
def service_page(service_name):
    service = Service.query.filter_by(name=service_name).first_or_404()

    if service.auth_mode == 'link':
        # Website link: hosted elsewhere with its own login; anyone may follow it
        return render_template('service_page.html', service=service, is_link=True)

    session_key = f'auth_{service_name}'
    user = current_user()
    denied = False

    # Logged-in users get access automatically if they are in an allowed project
    if not session.get(session_key) and user:
        if can_use_service(user, _allowed_projects(service)):
            _grant_service(service, user['id'], user['name'], user['uuid'])
        else:
            denied = True

    authenticated = bool(session.get(session_key))
    consumer_name = session.get(session_key, {}).get('name', '')
    widgets = json.loads(service.canvas_json) if authenticated else []

    # Parse nodes for dropdown
    nodes = []
    if service.nodes_json:
        nodes = json.loads(service.nodes_json)
    elif service.node_ipv6:
        nodes = [{"name": "Default", "ipv6": service.node_ipv6, "port": service.node_port}]

    return render_template(
        'service_page.html',
        service=service,
        is_link=False,
        widgets=widgets,
        authenticated=authenticated,
        denied=denied,
        consumer_name=consumer_name,
        nodes=nodes
    )
