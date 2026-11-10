from functools import wraps

from flask import Blueprint, request, jsonify, session, redirect, render_template, abort

from models import User, Service, UsageLog, AuthLog, db

admin_bp = Blueprint('admin', __name__)


def admin_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        if 'portal_user' not in session:
            return redirect('/login')
        # Re-check role from database instead of trusting session cookie
        user = User.query.get(session['portal_user']['id'])
        if not user or user.role != 'admin':
            abort(403)
        return f(*args, **kwargs)
    return decorated


@admin_bp.route('/admin')
@admin_required
def admin_dashboard():
    users = User.query.order_by(User.created_at.desc()).all()
    services = Service.query.order_by(Service.created_at.desc()).all()
    recent_auth = AuthLog.query.order_by(AuthLog.created_at.desc()).limit(50).all()
    recent_usage = UsageLog.query.order_by(UsageLog.created_at.desc()).limit(50).all()
    return render_template(
        'admin.html',
        users=users,
        services=services,
        recent_auth=recent_auth,
        recent_usage=recent_usage
    )


@admin_bp.route('/admin/users/<int:user_id>/toggle_active', methods=['POST'])
@admin_required
def toggle_user_active(user_id):
    user = User.query.get_or_404(user_id)
    user.is_active = not user.is_active
    db.session.commit()
    return jsonify({"ok": True, "is_active": user.is_active})


@admin_bp.route('/admin/users/<int:user_id>/set_role', methods=['POST'])
@admin_required
def set_user_role(user_id):
    user = User.query.get_or_404(user_id)
    data = request.json or {}
    new_role = data.get('role')
    if new_role not in ('admin', 'user'):
        return jsonify({"error": "Invalid role"}), 400
    user.role = new_role
    db.session.commit()
    return jsonify({"ok": True, "role": user.role})


@admin_bp.route('/admin/usage_log')
@admin_required
def admin_usage_log():
    service_name = request.args.get('service')
    user_id = request.args.get('user_id', type=int)
    page = request.args.get('page', 1, type=int)

    query = UsageLog.query.order_by(UsageLog.created_at.desc())
    if service_name:
        svc = Service.query.filter_by(name=service_name).first()
        if svc:
            query = query.filter_by(service_id=svc.id)
    if user_id:
        query = query.filter_by(user_id=user_id)

    logs = query.paginate(page=page, per_page=100, error_out=False)
    return render_template('admin_usage_log.html', logs=logs,
                           filter_service=service_name, filter_user_id=user_id)


@admin_bp.route('/admin/auth_log')
@admin_required
def admin_auth_log():
    page = request.args.get('page', 1, type=int)
    query = AuthLog.query.order_by(AuthLog.created_at.desc())
    logs = query.paginate(page=page, per_page=100, error_out=False)
    return render_template('admin_auth_log.html', logs=logs)
