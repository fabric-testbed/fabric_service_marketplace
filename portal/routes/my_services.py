import json
from datetime import datetime

from flask import Blueprint, request, jsonify, session, render_template

from access import author_page
from models import Service, db

my_services_bp = Blueprint('my_services', __name__)


@my_services_bp.route('/my_services')
@author_page
def my_services_page():
    services = Service.query.filter_by(
        owner_id=session['portal_user']['id']
    ).order_by(Service.created_at.desc()).all()
    return render_template('my_services.html', services=services)


@my_services_bp.route('/api/services/<service_name>', methods=['PUT'])
def update_service(service_name):
    if 'portal_user' not in session:
        return jsonify({"error": "Not logged in"}), 401

    service = Service.query.filter_by(name=service_name).first_or_404()

    # Ownership check: owner or admin
    if (service.owner_id != session['portal_user']['id']
            and session['portal_user']['role'] != 'admin'):
        return jsonify({"error": "Not authorized to modify this service."}), 403

    data = request.json or {}
    if 'description' in data:
        service.description = data['description']
    if 'access_project_id' in data:
        service.access_project_id = data['access_project_id']
    if 'node_ipv6' in data:
        service.node_ipv6 = data['node_ipv6']
    if 'node_port' in data:
        service.node_port = int(data['node_port'])
    if 'widgets' in data:
        service.canvas_json = json.dumps(data['widgets'])
    if 'icon_color' in data:
        service.icon_color = data['icon_color']
    service.updated_at = datetime.utcnow()

    db.session.commit()
    return jsonify({"ok": True}), 200


@my_services_bp.route('/api/services/<service_name>', methods=['DELETE'])
def delete_service(service_name):
    if 'portal_user' not in session:
        return jsonify({"error": "Not logged in"}), 401

    service = Service.query.filter_by(name=service_name).first_or_404()

    if (service.owner_id != session['portal_user']['id']
            and session['portal_user']['role'] != 'admin'):
        return jsonify({"error": "Not authorized to delete this service."}), 403

    db.session.delete(service)
    db.session.commit()
    return jsonify({"ok": True}), 200
