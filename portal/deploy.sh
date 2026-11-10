#!/usr/bin/env bash
set -euo pipefail

# Deploy the FABRIC Service Portal on Ubuntu/Debian or Rocky Linux/RHEL.
# Prerequisite: portal.env exists (copy portal.env.example and fill it in).
APP_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
VENV_DIR="$APP_DIR/venv"

if [ ! -f "$APP_DIR/portal.env" ]; then
    echo "Missing $APP_DIR/portal.env — copy portal.env.example and fill it in." >&2
    exit 1
fi
if grep -n '^[A-Z_]*=changeme' "$APP_DIR/portal.env"; then
    echo "Replace the changeme values above in $APP_DIR/portal.env first." >&2
    exit 1
fi
set -a; source "$APP_DIR/portal.env"; set +a

# Which Linux family: "debian" (Ubuntu) or "rhel" (Rocky, Alma, RHEL)
. /etc/os-release
if [[ " ${ID:-} ${ID_LIKE:-} " =~ \ (rhel|fedora|centos)\  ]]; then OS_FAMILY=rhel; else OS_FAMILY=debian; fi

echo "=== FABRIC Service Portal — Deploy ($PRETTY_NAME) ==="

# ── 1. Install system packages ──
echo "[1/6] Installing system packages..."
PYTHON=python3
if [ "$OS_FAMILY" = debian ]; then
    sudo apt update -qq
    sudo apt install -y -qq python3 python3-pip python3-venv nginx sqlite3 openssl
else
    sudo dnf install -y -q python3 nginx sqlite openssl
    # Flask 3.1 needs Python 3.9+; Rocky 8's default python3 is 3.6
    if ! python3 -c 'import sys; sys.exit(sys.version_info < (3, 9))'; then
        sudo dnf install -y -q python3.11
        PYTHON=python3.11
    fi
fi

# ── 2. Create venv and install Python deps ──
echo "[2/6] Setting up Python virtual environment ($($PYTHON --version))..."
$PYTHON -m venv "$VENV_DIR"
"$VENV_DIR/bin/pip" install --quiet -r "$APP_DIR/requirements.txt"
"$VENV_DIR/bin/pip" install --quiet gunicorn

# ── 3. Initialize the database ──
echo "[3/6] Initializing database..."
if [ -f "$APP_DIR/instance/portal.db" ]; then
    echo "  Existing database found — keeping it."
else
    echo "  No database found — creating new one."
fi
"$VENV_DIR/bin/python" -c "import sys; sys.path.insert(0,'$APP_DIR'); from app import app; print('DB initialized')"

# ── 4. Configure Nginx ──
echo "[4/6] Configuring Nginx..."
if [ -f "$APP_DIR/portal.crt" ] && [ -f "$APP_DIR/portal.key" ]; then
    echo "  Existing portal.crt found — keeping it."
else
    # Self-signed, for testing. Use a real certificate (e.g. certbot) in production.
    if [[ "$PORTAL_SERVER_NAME" =~ ^[0-9.]+$ ]]; then SAN="IP:$PORTAL_SERVER_NAME"; else SAN="DNS:$PORTAL_SERVER_NAME"; fi
    openssl req -x509 -nodes -days 365 -newkey rsa:2048 \
        -keyout "$APP_DIR/portal.key" -out "$APP_DIR/portal.crt" \
        -subj "/O=FABRIC/CN=$PORTAL_SERVER_NAME" -addext "subjectAltName=$SAN"
    chmod 600 "$APP_DIR/portal.key"
    echo "  Generated self-signed cert for $PORTAL_SERVER_NAME"
fi

if [ "$OS_FAMILY" = debian ]; then
    CRT="$APP_DIR/portal.crt"; KEY="$APP_DIR/portal.key"
    NGINX_CONF=/etc/nginx/sites-available/portal
else
    # SELinux doesn't let Nginx read files under /home, so the certificate is copied
    sudo install -d -m 755 /etc/nginx/ssl
    sudo install -m 644 "$APP_DIR/portal.crt" /etc/nginx/ssl/portal.crt
    sudo install -m 600 "$APP_DIR/portal.key" /etc/nginx/ssl/portal.key
    CRT=/etc/nginx/ssl/portal.crt; KEY=/etc/nginx/ssl/portal.key
    NGINX_CONF=/etc/nginx/conf.d/fabric-portal.conf
    # The stock nginx.conf may declare its own default server on port 80, which
    # clashes with this project's; drop that flag (a backup is kept as nginx.conf.bak)
    if grep -q 'default_server' /etc/nginx/nginx.conf; then
        sudo sed -i.bak 's/ default_server//' /etc/nginx/nginx.conf
    fi
fi
sed "s|PLACEHOLDER_CRT|$CRT|;s|PLACEHOLDER_KEY|$KEY|;s|PLACEHOLDER_SERVER_NAME|$PORTAL_SERVER_NAME|g" \
    "$APP_DIR/nginx/portal.conf" | sudo tee "$NGINX_CONF" > /dev/null
if [ "$OS_FAMILY" = debian ]; then
    sudo ln -sf /etc/nginx/sites-available/portal /etc/nginx/sites-enabled/portal
    sudo rm -f /etc/nginx/sites-enabled/default
fi

if [ "$OS_FAMILY" = rhel ]; then
    if command -v getenforce > /dev/null && [ "$(getenforce)" != Disabled ]; then
        echo "  SELinux: allowing Nginx to reach the app, labelling the venv as programs"
        sudo setsebool -P httpd_can_network_connect 1
        sudo restorecon -R /etc/nginx
        # systemd won't start programs labelled as home-directory files
        sudo chcon -R -t bin_t "$VENV_DIR/bin"
    fi
    if systemctl is-active --quiet firewalld; then
        echo "  firewalld: opening http and https"
        sudo firewall-cmd --quiet --permanent --add-service=http --add-service=https
        sudo firewall-cmd --quiet --reload
    fi
fi
sudo nginx -t
sudo systemctl enable --quiet nginx
sudo systemctl restart nginx

# ── 5. Create and start systemd service for Flask ──
echo "[5/6] Setting up systemd service..."
sudo tee /etc/systemd/system/fabric-portal.service > /dev/null << EOF
[Unit]
Description=FABRIC Service Portal
After=network.target

[Service]
User=root
WorkingDirectory=$APP_DIR
Environment="PATH=$VENV_DIR/bin"
ExecStart=$VENV_DIR/bin/gunicorn -w 4 -b 127.0.0.1:5000 app:app
Restart=always

[Install]
WantedBy=multi-user.target
EOF

sudo systemctl daemon-reload
sudo systemctl enable fabric-portal
sudo systemctl restart fabric-portal

# ── 6. Verify ──
echo "[6/6] Verifying..."
sleep 2
sudo systemctl is-active --quiet nginx && echo "  Nginx: running" || echo "  Nginx: FAILED"
sudo systemctl is-active --quiet fabric-portal && echo "  Flask app: running" || echo "  Flask app: FAILED"
(cd "$APP_DIR" && FLASK_APP=app.py "$VENV_DIR/bin/flask" check-gateway) || \
    echo "  Gateway check failed — see docs/SETUP.md, 'Troubleshooting'"

echo ""
echo "=== Done! Portal is live at https://$PORTAL_SERVER_NAME/ ==="
