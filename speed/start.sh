#!/usr/bin/env bash
# Boot the whole system: checks, then the web UI on http://localhost:8000
#
#   ./start.sh                                    prompt for a source in the page
#   ./start.sh --url <stream|video|path>          preload a source
#   ./start.sh --speed-limit 70 --camera "M6 J12" enable speeding log
#   ./start.sh --mode drone --speed-limit 30      overhead footage
#
# Any flag is passed straight through to live_pipeline.py.
set -euo pipefail
cd "$(dirname "$0")"

PY=python3
PORT=8000
prev=""                                       # honour a custom --port in the banner
for a in "$@"; do
  [ "$prev" = "--port" ] && PORT="$a"
  prev="$a"
done

echo "Vehicle Speed & 3D Localization"
echo "───────────────────────────────"

# --- preflight: fail with a clear reason rather than a stack trace ------------
$PY - <<'EOF' || exit 1
import importlib.util, shutil, sys      # .util needs an explicit import
missing = [m for m in ("cv2", "numpy", "torch", "ultralytics", "yt_dlp")
           if importlib.util.find_spec(m) is None]
if missing:
    print("  missing packages:", ", ".join(missing))
    print("  fix: python3 -m pip install " + " ".join(missing))
    sys.exit(1)
if shutil.which("ffmpeg") is None:
    print("  ffmpeg not found (needed to record calibration clips)")
    print("  fix: brew install ffmpeg")
    sys.exit(1)
import torch
print(f"  deps ok · torch {torch.__version__} · "
      f"{'MPS' if torch.backends.mps.is_available() else 'CPU'}")
EOF

# --- models ------------------------------------------------------------------
[ -f models/yolo11s.pt ] || { echo "  models/yolo11s.pt missing"; exit 1; }
if [ -d models/hf/hub/models--apple--DepthPro-hf ]; then
  echo "  models ok · detector + Depth Pro local"
else
  echo "  models ok · detector present; Depth Pro will download on first oblique calibration"
fi

# --- speeding log is optional; say which mode we are in ----------------------
if [ -f .env ] && grep -qE '^DATABASE_URL=postgres' .env 2>/dev/null; then
  # Check the VALUES only. The comment block is copied from .env.example and
  # still contains <YOUR-...> text, which a whole-file grep reads as unfilled.
  if grep -vE '^\s*#' .env | grep -qE '=.*(<|YOUR-)' 2>/dev/null; then
    echo "  .env still has placeholder values — logging will be disabled"
  else
    echo "  speeding log configured (.env present)"
  fi
else
  echo "  no .env — running without the speeding log (cp .env.example .env to enable)"
fi

# --- free the port so a stale run cannot silently shadow this one -------------
if lsof -nP -iTCP:"$PORT" -sTCP:LISTEN >/dev/null 2>&1; then
  echo "  port $PORT busy — stopping the previous instance"
  pkill -f "live_pipeline.py" 2>/dev/null || true
  sleep 2
fi

echo
echo "  ==> http://localhost:$PORT"
echo
exec $PY -u live_pipeline.py "$@"
