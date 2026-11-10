#!/usr/bin/env bash
# Quick restart after code or gateway.env changes.
set -euo pipefail
APP_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
set -a; source "$APP_DIR/gateway.env"; set +a
"$APP_DIR/venv/bin/pip" install --quiet -r "$APP_DIR/requirements.txt"
# Re-apply GATEWAY_ALLOWED_SOURCES to Nginx in case it changed
"$APP_DIR/nginx/write_allowlist.sh" /etc/nginx/fabric-gateway-allow.conf
sudo nginx -t && sudo systemctl reload nginx
sudo systemctl restart fabric-gateway
sudo systemctl is-active --quiet nginx && echo "Nginx: running" || echo "Nginx: FAILED"
sudo systemctl is-active --quiet fabric-gateway && echo "Gateway: running" || echo "Gateway: FAILED"
