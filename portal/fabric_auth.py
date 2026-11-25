import base64
import json
import re
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
    Returns ({"uuid", "name", "email", "project_ids", "service_projects"}, None)
    on success or (None, error_string) on failure.
    """
    scope_error = check_token_scope(id_token)
    if scope_error:
        return None, scope_error
    return _fetch_identity({"headers": {"Authorization": f"Bearer {id_token}"}},
                           invalid="Invalid or expired FABRIC token")


# A login cookie is a few KB; pieces are about 4 KB each
MAX_COOKIE_PIECES = 16


def vouch_cookies(cookies):
    """
    The FABRIC login cookie among `cookies` (a dict, e.g. request.cookies), as
    {name: value} ready to send to FABRIC, or {} if there is none.

    FABRIC's user service and Credential Manager read one whole cookie. A
    browser may hold a large one in pieces, so the pieces are joined here:
    vouch-proxy names them <name>_1of3, <name>_2of3, ...; <name>, <name>_1,
    <name>_2, ... is accepted too.
    """
    name = current_app.config['VOUCH_COOKIE_NAME']
    if not name:
        return {}

    # <name>_1of3, <name>_2of3, <name>_3of3
    # The names come from the browser: at most MAX_COOKIE_PIECES pieces, all
    # agreeing on how many there are, so a made-up "1of999999999" costs nothing.
    numbered, totals = {}, set()
    for key, value in cookies.items():
        match = re.fullmatch(re.escape(name) + r'_(\d{1,2})of(\d{1,2})', key)
        if match:
            numbered[int(match.group(1))] = value
            totals.add(int(match.group(2)))
    if numbered:
        total = totals.pop() if len(totals) == 1 else 0
        if not 1 <= total <= MAX_COOKIE_PIECES:
            return {}
        parts = [numbered.get(i) for i in range(1, total + 1)]
        return {name: ''.join(parts)} if all(parts) else {}

    # <name> alone, or followed by <name>_1, <name>_2, ...
    value = cookies.get(name)
    if not value:
        return {}
    for i in range(1, MAX_COOKIE_PIECES):
        piece = cookies.get(f'{name}_{i}')
        if not piece:
            break
        value += piece
    return {name: value}


def fetch_fabric_identity_from_cookies(cookies):
    """
    Same as fetch_fabric_identity, for a user who signed in with FABRIC: `cookies`
    is their login cookie, whole (see vouch_cookies), which FABRIC UIS checks itself.
    """
    if not cookies:
        return None, "You are not signed in with FABRIC."
    return _fetch_identity({"cookies": cookies},
                           invalid="Your FABRIC sign-in has expired. Please sign in again.")


def _fetch_identity(auth, invalid):
    """`auth` is what proves who the user is to FABRIC UIS: {"headers": ...} or {"cookies": ...}."""
    uis = current_app.config['FABRIC_UIS_URL']
    auth = dict(auth, headers=dict(auth.get('headers', {}), Accept='application/json'))

    try:
        resp = http_requests.get(f"{uis}/whoami", timeout=15, **auth)
    except http_requests.exceptions.RequestException as e:
        current_app.logger.warning("FABRIC UIS /whoami failed: %s", e)
        return None, "Could not reach FABRIC to check who you are. Please try again later."

    if resp.status_code != 200:
        return None, invalid

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
        resp = http_requests.get(f"{uis}/people/{uuid}?as_self=false", timeout=15, **auth)
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

    try:
        service_projects = _fetch_service_projects(uis, auth, uuid)
    except (http_requests.exceptions.RequestException, ValueError) as e:
        current_app.logger.warning("FABRIC UIS /projects failed: %s", e)
        return None, "Could not get your projects from FABRIC. Please try again later."

    return {"uuid": uuid, "name": name, "email": email,
            "project_ids": sorted(user_project_ids),
            "service_projects": service_projects}, None


def _fetch_service_projects(uis, auth, uuid):
    """
    The user's active service projects, as [{"uuid", "name", "owner"}] sorted by name.
    Only these give access to services: FABRIC issues "service" tokens, which are
    safe to pass to service nodes, for projects of this type only.
    "owner" is True if the user is an owner, the lead or the creator of the project: only
    they may register a service under it.
    """
    wanted = current_app.config['SERVICE_PROJECT_TYPE']
    found, offset, limit = [], 0, 200
    while True:
        resp = http_requests.get(
            f"{uis}/projects", timeout=15,
            params={"person_uuid": uuid, "offset": offset, "limit": limit,
                    "sort_by": "name", "order_by": "asc"}, **auth)
        if resp.status_code != 200:
            raise ValueError(f"/projects answered {resp.status_code}")
        body = resp.json()
        page = body.get("results") or []
        for p in page:
            if p.get("active", True) and p.get("project_type") == wanted and p.get("uuid"):
                roles = p.get("memberships") or {}
                found.append({"uuid": p["uuid"], "name": p.get("name") or p["uuid"],
                              "owner": bool(roles.get("is_owner") or roles.get("is_creator") or roles.get("is_lead"))})
        offset += len(page)
        if not page or offset >= (body.get("total") or 0):
            return found


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
