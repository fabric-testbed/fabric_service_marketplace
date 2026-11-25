"""
Portal login. One login for everyone (authors and consumers).

Every login method ends by calling login_with_fabric_token(token). Given a valid
FABRIC token, it does the rest: looks up the user and ALL their projects with
FABRIC, decides what they may do, keeps the token for service nodes, and
starts the session:
  - PORTAL_PROJECT_ID member -> can register services
  - member of a service's allowed projects -> can use that service

Two ways to log in:

  Sign in with FABRIC  /auth/fabric/start. The same login as the Credential
                       Manager: vouch-proxy sends the user to CILogon and sets
                       the FABRIC login cookie, and login_with_fabric_cookie()
                       asks FABRIC who that cookie belongs to. The service token
                       each service's nodes need is issued later, per service
                       project (credmgr_client.py). The login page's button goes
                       here when PORTAL_LOGIN_URL=/auth/fabric/start.
  Token test page      /token_test checks a pasted FABRIC service token and can
                       log in with it (POST /auth/login). For testing.

Either way the user may use the services whose allowed projects include one of
their FABRIC service projects.
"""
from urllib.parse import urlencode

from flask import (Blueprint, request, jsonify, session, redirect,
                   render_template, current_app, url_for)

import token_store
from access import can_register, can_use_service, safe_next
from auth_providers import identity_from_token, token_from_paste
from fabric_auth import (fetch_fabric_identity, fetch_fabric_identity_from_cookies,
                         get_or_create_user, log_auth_attempt, vouch_cookies)
from models import Service, User

auth_bp = Blueprint('auth', __name__)


def _default_landing(user):
    return '/register_service' if user['can_register'] else '/available_services'


def _start_session(identity, signed_in_with):
    """Create/update the user and log them in. Returns (session_user, error).
    signed_in_with: 'fabric' (Sign in with FABRIC) or 'token' (pasted token)."""
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
        # [{"uuid", "name"}]: the projects that give access to services
        'service_projects': list(identity.get('service_projects', [])),
        'can_register': can_register(project_ids, user.role),
        'signed_in_with': signed_in_with,
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
    return _start_session(identity, 'token')


def login_with_fabric_cookie(cookies):
    """
    Log the current browser session in as the owner of the FABRIC login cookie
    (`cookies`, see fabric_auth.vouch_cookies). Returns (session_user, error).
    """
    identity, error = fetch_fabric_identity_from_cookies(cookies)
    if error:
        _log_failure(error)
        return None, error
    return _start_session(identity, 'fabric')


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


# ── Sign in with FABRIC (vouch-proxy + CILogon, like the Credential Manager) ──

def _finish_fabric_login(next_url):
    user, error = login_with_fabric_cookie(vouch_cookies(request.cookies))
    if error:
        return redirect(url_for('auth.login_page', error=error, next=next_url or None))
    return redirect(next_url or _default_landing(user))


@auth_bp.route('/auth/fabric/start')
def fabric_login_start():
    next_url = safe_next(request.args.get('next'), '')
    if vouch_cookies(request.cookies):
        # Already signed in with FABRIC (the cookie is shared by FABRIC's sites)
        user, error = login_with_fabric_cookie(vouch_cookies(request.cookies))
        if not error:
            return redirect(next_url or _default_landing(user))
    # vouch-proxy sends the browser to CILogon, sets the cookie, then returns it here
    callback = request.url_root.rstrip('/') + url_for('auth.fabric_login_callback')
    if next_url:
        callback += '?' + urlencode({'next': next_url})
    login_url = current_app.config['VOUCH_LOGIN_URL']
    return redirect(f"{login_url}{'&' if '?' in login_url else '?'}{urlencode({'url': callback})}")


@auth_bp.route('/auth/fabric/callback')
def fabric_login_callback():
    return _finish_fabric_login(safe_next(request.args.get('next'), ''))


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
    existing = User.query.filter_by(fabric_uuid=identity['uuid']).first()
    role = existing.role if existing else 'user'
    usable = [{"name": svc.name, "auth_mode": svc.auth_mode}
              for svc in Service.query.filter_by(status='active').order_by(Service.name)
              if svc.auth_mode == 'link' or can_use_service(identity, _allowed_projects(svc))]
    return jsonify({
        "name": identity['name'], "uuid": identity['uuid'], "email": identity['email'],
        "project_ids": identity['project_ids'],
        "service_projects": identity['service_projects'],
        "portal_project_id": current_app.config['PORTAL_PROJECT_ID'],
        "role": role,
        "can_register": can_register(identity['project_ids'], role),
        "usable_services": usable,
    }), 200


@auth_bp.route('/auth/logout', methods=['POST'])
def auth_logout():
    signed_in_with = session.get('portal_user', {}).get('signed_in_with')
    token_store.clear()
    session.clear()
    result = {"ok": True}
    logout_url = current_app.config['VOUCH_LOGOUT_URL']
    if signed_in_with == 'fabric' and logout_url:
        # Also end the FABRIC sign-in (clears the login cookie), as the
        # Credential Manager's logout does; the browser goes there next.
        home = request.url_root.rstrip('/') + '/'
        result["redirect"] = f"{logout_url}{'&' if '?' in logout_url else '?'}{urlencode({'url': home})}"
    return jsonify(result), 200
