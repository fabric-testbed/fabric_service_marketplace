"""
Example FABRIC service: this app checks each user's FABRIC service token itself.
Run on your slice VM (with FABnetv6 attached):

    pip install flask requests
    ALLOWED_PROJECTS=<project-uuid>[,<project-uuid>...] python example_app.py

Every request must carry the user's FABRIC service token:  Authorization: Bearer <token>
Because the app checks the token with FABRIC, it works both
  - behind the portal: the gateway forwards the logged-in user's token, and
  - directly, from users' own VMs on FABnetv6:
      curl -H "Authorization: Bearer $FABRIC_TOKEN" \
           "http://[<node ipv6>]:8080/api/service_config?target=x"

The portal's widget calls one endpoint, /api/service_config, with either
  GET  — form inputs arrive as query parameters, or
  POST — form inputs arrive as a JSON body.
Whatever JSON you return is shown to the user.
"""
import base64
import json
import os
import platform
import time
from functools import wraps

import requests
from flask import Flask, g, jsonify, request

app = Flask(__name__)
FABRIC_UIS_URL = os.environ.get("FABRIC_UIS_URL", "https://uis.fabric-testbed.net").rstrip("/")
# Users must belong to at least one of these FABRIC projects (empty = any valid user)
ALLOWED_PROJECTS = {p.strip() for p in os.environ.get("ALLOWED_PROJECTS", "").split(",") if p.strip()}
PORT = int(os.environ.get("SERVICE_PORT", "8080"))

_cache = {}  # token -> (expires_at, user); avoids asking FABRIC on every request

# Only FABRIC "service" tokens are accepted. A "resource" token can create and
# delete slices as the user, so users should never send one to a service.
REQUIRED_SCOPE = "service"


def token_scope(token):
    """The token's "scope" claim. Its signature is checked by FABRIC in fabric_user()."""
    try:
        p = token.split(".")[1]
        return json.loads(base64.urlsafe_b64decode(p + "=" * (-len(p) % 4))).get("scope")
    except (IndexError, ValueError, AttributeError):
        return None


def fabric_user(token):
    """Ask FABRIC who owns this token and which projects they are in. None if invalid."""
    hit = _cache.get(token)
    if hit and hit[0] > time.time():
        return hit[1]
    headers = {"Authorization": f"Bearer {token}", "Accept": "application/json"}
    try:
        who = requests.get(f"{FABRIC_UIS_URL}/whoami", headers=headers, timeout=10)
        if who.status_code != 200:
            return None
        me = who.json()["results"][0]
        people = requests.get(f"{FABRIC_UIS_URL}/people/{me['uuid']}?as_self=false",
                              headers=headers, timeout=10)
        if people.status_code != 200:
            return None
        roles = people.json()["results"][0].get("roles") or []
    except (requests.RequestException, KeyError, IndexError, ValueError):
        return None
    # Role names look like "<project-uuid>-pm" / "<project-uuid>-po"
    projects = {r["name"][:-3] for r in roles if r.get("name", "").endswith(("-pm", "-po"))}
    user = {"uuid": me["uuid"], "name": me.get("name"), "projects": projects}
    _cache[token] = (time.time() + 300, user)
    return user


def require_auth(f):
    """Reject requests without a valid FABRIC token from an allowed project."""
    @wraps(f)
    def decorated(*args, **kwargs):
        auth = request.headers.get("Authorization", "")
        if not auth.startswith("Bearer "):
            return jsonify({"error": "Missing FABRIC token"}), 401
        token = auth[len("Bearer "):].strip()
        if token_scope(token) != REQUIRED_SCOPE:
            return jsonify({"error": "Please use a FABRIC service token"}), 401
        user = fabric_user(token)
        if not user:
            return jsonify({"error": "Invalid or expired FABRIC token"}), 401
        if ALLOWED_PROJECTS and not (user["projects"] & ALLOWED_PROJECTS):
            return jsonify({"error": "Not a member of an allowed project"}), 403
        g.user = user
        return f(*args, **kwargs)
    return decorated


def caller():
    """The FABRIC user who made this request (verified with FABRIC above)."""
    return {"uuid": g.user["uuid"], "name": g.user["name"]}


@app.route("/api/service_config", methods=["GET", "POST"])
@require_auth
def service_config():
    # Read the user's form inputs
    if request.method == "POST":
        inputs = request.get_json(silent=True) or {}
    else:
        inputs = request.args.to_dict()

    # ── Replace this with your service's logic ──
    # e.g. run a command with subprocess.run([...]) or call another local API.
    result = f"Hello {caller()['name']}, {platform.node()} received {inputs}"

    return jsonify({
        "node": platform.node(),
        "caller": caller(),
        "inputs": inputs,
        "result": result,
    })


if __name__ == "__main__":
    app.run(host="::", port=PORT)
