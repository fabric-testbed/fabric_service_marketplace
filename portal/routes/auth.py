"""
Portal login. One login for everyone (authors and consumers).

Every login method ends by calling login_with_fabric_token(token). Given a valid
FABRIC token, it does the rest: looks up the user and ALL their projects with
FABRIC, decides what they may do, keeps the token for service nodes, and
starts the session:
  - PORTAL_PROJECT_ID member -> can register services
  - member of a service's allowed projects -> can use that service

The /login page's "Sign in with FABRIC" button goes to PORTAL_LOGIN_URL, the
automated login (e.g. CILogon) still to be added. For testing, /token_test checks
a pasted FABRIC service token and can log in with it (POST /auth/login).

/token_test checks a real FABRIC token without logging in.
A new login method (e.g. CILogon) only has to obtain the user's FABRIC token
and call login_with_fabric_token(); see the README handover notes.
"""
from flask import (Blueprint, request, jsonify, session, redirect,
                   render_template, current_app)

import token_store
from access import can_register, safe_next
from auth_providers import identity_from_token, token_from_paste
from fabric_auth import fetch_fabric_identity, get_or_create_user, log_auth_attempt
from models import Service, User

auth_bp = Blueprint('auth', __name__)


def _default_landing(user):
    return '/register_service' if user['can_register'] else '/available_services'


def _start_session(identity):
    """Create/update the user and log them in. Returns (session_user, error)."""
    user = get_or_create_user(identity["uuid"], identity["name"], identity.get("email"))

    if not user.is_active:
        log_auth_attempt(
            fabric_uuid=user.fabric_uuid, username=user.username,
            auth_type='portal', service_name=None,
            success=False, error_message='Account disabled',
            ip_address=request.remote_addr, user_id=user.id
        )
        return None, "Your account has been disabled by an admin."

    log_auth_attempt(
        fabric_uuid=user.fabric_uuid, username=user.username,
        auth_type='portal', service_name=None,
        success=True, error_message=None,
        ip_address=request.remote_addr, user_id=user.id
    )

    project_ids = list(identity.get('project_ids', []))
    token_store.clear()
    session.clear()
    session.permanent = True
    session['portal_user'] = {
        'id': user.id,
        'name': user.username,
        'uuid': user.fabric_uuid,
        'role': user.role,
        'project_ids': project_ids,
        'can_register': can_register(project_ids, user.role),
    }
    if identity.get('token'):
        token_store.save(user.id, identity['token'])
    return session['portal_user'], None


def login_with_fabric_token(fabric_token):
    """
    Log the current browser session in as the owner of `fabric_token`.

    This is the one entry point for any login method. Returns
    (session_user, None) on success or (None, error_message) on failure;
    the caller only has to redirect or show the error.
    """
    if not fabric_token:
        return None, "Missing token"
    identity, error = identity_from_token(fabric_token)
    if error:
        _log_failure(error)
        return None, error
    return _start_session(identity)


def _log_failure(error):
    log_auth_attempt(
        fabric_uuid=None, username=None,
        auth_type='portal', service_name=None,
        success=False, error_message=error,
        ip_address=request.remote_addr
    )


@auth_bp.route('/login')
def login_page():
    next_url = safe_next(request.args.get('next'), '')
    if 'portal_user' in session:
        return redirect(next_url or _default_landing(session['portal_user']))
    return render_template('login.html', next_url=next_url, error=request.args.get('error'),
                           login_url=current_app.config['LOGIN_URL'])


# ── Token flow ──

@auth_bp.route('/auth/login', methods=['POST'])
def auth_login():
    data = request.json or {}
    raw_token = data.get('id_token', '').strip()
    if not raw_token:
        return jsonify({"error": "Missing token"}), 400

    user, error = login_with_fabric_token(token_from_paste(raw_token))
    if error:
        return jsonify({"error": error}), 401

    next_url = safe_next(data.get('next'), _default_landing(user))
    return jsonify({"ok": True, "user": user['name'], "role": user['role'],
                    "next": next_url}), 200


# ── Token test page ──

@auth_bp.route('/token_test')
def token_test_page():
    return render_template('token_test.html', uis_url=current_app.config['FABRIC_UIS_URL'],
                           next_url=safe_next(request.args.get('next'), ''))


@auth_bp.route('/api/token_test', methods=['POST'])
def token_test():
    """
    Check a real FABRIC token without logging in: shows what the portal would
    get from it (user, all projects, may register, usable services).
    Always asks the real FABRIC user service (FABRIC_UIS_URL).
    """
    raw = (request.json or {}).get('id_token', '').strip()
    if not raw:
        return jsonify({"error": "Missing token"}), 400
    identity, error = fetch_fabric_identity(token_from_paste(raw))
    if error:
        return jsonify({"error": error}), 401

    from routes.services import _allowed_projects
    projects = set(identity['project_ids'])
    existing = User.query.filter_by(fabric_uuid=identity['uuid']).first()
    role = existing.role if existing else 'user'
    usable = [{"name": svc.name, "auth_mode": svc.auth_mode}
              for svc in Service.query.filter_by(status='active').order_by(Service.name)
              if svc.auth_mode == 'external' or projects & set(_allowed_projects(svc))]
    return jsonify({
        "name": identity['name'], "uuid": identity['uuid'], "email": identity['email'],
        "project_ids": identity['project_ids'],
        "portal_project_id": current_app.config['PORTAL_PROJECT_ID'],
        "role": role,
        "can_register": can_register(identity['project_ids'], role),
        "usable_services": usable,
    }), 200


@auth_bp.route('/auth/logout', methods=['POST'])
def auth_logout():
    token_store.clear()
    session.clear()
    return jsonify({"ok": True}), 200
