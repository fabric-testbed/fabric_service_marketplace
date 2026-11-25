import os
import secrets

from dotenv import load_dotenv

_here = os.path.dirname(__file__)
load_dotenv(os.environ.get('PORTAL_ENV_FILE', os.path.join(_here, 'portal.env')))


def _bool(name, default):
    return os.environ.get(name, str(default)).strip().lower() == 'true'


# Generate and persist a secret key so sessions survive restarts
# (in Docker: PORTAL_SECRET_KEY_FILE points into the mounted data folder)
_secret_key_file = os.environ.get('PORTAL_SECRET_KEY_FILE') or os.path.join(_here, '.secret_key')
if os.environ.get('PORTAL_SECRET_KEY'):
    _secret_key = os.environ['PORTAL_SECRET_KEY']
elif os.path.exists(_secret_key_file):
    with open(_secret_key_file) as f:
        _secret_key = f.read().strip()
else:
    _secret_key = secrets.token_hex(32)
    # Readable by the owner only (root, which runs the portal)
    with os.fdopen(os.open(_secret_key_file, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600), 'w') as f:
        f.write(_secret_key)


def _tls_verify(value):
    """'true' -> system CAs, 'false' -> no verification, anything else -> CA file path."""
    v = value.strip()
    if v.lower() == 'true':
        return True
    if v.lower() == 'false':
        return False
    return v


class Config:
    SECRET_KEY = _secret_key
    SQLALCHEMY_DATABASE_URI = os.environ.get('PORTAL_DATABASE_URI', 'sqlite:///portal.db')
    SQLALCHEMY_TRACK_MODIFICATIONS = False

    # Members of this FABRIC project may register services
    PORTAL_PROJECT_ID = os.environ.get('PORTAL_PROJECT_ID', '')

    # FABRIC user service: turns a FABRIC token into the user and their projects
    FABRIC_UIS_URL = os.environ.get('FABRIC_UIS_URL', 'https://uis.fabric-testbed.net').rstrip('/')
    # Where the "Sign in with FABRIC" button goes: the start of the automated
    # login (e.g. CILogon). Empty = not set up yet; testers use /token_test.
    LOGIN_URL = os.environ.get('PORTAL_LOGIN_URL', '').strip()

    # "Sign in with FABRIC" (routes/auth.py, /auth/fabric/start): the same login as
    # the Credential Manager. vouch-proxy sends the user to CILogon and sets a
    # cookie; the portal passes that cookie to FABRIC to find out who the user is.
    # The cookie's name, as in vouch's config (vouch.cookie.name)
    VOUCH_COOKIE_NAME = os.environ.get('PORTAL_VOUCH_COOKIE_NAME', 'fabric-service').strip()
    # Where the browser goes to log in / out: vouch-proxy behind this portal's Nginx
    VOUCH_LOGIN_URL = os.environ.get('PORTAL_VOUCH_LOGIN_URL', '/vouch/login').strip()
    VOUCH_LOGOUT_URL = os.environ.get('PORTAL_VOUCH_LOGOUT_URL', '/vouch/logout').strip()

    # The FABRIC portal, where users ask to join a project (<url>/projects/<uuid>)
    FABRIC_PORTAL_URL = os.environ.get('FABRIC_PORTAL_URL', 'https://portal.fabric-testbed.net').rstrip('/')
    # Where users can ask a service's owners about access: shown next to
    # "Request to join" on a service they cannot use. Empty = no such button.
    ACCESS_HELP_URL = os.environ.get('PORTAL_ACCESS_HELP_URL', 'https://learn.fabric-testbed.net/forums/').strip()

    # FABRIC Credential Manager: issues the service token passed to a service's
    # nodes, one per service project, for users who signed in with FABRIC.
    CREDMGR_URL = os.environ.get('FABRIC_CREDMGR_URL', 'https://cm.fabric-testbed.net').rstrip('/')
    SERVICE_TOKEN_HOURS = int(os.environ.get('PORTAL_SERVICE_TOKEN_HOURS', '4'))
    # Only projects of this type give access to services (their tokens have scope "service")
    SERVICE_PROJECT_TYPE = os.environ.get('PORTAL_SERVICE_PROJECT_TYPE', 'service').strip()

    # Only FABRIC tokens with this "scope" claim are accepted ('' = any).
    # 'service' tokens can only identify the user; 'resource' tokens can create slices.
    REQUIRED_TOKEN_SCOPE = os.environ.get('PORTAL_REQUIRED_TOKEN_SCOPE', 'service').strip()

    # Service nodes must live inside one of these networks, separated by commas:
    # FABnetv6 and FABNetv4. Keep it the same as the gateway's GATEWAY_ALLOWED_TARGETS.
    SERVICE_NODE_NETWORK = os.environ.get('PORTAL_SERVICE_NODE_NETWORK', '2602:fcfb::/40,10.128.0.0/10')

    # The gateway that relays widget calls onto FABnetv6
    GATEWAY_URL = os.environ.get('GATEWAY_URL', '').rstrip('/')
    GATEWAY_SHARED_SECRET = os.environ.get('GATEWAY_SHARED_SECRET', '')
    GATEWAY_TLS_VERIFY = _tls_verify(os.environ.get('GATEWAY_TLS_VERIFY', 'true'))
    GATEWAY_TIMEOUT = float(os.environ.get('GATEWAY_TIMEOUT', '15'))

    # How often to check that each service's app is running (GET /health on its
    # nodes, through the gateway). 0 = no background check.
    HEALTH_CHECK_SECONDS = int(os.environ.get('PORTAL_HEALTH_CHECK_SECONDS', '60'))

    BEHIND_PROXY = _bool('PORTAL_BEHIND_PROXY', True)
    SESSION_COOKIE_SECURE = _bool('PORTAL_SECURE_COOKIES', True)
    SESSION_COOKIE_HTTPONLY = True
    SESSION_COOKIE_SAMESITE = 'Lax'
    PERMANENT_SESSION_LIFETIME = 14400  # 4 hours in seconds
