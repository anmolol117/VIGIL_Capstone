#!/bin/bash
# Double-click to run the parking system. The dashboard opens in your browser;
# its switch picks the source - the recorded clip or the live camera - and runs
# what that source needs (and feeds the mobile app when .env holds the service
# key). Calibrate sets up the camera. Closing this Terminal window stops everything.
cd "$(dirname "$0")" || exit 1

PY=/Users/anmol117/miniconda3/bin/python3
[ -x "$PY" ] || PY=python3
PORT=8080
URL="http://127.0.0.1:$PORT/"

echo "Parking system — starting…"
# stop previous instances so the camera and the port are free
# (anchored so the speed system's live_pipeline.py survives)
pkill -f "dashboard_server\.py|db_feeder\.py|(^|[ /])pipeline\.py|annotate_server\.py" 2>/dev/null
sleep 1

if [ ! -f occupancy_timeline.json ]; then
  echo "First run: building the recorded clip's occupancy timeline (~1 min)…"
  "$PY" dashboard_precompute.py || { echo "precompute failed."; read -n1 -p "Press any key to close…"; exit 1; }
fi

# When the server starts answering, open the dashboard in the default browser.
# NO_BROWSER: set by the combined launcher, whose page shows this dashboard.
[ -n "$NO_BROWSER" ] || ( until curl -s -o /dev/null "$URL"; do sleep 0.4; done; open "$URL" ) &

echo "Dashboard: $URL"
echo "Close this window to stop the system."
exec "$PY" dashboard_server.py --port "$PORT"
