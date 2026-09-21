#!/usr/bin/env bash
# Quick status check — prints current lock state from the server.
#
# Usage:
#   ./status.sh [server_url]            # one lock (the default id)
#   ./status.sh [server_url] <api_key>  # every lock, with age and expiry
#
# The second form is the one to use when a client reports a file stuck as
# locked: it shows each lock's age in minutes and whether the server already
# considers it expired.

SERVER="${1:-http://your.vps.ip:47291}"
API_KEY="${2:-}"

if [ -n "${API_KEY}" ]; then
  curl -sf -H "X-API-Key: ${API_KEY}" "${SERVER}/locks" | python3 -m json.tool
else
  curl -sf "${SERVER}/status" | python3 -m json.tool
fi
