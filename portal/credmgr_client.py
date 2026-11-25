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


class TokenError(Exception):
    """Could not get a service token; the message is safe to show the user."""


def _claims(token):
    try:
        payload = token.split('.')[1]
        return json.loads(base64.urlsafe_b64decode(payload + '=' * (-len(payload) % 4)))
    except (IndexError, ValueError, AttributeError):
        return {}


def _create(cookies, project_id, comment):
    c = current_app.config
    try:
        resp = http_requests.post(
            f"{c['CREDMGR_URL']}/credmgr/tokens/create",
            params={"project_id": project_id, "scope": "all",
                    "lifetime": c['SERVICE_TOKEN_HOURS'], "comment": comment[:100]},
            cookies=cookies, headers={"Accept": "application/json"}, timeout=20)
    except http_requests.exceptions.RequestException as e:
        current_app.logger.warning("Credential Manager unreachable: %s", e)
        raise TokenError("Could not reach FABRIC to get your service token. "
                         "Please try again later.") from e
    if resp.status_code == 401:
        raise TokenError("Your FABRIC sign-in has expired. Please log out and sign in again.")
    if resp.status_code != 200:
        current_app.logger.warning("Credential Manager answered %s for project %s: %s",
                                   resp.status_code, project_id, resp.text[:300])
        raise TokenError("FABRIC did not issue a service token for this project.")
    try:
        return resp.json()["data"][0]["id_token"]
    except (ValueError, KeyError, IndexError, TypeError) as e:
        raise TokenError("Unexpected answer from FABRIC when getting your service token.") from e


def service_token(user, project_id, cookies, service_name):
    """
    The signed-in user's service token for `project_id`: the one kept for this
    session, or a new one from the Credential Manager. Raises TokenError.
    """
    token = token_store.get_service_token(project_id)
    if token:
        return token
    if not cookies:
        raise TokenError("Your FABRIC sign-in has expired. Please log out and sign in again.")

    token = _create(cookies, project_id, f"FABRIC Service Marketplace: {service_name}")
    claims = _claims(token)
    # The cookie is the browser's, the session is the portal's: never pass on a
    # token for anyone other than the user this session belongs to.
    if claims.get("uuid") != user["uuid"]:
        raise TokenError("Your FABRIC sign-in changed. Please log out and sign in again.")
    scope_error = check_token_scope(token)
    if scope_error:
        current_app.logger.warning("Token for project %s is not usable: %s", project_id, scope_error)
        raise TokenError("FABRIC did not issue a service token for this project.")

    lifetime = timedelta(hours=current_app.config['SERVICE_TOKEN_HOURS'])
    try:
        expires_at = datetime.utcfromtimestamp(int(claims["exp"]))
    except (KeyError, TypeError, ValueError, OverflowError, OSError):
        expires_at = datetime.utcnow() + lifetime
    # Stop using it a minute early, so a node never gets a token about to expire
    token_store.save_service_token(user["id"], project_id, token,
                                   expires_at - timedelta(minutes=1))
    return token
