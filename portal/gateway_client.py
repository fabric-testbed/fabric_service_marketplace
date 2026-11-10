"""
Client for the FABRIC Service Gateway (see gateway/gateway.py).

The portal runs on central infrastructure and cannot reach FABnetv6 directly,
so every widget call is handed to the gateway, which forwards it to the
service node.
"""
import requests as http_requests
from flask import current_app


class GatewayError(Exception):
    pass


def _cfg():
    c = current_app.config
    if not c['GATEWAY_URL'] or not c['GATEWAY_SHARED_SECRET']:
        raise GatewayError('Gateway is not configured (GATEWAY_URL / GATEWAY_SHARED_SECRET)')
    return c


def relay(method, node_ipv6, node_port, endpoint, params=None, json_body=None,
          bearer_token=None, user=None):
    """
    Send one widget request through the gateway. Returns a requests.Response.
    bearer_token is the user's FABRIC token, which the service node checks.
    """
    c = _cfg()
    headers = {
        'X-Gateway-Secret': c['GATEWAY_SHARED_SECRET'],
        'X-Target-Host': node_ipv6,
        'X-Target-Port': str(node_port),
        'Accept': 'application/json',
    }
    if bearer_token:
        headers['Authorization'] = f'Bearer {bearer_token}'
    if user:
        # Lets the service node know who is calling (set by the portal, not the browser)
        headers['X-Fabric-User-Uuid'] = user.get('uuid', '')
        headers['X-Fabric-User-Name'] = user.get('name', '')

    try:
        return http_requests.request(
            method=method,
            url=f"{c['GATEWAY_URL']}/relay{endpoint}",
            headers=headers,
            params=params,
            json=json_body,
            timeout=c['GATEWAY_TIMEOUT'],
            verify=c['GATEWAY_TLS_VERIFY'],
        )
    except http_requests.exceptions.RequestException as e:
        raise GatewayError(f'Gateway unreachable: {e}') from e


def health():
    """Check TLS, IP allowlist and shared secret in one call. Returns (ok, detail)."""
    c = _cfg()
    try:
        resp = http_requests.get(
            f"{c['GATEWAY_URL']}/health",
            headers={'X-Gateway-Secret': c['GATEWAY_SHARED_SECRET']},
            timeout=c['GATEWAY_TIMEOUT'],
            verify=c['GATEWAY_TLS_VERIFY'],
        )
    except http_requests.exceptions.RequestException as e:
        return False, f'Cannot reach gateway: {e}'
    if resp.status_code != 200:
        return False, f'Gateway answered {resp.status_code}: {resp.text.strip()}'
    return True, resp.json()
