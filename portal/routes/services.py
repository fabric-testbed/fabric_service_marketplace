import json
from urllib.parse import quote

from flask import Blueprint, request, session, render_template, current_app, abort

import health

from access import current_user, can_use_service
from models import Service, User
from fabric_auth import log_auth_attempt

services_bp = Blueprint('services', __name__)


def _allowed_projects(service):
    # Use the list of allowed project IDs, falling back to legacy single ID
    if service.access_project_ids_json:
        return json.loads(service.access_project_ids_json)
    # None for a website link an admin added without a project
    return [service.access_project_id] if service.access_project_id else []


def _grant_service(service, user_id, name, uuid):
    log_auth_attempt(
        fabric_uuid=uuid, username=name,
        auth_type='service', service_name=service.name,
        success=True, error_message=None,
        ip_address=request.remote_addr, user_id=user_id
    )
    session[f'auth_{service.name}'] = {'id': user_id, 'name': name, 'uuid': uuid}


def _is_admin(user):
    """Checked against the database, like the admin pages, not the session."""
    row = User.query.get(user['id'])
    return bool(row and row.role == 'admin')


def _owner_mailto(service, user):
    """A mailto: link that opens the user's own mail program with a request to the
    service's owner filled in. The portal sends no mail itself. None if the owner
    has no email address. Only given to logged-in users who cannot use the service."""
    owner = service.owner
    if not user or not owner or not owner.email:
        return None
    project = service.access_project_name or service.access_project_id or ''
    me = User.query.get(user['id'])   # the session does not hold the email address
    subject = f"Access to {service.display_name} on the FABRIC Service Marketplace"
    body = (f"Hello {owner.username},\n\n"
            f"I would like to use {service.display_name}. "
            f"Could you add me to its FABRIC service project ({project})?\n\n"
            f"My FABRIC account: {user.get('name', '')}{f' ({me.email})' if me and me.email else ''}\n\n"
            "Thank you")
    return f"mailto:{quote(owner.email)}?subject={quote(subject)}&body={quote(body)}"


def _open_to_everyone(service):
    """A website link with no service project (only admins can add one)."""
    return service.auth_mode == 'link' and not _allowed_projects(service)


def _project_links(service):
    """Where a user asks to join each of the service's allowed projects: its page on the FABRIC portal."""
    base = current_app.config['FABRIC_PORTAL_URL']
    return [{'uuid': p, 'url': f'{base}/projects/{p}',
             'name': service.access_project_name if p == service.access_project_id else None}
            for p in _allowed_projects(service) if p]


@services_bp.route('/available_services')
def available_services():
    """Every registered service is listed. The ones the user cannot use yet (none
    of their service projects is allowed) are shown greyed out, with Request access."""
    user = current_user()
    # Website links waiting for an admin's approval (or rejected) are not listed
    services = Service.query.filter_by(status='active').order_by(Service.created_at.desc()).all()
    usable = {svc.id for svc in services
              if _open_to_everyone(svc) or can_use_service(user, _allowed_projects(svc))}
    return render_template('available_services.html', services=services, usable=usable,
                           service_projects=user.get('service_projects', []) if user else [],
                           active=health.statuses(current_app._get_current_object(), services))


@services_bp.route('/available_services/<service_name>')
def service_page(service_name):
    service = Service.query.filter_by(name=service_name).first_or_404()

    user = current_user()
    if service.status != 'active':
        # Waiting for approval, or rejected: only its owner and admins can look at it
        if not user or not (user['id'] == service.owner_id or _is_admin(user)):
            abort(404)

    if service.auth_mode == 'link':
        # Website link: hosted elsewhere with its own login. Members of its service
        # project get the link; others are shown how to ask for access. A link an
        # admin added without a project is for everyone.
        allowed = (_open_to_everyone(service) or can_use_service(user, _allowed_projects(service))
                   or service.status != 'active')
        return render_template('service_page.html', service=service, is_link=True, active=None,
                               authenticated=allowed, denied=bool(user) and not allowed,
                               project_links=[] if allowed else _project_links(service),
                               owner_mailto=None if allowed else _owner_mailto(service, user))

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
        active=health.statuses(current_app._get_current_object(), [service])[service.id],
        widgets=widgets,
        authenticated=authenticated,
        denied=denied,
        project_links=_project_links(service) if denied else [],
        owner_mailto=_owner_mailto(service, user) if denied else None,
        consumer_name=consumer_name,
        nodes=nodes
    )
