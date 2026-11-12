import base64
import json
from datetime import datetime

import requests as http_requests
from flask import current_app

from models import User, AuthLog, db


def token_scope(token):
    """The token's "scope" claim ('service', 'resource', ...), or None if unreadable.
    Reads the claims without checking the signature: FABRIC checks that afterwards."""
    try:
        payload = token.split('.')[1]
        claims = json.loads(base64.urlsafe_b64decode(payload + '=' * (-len(payload) % 4)))
        return claims.get('scope')
    except (IndexError, ValueError, AttributeError):
        return None


def check_token_scope(token):
    """
    Only accept tokens of REQUIRED_TOKEN_SCOPE (default 'service'). A FABRIC
    'resource' token can create and delete slices as the user, so it must never
    be passed on to service nodes. Returns an error string, or None if OK.
    """
    required = current_app.config['REQUIRED_TOKEN_SCOPE']
    if not required:
        return None
    scope = token_scope(token)
    if scope == required:
        return None
    if scope is None:
        return "Not a FABRIC token"
    return (f"This is a FABRIC '{scope}' token. Please use a '{required}' token "
            f"(create one on the FABRIC portal's token page).")


def fetch_fabric_identity(id_token):
    """
    Validate a FABRIC id_token against FABRIC UIS and look up the user's projects.
    Returns ({"uuid", "name", "email", "project_ids"}, None) on success
    or (None, error_string) on failure. Project membership is checked by the caller.
    """
    scope_error = check_token_scope(id_token)
    if scope_error:
        return None, scope_error

    uis = current_app.config['FABRIC_UIS_URL']
    headers = {"Authorization": f"Bearer {id_token}", "Accept": "application/json"}

    try:
        resp = http_requests.get(
            f"{uis}/whoami", headers=headers, timeout=15
        )
    except http_requests.exceptions.RequestException as e:
        current_app.logger.warning("FABRIC UIS /whoami failed: %s", e)
        return None, "Could not reach FABRIC to check the token. Please try again later."

    if resp.status_code != 200:
        return None, "Invalid or expired FABRIC token"

    results = resp.json().get("results", [])
    if not results:
        return None, "No results in FABRIC whoami response"

    user_data = results[0]
    uuid = user_data.get("uuid")
    name = user_data.get("name", "").replace(" ", "")
    email = user_data.get("email", "")
    if not uuid or not name:
        return None, "Incomplete user info from FABRIC"

    try:
        resp = http_requests.get(
            f"{uis}/people/{uuid}?as_self=false",
            headers=headers, timeout=15
        )
    except http_requests.exceptions.RequestException as e:
        current_app.logger.warning("FABRIC UIS /people failed: %s", e)
        return None, "Could not get your projects from FABRIC. Please try again later."

    if resp.status_code != 200:
        return None, "Failed to fetch roles from FABRIC"

    people_results = resp.json().get("results", [])
    if not people_results:
        return None, "No results in FABRIC people response"

    roles = people_results[0].get("roles") or []
    # Role names are either "<project-uuid>-pm" / "<project-uuid>-po" or plain names
    # Extract project UUIDs from roles by stripping the suffix
    user_project_ids = set()
    for r in roles:
        role_name = r.get("name", "")
        if role_name.endswith("-pm") or role_name.endswith("-po"):
            user_project_ids.add(role_name[:-3])
        else:
            user_project_ids.add(role_name)

    return {"uuid": uuid, "name": name, "email": email,
            "project_ids": sorted(user_project_ids)}, None


def get_or_create_user(fabric_uuid, username, email=None):
    """
    Find existing user by FABRIC UUID, or create a new one.
    Updates username/email on every login.
    Returns the User ORM object.
    """
    user = User.query.filter_by(fabric_uuid=fabric_uuid).first()
    if user:
        user.username = username
        if email:
            user.email = email
        user.last_login_at = datetime.utcnow()
    else:
        user = User(
            fabric_uuid=fabric_uuid,
            username=username,
            email=email,
            role='user',
            last_login_at=datetime.utcnow()
        )
        db.session.add(user)
    db.session.commit()
    return user


def log_auth_attempt(fabric_uuid, username, auth_type, service_name,
                     success, error_message, ip_address, user_id=None):
    """Log an authentication attempt to the auth_log table."""
    entry = AuthLog(
        user_id=user_id,
        fabric_uuid=fabric_uuid,
        username=username,
        auth_type=auth_type,
        service_name=service_name,
        success=success,
        error_message=error_message,
        ip_address=ip_address
    )
    db.session.add(entry)
    db.session.commit()
