"""
Turns a pasted FABRIC token into the user's identity.

identity_from_token() returns
    {"uuid", "name", "email", "project_ids", "service_projects", "token"}
by asking FABRIC's user service (FABRIC_UIS_URL) who the user is and listing
ALL their projects, whichever project the token was made for.
Only FABRIC service tokens are accepted (see fabric_auth.check_token_scope).

Used by the token test page. "Sign in with FABRIC" does not paste a token; see
routes.auth.login_with_fabric_cookie().
"""
import json

from fabric_auth import fetch_fabric_identity


def token_from_paste(raw_token):
    """Accept either the raw token or the full JSON downloaded from the FABRIC portal."""
    id_token = raw_token.strip()
    try:
        parsed = json.loads(id_token)
        if isinstance(parsed, dict) and 'id_token' in parsed:
            id_token = parsed['id_token'].strip()
    except ValueError:
        pass
    return id_token


def identity_from_token(fabric_token):
    """Ask FABRIC who owns this token. Returns (identity, None) or (None, error)."""
    identity, error = fetch_fabric_identity(fabric_token)
    if identity:
        identity['token'] = fabric_token
    return identity, error
