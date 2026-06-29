#!/usr/bin/env bash
# deploy.sh — sync server to VPS and restart service
#
# First-time VPS setup (run once as root):
#   apt update && apt install -y python3-pip python3-venv ufw
#   ufw allow 22 && ufw allow 8080 && ufw enable
#   useradd -r -s /bin/false locklauncher
#   mkdir -p /opt/locklauncher && chown locklauncher:locklauncher /opt/locklauncher
#   echo "API_KEY=$(python3 -c 'import secrets; print(secrets.token_hex(24))')" > /opt/locklauncher/.env
#   chmod 600 /opt/locklauncher/.env
#   chown locklauncher:locklauncher /opt/locklauncher/.env
#   cp locklauncher.service /etc/systemd/system/
#   systemctl daemon-reload && systemctl enable --now locklauncher
#
# After first-time setup, just run this script to redeploy.

set -euo pipefail

SSH_USER="root"        # SSH user on the VPS (root or admin account)
VPS_HOST="your.vps.ip" # <-- fill in your VPS IP or hostname
REMOTE_DIR="/opt/locklauncher"

echo "Syncing server code to ${VPS_HOST}..."
rsync -av \
  --exclude="*.pyc" \
  --exclude="__pycache__/" \
  --exclude=".env" \
  --exclude="lock_state.json" \
  --exclude="*.tmp" \
  "$(dirname "$0")/" "${SSH_USER}@${VPS_HOST}:${REMOTE_DIR}/"

echo "Installing dependencies and restarting service..."
ssh "${SSH_USER}@${VPS_HOST}" bash <<'REMOTE'
  set -euo pipefail
  cd /opt/locklauncher
  if [ ! -d venv ]; then
    python3 -m venv venv
  fi
  venv/bin/pip install -q -r requirements.txt
  systemctl restart locklauncher
  systemctl is-active locklauncher
  echo "Deploy complete."
REMOTE
