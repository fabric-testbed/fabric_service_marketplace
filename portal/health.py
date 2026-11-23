"""
Is each service's app running? Every PORTAL_HEALTH_CHECK_SECONDS (default 60)
a background thread asks every node of every node service for GET /health,
through the gateway. A node is up when it answers 200. A service is Active when
at least one of its nodes is up, otherwise Inactive. Website links aren't checked.

Results are kept in memory; pages read them without waiting. A node not checked
yet (e.g. just registered) is checked on the spot.
"""
import json
import threading
from concurrent.futures import ThreadPoolExecutor

import gateway_client

CHECK_TIMEOUT = 3   # seconds to wait for one node's /health
_results = {}       # (ipv6, port) -> True/False
_lock = threading.Lock()
_started = False
_stop = threading.Event()


def service_nodes(service):
    """[(ipv6, port), ...] for a node service; [] for a website link."""
    if service.auth_mode == 'link':
        return []
    if service.nodes_json:
        return [(n['ipv6'], int(n['port'])) for n in json.loads(service.nodes_json)]
    if service.node_ipv6 and service.node_port:
        return [(service.node_ipv6, int(service.node_port))]
    return []


def _check_node(app, node):
    ipv6, port = node
    with app.app_context():
        try:
            resp = gateway_client.relay('GET', ipv6, port, '/health', timeout=CHECK_TIMEOUT)
            up = resp.status_code == 200
        except gateway_client.GatewayError:
            up = False
    with _lock:
        _results[node] = up
    return up


def check_nodes(app, nodes):
    """Check these nodes now, in parallel."""
    nodes = list(set(nodes))
    if nodes:
        with ThreadPoolExecutor(max_workers=min(8, len(nodes))) as pool:
            list(pool.map(lambda n: _check_node(app, n), nodes))


def check_all(app):
    from models import Service
    with app.app_context():
        nodes = [n for s in Service.query.all() for n in service_nodes(s)]
    check_nodes(app, nodes)


def node_up(app, node):
    with _lock:
        if node in _results:
            return _results[node]
    return _check_node(app, node)


def statuses(app, services):
    """{service.id: True (Active) / False (Inactive) / None (website link)}"""
    with _lock:
        missing = {n for s in services for n in service_nodes(s) if n not in _results}
    check_nodes(app, missing)
    out = {}
    for s in services:
        nodes = service_nodes(s)
        out[s.id] = None if s.auth_mode == 'link' else any(node_up(app, n) for n in nodes)
    return out


def stop():
    """Stop the background check (tests)."""
    global _started
    _stop.set()
    with _lock:
        _started = False


def forget():
    """Drop all results (tests)."""
    with _lock:
        _results.clear()


def start(app):
    """Start the once-a-minute background check (once per portal process)."""
    global _started
    interval = app.config['HEALTH_CHECK_SECONDS']
    with _lock:
        if _started or interval <= 0:
            return
        _started = True
        _stop.clear()

    def loop():
        while not _stop.is_set():
            try:
                check_all(app)
            except Exception:  # keep checking even if one round fails
                app.logger.exception('Service health check failed')
            _stop.wait(interval)

    threading.Thread(target=loop, name='service-health', daemon=True).start()
