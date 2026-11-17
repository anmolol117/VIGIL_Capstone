#!/bin/bash
# Double-click to start all three systems behind one page, http://localhost:8070,
# whose slider switches between Parking, Speed and the RFID gate log.
# Each system runs its own launcher, with output in logs/<system>.log.
# Closing this window (or Ctrl-C) stops everything.
cd "$(dirname "$0")" || exit 1

# Finder does not inherit the conda PATH; the systems need its packages.
[ -d "$HOME/miniconda3/bin" ] && export PATH="$HOME/miniconda3/bin:$PATH"

OPEN_PAGE=${NO_BROWSER:-}
export NO_BROWSER=1          # the systems' launchers skip opening their own tabs
mkdir -p logs
trap 'trap - EXIT INT TERM HUP; kill 0' EXIT INT TERM HUP   # stops every process started below

echo "VIGIL Capstone — starting…"
python3 -m http.server 8070 --bind 127.0.0.1 --directory web >logs/web.log 2>&1 &
# The gate log reads Supabase straight from the browser, so it works without the
# boards. rfid/start.sh sees this port taken and starts only the gateway.
python3 -m http.server 8777 --directory rfid/dashboard >logs/rfid-dashboard.log 2>&1 &
sleep 1
"./parking/Launch Parking System.command" >logs/parking.log 2>&1 </dev/null &
"./speed/Start Vehicle Monitor.command"   >logs/speed.log   2>&1 </dev/null &
"./rfid/Start VIGIL.command"              >logs/rfid.log    2>&1 </dev/null &   # exits if no ESP32 boards are plugged in

[ -n "$OPEN_PAGE" ] || ( until curl -s -o /dev/null http://localhost:8070/; do sleep 0.3; done; open http://localhost:8070/ ) &

echo
echo "  ==> http://localhost:8070"
echo
echo "  Parking  http://127.0.0.1:8080   logs/parking.log"
echo "  Speed    http://localhost:8000   logs/speed.log   (waits up to 30 s for the drone)"
echo "  RFID     http://localhost:8777   logs/rfid.log    (gateway needs both ESP32 boards)"
echo
echo "Close this window to stop everything."
wait
