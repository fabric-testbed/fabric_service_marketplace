"""Access checks shared by the portal routes."""
from functools import wraps

from flask import session, redirect, request, jsonify, render_template, current_app, url_for


def current_user():
    return session.get('portal_user')


def can_register(identity_project_ids, role):
    """Members of PORTAL_PROJECT_ID (and admins) may register services."""
    return role == 'admin' or current_app.config['PORTAL_PROJECT_ID'] in identity_project_ids


def service_project_for(user, allowed_project_ids):
    """
    The service project that lets `user` use a service: the first of the
    service's allowed projects that is one of the user's service projects, or None.
    Only service projects count, because only they give a token (scope "service")
    that is safe to pass to the service's nodes.
    """
    if not user:
        return None
    mine = {p['uuid'] for p in user.get('service_projects', [])}
    return next((p for p in allowed_project_ids if p in mine), None)


def can_use_service(user, allowed_project_ids):
    return service_project_for(user, allowed_project_ids) is not None


def safe_next(target, default):
    """Only allow redirects to local paths."""
    if target and target.startswith('/') and not target.startswith('//'):
        return target
    return default


def author_page(f):
    """Page for service authors: login required + member of PORTAL_PROJECT_ID."""
    @wraps(f)
    def decorated(*args, **kwargs):
        user = current_user()
        if not user:
            return redirect(url_for('auth.login_page', next=request.path))
        if not user.get('can_register'):
            return render_template('not_author.html'), 403
        # A session from before project ownership was recorded: read the user's
        # projects again (no new login needed while the FABRIC cookie is valid)
        # Tried once only: the old entries are first marked "not an owner", so if
        # reading the projects fails the user is not sent round again.
        stale = [p for p in user.get('service_projects', []) if 'owner' not in p]
        if stale:
            for p in stale:
                p['owner'] = False
            session.modified = True
            if user.get('signed_in_with') == 'fabric':
                return redirect(url_for('auth.fabric_login_start', next=request.path))
        return f(*args, **kwargs)
    return decorated


def author_api(f):
    """JSON API for service authors."""
    @wraps(f)
    def decorated(*args, **kwargs):
        user = current_user()
        if not user:
            return jsonify({"error": "Not logged in"}), 401
        if not user.get('can_register'):
            return jsonify({"error": "You are not a member of the portal project."}), 403
        return f(*args, **kwargs)
    return decorated
