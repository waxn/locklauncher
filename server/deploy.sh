#!/usr/bin/env bash
# deploy.sh — pull latest code on the VPS and restart the service
#
# First-time VPS setup (run once as root on the server):
#   apt update && apt install -y python3-venv ufw git
#   ufw allow 22 && ufw allow 47291 && ufw enable
#   cd /locklauncher
#   python3 -m venv /locklauncher/venv
#   venv/bin/pip install -r server/requirements.txt
#   echo "API_KEY=$(python3 -c 'import secrets; print(secrets.token_hex(24))')" > /locklauncher/.env
#   chmod 600 /locklauncher/.env
#   cp server/locklauncher.service /etc/systemd/system/
#   systemctl daemon-reload && systemctl enable --now locklauncher
#
# After first-time setup, push your changes, then run this script to redeploy.

set -euo pipefail

SSH_USER="root"         # SSH user on the VPS
VPS_HOST="your.vps.ip"  # <-- fill in your VPS IP or hostname

echo "Deploying to ${VPS_HOST}..."
ssh "${SSH_USER}@${VPS_HOST}" bash <<'REMOTE'
  set -euo pipefail
  cd /locklauncher
  git pull
  venv/bin/pip install -q -r server/requirements.txt
  systemctl restart locklauncher
  systemctl is-active locklauncher
  echo "Deploy complete."
REMOTE
