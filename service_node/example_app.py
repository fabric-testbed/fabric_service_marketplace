"""
Example FABRIC service app. Run it on your slice VM (with FABnetv6 attached):

    pip install flask requests
    ALLOWED_PROJECTS=<project-uuid>[,<project-uuid>...] python example_app.py

The file has four parts:

  1. Setup                              keep
  2. Health check (GET /health)         keep: lets anyone check that your app is running
  3. FABRIC token check                 OPTIONAL: turn off with CHECK_FABRIC_TOKEN=false
  4. Your endpoints                     FILL IN: your service's logic

How requests reach the app:
  - Through the portal: the portal logs the user in, checks they are in one of
    the service's allowed projects, and forwards their request through the
    gateway, adding their FABRIC token and X-Fabric-User-* headers.
  - Directly, from a user's own slice VM on FABnetv6:
      curl -H "Authorization: Bearer $FABRIC_TOKEN" \\
           "http://[<node ipv6>]:8080/api/service_config?target=x"
"""
import base64
import json
import os
import platform
import time
from functools import wraps

import requests
from flask import Flask, g, jsonify, request


# ═══ 1. Setup (keep) ═════════════════════════════════════════════════════════

app = Flask(__name__)
PORT = int(os.environ.get("SERVICE_PORT", "8080"))


# ═══ 2. Health check (keep) ══════════════════════════════════════════════════
# Answers without a token, so the portal, the author or a monitoring tool can
# check that the app is running. Keep it fast and don't return private data.

@app.route("/health")
def health():
    return jsonify({"status": "ok"})


# ═══ 3. FABRIC token check (OPTIONAL) ════════════════════════════════════════
# Your node's FABnetv6 address can be reached from any FABRIC slice, not only
# through the portal. The portal already checks users before forwarding their
# requests, but direct callers skip the portal.
#
# With the check ON (default), only FABRIC users in your allowed projects can
# call your endpoints, from the portal or directly, and caller() tells you
# reliably who they are (e.g. to record who used what).
#
# With the check OFF, anyone on FABnetv6 can call your endpoints. caller() then
# reads the X-Fabric-User-* headers the portal adds: fine for display, but a
# direct caller can send any values. Turn it off only if that is acceptable.

CHECK_FABRIC_TOKEN = os.environ.get("CHECK_FABRIC_TOKEN", "true").lower() != "false"
# Users must belong to at least one of these FABRIC projects (empty = any FABRIC user)
ALLOWED_PROJECTS = {p.strip() for p in os.environ.get("ALLOWED_PROJECTS", "").split(",") if p.strip()}
FABRIC_UIS_URL = os.environ.get("FABRIC_UIS_URL", "https://uis.fabric-testbed.net").rstrip("/")
# Only "service" tokens are accepted: a "resource" token can create and delete
# slices as the user, so users should never send one to a service.
REQUIRED_SCOPE = "service"
_cache = {}  # token -> (expires_at, user); avoids asking FABRIC on every request


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
    """With the check on: refuse requests without a valid FABRIC service token
    from an allowed project. With it off: let every request through."""
    @wraps(f)
    def decorated(*args, **kwargs):
        g.user = None
        if not CHECK_FABRIC_TOKEN:
            return f(*args, **kwargs)
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
    """Who made this request.
    Check on: verified with FABRIC. Check off: from the portal's X-Fabric-User-*
    headers (None for direct calls, and a direct caller can fake them)."""
    if g.get("user"):
        return {"uuid": g.user["uuid"], "name": g.user["name"], "verified": True}
    return {"uuid": request.headers.get("X-Fabric-User-Uuid"),
            "name": request.headers.get("X-Fabric-User-Name"), "verified": False}


# ═══ 4. Your endpoints (FILL IN) ═════════════════════════════════════════════
# One route per portal widget. The path and method must match the widget's
# endpoint and method on the registration form.
#   GET:  the widget's form inputs arrive as query parameters
#   POST: they arrive as a JSON body
# Whatever JSON you return is shown to the user.
# @require_auth applies the token check from part 3 (it does nothing when the
# check is off).

@app.route("/api/service_config", methods=["GET", "POST"])
@require_auth
def service_config():
    # Read the user's form inputs
    if request.method == "POST":
        inputs = request.get_json(silent=True) or {}
    else:
        inputs = request.args.to_dict()

    # >>> FILL IN: your service's logic <<<
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
