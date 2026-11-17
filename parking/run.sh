#!/usr/bin/env bash
# Run the drone parking system. The live camera and its lot come from
# config.yaml; the recorded-clip demo uses VIDEO / LOT. Override via env.
#
#   ./run.sh                 dashboard (Recorded / Live switch, Calibrate) - precomputes if needed
#   ./run.sh live            the same, starting on the live camera
#   ./run.sh annotate        standalone bay marker for the camera (Calibrate does this too)
#   ./run.sh precompute      rebuild occupancy_timeline.json from the clip
#   ./run.sh pipeline ...    run the live/offline pipeline (args passed through)
#   ./run.sh feeder ...      replay demo occupancy into Supabase for the mobile app
#   ./run.sh test            run the unit checks
set -euo pipefail
cd "$(dirname "$0")"

PY="${PY:-python3}"
VIDEO="${VIDEO:-/Users/anmol117/Downloads/istockphoto-1359195980-640_adpp_is.mp4}"
LOT="${LOT:-ISTOCK}"
PORT="${PORT:-8080}"
cmd="${1:-dashboard}"; shift || true
cfg() { "$PY" -c "import yaml; print(yaml.safe_load(open('config.yaml'))['$1'])"; }

case "$cmd" in
  annotate)
    exec "$PY" annotate_server.py --source "$(cfg source)" --lot-code "$(cfg lot_code)" "$@"
    ;;
  live)
    exec "$0" dashboard --mode live "$@"
    ;;
  precompute)
    exec "$PY" dashboard_precompute.py --source "$VIDEO" \
         --lot-file "lots/$LOT.json" --reference "lots/$LOT.jpg" "$@"
    ;;
  dashboard)
    [ -f occupancy_timeline.json ] || "$PY" dashboard_precompute.py --source "$VIDEO" \
         --lot-file "lots/$LOT.json" --reference "lots/$LOT.jpg"
    pkill -f "dashboard_server\.py|db_feeder\.py|(^|[ /])pipeline\.py" 2>/dev/null || true   # free port + camera, not speed's live_pipeline.py
    exec "$PY" dashboard_server.py --source "$VIDEO" --port "$PORT" "$@"
    ;;
  pipeline)
    exec "$PY" pipeline.py "$@"
    ;;
  feeder)
    exec "$PY" db_feeder.py "$@"
    ;;
  test)
    exec "$PY" test_pipeline.py
    ;;
  *)
    echo "usage: ./run.sh [live|annotate|precompute|dashboard|pipeline|feeder|test]" >&2
    exit 2
    ;;
esac
