#!/usr/bin/env bash
# Deploy the FABRIC Service Gateway on a FABRIC VM (FABNetv4Ext + FABnetv6).
# Prerequisite: gateway.env exists (copy gateway.env.example and fill it in).
set -euo pipefail

APP_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
VENV_DIR="$APP_DIR/venv"

if [ ! -f "$APP_DIR/gateway.env" ]; then
    echo "Missing $APP_DIR/gateway.env — copy gateway.env.example and fill it in." >&2
    exit 1
fi
if grep -n '^[A-Z_]*=changeme' "$APP_DIR/gateway.env"; then
    echo "Replace the changeme values above in $APP_DIR/gateway.env first." >&2
    exit 1
fi
set -a; source "$APP_DIR/gateway.env"; set +a

echo "=== FABRIC Service Gateway — Deploy ==="

echo "[1/5] Installing system packages..."
sudo apt update -qq
sudo apt install -y -qq python3 python3-pip python3-venv nginx openssl

echo "[2/5] Setting up Python virtual environment..."
python3 -m venv "$VENV_DIR"
"$VENV_DIR/bin/pip" install --quiet -r "$APP_DIR/requirements.txt" gunicorn

echo "[3/5] TLS certificate..."
if [ -f "$APP_DIR/gateway.crt" ]; then
    echo "  Existing gateway.crt found — keeping it."
else
    openssl req -x509 -nodes -days 365 -newkey rsa:2048 \
        -keyout "$APP_DIR/gateway.key" -out "$APP_DIR/gateway.crt" \
        -subj "/O=FABRIC/CN=$GATEWAY_PUBLIC_IP" \
        -addext "subjectAltName=IP:$GATEWAY_PUBLIC_IP"
    chmod 600 "$APP_DIR/gateway.key"
    echo "  Generated self-signed cert for $GATEWAY_PUBLIC_IP"
fi

echo "[4/5] Configuring Nginx..."
sed "s|PLACEHOLDER_CRT|$APP_DIR/gateway.crt|;s|PLACEHOLDER_KEY|$APP_DIR/gateway.key|" \
    "$APP_DIR/nginx/gateway.conf" | sudo tee /etc/nginx/sites-available/gateway > /dev/null
"$APP_DIR/nginx/write_allowlist.sh" /etc/nginx/fabric-gateway-allow.conf
sudo ln -sf /etc/nginx/sites-available/gateway /etc/nginx/sites-enabled/gateway
sudo rm -f /etc/nginx/sites-enabled/default
sudo nginx -t
sudo systemctl restart nginx

echo "[5/5] Setting up systemd service..."
sudo tee /etc/systemd/system/fabric-gateway.service > /dev/null << EOF
[Unit]
Description=FABRIC Service Gateway
After=network.target

[Service]
User=root
WorkingDirectory=$APP_DIR
Environment="PATH=$VENV_DIR/bin"
ExecStart=$VENV_DIR/bin/gunicorn -w 4 -b 127.0.0.1:5001 gateway:app
Restart=always

[Install]
WantedBy=multi-user.target
EOF
sudo systemctl daemon-reload
sudo systemctl enable fabric-gateway
sudo systemctl restart fabric-gateway

sudo systemctl is-active --quiet nginx && echo "  Nginx: running" || echo "  Nginx: FAILED"
sudo systemctl is-active --quiet fabric-gateway && echo "  Gateway: running" || echo "  Gateway: FAILED"

echo ""
echo "=== Done. Gateway is listening on https://$GATEWAY_PUBLIC_IP/ ==="
echo "Next: copy $APP_DIR/gateway.crt to the portal host and set"
echo "      GATEWAY_TLS_VERIFY=/path/to/gateway.crt in portal.env"
