import os
import secrets

from dotenv import load_dotenv

_here = os.path.dirname(__file__)
load_dotenv(os.environ.get('PORTAL_ENV_FILE', os.path.join(_here, 'portal.env')))


def _bool(name, default):
    return os.environ.get(name, str(default)).strip().lower() == 'true'


# Generate and persist a secret key so sessions survive restarts
_secret_key_file = os.path.join(_here, '.secret_key')
if os.environ.get('PORTAL_SECRET_KEY'):
    _secret_key = os.environ['PORTAL_SECRET_KEY']
elif os.path.exists(_secret_key_file):
    with open(_secret_key_file) as f:
        _secret_key = f.read().strip()
else:
    _secret_key = secrets.token_hex(32)
    with open(_secret_key_file, 'w') as f:
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

    # Only FABRIC tokens with this "scope" claim are accepted ('' = any).
    # 'service' tokens can only identify the user; 'resource' tokens can create slices.
    REQUIRED_TOKEN_SCOPE = os.environ.get('PORTAL_REQUIRED_TOKEN_SCOPE', 'service').strip()

    # Service nodes must live inside this network (FABnetv6)
    SERVICE_NODE_NETWORK = os.environ.get('PORTAL_SERVICE_NODE_NETWORK', '2602:fcfb::/40')

    # The gateway that relays widget calls onto FABnetv6
    GATEWAY_URL = os.environ.get('GATEWAY_URL', '').rstrip('/')
    GATEWAY_SHARED_SECRET = os.environ.get('GATEWAY_SHARED_SECRET', '')
    GATEWAY_TLS_VERIFY = _tls_verify(os.environ.get('GATEWAY_TLS_VERIFY', 'true'))
    GATEWAY_TIMEOUT = float(os.environ.get('GATEWAY_TIMEOUT', '15'))

    BEHIND_PROXY = _bool('PORTAL_BEHIND_PROXY', True)
    SESSION_COOKIE_SECURE = _bool('PORTAL_SECURE_COOKIES', True)
    SESSION_COOKIE_HTTPONLY = True
    SESSION_COOKIE_SAMESITE = 'Lax'
    PERMANENT_SESSION_LIFETIME = 14400  # 4 hours in seconds
