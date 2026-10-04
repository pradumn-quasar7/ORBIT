#!/usr/bin/env bash
# Start (or restart) the ORBIT demo server on port 8765.
# Stops any previous server first — live pages keep streaming connections open, so an
# old server is given 2 s to finish them (--timeout-graceful-shutdown) and is then
# force-stopped if it is still there. Usage: scripts/run_demo.sh [--reset]
set -euo pipefail
cd "$(dirname "$0")/.."
PORT=8765
PATTERN="uvicorn backend.app.main:app --port $PORT"

if pgrep -f "$PATTERN" > /dev/null; then
  echo "Stopping the running ORBIT server…"
  pkill -f "$PATTERN" || true
  for _ in 1 2 3 4 5 6; do pgrep -f "$PATTERN" > /dev/null || break; sleep 0.5; done
  pkill -9 -f "$PATTERN" 2> /dev/null || true
fi

if [ "${1:-}" = "--reset" ]; then
  .venv/bin/python scripts/seed_demo.py --reset > /dev/null
  echo "Demo world re-seeded."
fi

mkdir -p .run
ORBIT_DATABASE_URL=sqlite:///./orbit_demo.db nohup .venv/bin/uvicorn backend.app.main:app --port "$PORT" \
  --timeout-graceful-shutdown 2 > .run/server.log 2>&1 &

for _ in $(seq 1 30); do
  if curl -sf "http://localhost:$PORT/assistant" > /dev/null; then
    echo "ORBIT is running:"
    echo "  Assistant  http://localhost:$PORT/ui/assistant.html"
    echo "  Inspector  http://localhost:$PORT/ui/"
    echo "  Camera     http://localhost:$PORT/ui/camera.html"
    echo "  Log        .run/server.log"
    exit 0
  fi
  sleep 0.5
done
echo "ORBIT did not start; see .run/server.log" >&2
exit 1
