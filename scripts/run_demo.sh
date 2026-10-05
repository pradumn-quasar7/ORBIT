#!/usr/bin/env bash
# Start (or restart) the ORBIT demo server on port 8765.
# Stops any previous server first — live pages keep streaming connections open, so an
# old server is given 2 s to finish them (--timeout-graceful-shutdown) and is then
# force-stopped if it is still there.
# Usage: scripts/run_demo.sh [--reset] [--lan [--new-code]]
#   --reset  re-seed the demo world
#   --lan    also serve https://<this-computer>:8766 for a headset on the same Wi-Fi
#            (devices must pair with the code printed below; Phase 18)
#   --new-code  with --lan: issue a new pairing code, unpairing every device
set -euo pipefail
cd "$(dirname "$0")/.."
PORT=8765
PATTERN="backend\.app\.serve|uvicorn backend\.app\.main:app"  # current and pre-Phase-18 launchers
RESET=0; LAN=""
for arg in "$@"; do
  case "$arg" in
    --reset) RESET=1 ;;
    --lan) LAN="$LAN --lan" ;;
    --new-code) LAN="$LAN --new-code" ;;
    *) echo "unknown option $arg" >&2; exit 2 ;;
  esac
done

if pgrep -f "$PATTERN" > /dev/null; then
  echo "Stopping the running ORBIT server…"
  pkill -f "$PATTERN" || true
  for _ in 1 2 3 4 5 6; do pgrep -f "$PATTERN" > /dev/null || break; sleep 0.5; done
  pkill -9 -f "$PATTERN" 2> /dev/null || true
fi

if [ "$RESET" = 1 ]; then
  .venv/bin/python scripts/seed_demo.py --reset > /dev/null
  echo "Demo world re-seeded."
fi

mkdir -p .run
ORBIT_DATABASE_URL=sqlite:///./orbit_demo.db nohup .venv/bin/python -m backend.app.serve --port "$PORT" $LAN \
  > .run/server.log 2>&1 &

for _ in $(seq 1 30); do
  if curl -sf "http://localhost:$PORT/assistant" > /dev/null; then
    echo "ORBIT is running:"
    echo "  Assistant  http://localhost:$PORT/ui/assistant.html"
    echo "  Inspector  http://localhost:$PORT/ui/"
    echo "  Camera     http://localhost:$PORT/ui/camera.html"
    echo "  Log        .run/server.log"
    sleep 0.5
    grep -E "headset|Pairing" .run/server.log | sed 's/^/  /' || true
    exit 0
  fi
  sleep 0.5
done
echo "ORBIT did not start; see .run/server.log" >&2
exit 1
