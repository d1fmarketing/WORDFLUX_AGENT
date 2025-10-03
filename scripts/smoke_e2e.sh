#!/bin/bash

BASE="${BASE:-http://wordflux.3-228-174-188.nip.io}"
BOARD_BASE="${BOARD_BASE:-$BASE}"
CHAT_BASE="${CHAT_BASE:-$BASE}"
EVENTS_BASE="${EVENTS_BASE:-$BASE}"
set -euo pipefail

PYTHON_BIN="${PYTHON_BIN:-.venv/bin/python}"
if [ ! -x "$PYTHON_BIN" ]; then
  PYTHON_BIN="$(command -v python3)"
fi

curl -s "$BOARD_BASE/board/state" | jq -e '.columns|keys|sort==["Agendado","Aprovação","Espera","Finalizado","Produção"]' > /dev/null

tmp_event="$(mktemp)"
trap 'rm -f "$tmp_event"' EXIT

(
  "$PYTHON_BIN" - <<'PY' "$EVENTS_BASE/events/stream" "$tmp_event"
import sys
import time
import requests

url = sys.argv[1]
tmp_path = sys.argv[2]
deadline = time.time() + 30

try:
    with requests.get(url, stream=True, timeout=(5, 35)) as resp:
        resp.raise_for_status()
        for raw_line in resp.iter_lines():
            if time.time() > deadline:
                break
            if not raw_line or raw_line.startswith(b':'):
                continue
            line = raw_line.decode('utf-8', 'ignore')
            if 'card.pending' in line or 'card.created' in line:
                with open(tmp_path, 'w', encoding='utf-8') as capture:
                    capture.write(line + '\n')
                print(line)
                sys.exit(0)
except Exception as exc:
    print(f'sse_error: {exc}', file=sys.stderr)

sys.exit(1)
PY
) &
SSE_PID=$!

sleep 1

curl -s -X POST "$CHAT_BASE/chat/" -H 'Content-Type: application/json' \
  -d '{"session_id":"smoke","text":"Crie o card \"E2E Test\" em Espera"}' | jq -e '.message' > /dev/null

wait $SSE_PID
cat "$tmp_event"

curl -s "$BOARD_BASE/board/state" | jq -e '.columns."Espera"[]|select(.title=="E2E Test")'

echo "OK"
