"""
Widget calls from the browser. The portal checks that the consumer may use the
service, picks the target node, and hands the request to the gateway
(gateway_client.relay), which forwards it to the node over FABnetv6.

The node receives Authorization: Bearer <the user's FABRIC token> plus
X-Fabric-User-* identity headers, and checks the token with FABRIC itself.
Website links are not proxied; the portal only links to them.
"""
import json

from flask import Blueprint, request, jsonify, session

import gateway_client
import token_store
from models import Service, UsageLog, db

proxy_bp = Blueprint('proxy', __name__)


@proxy_bp.route(
    '/available_services/<service_name>/proxy/<widget_id>',
    methods=['GET', 'POST']
)
def widget_proxy(service_name, widget_id):
    session_key = f'auth_{service_name}'
    if not session.get(session_key):
        return jsonify({"error": "Not authenticated"}), 401

    service = Service.query.filter_by(name=service_name).first()
    if not service:
        return jsonify({"error": "Service not found"}), 404
    if service.auth_mode == 'link':
        return jsonify({"error": "This service is hosted elsewhere; open its link instead."}), 400

    bearer_token = token_store.get()
    if not bearer_token:
        return jsonify({"error": "This service needs your FABRIC token. "
                                 "Please log out and log in again."}), 401

    widgets = json.loads(service.canvas_json)
    widget = next((w for w in widgets if w['id'] == widget_id), None)
    if not widget:
        return jsonify({"error": "Widget not found"}), 404

    # Build the list of allowed nodes for this service
    if service.nodes_json:
        allowed_nodes = json.loads(service.nodes_json)
    elif service.node_ipv6 and service.node_port:
        allowed_nodes = [{"ipv6": service.node_ipv6, "port": service.node_port}]
    else:
        return jsonify({"error": "No nodes configured for this service"}), 400

    # Default to the first registered node
    target_ipv6 = allowed_nodes[0]['ipv6']
    target_port = int(allowed_nodes[0]['port'])

    # If user selected a specific node, validate it against the allowed list
    node_param = request.args.get('node')
    if node_param and ':' in node_param:
        last_colon = node_param.rfind(':')
        requested_ipv6 = node_param[:last_colon]
        try:
            requested_port = int(node_param[last_colon + 1:])
        except ValueError:
            return jsonify({"error": "Invalid port number"}), 400

        valid = any(
            n['ipv6'] == requested_ipv6 and int(n['port']) == requested_port
            for n in allowed_nodes
        )
        if not valid:
            return jsonify({"error": "Invalid node selection"}), 400

        target_ipv6 = requested_ipv6
        target_port = requested_port

    # Forward query params to the target, but strip out our internal 'node' param
    forward_params = {k: v for k, v in request.args.items() if k != 'node'}
    json_body = request.get_json(silent=True) if request.is_json else None

    # Capture request params for audit
    if json_body:
        request_params = json.dumps(json_body)
    elif forward_params:
        request_params = json.dumps(forward_params)
    else:
        request_params = None

    consumer_info = session[session_key]
    resp = None
    try:
        resp = gateway_client.relay(
            method=widget['method'],
            node_ipv6=target_ipv6,
            node_port=target_port,
            endpoint=widget['endpoint'],
            params=forward_params,
            json_body=json_body,
            bearer_token=bearer_token,
            user=consumer_info,
        )
        response_status = resp.status_code
    except gateway_client.GatewayError:
        response_status = 502

    # Log usage
    log_entry = UsageLog(
        service_id=service.id,
        user_id=consumer_info['id'],
        consumer_name=consumer_info['name'],
        widget_id=widget_id,
        endpoint=widget['endpoint'],
        method=widget['method'],
        request_params=request_params,
        response_status=response_status
    )
    db.session.add(log_entry)
    db.session.commit()

    if resp is None:
        return jsonify({"error": "Service unavailable (gateway unreachable)"}), 502

    # Force JSON content type to prevent XSS from proxied HTML responses
    return (
        resp.content,
        resp.status_code,
        {"Content-Type": "application/json"}
    )
