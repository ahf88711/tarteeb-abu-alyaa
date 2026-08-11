#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."
export PATH="$HOME/Library/Python/3.9/bin:$PATH"
# 0.0.0.0 = متصفح الجهاز + جوال على نفس الشبكة
HOST="${HOST:-0.0.0.0}"
PORT="${PORT:-8765}"
echo "ترتيب أبو علياء"
echo "  محلي:  http://127.0.0.1:${PORT}"
IP=$(ipconfig getifaddr en0 2>/dev/null || ipconfig getifaddr en1 2>/dev/null || true)
if [[ -n "${IP:-}" ]]; then
  echo "  جوال:  http://${IP}:${PORT}"
fi
python3 -m uvicorn app.main:app --host "$HOST" --port "$PORT"
