"""
End-to-end test of the split architecture on one machine:

    portal (in-process)  ->  gateway (subprocess, 127.0.0.1)
                         ->  service node app (subprocess, [::1])

Users log in with tokens from a stand-in for FABRIC's user service
(fabric_uis_standin.py), which the portal and the node apps check tokens against.

Covers both kinds of service: an app on the author's nodes that checks the
user's FABRIC token (fabric_token), and a website link (link, admins only).

Run from the repo root:  python -m pytest tests -v
"""
import contextlib
import os
import socket
import subprocess
import sys
import time

import pytest
import requests

import fabric_uis_standin
from fabric_uis_standin import make_token

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SECRET = 'test-shared-secret'
from fabric_uis_standin import PORTAL_PROJECT, CONSUMER_PROJECT  # noqa: E402


def _free_port(family=socket.AF_INET, host='127.0.0.1'):
    with socket.socket(family) as s:
        s.bind((host, 0))
        return s.getsockname()[1]


def _wait_for(url, timeout=10):
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            requests.get(url, timeout=1)
            return
        except requests.ConnectionError:
            time.sleep(0.1)
    raise RuntimeError(f'{url} did not start')


def _start_gateway(port, allowed_sources):
    env = dict(os.environ,
               GATEWAY_ENV_FILE='/nonexistent',
               GATEWAY_SHARED_SECRET=SECRET,
               GATEWAY_ALLOWED_SOURCES=allowed_sources,
               GATEWAY_ALLOWED_TARGETS='::1/128',
               GATEWAY_BEHIND_PROXY='false')
    proc = subprocess.Popen(
        [sys.executable, '-m', 'flask', '--app', 'gateway', 'run', '--port', str(port)],
        cwd=os.path.join(ROOT, 'gateway'), env=env,
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    _wait_for(f'http://127.0.0.1:{port}/health')
    return proc


@pytest.fixture(scope='session')
def node():
    yield {'port': _free_port(socket.AF_INET6, '::1')}


@pytest.fixture(scope='session')
def gateway():
    port = _free_port()
    proc = _start_gateway(port, '127.0.0.1/32')
    yield f'http://127.0.0.1:{port}'
    proc.terminate()


@pytest.fixture(scope='session')
def fabric_uis():
    """Serve the stand-in FABRIC user service over HTTP (portal and node apps use it)."""
    import threading
    from werkzeug.serving import make_server
    port = _free_port()
    server = make_server('127.0.0.1', port, fabric_uis_standin.app, threaded=True)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    yield f'http://127.0.0.1:{port}'
    server.shutdown()


@pytest.fixture(scope='session')
def portal(gateway, fabric_uis, tmp_path_factory):
    tmp = tmp_path_factory.mktemp('portal')
    os.environ.update(
        PORTAL_ENV_FILE='/nonexistent',
        PORTAL_SECRET_KEY='test-secret-key',
        PORTAL_DATABASE_URI=f'sqlite:///{tmp}/portal.db',
        FABRIC_UIS_URL=fabric_uis,
        FABRIC_CREDMGR_URL=fabric_uis,  # the stand-in also plays the Credential Manager
        PORTAL_SERVICE_NODE_NETWORK='::1/128',
        PORTAL_PROJECT_ID=PORTAL_PROJECT,
        GATEWAY_URL=gateway,
        GATEWAY_SHARED_SECRET=SECRET,
        GATEWAY_TLS_VERIFY='false',
        PORTAL_BEHIND_PROXY='false',
        PORTAL_SECURE_COOKIES='false',
        PORTAL_HEALTH_CHECK_SECONDS='0',
    )
    sys.path.insert(0, os.path.join(ROOT, 'portal'))
    from app import app
    app.config['WTF_CSRF_ENABLED'] = False
    return app


USER_IDS = {u['name']: u['uuid'] for u in fabric_uis_standin.USERS}


def login(client, user_name):
    """Log in by pasting a FABRIC service token; return the landing path."""
    client.post('/auth/logout')
    resp = client.post('/auth/login', json={'id_token': make_token(USER_IDS[user_name])})
    assert resp.status_code == 200, resp.json
    return resp.json['next']


@pytest.fixture(scope='session')
def service(portal, node):
    """The author registers a service; then the echo node is started."""
    client = portal.test_client()
    assert login(client, 'TestAuthor') == '/register_service'
    resp = client.post('/api/register_service', json={
        'service_name': 'echo-svc',
        'description': 'test',
        'access_project_ids': [CONSUMER_PROJECT],
        'nodes': [{'name': 'n1', 'ipv6': '::1', 'port': node['port']}],
        'widgets': [
            {'id': 'w1', 'type': 'form', 'label': 'Echo GET', 'endpoint': '/api/echo',
             'method': 'GET', 'input_fields': [{'name': 'host', 'label': 'Host'}]},
            {'id': 'w2', 'type': 'form', 'label': 'Echo POST', 'endpoint': '/api/echo',
             'method': 'POST', 'input_fields': [{'name': 'title', 'label': 'Title'}]},
        ],
    })
    assert resp.status_code == 201, resp.json

    proc = subprocess.Popen(
        [sys.executable, os.path.join(ROOT, 'tests', 'fake_service_node.py'), str(node['port'])],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    _wait_for(f'http://[::1]:{node["port"]}/api/echo')
    yield
    proc.terminate()


# ── Login and permissions ──

def test_author_lands_on_register(portal):
    assert login(portal.test_client(), 'TestAuthor') == '/register_service'


def test_consumer_cannot_register(portal):
    client = portal.test_client()
    assert login(client, 'TestConsumer') == '/available_services'
    assert client.get('/register_service').status_code == 403
    assert client.post('/api/register_service', json={}).status_code == 403


# ── Registration validation ──

@pytest.mark.parametrize('node_ip,endpoint,expected_error', [
    ('2001:db8::1', '/api/x', 'inside'),
    ('::1', '@evil.example/x', 'Invalid endpoint'),
    ('::1', '/api/../admin', 'Invalid endpoint'),
    ('::1', '/', 'Invalid endpoint'),
    ('::1', '//evil/x', 'Invalid endpoint'),
    ('::1', 'api/no-slash', 'Invalid endpoint'),
    ('::1', '/health', 'health check'),
])
def test_registration_validation(portal, node_ip, endpoint, expected_error):
    client = portal.test_client()
    login(client, 'TestAuthor')
    resp = client.post('/api/register_service', json={
        'service_name': 'bad-svc', 'access_project_ids': [CONSUMER_PROJECT],
        'nodes': [{'name': 'n', 'ipv6': node_ip, 'port': 8080}],
        'widgets': [{'id': 'w1', 'type': 'button', 'label': 'x',
                     'endpoint': endpoint, 'method': 'GET'}],
    })
    assert resp.status_code == 400 and expected_error in resp.json['error']


# ── Full path: portal -> gateway -> node ──

def test_consumer_get_through_gateway(portal, service):
    client = portal.test_client()
    login(client, 'TestConsumer')
    page = client.get('/available_services/echo-svc')
    assert b'Echo GET' in page.data  # auto-granted, widgets visible

    resp = client.get('/available_services/echo-svc/proxy/w1?host=example.org')
    assert resp.status_code == 200, resp.data
    data = resp.json
    assert data['method'] == 'GET'
    assert data['args'] == {'host': 'example.org'}
    assert data['user_name'] == 'TestConsumer'
    assert data['user_uuid'] == '22222222-2222-2222-2222-222222222222'
    assert data['cookie'] is None          # browser cookies never reach the node
    assert data['gateway_secret'] is None  # gateway secret never reaches the node
    assert data['authorization'].startswith('Bearer ')  # the user's FABRIC token
    assert data['api_key'] is None


def test_consumer_post_through_gateway(portal, service):
    client = portal.test_client()
    login(client, 'TestConsumer')
    client.get('/available_services/echo-svc')
    resp = client.post('/available_services/echo-svc/proxy/w2', json={'title': 'hi'})
    assert resp.status_code == 200, resp.data
    assert resp.json['method'] == 'POST' and resp.json['json'] == {'title': 'hi'}


def test_usage_is_logged(portal, service):
    from models import UsageLog
    with portal.app_context():
        assert UsageLog.query.filter_by(consumer_name='TestConsumer').count() >= 1


def test_outsider_denied(portal, service):
    client = portal.test_client()
    login(client, 'TestOutsider')
    page = client.get('/available_services/echo-svc')
    assert b'not a\n            member' in page.data or b'not a member' in page.data
    assert client.get('/available_services/echo-svc/proxy/w1').status_code == 401


def test_check_gateway_cli(portal):
    result = portal.test_cli_runner().invoke(args=['check-gateway'])
    assert 'OK' in result.output, result.output


# ── Gateway guards (called directly, as an attacker would) ──

def test_gateway_passes_on_only_json(gateway, node, service):
    headers = {'X-Gateway-Secret': SECRET, 'X-Target-Host': '::1', 'X-Target-Port': str(node['port'])}
    ok = requests.get(f'{gateway}/relay/api/echo', headers=headers)
    assert ok.status_code == 200 and ok.headers['Content-Type'] == 'application/json'
    assert ok.headers['X-Content-Type-Options'] == 'nosniff' and ok.json()['method'] == 'GET'
    # A node that answers with HTML gets an error, never its HTML
    html = requests.get(f'{gateway}/relay/api/html', headers=headers)
    assert html.status_code == 502 and b'<script>' not in html.content
    assert html.json() == {'error': 'Gateway: service node did not return JSON'}


def _start_gateway_gunicorn(port, extra_args):
    env = dict(os.environ, GATEWAY_ENV_FILE='/nonexistent', GATEWAY_SHARED_SECRET=SECRET,
               GATEWAY_ALLOWED_SOURCES='127.0.0.1/32', GATEWAY_ALLOWED_TARGETS='::1/128',
               GATEWAY_BEHIND_PROXY='false')
    proc = subprocess.Popen(
        [sys.executable, '-m', 'gunicorn', '-w', '1', '-b', f'127.0.0.1:{port}', *extra_args, 'gateway:app'],
        cwd=os.path.join(ROOT, 'gateway'), env=env,
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    _wait_for(f'http://127.0.0.1:{port}/health')
    return proc


def test_gateway_accepts_long_fabric_tokens(node, service):
    """Real FABRIC tokens can be over 8 KB; the gateway runs Gunicorn with the
    options from deploy.sh, which must let such an Authorization header through."""
    pytest.importorskip('gunicorn')
    import re
    import shlex
    deploy = open(os.path.join(ROOT, 'gateway', 'deploy.sh')).read()
    args = shlex.split(re.search(r'ExecStart=\S*gunicorn (.*) gateway:app', deploy).group(1))
    args = [a for i, a in enumerate(args)  # keep everything but workers and bind address
            if a not in ('-w', '-b') and (i == 0 or args[i - 1] not in ('-w', '-b'))]
    long_token = 'x' * 9000
    headers = {'X-Gateway-Secret': SECRET, 'X-Target-Host': '::1', 'X-Target-Port': str(node['port']),
               'Authorization': f'Bearer {long_token}'}
    # deploy.sh options vs Gunicorn's defaults (400 or 431 "header too large", by version)
    for extra, expected in ((args, {200}), ([], {400, 431})):
        port = _free_port()
        proc = _start_gateway_gunicorn(port, extra)
        try:
            resp = requests.get(f'http://127.0.0.1:{port}/relay/api/echo', headers=headers)
            assert resp.status_code in expected, (extra, resp.status_code)
            if expected == {200}:
                assert resp.json()['authorization'] == f'Bearer {long_token}'
        finally:
            proc.terminate()


def test_gateway_nginx_allows_long_headers():
    conf = open(os.path.join(ROOT, 'gateway', 'nginx', 'gateway.conf')).read()
    assert 'large_client_header_buffers 4 32k;' in conf


def test_gateway_rejects_wrong_secret(gateway, node):
    resp = requests.get(f'{gateway}/relay/api/echo', headers={
        'X-Gateway-Secret': 'wrong', 'X-Target-Host': '::1',
        'X-Target-Port': str(node['port'])})
    assert resp.status_code == 403


def test_gateway_rejects_target_outside_allowed_network(gateway):
    resp = requests.get(f'{gateway}/relay/api/echo', headers={
        'X-Gateway-Secret': SECRET, 'X-Target-Host': '127.0.0.1', 'X-Target-Port': '22'})
    assert resp.status_code == 403
    assert 'target address not allowed' in resp.json()['error']


def test_gateway_rejects_unlisted_source_ip():
    port = _free_port()
    proc = _start_gateway(port, '10.0.0.0/8')  # portal IP is not 127.0.0.1 here
    try:
        resp = requests.get(f'http://127.0.0.1:{port}/health',
                            headers={'X-Gateway-Secret': SECRET})
        assert resp.status_code == 403 and 'caller IP' in resp.json()['error']
    finally:
        proc.terminate()


def test_gateway_down_gives_502(portal, service):
    client = portal.test_client()
    login(client, 'TestConsumer')
    client.get('/available_services/echo-svc')
    original = portal.config['GATEWAY_URL']
    portal.config['GATEWAY_URL'] = f'http://127.0.0.1:{_free_port()}'
    try:
        resp = client.get('/available_services/echo-svc/proxy/w1')
        assert resp.status_code == 502
    finally:
        portal.config['GATEWAY_URL'] = original



# ── Active / Inactive: is the service's app answering GET /health? ──

ACTIVE = b'badge bg-success fs-6'
INACTIVE = b'badge bg-secondary fs-6'


def test_running_service_is_active(portal, service):
    import health
    health.forget()
    page = portal.test_client().get('/available_services/echo-svc').data
    assert ACTIVE in page and INACTIVE not in page
    assert b'>Active</span>' in portal.test_client().get('/available_services').data


def test_service_is_inactive_until_its_app_runs(portal, fabric_uis):
    import health
    name, port = 'not-running-yet', _free_port(socket.AF_INET6, '::1')
    _register(portal, name, 'GET', port)
    health.forget()
    page = portal.test_client().get(f'/available_services/{name}').data
    assert INACTIVE in page and b'not running right now' in page

    proc = _start_node_app(EXAMPLE_APP, port, FABRIC_UIS_URL=fabric_uis)
    try:
        # Before the next check the old result stays; the next check sees it running
        assert INACTIVE in portal.test_client().get(f'/available_services/{name}').data
        health.check_all(portal)
        assert ACTIVE in portal.test_client().get(f'/available_services/{name}').data
    finally:
        proc.terminate()
        proc.wait()
    health.check_all(portal)
    assert INACTIVE in portal.test_client().get(f'/available_services/{name}').data


def test_gateway_down_means_inactive(portal, service):
    import health
    health.forget()
    original = portal.config['GATEWAY_URL']
    portal.config['GATEWAY_URL'] = f'http://127.0.0.1:{_free_port()}'
    try:
        assert INACTIVE in portal.test_client().get('/available_services/echo-svc').data
    finally:
        portal.config['GATEWAY_URL'] = original
        health.forget()


def test_website_links_have_no_status(portal):
    client = portal.test_client()
    with as_admin(portal, client, 'TestAuthor'):
        client.post('/api/register_service', json={
            'service_name': 'link-no-status', 'auth_mode': 'link', 'access_project_ids': [CONSUMER_PROJECT], 'link_url': 'https://example.org'})
    page = client.get('/available_services/link-no-status').data
    assert ACTIVE not in page and INACTIVE not in page


def test_background_check_runs_every_interval(portal, service):
    """With PORTAL_HEALTH_CHECK_SECONDS set, a background thread refreshes the results."""
    import health
    health.forget()
    portal.config['HEALTH_CHECK_SECONDS'] = 1
    try:
        health.start(portal)
        deadline = time.time() + 10
        while time.time() < deadline and ('::1', int(service_port(portal))) not in health._results:
            time.sleep(0.2)
        assert health._results.get(('::1', int(service_port(portal)))) is True
    finally:
        health.stop()
        portal.config['HEALTH_CHECK_SECONDS'] = 0
        time.sleep(1.5)  # let the last round finish
        health.forget()


def service_port(portal):
    from models import Service
    with portal.app_context():
        return Service.query.filter_by(name='echo-svc').first().node_port


# ── The example app and the generated app work behind the gateway ──

EXAMPLE_APP = os.path.join(ROOT, 'service_node', 'example_app.py')


def _start_node_app(script, port, log_file=None, **extra_env):
    out = open(log_file, 'w') if log_file else subprocess.DEVNULL
    proc = subprocess.Popen(
        [sys.executable, str(script)],
        env=dict(os.environ, SERVICE_PORT=str(port), **extra_env),
        stdout=out, stderr=out)
    _wait_for(f'http://[::1]:{port}/health')
    return proc


def _register(portal, name, method, port, endpoint='/api/service_config'):
    """Register a one-widget service as the author. Returns the generated app."""
    client = portal.test_client()
    login(client, 'TestAuthor')
    resp = client.post('/api/register_service', json={
        'service_name': name, 'access_project_ids': [CONSUMER_PROJECT],
        'nodes': [{'name': 'n1', 'ipv6': '::1', 'port': port}],
        'widgets': [{'id': 'w1', 'type': 'form', 'label': 'Config',
                     'endpoint': endpoint, 'method': method,
                     'input_fields': [{'name': 'target', 'label': 'Target'}]}],
    })
    assert resp.status_code == 201, resp.json
    return resp.json['flask_template']


def _call_as_consumer(portal, name, method):
    consumer = portal.test_client()
    login(consumer, 'TestConsumer')
    consumer.get(f'/available_services/{name}')
    url = f'/available_services/{name}/proxy/w1'
    if method == 'GET':
        return consumer.get(url + '?target=n2')
    return consumer.post(url, json={'target': 'n2'})


# ── The node checks the user's FABRIC token itself ──


CONSUMER = '22222222-2222-2222-2222-222222222222'
OUTSIDER = '33333333-3333-3333-3333-333333333333'


@pytest.mark.parametrize('method', ['GET', 'POST'])
def test_token_example_behind_gateway(portal, fabric_uis, method):
    name, port = f'token-example-{method.lower()}', _free_port(socket.AF_INET6, '::1')
    _register(portal, name, method, port)
    proc = _start_node_app(EXAMPLE_APP, port,
                           FABRIC_UIS_URL=fabric_uis, ALLOWED_PROJECTS=CONSUMER_PROJECT)
    try:
        resp = _call_as_consumer(portal, name, method)
        assert resp.status_code == 200, resp.data
        assert resp.json['inputs'] == {'target': 'n2'}
        assert resp.json['caller']['name'] == 'TestConsumer'  # verified via the token, not headers
    finally:
        proc.terminate()


def test_token_direct_calls_from_a_vm(portal, fabric_uis):
    """Nodes can be called without the portal, using the user's own token."""
    port = _free_port(socket.AF_INET6, '::1')
    proc = _start_node_app(EXAMPLE_APP, port,
                           FABRIC_UIS_URL=fabric_uis, ALLOWED_PROJECTS=CONSUMER_PROJECT)
    url = f'http://[::1]:{port}/api/service_config?target=x'
    try:
        assert requests.get(url).status_code == 401
        assert requests.get(url, headers={'Authorization': 'Bearer forged'}).status_code == 401
        consumer = requests.get(url, headers={'Authorization': f'Bearer {make_token(CONSUMER)}'})
        assert consumer.status_code == 200 and consumer.json()['caller']['name'] == 'TestConsumer'
        outsider = requests.get(url, headers={'Authorization': f'Bearer {make_token(OUTSIDER)}'})
        assert outsider.status_code == 403  # valid token, but not in an allowed project
        resource = requests.get(url, headers={
            'Authorization': f'Bearer {make_token(CONSUMER, scope="resource")}'})
        assert resource.status_code == 401  # only service tokens are accepted
    finally:
        proc.terminate()


@pytest.mark.parametrize('method', ['GET', 'POST'])
def test_token_generated_template_behind_gateway(portal, fabric_uis, tmp_path, method):
    name, port = f'token-generated-{method.lower()}', _free_port(socket.AF_INET6, '::1')
    template = _register(portal, name, method, port)
    assert CONSUMER_PROJECT in template  # allowed projects are baked in
    script = tmp_path / 'app.py'
    script.write_text(template)
    log_file = tmp_path / 'app.log'
    proc = _start_node_app(script, port, log_file=log_file, FABRIC_UIS_URL=fabric_uis)
    try:
        resp = _call_as_consumer(portal, name, method)
        assert resp.status_code == 200, resp.data
        assert resp.json['caller']['name'] == 'TestConsumer'
        direct = requests.request(method, f'http://[::1]:{port}/api/service_config', json={})
        assert direct.status_code == 401
        # /health answers without a token
        assert requests.get(f'http://[::1]:{port}/health').json() == {'status': 'ok'}
    finally:
        proc.terminate()
        proc.wait()
    # The app printed who made the request
    assert '/api/service_config requested by TestConsumer' in log_file.read_text()


def test_generated_template_marks_optional_and_fill_in_parts(portal):
    template = _register(portal, 'template-parts', 'GET', 8080)
    for text in ('2. Health check (GET /health)', '3. FABRIC token check', 'OPTIONAL',
                 'CHECK_FABRIC_TOKEN = True', '>>> FILL IN', 'target = request.args.get("target")',
                 'user = caller()', 'user["uuid"] as the key'):
        assert text in template, text


def test_token_check_can_be_turned_off(portal, fabric_uis, tmp_path):
    """With CHECK_FABRIC_TOKEN = False the app trusts the portal's identity headers."""
    name, port = 'token-off', _free_port(socket.AF_INET6, '::1')
    template = _register(portal, name, 'GET', port)
    script = tmp_path / 'app.py'
    script.write_text(template.replace('CHECK_FABRIC_TOKEN = True', 'CHECK_FABRIC_TOKEN = False'))
    proc = _start_node_app(script, port, FABRIC_UIS_URL=fabric_uis)
    try:
        resp = _call_as_consumer(portal, name, 'GET')
        assert resp.status_code == 200, resp.data
        assert resp.json['caller'] == {'uuid': CONSUMER, 'name': 'TestConsumer', 'verified': False}
        # Anyone on FABnetv6 can now call it directly
        assert requests.get(f'http://[::1]:{port}/api/service_config').status_code == 200
    finally:
        proc.terminate()


def test_whoami_shows_whether_the_token_arrived(fabric_uis):
    port = _free_port(socket.AF_INET6, '::1')
    proc = _start_node_app(EXAMPLE_APP, port, FABRIC_UIS_URL=fabric_uis, ALLOWED_PROJECTS=CONSUMER_PROJECT)
    url = f'http://[::1]:{port}/whoami'
    try:
        none = requests.get(url).json()
        assert none['token_received'] is False and none['user'] is None
        good = requests.get(url, headers={'Authorization': f'Bearer {make_token(CONSUMER)}'}).json()
        assert good['token_received'] and good['token_valid'] and good['in_allowed_project']
        assert good['token_scope'] == 'service' and good['user']['name'] == 'TestConsumer'
        outsider = requests.get(url, headers={'Authorization': f'Bearer {make_token(OUTSIDER)}'}).json()
        assert outsider['token_valid'] and not outsider['in_allowed_project']
        resource = requests.get(url, headers={
            'Authorization': f'Bearer {make_token(CONSUMER, scope="resource")}'}).json()
        assert resource['token_scope'] == 'resource'
        assert make_token(CONSUMER) not in requests.get(
            url, headers={'Authorization': f'Bearer {make_token(CONSUMER)}'}).text  # never echoed
    finally:
        proc.terminate()


def test_whoami_widget_through_the_portal(portal, fabric_uis):
    """A widget with endpoint /whoami on the example app shows that the portal passed the user's token."""
    name, port = 'whoami-test', _free_port(socket.AF_INET6, '::1')
    _register(portal, name, 'GET', port, endpoint='/whoami')
    proc = _start_node_app(EXAMPLE_APP, port, FABRIC_UIS_URL=fabric_uis, ALLOWED_PROJECTS=CONSUMER_PROJECT)
    try:
        resp = _call_as_consumer(portal, name, 'GET')
        assert resp.status_code == 200, resp.data
        assert resp.json['token_received'] and resp.json['token_valid'] and resp.json['in_allowed_project']
        assert resp.json['user']['name'] == 'TestConsumer'
        assert resp.json['portal_headers']['name'] == 'TestConsumer'
    finally:
        proc.terminate()
        proc.wait()


def test_example_app_logs_calls_with_the_caller(portal, fabric_uis, tmp_path):
    name, port = 'logged-calls', _free_port(socket.AF_INET6, '::1')
    _register(portal, name, 'GET', port)
    log_file = tmp_path / 'app.log'
    proc = _start_node_app(EXAMPLE_APP, port, log_file=log_file,
                           FABRIC_UIS_URL=fabric_uis, ALLOWED_PROJECTS=CONSUMER_PROJECT)
    try:
        resp = _call_as_consumer(portal, name, 'GET')
        assert resp.status_code == 200 and resp.json['requested_by'] == 'TestConsumer'
    finally:
        proc.terminate()
        proc.wait()
    log = log_file.read_text()
    assert '/api/service_config requested by TestConsumer' in log and 'verified=True' in log


def test_example_app_health_and_token_off(fabric_uis):
    port = _free_port(socket.AF_INET6, '::1')
    proc = _start_node_app(EXAMPLE_APP, port, FABRIC_UIS_URL=fabric_uis, CHECK_FABRIC_TOKEN='false')
    try:
        assert requests.get(f'http://[::1]:{port}/health').json() == {'status': 'ok'}
        resp = requests.get(f'http://[::1]:{port}/api/service_config?target=x')
        assert resp.status_code == 200 and resp.json()['caller']['verified'] is False
    finally:
        proc.terminate()


# ── Website link (asked for by a project owner and approved by an admin, or added by an admin) ──

@contextlib.contextmanager
def as_admin(portal, client, user_name):
    """Log in as user_name with the admin role; the role is reset afterwards."""
    from models import User, db
    login(client, user_name)  # creates the user row on first login
    with portal.app_context():
        User.query.filter_by(fabric_uuid=USER_IDS[user_name]).first().role = 'admin'
        db.session.commit()
    login(client, user_name)
    try:
        yield client
    finally:
        with portal.app_context():
            User.query.filter_by(fabric_uuid=USER_IDS[user_name]).first().role = 'user'
            db.session.commit()


def approve_link(admin_client, name, url):
    """As an admin: approve a website link, as on the Admin page."""
    resp = admin_client.post(f'/admin/services/{name}/review', json={'decision': 'approve', 'link_url': url})
    assert resp.status_code == 200, resp.json


def test_owner_asks_for_a_link_and_an_admin_approves_it(portal, service):
    owner = portal.test_client()
    login(owner, 'TestAuthor')
    link = {'service_name': 'asked-link', 'title': 'Asked Link', 'auth_mode': 'link',
            'link_url': 'https://example.org/asked', 'access_project_ids': [CONSUMER_PROJECT]}

    # Needs a service project the author owns
    resp = owner.post('/api/register_service', json=dict(link, access_project_ids=[]))
    assert resp.status_code == 400
    member = portal.test_client()
    login(member, 'TestMemberAuthor')
    assert member.post('/api/register_service', json=link).status_code == 400

    # The request is saved, waiting for approval
    resp = owner.post('/api/register_service', json=link)
    assert resp.status_code == 201 and resp.json['status'] == 'pending', resp.json
    assert b'Request sent for approval' in owner.get('/register_service/success/asked-link').data
    assert b'Waiting for approval' in owner.get('/my_services').data

    # Not listed, and its page is hidden from everyone but the owner and admins
    consumer = portal.test_client()
    login(consumer, 'TestConsumer')
    for client in (consumer, portal.test_client()):
        assert b'asked-link' not in client.get('/available_services').data
        assert client.get('/available_services/asked-link').status_code == 404
    assert owner.get('/available_services/asked-link').status_code == 200

    # Only admins can decide
    assert owner.post('/admin/services/asked-link/review', json={'decision': 'approve', 'link_url': 'https://example.org/asked'}).status_code == 403
    admin = portal.test_client()
    with as_admin(portal, admin, 'TestOutsider'):
        page = admin.get('/admin').get_data(as_text=True)
        assert 'Website links waiting for approval' in page and 'https://example.org/asked' in page
        assert admin.get('/available_services/asked-link').status_code == 200
        assert admin.post('/admin/services/asked-link/review', json={'decision': 'maybe'}).status_code == 400
        # Approval is for the address the admin was shown: not for one changed since, or none
        for seen in ({}, {'link_url': 'https://example.org/something-else'}):
            resp = admin.post('/admin/services/asked-link/review', json=dict(seen, decision='approve'))
            assert resp.status_code == 409, resp.json
        resp = admin.post('/admin/services/asked-link/review',
                          json={'decision': 'approve', 'link_url': 'https://example.org/asked'})
        assert resp.json == {'ok': True, 'status': 'active'}
        # Node services are not reviewed
        assert admin.post('/admin/services/echo-svc/review', json={'decision': 'reject'}).status_code == 400
        # No longer among the links waiting (other tests may have left theirs there)
        assert 'data-name="asked-link" data-url' not in admin.get('/admin').get_data(as_text=True)

    # Approved: members of the project get the link, others are shown how to ask
    assert b'href="https://example.org/asked"' in consumer.get('/available_services/asked-link').data
    outsider = portal.test_client()
    login(outsider, 'TestOutsider')
    page = outsider.get('/available_services/asked-link')
    assert page.status_code == 200 and b'href="https://example.org/asked"' not in page.data
    assert b'Request access' in page.data
    assert b'Request access' in outsider.get('/available_services').data

    # Editing the description keeps it approved; a new web address needs approval again
    resp = owner.post('/api/update_service', json=dict(link, description='new words'))
    assert resp.json['status'] == 'active'
    resp = owner.post('/api/update_service', json=dict(link, link_url='https://example.org/other'))
    assert resp.json['status'] == 'pending'
    assert consumer.get('/available_services/asked-link').status_code == 404

    # Rejected: stays hidden, and the owner is told
    with as_admin(portal, admin, 'TestOutsider'):
        assert admin.post('/admin/services/asked-link/review', json={'decision': 'reject'}).json['status'] == 'rejected'
    assert consumer.get('/available_services/asked-link').status_code == 404
    assert b'Rejected' in owner.get('/my_services').data

    # Everyone who may register sees the choice on the form
    assert b'id="mode-link"' in owner.get('/register_service').data


def test_link_service(portal):
    client = portal.test_client()
    with as_admin(portal, client, 'TestAuthor'):
        _check_link_service(portal, client)


def _check_link_service(portal, client):
    resp = client.post('/api/register_service', json={
        'service_name': 'link-svc', 'auth_mode': 'link', 'access_project_ids': [CONSUMER_PROJECT],
        'link_url': 'https://example.org/my-service', 'description': 'hosted elsewhere'})
    assert resp.status_code == 201, resp.json
    assert resp.json['flask_template'] is None
    assert resp.json['status'] == 'pending'  # approval is explicit, for admins too
    approve_link(client, 'link-svc', 'https://example.org/my-service')

    # A link without a service project is refused, for admins too
    resp = client.post('/api/register_service', json={
        'service_name': 'link-no-project', 'auth_mode': 'link', 'link_url': 'https://example.org/x'})
    assert resp.status_code == 400

    # Listed for everyone; the link itself is for members of its service project
    anonymous = portal.test_client()
    page = anonymous.get('/available_services/link-svc')
    assert page.status_code == 200
    assert b'href="https://example.org/my-service"' not in page.data
    assert b'Website link' in anonymous.get('/available_services').data

    # Never proxied, even for a logged-in project member
    login(anonymous, 'TestConsumer')
    assert b'href="https://example.org/my-service"' in anonymous.get('/available_services/link-svc').data
    assert anonymous.get('/available_services/link-svc/proxy/w1').status_code in (400, 401)
    assert b'Nothing to install' in client.get('/register_service/success/link-svc').data


@pytest.mark.parametrize('url', ['javascript:alert(1)', 'ftp://example.org', 'example.org', ''])
def test_link_rejects_bad_urls(portal, url):
    client = portal.test_client()
    with as_admin(portal, client, 'TestAuthor'):
        resp = client.post('/api/register_service', json={
            'service_name': 'link-bad', 'auth_mode': 'link', 'access_project_ids': [CONSUMER_PROJECT], 'link_url': url})
        assert resp.status_code == 400
        assert 'Only admins' not in resp.json['error']


def test_switch_mode_on_edit(portal):
    client = portal.test_client()
    _register(portal, 'switcher', 'GET', 8080)
    with as_admin(portal, client, 'TestAuthor'):
        resp = client.post('/api/update_service', json={
            'service_name': 'switcher', 'auth_mode': 'link', 'access_project_ids': [CONSUMER_PROJECT], 'link_url': 'https://example.org'})
    assert resp.status_code == 200, resp.json
    assert b'href="https://example.org"' in client.get('/available_services/switcher').data
    assert client.get('/edit_service/switcher').status_code == 200

    # A failed update leaves the service unchanged (the API-key mode no longer exists)
    resp = client.post('/api/update_service', json={'service_name': 'switcher', 'auth_mode': 'api_key'})
    assert resp.status_code == 400
    from models import Service
    with portal.app_context():
        assert Service.query.filter_by(name='switcher').first().auth_mode == 'link'


# ── Existing databases get the new columns ──

def test_old_database_is_upgraded(tmp_path):
    import sqlite3
    db_file = tmp_path / 'old.db'
    conn = sqlite3.connect(db_file)
    conn.execute("""CREATE TABLE services (id INTEGER PRIMARY KEY, name VARCHAR NOT NULL,
        description VARCHAR, owner_id INTEGER NOT NULL, access_project_id VARCHAR,
        access_project_ids_json TEXT, node_ipv6 VARCHAR, node_port INTEGER, nodes_json TEXT,
        api_key VARCHAR, icon_color VARCHAR, canvas_json TEXT NOT NULL, status VARCHAR NOT NULL,
        created_at DATETIME, updated_at DATETIME)""")
    conn.execute("INSERT INTO services (name, owner_id, canvas_json, status) VALUES ('old', 1, '[]', 'active')")
    conn.commit()
    conn.close()

    env = dict(os.environ, PORTAL_ENV_FILE='/nonexistent', PORTAL_SECRET_KEY='k',
               PORTAL_DATABASE_URI=f'sqlite:///{db_file}')
    subprocess.run([sys.executable, '-c', 'import app'], cwd=os.path.join(ROOT, 'portal'),
                   env=env, check=True, capture_output=True)

    conn = sqlite3.connect(db_file)
    row = conn.execute("SELECT auth_mode, link_url FROM services WHERE name='old'").fetchone()
    assert row == ('fabric_token', None)


def test_api_key_services_become_token_services(tmp_path):
    import sqlite3
    db_file = tmp_path / 'with_api_key.db'
    conn = sqlite3.connect(db_file)
    conn.execute("""CREATE TABLE services (id INTEGER PRIMARY KEY, name VARCHAR NOT NULL,
        description VARCHAR, owner_id INTEGER NOT NULL, access_project_id VARCHAR,
        access_project_ids_json TEXT, node_ipv6 VARCHAR, node_port INTEGER, nodes_json TEXT,
        api_key VARCHAR, auth_mode VARCHAR NOT NULL, link_url VARCHAR, icon_color VARCHAR,
        canvas_json TEXT NOT NULL, status VARCHAR NOT NULL, created_at DATETIME, updated_at DATETIME)""")
    conn.execute("INSERT INTO services (name, owner_id, auth_mode, canvas_json, status) "
                 "VALUES ('a', 1, 'api_key', '[]', 'active'), ('b', 1, 'link', '[]', 'active')")
    conn.commit()
    conn.close()

    env = dict(os.environ, PORTAL_ENV_FILE='/nonexistent', PORTAL_SECRET_KEY='k',
               PORTAL_DATABASE_URI=f'sqlite:///{db_file}')
    subprocess.run([sys.executable, '-c', 'import app'], cwd=os.path.join(ROOT, 'portal'),
                   env=env, check=True, capture_output=True)

    conn = sqlite3.connect(db_file)
    rows = dict(conn.execute("SELECT name, auth_mode FROM services").fetchall())
    assert rows == {'a': 'fabric_token', 'b': 'link'}


def test_api_key_mode_cannot_be_registered(portal):
    client = portal.test_client()
    login(client, 'TestAuthor')
    resp = client.post('/api/register_service', json={
        'service_name': 'old-style', 'auth_mode': 'api_key', 'access_project_ids': [CONSUMER_PROJECT],
        'nodes': [{'name': 'n', 'ipv6': '::1', 'port': 8080}],
        'widgets': [{'id': 'w1', 'type': 'button', 'label': 'x', 'endpoint': '/api/x', 'method': 'GET'}]})
    assert resp.status_code == 400



# ── Handover contract: any login method only has to supply a valid FABRIC token ──

AUTHOR = '11111111-1111-1111-1111-111111111111'


@pytest.mark.parametrize('user_uuid,can_register,projects', [
    (AUTHOR, True, {PORTAL_PROJECT, CONSUMER_PROJECT}),  # full project list, not just one
    (CONSUMER, False, {CONSUMER_PROJECT}),
])
def test_login_with_fabric_token(portal, fabric_uis, user_uuid, can_register, projects):
    """What a new login method (e.g. CILogon) calls once it has the token.
    Runs the real FABRIC lookup code against the stand-in FABRIC user service."""
    from flask import session
    from routes.auth import login_with_fabric_token
    token = make_token(user_uuid)

    with portal.test_request_context():
        user, error = login_with_fabric_token(token)
        assert error is None
        assert user['can_register'] is can_register
        assert set(user['project_ids']) == projects
        assert session['portal_user']['uuid'] == user_uuid
        import token_store
        assert token_store.get() == token  # passed on to service nodes


def test_login_rejects_resource_token(portal, fabric_uis):
    """Resource tokens can create slices as the user, so they must never reach services."""
    from routes.auth import login_with_fabric_token
    with portal.test_request_context():
        user, error = login_with_fabric_token(make_token(CONSUMER, scope='resource'))
        assert user is None and "'resource' token" in error
    # The login page refuses it too
    resp = portal.test_client().post('/auth/login',
                                     json={'id_token': make_token(CONSUMER, scope='resource')})
    assert resp.status_code == 401 and 'service' in resp.json['error']


@pytest.mark.parametrize('token', ['', 'not-a-fabric-token'])
def test_login_with_invalid_fabric_token(portal, fabric_uis, token):
    from routes.auth import login_with_fabric_token
    with portal.test_request_context():
        user, error = login_with_fabric_token(token)
        assert user is None and error


# ── Token test page: checks a real token with FABRIC, no login ──

def test_token_test_page(portal, fabric_uis):
    client = portal.test_client()
    assert b'Test a FABRIC token' in client.get('/token_test').data

    resp = client.post('/api/token_test', json={'id_token': make_token(CONSUMER)})
    assert resp.status_code == 200
    assert resp.json['name'] == 'TestConsumer'
    assert resp.json['project_ids'] == [CONSUMER_PROJECT]
    assert resp.json['can_register'] is False
    with client.session_transaction() as s:
        assert 'portal_user' not in s  # checking does not log in

    author_info = client.post('/api/token_test', json={'id_token': make_token(AUTHOR)}).json
    assert author_info['can_register'] is True
    assert set(author_info['project_ids']) == {PORTAL_PROJECT, CONSUMER_PROJECT}

    assert client.post('/api/token_test', json={'id_token': 'garbage'}).status_code == 401
    resource = client.post('/api/token_test',
                           json={'id_token': make_token(CONSUMER, scope='resource')})
    assert resource.status_code == 401 and 'service' in resource.json['error']
    assert client.post('/api/token_test', json={}).status_code == 400


# ── Login page: "Sign in with FABRIC" button, token paste only on /token_test ──

def test_login_page_without_automated_login(portal):
    page = portal.test_client().get('/login?next=/available_services/x').data
    assert b'Sign in with FABRIC' in page and b'disabled' in page
    assert b'href="/token_test?next=/available_services/x"' in page
    assert b'<textarea' not in page  # no token paste on the login page


def test_login_page_points_at_automated_login(portal, monkeypatch):
    monkeypatch.setitem(portal.config, 'LOGIN_URL', '/auth/fabric/start')
    page = portal.test_client().get('/login?next=/my_services').data
    assert b'href="/auth/fabric/start?next=/my_services"' in page


def test_service_page_asks_to_log_in(portal, service):
    page = portal.test_client().get('/available_services/echo-svc').data
    assert b'href="/login?next=/available_services/echo-svc"' in page
    assert b'<textarea' not in page
    assert portal.test_client().post('/available_services/echo-svc/auth',
                                     json={'id_token': make_token(CONSUMER)}).status_code in (404, 405)


def test_token_test_login_keeps_next(portal):
    page = portal.test_client().get('/token_test?next=/available_services/echo-svc').data
    assert b'"/available_services/echo-svc"' in page



# ── Authors can use their own endpoint paths (e.g. an existing API) ──

@pytest.mark.parametrize('method', ['GET', 'POST'])
def test_custom_endpoint_behind_gateway(portal, fabric_uis, tmp_path, method):
    name, port = f'custom-path-{method.lower()}', _free_port(socket.AF_INET6, '::1')
    template = _register(portal, name, method, port, endpoint='/v1/2d-scan')
    assert "@app.route('/v1/2d-scan'" in template
    script = tmp_path / 'app.py'
    script.write_text(template)
    proc = _start_node_app(script, port, FABRIC_UIS_URL=fabric_uis)
    try:
        resp = _call_as_consumer(portal, name, method)
        assert resp.status_code == 200, resp.data
        assert resp.json['caller']['name'] == 'TestConsumer'
    finally:
        proc.terminate()


@pytest.mark.parametrize('path,expected', [
    ('/api/service_config', 'api_service_config'),
    ('/v1/2d-scan', 'v1_2d_scan'),
    ('/2d/scan', 'endpoint_2d_scan'),
    ('/class', 'endpoint_class'),
    ('/x.json', 'x_json'),
])
def test_generated_function_names_are_valid(path, expected):
    from template_generator import _func_name, generate_flask_template
    assert _func_name(path) == expected
    widget = {'id': 'w1', 'type': 'form', 'label': 'x', 'endpoint': path, 'method': 'GET'}
    compile(generate_flask_template([widget], 8080), 'app.py', 'exec')


# ── Sign in with FABRIC: login cookie -> service projects -> a service token per project ──

from urllib.parse import parse_qs, urlparse  # noqa: E402

from fabric_uis_standin import (COOKIE_NAME, ISSUED, RESEARCH_PROJECT,  # noqa: E402
                                SECOND_SERVICE_PROJECT, login_cookie)

RESEARCHER = '44444444-4444-4444-4444-444444444444'
TWO_PROJECTS = '55555555-5555-5555-5555-555555555555'


def _claims(token):
    import base64, json
    payload = token.split('.')[1]
    return json.loads(base64.urlsafe_b64decode(payload + '=' * (-len(payload) % 4)))


def fabric_sign_in(client, user_uuid, next_url=None):
    """What the browser does after vouch-proxy has set the FABRIC login cookie."""
    client.post('/auth/logout')
    client.set_cookie(COOKIE_NAME, login_cookie(user_uuid))
    return client.get('/auth/fabric/callback' + (f'?next={next_url}' if next_url else ''))


def test_sign_in_starts_at_vouch(portal):
    """Without the login cookie, the browser is sent to vouch-proxy, to come back to the callback."""
    resp = portal.test_client().get('/auth/fabric/start?next=/available_services/echo-svc')
    assert resp.status_code == 302
    target = urlparse(resp.headers['Location'])
    assert target.path == '/vouch/login'
    callback = urlparse(parse_qs(target.query)['url'][0])
    assert callback.scheme and callback.netloc  # vouch needs a full address
    assert callback.path == '/auth/fabric/callback'
    assert parse_qs(callback.query)['next'] == ['/available_services/echo-svc']


def test_sign_in_does_not_return_to_other_sites(portal):
    resp = portal.test_client().get('/auth/fabric/start?next=https://evil.example/')
    callback = urlparse(parse_qs(urlparse(resp.headers['Location']).query)['url'][0])
    assert callback.query == ''
    client = portal.test_client()
    resp = fabric_sign_in(client, CONSUMER, next_url='//evil.example/')
    assert resp.headers['Location'] == '/available_services'


def test_sign_in_with_fabric_cookie(portal, fabric_uis):
    client = portal.test_client()
    resp = fabric_sign_in(client, AUTHOR)
    assert resp.status_code == 302 and resp.headers['Location'] == '/register_service'
    with client.session_transaction() as s:
        user = s['portal_user']
    assert user['uuid'] == AUTHOR and user['signed_in_with'] == 'fabric'
    assert user['can_register'] is True
    assert set(user['project_ids']) == {PORTAL_PROJECT, CONSUMER_PROJECT}
    # Only the service project counts for using services; the portal project is not one
    assert user['service_projects'] == [{'uuid': CONSUMER_PROJECT, 'name': 'Service - Echo', 'owner': True}]

    # Already signed in with FABRIC elsewhere: the Sign in button logs straight in
    client.post('/auth/logout')
    client.set_cookie(COOKIE_NAME, login_cookie(CONSUMER))
    resp = client.get('/auth/fabric/start?next=/available_services/echo-svc')
    assert resp.headers['Location'] == '/available_services/echo-svc'


@pytest.mark.parametrize('names', [
    ['{n}_1of3', '{n}_2of3', '{n}_3of3'],   # how vouch-proxy names the pieces
    ['{n}', '{n}_1', '{n}_2'],
])
def test_sign_in_joins_a_cookie_split_in_pieces(portal, fabric_uis, service, names):
    """A large login cookie arrives in pieces. FABRIC only reads one whole cookie
    (so does the stand-in), so the portal joins the pieces before passing it on."""
    client = portal.test_client()
    value = login_cookie(CONSUMER)
    for name, piece in zip(names, (value[:40], value[40:80], value[80:])):
        client.set_cookie(name.format(n=COOKIE_NAME), piece)
    assert client.get('/auth/fabric/callback').headers['Location'] == '/available_services'
    # ... also when asking the Credential Manager for the service token
    client.get('/available_services/echo-svc')
    resp = client.get('/available_services/echo-svc/proxy/w1')
    assert resp.status_code == 200, resp.data
    assert _claims(resp.json['authorization'][len('Bearer '):])['uuid'] == CONSUMER


@pytest.mark.parametrize('name', ['{n}_1of999999999999', '{n}_1of99', '{n}_5of17', '{n}_1of0'])
def test_made_up_piece_counts_are_ignored(portal, name):
    """Cookie names come from the browser; a huge piece count must not make the portal work."""
    from fabric_auth import vouch_cookies
    with portal.test_request_context():
        start = time.time()
        assert vouch_cookies({name.format(n=COOKIE_NAME): 'x'}) == {}
        # Pieces that disagree on the count are not joined either
        assert vouch_cookies({f'{COOKIE_NAME}_1of2': 'a', f'{COOKIE_NAME}_2of3': 'b'}) == {}
        assert time.time() - start < 0.5


def test_sign_in_refuses_a_cookie_with_a_piece_missing(portal, fabric_uis):
    client = portal.test_client()
    value = login_cookie(CONSUMER)
    client.set_cookie(f'{COOKIE_NAME}_1of3', value[:40])
    client.set_cookie(f'{COOKIE_NAME}_3of3', value[80:])
    assert client.get('/auth/fabric/callback').headers['Location'].startswith('/login?error=')


@pytest.mark.parametrize('cookie', [None, 'garbage', 'expired'])
def test_sign_in_fails_without_a_valid_cookie(portal, fabric_uis, cookie):
    client = portal.test_client()
    if cookie:
        client.set_cookie(COOKIE_NAME, login_cookie(CONSUMER, lifetime=-10) if cookie == 'expired' else cookie)
    resp = client.get('/auth/fabric/callback')
    assert resp.status_code == 302 and resp.headers['Location'].startswith('/login?error=')
    with client.session_transaction() as s:
        assert 'portal_user' not in s
    # A cookie FABRIC refuses does not log in from the start page either: go to vouch
    assert urlparse(client.get('/auth/fabric/start').headers['Location']).path == '/vouch/login'


def test_user_sees_all_their_service_projects(portal, fabric_uis):
    """The stand-in returns projects 5 at a time; inactive and non-service ones are left out."""
    client = portal.test_client()
    fabric_sign_in(client, TWO_PROJECTS)
    page = client.get('/available_services').data
    assert b'Service - Echo' in page and b'Service - Second' in page
    fabric_sign_in(client, RESEARCHER)
    page = client.get('/available_services').data
    assert b'not a member of any FABRIC service project yet' in page
    assert b'Service - Retired' not in page and b'Some Research' not in page


def _set_service_project(portal, name, project):
    """Put a service under a project its author is not a member of (the form only
    offers the author's own service projects), as if it had been registered by a member."""
    import json as _json
    from models import Service, db
    with portal.app_context():
        svc = Service.query.filter_by(name=name).first()
        svc.access_project_id = project
        svc.access_project_ids_json = _json.dumps([project])
        db.session.commit()


def test_service_list_greys_out_services_the_user_cannot_use(portal, service):
    client = portal.test_client()
    # A service open to a research project, and one open to another service project
    login(client, 'TestAuthor')
    for name, project in (('research-only', RESEARCH_PROJECT), ('second-only', SECOND_SERVICE_PROJECT)):
        resp = client.post('/api/register_service', json={
            'service_name': name, 'access_project_ids': [CONSUMER_PROJECT],
            'nodes': [{'name': 'n', 'ipv6': '::1', 'port': 8080}],
            'widgets': [{'id': 'w1', 'type': 'button', 'label': 'x', 'endpoint': '/api/x', 'method': 'GET'}]})
        assert resp.status_code == 201, resp.json
        _set_service_project(portal, name, project)

    import re

    def locked(page, name):
        """True if the service's card is greyed out, False if usable. It must be listed."""
        card = re.search(rb'href="/available_services/' + name.encode() + rb'".*?card-footer.*?</div>\s*</div>',
                         page, re.S)
        assert card, f'{name} is not listed'
        return b'service-card-locked' in card.group(0)

    # Every service is listed for everyone; the author cannot use research-only either
    page = client.get('/available_services').data
    assert not locked(page, 'echo-svc')
    assert locked(page, 'research-only') and locked(page, 'second-only')
    assert b'Request access' in page

    fabric_sign_in(client, CONSUMER)
    page = client.get('/available_services').data
    assert not locked(page, 'echo-svc')
    assert locked(page, 'research-only') and locked(page, 'second-only')

    fabric_sign_in(client, TWO_PROJECTS)
    page = client.get('/available_services').data
    assert not locked(page, 'echo-svc') and not locked(page, 'second-only')
    assert locked(page, 'research-only')

    # A research project gives no access, even when the service lists it
    fabric_sign_in(client, RESEARCHER)
    page = client.get('/available_services').data
    assert locked(page, 'echo-svc') and locked(page, 'research-only')
    assert client.get('/available_services/research-only/proxy/w1').status_code == 401
    # The service's page says how to get access: the allowed project on the FABRIC portal
    page = client.get('/available_services/echo-svc').data
    assert b'Request access' in page and b'Echo GET' not in page
    assert f'href="https://portal.fabric-testbed.net/projects/{CONSUMER_PROJECT}"'.encode() in page

    # Not logged in: listed, greyed out, asked to log in rather than to request access
    anonymous = portal.test_client().get('/available_services').data
    assert locked(anonymous, 'echo-svc')
    assert b'Log in to use' in anonymous and b'Request access' not in anonymous


def test_approved_website_links_are_listed_for_everyone(portal):
    client = portal.test_client()
    with as_admin(portal, client, 'TestAuthor'):
        client.post('/api/register_service', json={
            'service_name': 'link-for-all', 'auth_mode': 'link', 'access_project_ids': [CONSUMER_PROJECT], 'link_url': 'https://example.org'})
        # An admin's own link waits for approval too
        assert b'link-for-all' not in portal.test_client().get('/available_services').data
        approve_link(client, 'link-for-all', 'https://example.org')
    assert b'href="/available_services/link-for-all"' in portal.test_client().get('/available_services').data


def test_node_gets_a_service_token_for_the_service_project(portal, service):
    """Signed in with FABRIC: the Credential Manager issues the token, once per project."""
    client = portal.test_client()
    fabric_sign_in(client, TWO_PROJECTS)
    del ISSUED[:]
    assert b'Echo GET' in client.get('/available_services/echo-svc').data

    resp = client.get('/available_services/echo-svc/proxy/w1?host=example.org')
    assert resp.status_code == 200, resp.data
    assert resp.json['user_uuid'] == TWO_PROJECTS and resp.json['cookie'] is None
    token = resp.json['authorization'][len('Bearer '):]
    claims = _claims(token)
    assert claims['uuid'] == TWO_PROJECTS and claims['scope'] == 'service'
    # echo-svc allows CONSUMER_PROJECT, not the user's first service project
    assert claims['projects'] == [{'uuid': CONSUMER_PROJECT}]
    assert ISSUED == [(TWO_PROJECTS, CONSUMER_PROJECT, 'FABRIC Service Marketplace: echo-svc')]

    # The token is kept for the session: not issued again
    again = client.post('/available_services/echo-svc/proxy/w2', json={'title': 'hi'})
    assert again.json['authorization'] == f'Bearer {token}' and len(ISSUED) == 1

    # A service open to the user's other project gets a token for that project.
    # (Its node is not running, so the call fails after the token was issued.)
    login_as_author = portal.test_client()
    login(login_as_author, 'TestAuthor')
    login_as_author.post('/api/register_service', json={
        'service_name': 'second-project-svc', 'access_project_ids': [CONSUMER_PROJECT],
        'nodes': [{'name': 'n', 'ipv6': '::1', 'port': _free_port(socket.AF_INET6, '::1')}],
        'widgets': [{'id': 'w1', 'type': 'button', 'label': 'x', 'endpoint': '/api/x', 'method': 'GET'}]})
    _set_service_project(portal, 'second-project-svc', SECOND_SERVICE_PROJECT)
    client.get('/available_services/second-project-svc')
    client.get('/available_services/second-project-svc/proxy/w1')
    assert ISSUED[1:] == [(TWO_PROJECTS, SECOND_SERVICE_PROJECT,
                           'FABRIC Service Marketplace: second-project-svc')]

    # Logging out forgets the tokens
    from models import ServiceToken, User
    with portal.app_context():
        user_id = User.query.filter_by(fabric_uuid=TWO_PROJECTS).first().id
        assert ServiceToken.query.filter_by(user_id=user_id).count() == 2
    client.post('/auth/logout')
    with portal.app_context():
        assert ServiceToken.query.filter_by(user_id=user_id).count() == 0


def test_no_token_for_another_user(portal, service):
    """The login cookie changed to another user after signing in: nothing is passed on."""
    client = portal.test_client()
    fabric_sign_in(client, CONSUMER)
    client.get('/available_services/echo-svc')
    client.set_cookie(COOKIE_NAME, login_cookie(TWO_PROJECTS))
    resp = client.get('/available_services/echo-svc/proxy/w1')
    assert resp.status_code == 401 and 'sign-in changed' in resp.json['error']


def test_widget_needs_the_login_cookie_to_get_a_token(portal, service):
    client = portal.test_client()
    fabric_sign_in(client, CONSUMER)
    client.get('/available_services/echo-svc')
    client.delete_cookie(COOKIE_NAME)
    resp = client.get('/available_services/echo-svc/proxy/w1')
    assert resp.status_code == 401 and 'sign in again' in resp.json['error']


def test_logout_also_ends_the_fabric_sign_in(portal, fabric_uis):
    client = portal.test_client()
    fabric_sign_in(client, CONSUMER)
    target = urlparse(client.post('/auth/logout').json['redirect'])
    assert target.path == '/vouch/logout' and parse_qs(target.query)['url'][0].endswith('/')
    # Logged in with a pasted token: there is no FABRIC sign-in to end
    login(client, 'TestConsumer')
    assert 'redirect' not in client.post('/auth/logout').json


def test_token_test_page_shows_service_projects(portal, fabric_uis):
    info = portal.test_client().post('/api/token_test', json={'id_token': make_token(AUTHOR)}).json
    assert info['service_projects'] == [{'uuid': CONSUMER_PROJECT, 'name': 'Service - Echo', 'owner': True}]


def test_old_session_without_service_projects_is_denied(portal, service):
    """A session from before this version has no service projects: log in again, no error page."""
    client = portal.test_client()
    login(client, 'TestConsumer')
    with client.session_transaction() as s:
        user = dict(s['portal_user'])
        user.pop('service_projects'); user.pop('signed_in_with')
        s['portal_user'] = user
        s.pop('auth_echo-svc', None)
    assert client.get('/available_services').status_code == 200
    assert b'Echo GET' not in client.get('/available_services/echo-svc').data


def test_a_service_has_one_project_which_must_be_the_authors_own(portal, service):
    client = portal.test_client()
    login(client, 'TestAuthor')

    def register(name, projects):
        return client.post('/api/register_service', json={
            'service_name': name, 'access_project_ids': projects,
            'nodes': [{'name': 'n', 'ipv6': '::1', 'port': 8080}],
            'widgets': [{'id': 'w1', 'type': 'button', 'label': 'x', 'endpoint': '/api/x', 'method': 'GET'}]})

    # Not a member of that service project
    assert register('not-mine', [SECOND_SERVICE_PROJECT]).status_code == 400
    # A project of theirs that is not a service project
    assert register('not-service', [PORTAL_PROJECT]).status_code == 400
    # More than one, or none
    assert register('two-projects', [CONSUMER_PROJECT, SECOND_SERVICE_PROJECT]).status_code == 400
    assert register('no-project', []).status_code == 400
    assert register('one-own-project', [CONSUMER_PROJECT]).status_code == 201

    # The form offers the service projects the author owns, by name
    page = client.get('/register_service').get_data(as_text=True)
    assert 'id="svc-project"' in page and 'Service - Echo' in page

    # A member who is not an owner of the project cannot register under it,
    # and the form does not offer it
    member = portal.test_client()
    login(member, 'TestMemberAuthor')
    resp = member.post('/api/register_service', json={
        'service_name': 'member-not-owner', 'access_project_ids': [CONSUMER_PROJECT],
        'nodes': [{'name': 'n', 'ipv6': '::1', 'port': 8080}],
        'widgets': [{'id': 'w1', 'type': 'button', 'label': 'x', 'endpoint': '/api/x', 'method': 'GET'}]})
    assert resp.status_code == 400 and 'own' in resp.json['error']
    assert 'Service - Echo' not in member.get('/register_service').get_data(as_text=True)


def test_editing_an_older_service_keeps_its_other_projects(portal, service):
    import json as _json
    from models import Service, db
    client = portal.test_client()
    login(client, 'TestAuthor')
    body = {'service_name': 'older-svc', 'access_project_ids': [CONSUMER_PROJECT], 'description': 'a',
            'nodes': [{'name': 'n', 'ipv6': '::1', 'port': 8080}],
            'widgets': [{'id': 'w1', 'type': 'button', 'label': 'x', 'endpoint': '/api/x', 'method': 'GET'}]}
    assert client.post('/api/register_service', json=body).status_code == 201
    # As registered before the one-project rule: two projects
    with portal.app_context():
        svc = Service.query.filter_by(name='older-svc').first()
        svc.access_project_ids_json = _json.dumps([CONSUMER_PROJECT, SECOND_SERVICE_PROJECT])
        db.session.commit()

    # Editing the description, project left as it is: both projects stay
    resp = client.post('/api/update_service', json=dict(body, description='b'))
    assert resp.status_code == 200, resp.json
    with portal.app_context():
        svc = Service.query.filter_by(name='older-svc').first()
        assert svc.description == 'b'
        assert _json.loads(svc.access_project_ids_json) == [CONSUMER_PROJECT, SECOND_SERVICE_PROJECT]


def test_old_session_reads_projects_again_once_only(portal, service):
    """A session from before ownership was recorded is sent to read the projects
    again, once: if that fails, the next visit shows the form, not another redirect."""
    client = portal.test_client()
    login(client, 'TestAuthor')
    with client.session_transaction() as sess:
        user = sess['portal_user']
        user['signed_in_with'] = 'fabric'
        for p in user['service_projects']:
            del p['owner']
        sess['portal_user'] = user

    first = client.get('/register_service')
    assert first.status_code == 302 and '/auth/fabric/start' in first.headers['Location']
    # The refresh did not happen (say FABRIC was unreachable): no second redirect
    second = client.get('/register_service')
    assert second.status_code == 200
    assert 'Service - Echo' not in second.get_data(as_text=True)


def test_service_has_a_friendly_name_and_an_address(portal, service):
    client = portal.test_client()
    login(client, 'TestAuthor')
    body = {'service_name': 'friendly-svc', 'title': 'My Friendly Service',
            'access_project_ids': [CONSUMER_PROJECT],
            'nodes': [{'name': 'n', 'ipv6': '::1', 'port': 8080}],
            'widgets': [{'id': 'w1', 'type': 'button', 'label': 'x', 'endpoint': '/api/x', 'method': 'GET'}]}
    assert client.post('/api/register_service', json=body).status_code == 201

    # People see the name; the address keeps the short form
    listing = client.get('/available_services').get_data(as_text=True)
    assert 'My Friendly Service' in listing and '/available_services/friendly-svc' in listing
    assert 'My Friendly Service' in client.get('/available_services/friendly-svc').get_data(as_text=True)
    assert 'My Friendly Service' in client.get('/edit_service/friendly-svc').get_data(as_text=True)

    # A name typed where the address goes is refused, and the answer says which field
    resp = client.post('/api/register_service', json=dict(body, service_name='My Friendly Service 2'))
    assert resp.status_code == 400 and resp.json['field'] == 'service_name'
    # Too long a name
    resp = client.post('/api/register_service', json=dict(body, service_name='long-title', title='x' * 81))
    assert resp.status_code == 400

    # Without a name (older clients), the short name is shown
    assert client.post('/api/register_service', json={
        k: v for k, v in dict(body, service_name='no-title-svc').items() if k != 'title'}).status_code == 201
    assert 'no-title-svc' in client.get('/available_services').get_data(as_text=True)


def test_nodes_may_be_on_fabnetv6_or_fabnetv4(portal, service):
    client = portal.test_client()
    login(client, 'TestAuthor')
    before = portal.config['SERVICE_NODE_NETWORK']
    portal.config['SERVICE_NODE_NETWORK'] = '2602:fcfb::/40, 10.128.0.0/10'

    def register(name, address):
        return client.post('/api/register_service', json={
            'service_name': name, 'access_project_ids': [CONSUMER_PROJECT],
            'nodes': [{'name': 'n', 'ipv6': address, 'port': 8080}],
            'widgets': [{'id': 'w1', 'type': 'button', 'label': 'x', 'endpoint': '/api/x', 'method': 'GET'}]})
    try:
        assert register('v4-node', '10.141.1.2').status_code == 201
        assert register('v6-node', '2602:fcfb:9:18::2').status_code == 201
        # Outside both networks, and not an address at all
        for name, address in (('public-v4', '8.8.8.8'), ('private-v4', '192.168.1.5'),
                              ('local-v6', '::1'), ('not-ip', 'my-node.example.org')):
            resp = register(name, address)
            assert resp.status_code == 400, (address, resp.json)
    finally:
        portal.config['SERVICE_NODE_NETWORK'] = before


def test_approval_does_not_carry_over_to_an_address_changed_at_the_same_time(portal, service):
    """The owner's edit read the service while it was pending; the admin approves the
    old address before the edit is saved. The saved service must be pending again."""
    from models import Service, db
    owner = portal.test_client()
    login(owner, 'TestAuthor')
    link = {'service_name': 'raced-link', 'auth_mode': 'link',
            'link_url': 'https://example.org/first', 'access_project_ids': [CONSUMER_PROJECT]}
    assert owner.post('/api/register_service', json=link).status_code == 201

    with portal.app_context():
        # The owner's edit, up to the point of saving
        svc = Service.query.filter_by(name='raced-link').first()
        assert svc.status == 'pending'
        svc.link_url = 'https://example.org/second'
        svc.status = 'pending'
        from sqlalchemy.orm.attributes import flag_modified
        flag_modified(svc, 'status')
        # Meanwhile the admin approves the first address (another connection)
        with db.engine.begin() as conn:
            conn.execute(db.text("UPDATE services SET status = 'active' "
                                 "WHERE name = 'raced-link' AND link_url = 'https://example.org/first'"))
        db.session.commit()
        db.session.expire_all()
        svc = Service.query.filter_by(name='raced-link').first()
        assert (svc.link_url, svc.status) == ('https://example.org/second', 'pending')


def test_request_access_names_the_project(portal, service):
    owner = portal.test_client()
    login(owner, 'TestAuthor')
    assert owner.post('/api/register_service', json={
        'service_name': 'named-project-svc', 'access_project_ids': [CONSUMER_PROJECT],
        'nodes': [{'name': 'n', 'ipv6': '::1', 'port': 8080}],
        'widgets': [{'id': 'w1', 'type': 'button', 'label': 'x', 'endpoint': '/api/x', 'method': 'GET'}],
    }).status_code == 201
    outsider = portal.test_client()
    login(outsider, 'TestOutsider')
    page = outsider.get('/available_services/named-project-svc').get_data(as_text=True)
    assert 'Request to join Service - Echo' in page
    assert f'/projects/{CONSUMER_PROJECT}' in page
