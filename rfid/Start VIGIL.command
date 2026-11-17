#!/bin/bash
# Double-clickable launcher. Finder opens this in Terminal.
#
# Does one thing start.sh cannot: works out which USB port the receiver is on
# before launching. These CH340 adapters report no serial number, so macOS names
# them after the physical socket -- move a board and the name in .env goes stale,
# after which the gateway opens a port that never sends anything and fails
# silently. Detecting it here means socket order stops mattering.

cd "$(dirname "$0")" || exit 1

# Finder closes the window the instant the script ends, taking any error with
# it. Hold it open so a failure is actually readable.
trap 'echo; echo "── press any key to close ──"; read -r -n 1 -s' EXIT

printf '\033[1mVIGIL — starting\033[0m\n\n'

if ! compgen -G "/dev/cu.usbserial-*" >/dev/null; then
  echo "No ESP32 boards found."
  echo "Plug both in over USB and run this again."
  exit 1
fi

echo "Looking for the receiver..."
RX=$(python3 - <<'PY'
import glob, sys, time
try:
    import serial
except ImportError:
    sys.exit(0)
for port in sorted(glob.glob("/dev/cu.usbserial-*")):
    try:
        with serial.Serial(port, 115200, timeout=1) as s:
            s.setDTR(False); time.sleep(0.12); s.setDTR(True); time.sleep(2.6)
            out = s.read(s.in_waiting or 1).decode("ascii", "replace")
        if "relay ready" in out:          # the receiver announces itself
            print(port); break
    except Exception:
        continue                          # busy or not a board; try the next
PY
)

if [ -n "$RX" ]; then
  echo "  receiver on $RX"
  sed -i '' "s|^SERIAL_PORT=.*|SERIAL_PORT=$RX|" gateway/.env
else
  echo "  could not identify the receiver; using the port already in .env"
  echo "  (if nothing logs, check that both boards are powered)"
fi

echo
exec ./start.sh
