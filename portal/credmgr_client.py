"""
Gets FABRIC service tokens from the Credential Manager for users who signed in
with FABRIC.

The portal cannot issue FABRIC tokens itself. It asks the Credential Manager,
proving who the user is with their FABRIC login cookie, the same way the
Credential Manager's own web page does (POST /credmgr/tokens/create).

A token belongs to one project. The Credential Manager gives it the scope
"service" only for a service project, and such a token only identifies the
user, so it is safe to pass to a service's nodes.
"""
import base64
import json
from datetime import datetime, timedelta

import requests as http_requests
from flask import current_app

import token_store
from fabric_auth import check_token_scope


# Messages shown to the user. They are fixed texts: nothing from an exception or
# from FABRIC's answer is ever put in a response (details go to the log).
UNREACHABLE = "Could not reach FABRIC to get your service token. Please try again later."
SIGN_IN_EXPIRED = "Your FABRIC sign-in has expired. Please log out and sign in again."
SIGN_IN_CHANGED = "Your FABRIC sign-in changed. Please log out and sign in again."
NOT_ISSUED = "FABRIC did not issue a service token for this project."
UNEXPECTED = "Unexpected answer from FABRIC when getting your service token."


def _claims(token):
    try:
        payload = token.split('.')[1]
        return json.loads(base64.urlsafe_b64decode(payload + '=' * (-len(payload) % 4)))
    except (IndexError, ValueError, AttributeError):
        return {}


def _create(cookies, project_id, comment):
    """Ask the Credential Manager for a token. Returns (token, None) or (None, message)."""
    c = current_app.config
    try:
        resp = http_requests.post(
            f"{c['CREDMGR_URL']}/credmgr/tokens/create",
            params={"project_id": project_id, "scope": "all",
                    "lifetime": c['SERVICE_TOKEN_HOURS'], "comment": comment[:100]},
            cookies=cookies, headers={"Accept": "application/json"}, timeout=20)
    except http_requests.exceptions.RequestException as e:
        current_app.logger.warning("Credential Manager unreachable: %s", e)
        return None, UNREACHABLE
    if resp.status_code == 401:
        return None, SIGN_IN_EXPIRED
    if resp.status_code != 200:
        current_app.logger.warning("Credential Manager answered %s for project %s: %s",
                                   resp.status_code, project_id, resp.text[:300])
        return None, NOT_ISSUED
    try:
        return resp.json()["data"][0]["id_token"], None
    except (ValueError, KeyError, IndexError, TypeError):
        current_app.logger.warning("Credential Manager answer for project %s had no token", project_id)
        return None, UNEXPECTED


def service_token(user, project_id, cookies, service_name):
    """
    The signed-in user's service token for `project_id`: the one kept for this
    session, or a new one from the Credential Manager.
    Returns (token, None), or (None, a message for the user).
    """
    token = token_store.get_service_token(project_id)
    if token:
        return token, None
    if not cookies:
        return None, SIGN_IN_EXPIRED

    token, error = _create(cookies, project_id, f"FABRIC Service Marketplace: {service_name}")
    if error:
        return None, error
    claims = _claims(token)
    # The cookie is the browser's, the session is the portal's: never pass on a
    # token for anyone other than the user this session belongs to.
    if claims.get("uuid") != user["uuid"]:
        return None, SIGN_IN_CHANGED
    scope_error = check_token_scope(token)
    if scope_error:
        current_app.logger.warning("Token for project %s is not usable: %s", project_id, scope_error)
        return None, NOT_ISSUED

    lifetime = timedelta(hours=current_app.config['SERVICE_TOKEN_HOURS'])
    try:
        expires_at = datetime.utcfromtimestamp(int(claims["exp"]))
    except (KeyError, TypeError, ValueError, OverflowError, OSError):
        expires_at = datetime.utcnow() + lifetime
    # Stop using it a minute early, so a node never gets a token about to expire
    token_store.save_service_token(user["id"], project_id, token,
                                   expires_at - timedelta(minutes=1))
    return token, None
