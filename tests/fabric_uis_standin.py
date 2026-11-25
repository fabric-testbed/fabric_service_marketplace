"""
Stand-in for FABRIC's user service (https://uis.fabric-testbed.net), for tests.

make_token() issues tokens shaped like real FABRIC tokens (header.claims.signature,
with a "scope" claim), and the app answers /whoami, /people/<uuid> and /projects
with the same response shapes as the real service, so the portal and the node
apps run their real token-checking code against it.

It also stands in for two more things "Sign in with FABRIC" needs:
  - the FABRIC login cookie (set by vouch-proxy in a real deployment): here the
    cookie's value is simply a make_token() token, see login_cookie()
  - the Credential Manager's POST /credmgr/tokens/create, which issues a token
    for one project to whoever the cookie belongs to
"""
import base64
import hashlib
import hmac
import json
import time

from flask import Flask, jsonify, request

PORTAL_PROJECT = 'pppppppp-0000-0000-0000-000000000001'
CONSUMER_PROJECT = 'aaaaaaaa-0000-0000-0000-000000000001'
SECOND_SERVICE_PROJECT = 'aaaaaaaa-0000-0000-0000-000000000002'
RESEARCH_PROJECT = 'bbbbbbbb-0000-0000-0000-000000000001'
RETIRED_SERVICE_PROJECT = 'cccccccc-0000-0000-0000-000000000001'

# Only "service" projects give access to services
PROJECTS = {
    PORTAL_PROJECT: {"name": "Portal Authors", "project_type": "research", "active": True},
    CONSUMER_PROJECT: {"name": "Service - Echo", "project_type": "service", "active": True},
    SECOND_SERVICE_PROJECT: {"name": "Service - Second", "project_type": "service", "active": True},
    RESEARCH_PROJECT: {"name": "Some Research", "project_type": "research", "active": True},
    RETIRED_SERVICE_PROJECT: {"name": "Service - Retired", "project_type": "service", "active": False},
}
COOKIE_NAME = 'fabric-service'

USERS = [
    # Member of the portal project: can register services
    {"uuid": "11111111-1111-1111-1111-111111111111", "name": "TestAuthor",
     "email": "author@example.org", "project_ids": [PORTAL_PROJECT, CONSUMER_PROJECT]},
    # Can use services that allow CONSUMER_PROJECT, cannot register
    {"uuid": "22222222-2222-2222-2222-222222222222", "name": "TestConsumer",
     "email": "consumer@example.org", "project_ids": [CONSUMER_PROJECT]},
    # No projects: can browse, cannot use or register services
    {"uuid": "33333333-3333-3333-3333-333333333333", "name": "TestOutsider",
     "email": "outsider@example.org", "project_ids": []},
    # Only in projects that are not active service projects: cannot use services
    {"uuid": "44444444-4444-4444-4444-444444444444", "name": "TestResearcher",
     "email": "researcher@example.org", "project_ids": [RESEARCH_PROJECT, RETIRED_SERVICE_PROJECT]},
    # In two service projects
    {"uuid": "55555555-5555-5555-5555-555555555555", "name": "TestTwoProjects",
     "email": "two@example.org", "project_ids": [SECOND_SERVICE_PROJECT, CONSUMER_PROJECT]},
]

# Every token the stand-in Credential Manager issued: (user uuid, project id, comment)
ISSUED = []

_KEY = b'test-signing-key'


def _b64(data):
    return base64.urlsafe_b64encode(data).rstrip(b'=').decode()


def _sign(header, claims):
    return _b64(hmac.new(_KEY, f'{header}.{claims}'.encode(), hashlib.sha256).digest())


def make_token(user_uuid, scope='service', lifetime=3600, project=None):
    header = _b64(json.dumps({"alg": "HS256", "typ": "JWT"}).encode())
    data = {"uuid": user_uuid, "scope": scope, "exp": int(time.time()) + lifetime}
    if project:
        data["projects"] = [{"uuid": project}]
    claims = _b64(json.dumps(data).encode())
    return f'{header}.{claims}.{_sign(header, claims)}'


def login_cookie(user_uuid, lifetime=3600):
    """The value of the FABRIC login cookie for this user."""
    return make_token(user_uuid, scope='login', lifetime=lifetime)


def _user_from_token(token):
    try:
        header, claims, signature = token.split('.')
        if not hmac.compare_digest(signature, _sign(header, claims)):
            return None
        data = json.loads(base64.urlsafe_b64decode(claims + '=' * (-len(claims) % 4)))
        if data['exp'] < time.time():
            return None
    except (ValueError, KeyError, TypeError):
        return None
    return next((u for u in USERS if u['uuid'] == data.get('uuid')), None)


def _cookie_user():
    """Whoever the login cookie belongs to. Like FABRIC's user service and
    Credential Manager, only the cookie itself is read: never pieces of it."""
    value = request.cookies.get(COOKIE_NAME, '')
    return _user_from_token(value) if value else None


def _caller():
    auth = request.headers.get('Authorization', '')
    if auth.startswith('Bearer '):
        return _user_from_token(auth[len('Bearer '):].strip())
    return _cookie_user()


app = Flask(__name__)


@app.route('/whoami')
def whoami():
    user = _caller()
    if not user:
        return jsonify({"errors": [{"message": "Invalid token"}]}), 401
    return jsonify({"results": [{"uuid": user['uuid'], "name": user['name'],
                                 "email": user['email']}]})


@app.route('/people/<uuid>')
def people(uuid):
    user = _caller()
    if not user or user['uuid'] != uuid:
        return jsonify({"errors": [{"message": "Invalid token"}]}), 401
    roles = [{"name": f"{pid}-pm"} for pid in user['project_ids']]
    return jsonify({"results": [{"uuid": user['uuid'], "roles": roles}]})


@app.route('/projects')
def projects():
    user = _caller()
    if not user or user['uuid'] != request.args.get('person_uuid'):
        return jsonify({"errors": [{"message": "Invalid token"}]}), 401
    offset = int(request.args.get('offset', 0))
    limit = int(request.args.get('limit', 5))
    mine = [dict(PROJECTS[pid], uuid=pid) for pid in user['project_ids']]
    page = mine[offset:offset + limit]
    return jsonify({"results": page, "size": len(page), "total": len(mine),
                    "offset": offset, "limit": limit})


@app.route('/credmgr/tokens/create', methods=['POST'])
def tokens_create():
    """Like the Credential Manager: login cookie only, one project, scope by project type."""
    user = _cookie_user()
    if not user:
        return jsonify({"errors": [{"message": "Login required"}]}), 401
    project_id = request.args.get('project_id')
    comment = request.args.get('comment', '')
    if not 10 <= len(comment) <= 100:
        return jsonify({"errors": [{"message": "Comment must be between 10 and 100 characters."}]}), 400
    project = PROJECTS.get(project_id)
    if not project or project_id not in user['project_ids'] or not project['active']:
        return jsonify({"errors": [{"message": "User is not a member of Project"}]}), 500
    ISSUED.append((user['uuid'], project_id, comment))
    scope = 'service' if project['project_type'] == 'service' else 'resource'
    lifetime = int(request.args.get('lifetime', 4)) * 3600
    return jsonify({"data": [{"id_token": make_token(user['uuid'], scope=scope, lifetime=lifetime,
                                                     project=project_id)}],
                    "size": 1, "type": "token"})
