"""
End-to-end test of the split architecture on one machine:

    portal (in-process)  ->  gateway (subprocess, 127.0.0.1)
                         ->  service node app (subprocess, [::1])

Users log in with tokens from a stand-in for FABRIC's user service
(fabric_uis_standin.py), which the portal and the node apps check tokens against.

Covers both kinds of service: an app on the author's nodes that checks the
user's FABRIC token (fabric_token), and an external website (external).

Run from the repo root:  python -m pytest tests -v
"""
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
        PORTAL_SERVICE_NODE_NETWORK='::1/128',
        PORTAL_PROJECT_ID=PORTAL_PROJECT,
        GATEWAY_URL=gateway,
        GATEWAY_SHARED_SECRET=SECRET,
        GATEWAY_TLS_VERIFY='false',
        PORTAL_BEHIND_PROXY='false',
        PORTAL_SECURE_COOKIES='false',
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



# ── The example app and the generated app work behind the gateway ──

EXAMPLE_APP = os.path.join(ROOT, 'service_node', 'example_app.py')


def _start_node_app(script, port, **extra_env):
    proc = subprocess.Popen(
        [sys.executable, str(script)],
        env=dict(os.environ, SERVICE_PORT=str(port), **extra_env),
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    _wait_for(f'http://[::1]:{port}/api/service_config')
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
    proc = _start_node_app(script, port, FABRIC_UIS_URL=fabric_uis)
    try:
        resp = _call_as_consumer(portal, name, method)
        assert resp.status_code == 200, resp.data
        assert resp.json['caller']['name'] == 'TestConsumer'
        direct = requests.request(method, f'http://[::1]:{port}/api/service_config', json={})
        assert direct.status_code == 401
    finally:
        proc.terminate()


# ── External web service, link only ──

def test_external_external_service(portal):
    client = portal.test_client()
    login(client, 'TestAuthor')
    resp = client.post('/api/register_service', json={
        'service_name': 'ext-svc', 'auth_mode': 'external',
        'external_url': 'https://example.org/my-service', 'description': 'hosted elsewhere'})
    assert resp.status_code == 201, resp.json
    assert resp.json['flask_template'] is None

    anonymous = portal.test_client()
    page = anonymous.get('/available_services/ext-svc')
    assert page.status_code == 200
    assert b'href="https://example.org/my-service"' in page.data
    assert b'External site' in anonymous.get('/available_services').data

    # Never proxied, even for a logged-in project member
    login(anonymous, 'TestConsumer')
    anonymous.get('/available_services/ext-svc')
    assert anonymous.get('/available_services/ext-svc/proxy/w1').status_code in (400, 401)
    assert b'Nothing to install' in client.get('/register_service/success/ext-svc').data


@pytest.mark.parametrize('url', ['javascript:alert(1)', 'ftp://example.org', 'example.org', ''])
def test_external_rejects_bad_urls(portal, url):
    client = portal.test_client()
    login(client, 'TestAuthor')
    resp = client.post('/api/register_service', json={
        'service_name': 'ext-bad', 'auth_mode': 'external', 'external_url': url})
    assert resp.status_code == 400


def test_switch_mode_on_edit(portal):
    client = portal.test_client()
    login(client, 'TestAuthor')
    _register(portal, 'switcher', 'GET', 8080)
    resp = client.post('/api/update_service', json={
        'service_name': 'switcher', 'auth_mode': 'external', 'external_url': 'https://example.org'})
    assert resp.status_code == 200, resp.json
    assert b'href="https://example.org"' in client.get('/available_services/switcher').data
    assert client.get('/edit_service/switcher').status_code == 200

    # A failed update leaves the service unchanged (the API-key mode no longer exists)
    resp = client.post('/api/update_service', json={'service_name': 'switcher', 'auth_mode': 'api_key'})
    assert resp.status_code == 400
    from models import Service
    with portal.app_context():
        assert Service.query.filter_by(name='switcher').first().auth_mode == 'external'


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
    row = conn.execute("SELECT auth_mode, external_url FROM services WHERE name='old'").fetchone()
    assert row == ('fabric_token', None)


def test_api_key_services_become_token_services(tmp_path):
    import sqlite3
    db_file = tmp_path / 'with_api_key.db'
    conn = sqlite3.connect(db_file)
    conn.execute("""CREATE TABLE services (id INTEGER PRIMARY KEY, name VARCHAR NOT NULL,
        description VARCHAR, owner_id INTEGER NOT NULL, access_project_id VARCHAR,
        access_project_ids_json TEXT, node_ipv6 VARCHAR, node_port INTEGER, nodes_json TEXT,
        api_key VARCHAR, auth_mode VARCHAR NOT NULL, external_url VARCHAR, icon_color VARCHAR,
        canvas_json TEXT NOT NULL, status VARCHAR NOT NULL, created_at DATETIME, updated_at DATETIME)""")
    conn.execute("INSERT INTO services (name, owner_id, auth_mode, canvas_json, status) "
                 "VALUES ('a', 1, 'api_key', '[]', 'active'), ('b', 1, 'external', '[]', 'active')")
    conn.commit()
    conn.close()

    env = dict(os.environ, PORTAL_ENV_FILE='/nonexistent', PORTAL_SECRET_KEY='k',
               PORTAL_DATABASE_URI=f'sqlite:///{db_file}')
    subprocess.run([sys.executable, '-c', 'import app'], cwd=os.path.join(ROOT, 'portal'),
                   env=env, check=True, capture_output=True)

    conn = sqlite3.connect(db_file)
    rows = dict(conn.execute("SELECT name, auth_mode FROM services").fetchall())
    assert rows == {'a': 'fabric_token', 'b': 'external'}


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
