"""Access checks shared by the portal routes."""
from functools import wraps

from flask import session, redirect, request, jsonify, render_template, current_app, url_for


def current_user():
    return session.get('portal_user')


def can_register(identity_project_ids, role):
    """Members of PORTAL_PROJECT_ID (and admins) may register services."""
    return role == 'admin' or current_app.config['PORTAL_PROJECT_ID'] in identity_project_ids


def can_use_service(user, allowed_project_ids):
    return bool(user) and bool(set(user.get('project_ids', [])) & set(allowed_project_ids))


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
