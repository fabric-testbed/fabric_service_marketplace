#!/usr/bin/env bash
# Quick restart — use after uploading code changes.
# Preserves database, secret key, and SSL certs. Picks up portal.env changes.
# Only reinstalls Python deps and restarts the Flask app.

set -euo pipefail

APP_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
VENV_DIR="$APP_DIR/venv"

echo "=== Quick restart ==="

# Reinstall deps (in case requirements.txt changed)
echo "[1/2] Installing Python dependencies..."
"$VENV_DIR/bin/pip" install --quiet -r "$APP_DIR/requirements.txt"

# Restart the Flask app
echo "[2/2] Restarting Flask app..."
sudo systemctl restart fabric-portal

sudo systemctl is-active --quiet fabric-portal && echo "  Flask app: running" || echo "  Flask app: FAILED"
echo "=== Done ==="
