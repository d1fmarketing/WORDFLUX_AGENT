#!/bin/bash

BASE="${BASE:-http://wordflux.3-228-174-188.nip.io}"
BOARD_BASE="${BOARD_BASE:-$BASE}"
CHAT_BASE="${CHAT_BASE:-$BASE}"
set -euo pipefail

curl -s "$BOARD_BASE/board/state" | jq -e '.columns|keys|length==5' > /dev/null

curl -s -X POST "$CHAT_BASE/chat/" -H 'Content-Type: application/json' \
  -d '{"session_id":"smoke","text":"Quantos cards temos no total?"}' \
  | jq -e '.message | test("Total: [0-9]+")' > /dev/null

echo "OK-summary"
