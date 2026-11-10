from datetime import datetime

from flask_sqlalchemy import SQLAlchemy

db = SQLAlchemy()

# How a service decides who may call it:
#   fabric_token  app on the author's nodes; the portal passes the user's FABRIC
#                 token (Authorization: Bearer) and the app checks it with FABRIC
#   external      a web service at its own URL with its own login; portal only links to it
SERVICE_AUTH_MODES = ('fabric_token', 'external')


class User(db.Model):
    __tablename__ = 'users'

    id = db.Column(db.Integer, primary_key=True, autoincrement=True)
    fabric_uuid = db.Column(db.String, unique=True, nullable=False)
    username = db.Column(db.String, nullable=False)
    email = db.Column(db.String)
    role = db.Column(db.String, nullable=False, default='user')  # 'admin' | 'user'
    is_active = db.Column(db.Boolean, nullable=False, default=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    last_login_at = db.Column(db.DateTime)

    services = db.relationship('Service', backref='owner', lazy=True,
                               cascade='all, delete-orphan')


class Service(db.Model):
    __tablename__ = 'services'

    id = db.Column(db.Integer, primary_key=True, autoincrement=True)
    name = db.Column(db.String, unique=True, nullable=False)
    description = db.Column(db.String)
    owner_id = db.Column(db.Integer, db.ForeignKey('users.id', ondelete='CASCADE'),
                         nullable=False)
    access_project_id = db.Column(db.String, nullable=True)   # legacy single project
    access_project_ids_json = db.Column(db.Text, nullable=True)  # JSON array of project UUIDs
    node_ipv6 = db.Column(db.String, nullable=True)       # legacy single-node
    node_port = db.Column(db.Integer, nullable=True)       # legacy single-node
    nodes_json = db.Column(db.Text, nullable=True)         # JSON array of {name, ipv6, port}
    auth_mode = db.Column(db.String, nullable=False, default='fabric_token')  # see SERVICE_AUTH_MODES
    external_url = db.Column(db.String, nullable=True)       # external services only
    icon_color = db.Column(db.String, default='#3B82F6')
    canvas_json = db.Column(db.Text, nullable=False)  # JSON array of widgets
    status = db.Column(db.String, nullable=False, default='active')
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow)


class UsageLog(db.Model):
    __tablename__ = 'usage_log'

    id = db.Column(db.Integer, primary_key=True, autoincrement=True)
    service_id = db.Column(db.Integer, db.ForeignKey('services.id', ondelete='CASCADE'),
                           nullable=False)
    user_id = db.Column(db.Integer, db.ForeignKey('users.id'))
    consumer_name = db.Column(db.String)
    widget_id = db.Column(db.String, nullable=False)
    endpoint = db.Column(db.String, nullable=False)
    method = db.Column(db.String, nullable=False)
    request_params = db.Column(db.Text)  # JSON
    response_status = db.Column(db.Integer)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    service = db.relationship(
        'Service',
        backref=db.backref('usage_logs', cascade='all, delete-orphan')
    )
    user = db.relationship('User', backref='usage_logs')


class AuthLog(db.Model):
    __tablename__ = 'auth_log'

    id = db.Column(db.Integer, primary_key=True, autoincrement=True)
    user_id = db.Column(db.Integer, db.ForeignKey('users.id'))
    fabric_uuid = db.Column(db.String)
    username = db.Column(db.String)
    auth_type = db.Column(db.String, nullable=False)  # 'portal' | 'service'
    service_name = db.Column(db.String)
    success = db.Column(db.Boolean, nullable=False)
    error_message = db.Column(db.String)
    ip_address = db.Column(db.String)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    user = db.relationship('User', backref='auth_logs')


class UserToken(db.Model):
    """
    A logged-in user's FABRIC token, kept server-side (JWTs are too large for the
    session cookie). Only used to call services on the author's nodes on the
    user's behalf. The session holds the random `ref`; rows are deleted on logout
    and expire with the session.
    """
    __tablename__ = 'user_tokens'

    id = db.Column(db.Integer, primary_key=True, autoincrement=True)
    ref = db.Column(db.String, unique=True, nullable=False)
    user_id = db.Column(db.Integer, db.ForeignKey('users.id', ondelete='CASCADE'))
    token = db.Column(db.Text, nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
