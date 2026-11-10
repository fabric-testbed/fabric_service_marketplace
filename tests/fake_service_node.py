"""
Stand-in for a service author's Flask app. Echoes back what it received so
tests can check exactly what the gateway forwarded.
Usage: python fake_service_node.py <port>
"""
import sys

from flask import Flask, jsonify, request

app = Flask(__name__)


@app.route('/api/echo', methods=['GET', 'POST'])
def echo():
    return jsonify({
        "method": request.method,
        "args": request.args.to_dict(),
        "json": request.get_json(silent=True),
        "user_uuid": request.headers.get('X-Fabric-User-Uuid'),
        "user_name": request.headers.get('X-Fabric-User-Name'),
        "cookie": request.headers.get('Cookie'),
        "authorization": request.headers.get('Authorization'),
        "api_key": request.headers.get('X-Api-Key'),
        "gateway_secret": request.headers.get('X-Gateway-Secret'),
    })


if __name__ == '__main__':
    app.run(host='::1', port=int(sys.argv[1]))
