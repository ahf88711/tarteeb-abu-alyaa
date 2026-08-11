#!/usr/bin/env bash
# Keep ترتيب أبو علياء server running; restart if it dies.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"
PORT="${PORT:-8765}"
# bind all interfaces for phone access; health-check via localhost
HOST="${HOST:-0.0.0.0}"
export PATH="$HOME/Library/Python/3.9/bin:/usr/local/bin:$PATH"

is_up() {
  curl -fsS -m 2 "http://127.0.0.1:${PORT}/api/health" >/dev/null 2>&1
}

start() {
  echo "[$(date '+%H:%M:%S')] starting ترتيب أبو علياء on ${HOST}:${PORT}"
  # free port if stale
  if command -v lsof >/dev/null 2>&1; then
    lsof -ti ":${PORT}" | while read -r pid; do kill "$pid" 2>/dev/null || true; done
    sleep 1
  fi
  nohup python3 -m uvicorn app.main:app --host "$HOST" --port "$PORT" \
    >>/tmp/tarteeb-abu-alyaa.log 2>&1 &
  echo $! >/tmp/tarteeb-abu-alyaa.pid
  sleep 2
}

if [[ "${1:-}" == "once" ]]; then
  is_up || start
  is_up && echo OK || echo FAIL
  exit 0
fi

echo "Watchdog for ترتيب أبو علياء (Ctrl+C to stop)"
while true; do
  if ! is_up; then
    echo "[$(date '+%H:%M:%S')] down — restarting"
    start
  fi
  sleep 8
done
