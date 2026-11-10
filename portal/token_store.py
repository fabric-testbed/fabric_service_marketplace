"""Server-side storage for the logged-in user's FABRIC token (see models.UserToken)."""
import secrets
from datetime import datetime, timedelta

from flask import session, current_app

from models import UserToken, db


def save(user_id, token):
    """Store the token for this browser session, replacing any previous one."""
    clear()
    cutoff = datetime.utcnow() - timedelta(
        seconds=current_app.config['PERMANENT_SESSION_LIFETIME'])
    UserToken.query.filter(UserToken.created_at < cutoff).delete()
    ref = secrets.token_urlsafe(24)
    db.session.add(UserToken(ref=ref, user_id=user_id, token=token))
    db.session.commit()
    session['token_ref'] = ref


def get():
    ref = session.get('token_ref')
    if not ref:
        return None
    row = UserToken.query.filter_by(ref=ref).first()
    return row.token if row else None


def clear():
    ref = session.pop('token_ref', None)
    if ref:
        UserToken.query.filter_by(ref=ref).delete()
        db.session.commit()
