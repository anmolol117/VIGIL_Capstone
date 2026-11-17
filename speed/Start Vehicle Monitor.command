#!/bin/bash
# Double-click to launch the whole system. Opens the web UI in your browser.
#
# To change enforcement, edit the two lines just below.

LIMIT=40                 # km/h — vehicles sustained above this are logged
CAMERA="Overhead Cam"    # location label stored with each speeding event
LAT="30.351778"          # drone hover position 30°21'06.4"N, decimal degrees
LON="76.370417"          # 76°22'13.5"E  (empty = events logged, no guard notified)
PORT=8000
DRONE=1                  # 1 = use the drone's camera (Mac must be on the drone's Wi-Fi)
                         # 0 = paste a stream or video into the page instead

# --- run from the project folder, wherever this file was double-clicked from --
cd "$(dirname "$0")" || { echo "cannot find project folder"; read -r; exit 1; }

# Finder does not inherit the conda PATH, so `python3` could otherwise resolve to
# system Python without the project's packages. Prefer the miniconda interpreter.
[ -d "$HOME/miniconda3/bin" ] && export PATH="$HOME/miniconda3/bin:$PATH"

# Open the browser once the server is actually listening (up to 3 min for a
# first-time calibration that has to record a clip). NO_BROWSER: set by the
# combined launcher, whose page shows this dashboard.
[ -n "$NO_BROWSER" ] || ( for _ in $(seq 1 180); do
    curl -s "http://localhost:$PORT/stats" >/dev/null 2>&1 && { open "http://localhost:$PORT"; break; }
    sleep 1
  done ) &

# Drone camera: its own RTSP stream (MJPEG 640x352, UDP). Wait until it answers
# before the pipeline opens it, so a drone that is still booting is not an error.
DRONE_URL="rtsp://192.168.1.1:7070/webcam"
export PATH="$PATH:/opt/homebrew/bin"   # ffprobe from Finder; appended so the miniconda python3 still wins
SRC=()
if [ "$DRONE" = "1" ]; then
  echo "waiting for drone video (is the Mac on the drone's Wi-Fi?) ..."
  OK=""
  END=$((SECONDS + 30))               # 30 s of real time, however long each probe takes
  while [ $SECONDS -lt $END ]; do
    # 1 s port check first: if nothing answers, a TCP connect alone can block
    # for over a minute, whatever timeout ffprobe is given.
    nc -z -G 1 192.168.1.1 7070 >/dev/null 2>&1 &&
    ffprobe -v error -rtsp_transport udp -timeout 3000000 -select_streams v:0 \
            -show_entries stream=width -of csv=p=0 "$DRONE_URL" >/dev/null 2>&1 && { OK=1; break; }
    sleep 1
  done
  if [ -n "$OK" ]; then
    echo "drone video OK"
    SRC=(--url "$DRONE_URL" --mode drone)
    # The drone's Wi-Fi has no internet. If it is also the Mac's default route,
    # new connections (dashboard log, evidence uploads, reconnects) all fail.
    if [ "$(route -n get default 2>/dev/null | awk '/gateway/{print $2}')" = "192.168.1.1" ]; then
      echo "WARNING: internet is going out through the drone's Wi-Fi, which has none."
      echo "  The speeding log and guard alerts will fail. Fix: plug in iPhone USB (or Ethernet),"
      echo "  then System Settings > Network > ... > Set Service Order: put it above Wi-Fi."
    fi
  else
    echo "no drone video -- starting without it (paste $DRONE_URL in the page once connected)"
  fi
fi

# Hand off to the launcher (preflight checks + server). Runs in the foreground so
# Ctrl-C here stops everything.
POS=()
[ -n "$LAT" ] && [ -n "$LON" ] && POS=(--lat "$LAT" --lon "$LON")
./start.sh --speed-limit "$LIMIT" --camera "$CAMERA" --port "$PORT" "${POS[@]}" "${SRC[@]}"

# Keep the window readable if it stopped or the preflight failed, instead of the
# terminal vanishing the moment the process ends.
echo
echo "── stopped ── press Return to close ──"
read -r
