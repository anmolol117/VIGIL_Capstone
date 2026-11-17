#!/usr/bin/env bash
# Starts the whole system: dashboard web server + gateway.
# Ctrl-C stops both.
set -euo pipefail
cd "$(dirname "$0")"

PORT=8777
SERVER_PID=""
GW_PID=""

# Kill by explicit PID rather than `kill 0`: a process-group kill would also
# take out the caller when this script is invoked from another script.
cleanup() {
  trap - EXIT INT TERM
  [ -n "$GW_PID" ]     && kill "$GW_PID"     2>/dev/null || true
  [ -n "$SERVER_PID" ] && kill "$SERVER_PID" 2>/dev/null || true
}
trap cleanup EXIT INT TERM

if [ ! -f gateway/.env ]; then
  echo "gateway/.env is missing -- copy gateway/.env.example and fill it in." >&2
  exit 1
fi

# Fail here with something readable rather than deep inside pyserial.
PORT_DEV=$(grep -E '^SERIAL_PORT=' gateway/.env | cut -d= -f2- || true)
if [ -n "$PORT_DEV" ] && [ ! -e "$PORT_DEV" ]; then
  echo "warning: $PORT_DEV not found -- is the receiver plugged in?" >&2
  echo "         available: $(ls /dev/cu.usbserial-* 2>/dev/null | tr '\n' ' ')" >&2
fi

if lsof -i ":$PORT" >/dev/null 2>&1; then
  echo "dashboard: already serving on :$PORT"
else
  python3 -m http.server "$PORT" --directory dashboard >/dev/null 2>&1 &
  SERVER_PID=$!
  echo "dashboard: http://localhost:$PORT"
fi

# NO_BROWSER: set by the combined launcher, whose page shows this dashboard.
[ -z "${NO_BROWSER:-}" ] && command -v open >/dev/null && open "http://localhost:$PORT" 2>/dev/null || true

echo "gateway:   starting (Ctrl-C stops everything)"
echo

# Backgrounded + wait, deliberately: bash defers signal traps while a
# *foreground* child runs, so a plain `python3 ...` would leave the web server
# alive until the gateway itself died. With `wait`, the trap fires immediately.
python3 -u gateway/gateway.py "$@" &
GW_PID=$!
wait "$GW_PID"
