"""
Server-side storage for the logged-in user's FABRIC tokens.

UserToken     the token a tester pasted on /token_test
ServiceToken  tokens the Credential Manager issued for a user who signed in
              with FABRIC, one per service project (see credmgr_client.py)

The session cookie only holds a random reference to them.
"""
import secrets
from datetime import datetime, timedelta

from flask import session, current_app

from models import ServiceToken, UserToken, db


def _ref():
    if not session.get('token_ref'):
        session['token_ref'] = secrets.token_urlsafe(24)
    return session['token_ref']


def save(user_id, token):
    """Store the pasted token for this browser session, replacing any previous one."""
    clear()
    cutoff = datetime.utcnow() - timedelta(
        seconds=current_app.config['PERMANENT_SESSION_LIFETIME'])
    UserToken.query.filter(UserToken.created_at < cutoff).delete()
    db.session.add(UserToken(ref=_ref(), user_id=user_id, token=token))
    db.session.commit()


def get():
    ref = session.get('token_ref')
    if not ref:
        return None
    row = UserToken.query.filter_by(ref=ref).first()
    return row.token if row else None


def get_service_token(project_id):
    """This session's unexpired service token for the project, or None."""
    ref = session.get('token_ref')
    if not ref:
        return None
    row = (ServiceToken.query.filter_by(ref=ref, project_id=project_id)
           .filter(ServiceToken.expires_at > datetime.utcnow()).first())
    return row.token if row else None


def save_service_token(user_id, project_id, token, expires_at):
    ref = _ref()
    ServiceToken.query.filter(ServiceToken.expires_at <= datetime.utcnow()).delete()
    ServiceToken.query.filter_by(ref=ref, project_id=project_id).delete()
    db.session.add(ServiceToken(ref=ref, user_id=user_id, project_id=project_id,
                                token=token, expires_at=expires_at))
    db.session.commit()


def clear():
    ref = session.pop('token_ref', None)
    if ref:
        UserToken.query.filter_by(ref=ref).delete()
        ServiceToken.query.filter_by(ref=ref).delete()
        db.session.commit()
