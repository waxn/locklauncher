#!/usr/bin/env bash
# Quick status check — prints current lock state from the server.
# Usage: ./status.sh [server_url]

SERVER="${1:-http://your.vps.ip:8080}"
curl -sf "${SERVER}/status" | python3 -m json.tool
