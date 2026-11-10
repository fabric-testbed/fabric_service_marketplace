"""
Stand-in for FABRIC's user service (https://uis.fabric-testbed.net), for tests.

make_token() issues tokens shaped like real FABRIC tokens (header.claims.signature,
with a "scope" claim), and the app answers /whoami and /people/<uuid> with the
same response shapes as the real service, so the portal and the node apps run
their real token-checking code against it.
"""
import base64
import hashlib
import hmac
import json
import time

from flask import Flask, jsonify, request

PORTAL_PROJECT = 'pppppppp-0000-0000-0000-000000000001'
CONSUMER_PROJECT = 'aaaaaaaa-0000-0000-0000-000000000001'

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
]

_KEY = b'test-signing-key'


def _b64(data):
    return base64.urlsafe_b64encode(data).rstrip(b'=').decode()


def _sign(header, claims):
    return _b64(hmac.new(_KEY, f'{header}.{claims}'.encode(), hashlib.sha256).digest())


def make_token(user_uuid, scope='service', lifetime=3600):
    header = _b64(json.dumps({"alg": "HS256", "typ": "JWT"}).encode())
    claims = _b64(json.dumps({"uuid": user_uuid, "scope": scope,
                              "exp": int(time.time()) + lifetime}).encode())
    return f'{header}.{claims}.{_sign(header, claims)}'


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


def _caller():
    auth = request.headers.get('Authorization', '')
    return _user_from_token(auth[len('Bearer '):].strip()) if auth.startswith('Bearer ') else None


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
