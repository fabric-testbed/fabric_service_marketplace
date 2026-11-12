"""
FABRIC Service Gateway — runs on a FABRIC VM with a public IPv4 (FABNetv4Ext)
and a FABnetv6 interface.

It is a stateless relay: the portal (central infrastructure) cannot reach
FABnetv6, so it hands each widget request to this gateway, which forwards it
to the service node over FABnetv6 and returns the response.

The gateway has no database and does no user authentication. It trusts the
portal, and only the portal:
  1. the caller's IP must be in GATEWAY_ALLOWED_SOURCES (the portal's IP), and
  2. the caller must send the shared secret in X-Gateway-Secret.
It will only forward to addresses inside GATEWAY_ALLOWED_TARGETS, so it can
never be used as an open proxy to the internet.

Request format (sent by portal/gateway_client.py):
    <METHOD> /relay/<path on the service node>?<query>
    X-Gateway-Secret: <shared secret>
    X-Target-Host:    <service node IPv6>
    X-Target-Port:    <service node port>
    Authorization, X-Fabric-User-*, Content-Type  (passed through)
"""
import hmac
import ipaddress
import os

import requests as http_requests
from dotenv import load_dotenv
from flask import Flask, request, jsonify
from werkzeug.middleware.proxy_fix import ProxyFix

load_dotenv(os.environ.get('GATEWAY_ENV_FILE',
                           os.path.join(os.path.dirname(__file__), 'gateway.env')))


def _networks(value):
    return [ipaddress.ip_network(v.strip(), strict=False)
            for v in value.split(',') if v.strip()]


SHARED_SECRET = os.environ.get('GATEWAY_SHARED_SECRET', '')
ALLOWED_SOURCES = _networks(os.environ.get('GATEWAY_ALLOWED_SOURCES', ''))
ALLOWED_TARGETS = _networks(os.environ.get('GATEWAY_ALLOWED_TARGETS', '2602:fcfb::/40'))
REQUEST_TIMEOUT = float(os.environ.get('GATEWAY_REQUEST_TIMEOUT', '10'))
BEHIND_PROXY = os.environ.get('GATEWAY_BEHIND_PROXY', 'true').lower() == 'true'

if not SHARED_SECRET:
    raise RuntimeError('GATEWAY_SHARED_SECRET is not set (see gateway.env.example)')
if not ALLOWED_SOURCES:
    raise RuntimeError('GATEWAY_ALLOWED_SOURCES is not set (see gateway.env.example)')

# Headers passed from the portal through to the service node. Everything
# else (cookies, the gateway secret) is dropped. Authorization carries the
# user's FABRIC token, which the service node checks.
FORWARDED_HEADERS = ('content-type', 'accept', 'authorization',
                     'x-fabric-user-uuid', 'x-fabric-user-name')
RELAY_METHODS = ['GET', 'POST', 'PUT', 'PATCH', 'DELETE']

app = Flask(__name__)
if BEHIND_PROXY:
    # Nginx sets X-Forwarded-For to the real client IP
    app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1)


def _in_networks(addr, networks):
    try:
        ip = ipaddress.ip_address(addr)
    except ValueError:
        return False
    return any(ip in net for net in networks)


@app.before_request
def check_caller():
    if not _in_networks(request.remote_addr, ALLOWED_SOURCES):
        return jsonify({"error": "Gateway: caller IP not allowed"}), 403
    secret = request.headers.get('X-Gateway-Secret', '')
    if not hmac.compare_digest(secret, SHARED_SECRET):
        return jsonify({"error": "Gateway: invalid shared secret"}), 403


@app.route('/health')
def health():
    """Used by `flask check-gateway` on the portal to verify the whole setup."""
    return jsonify({
        "status": "ok",
        "allowed_targets": [str(n) for n in ALLOWED_TARGETS],
    })


@app.route('/relay/<path:subpath>', methods=RELAY_METHODS)
def relay(subpath):
    target_host = request.headers.get('X-Target-Host', '')
    try:
        target_port = int(request.headers.get('X-Target-Port', ''))
        if not 1 <= target_port <= 65535:
            raise ValueError
    except ValueError:
        return jsonify({"error": "Gateway: invalid target port"}), 400

    if not _in_networks(target_host, ALLOWED_TARGETS):
        return jsonify({"error": "Gateway: target address not allowed"}), 403

    ip = ipaddress.ip_address(target_host)
    host = f'[{ip}]' if ip.version == 6 else str(ip)
    target_url = f'http://{host}:{target_port}/{subpath}'

    headers = {k: v for k, v in request.headers.items()
               if k.lower() in FORWARDED_HEADERS}

    try:
        resp = http_requests.request(
            method=request.method,
            url=target_url,
            headers=headers,
            params=request.args.to_dict(flat=False),
            data=request.get_data(),
            timeout=REQUEST_TIMEOUT,
            allow_redirects=False,
        )
    except http_requests.exceptions.RequestException:
        return jsonify({"error": "Gateway: service node unreachable"}), 502

    # Always JSON: a node's response is never served as HTML or script
    return (
        resp.content,
        resp.status_code,
        {"Content-Type": "application/json", "X-Content-Type-Options": "nosniff"}
    )


if __name__ == '__main__':
    # Local testing only; deployments run Gunicorn. For debug mode use: flask --app gateway --debug run
    app.run(host='127.0.0.1', port=5001)
