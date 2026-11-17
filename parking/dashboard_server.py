"""Local server for the parking dashboard, and supervisor of what feeds it.

The dashboard switches between two sources:

  recorded  the demo clip on a loop, its bays drawn from the precomputed
            occupancy_timeline.json, while db_feeder.py replays the same
            occupancy into Supabase (Parking A) so the mobile app follows along.
  live      the analog camera. pipeline.py watches it, writes to Supabase (the
            camera's own lot) and publishes each frame and bay state to live/,
            which is streamed to the page as MJPEG.

Only the worker for the chosen source runs, and the choice is kept in .mode.

/calibrate/ sets up the camera's lot: grab a reference frame from the camera,
mark the bays and the ground rectangle, Save. Live counting pauses while that
page is open - it needs the camera, and the bays it counts against are being
changed - and restarts on the new calibration when you leave.

    python dashboard_server.py              # open the printed URL
    python dashboard_server.py --mode live
"""

from __future__ import annotations

import argparse
import csv
import io
import json
import os
import re
import signal
import subprocess
import sys
import threading
import time
import zipfile
from collections import deque
from datetime import datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import cv2
import yaml

import db
from annotate_server import build_page, load_reference, save_lot
from camera import is_blank

VIDEO_DEFAULT = "/Users/anmol117/Downloads/istockphoto-1359195980-640_adpp_is.mp4"
MODE_FILE = Path(".mode")
LIVE_DIR = Path("live")
MODES = ("recorded", "live")

_client = None


def load_vehicles(live_lot: str | None) -> dict:
    """Drone CV vehicle log (cv_vehicle_log), not the RFID gate log. Recorded
    mode shows the clip's own precomputed log; live mode reads the camera lot's
    stays from Supabase."""
    global _client
    if live_lot is None:
        snap = json.loads(Path("data/cv_vehicle_log.json").read_text())
        return {"source": "snapshot", "logs": snap["logs"]}
    try:
        _client = _client or db.connect()
        logs = (
            _client.table("cv_vehicle_log")
            .select("track_id, vehicle_type, slot_code, entry_at, exit_at, dwell_seconds, status")
            .like("slot_code", f"{live_lot}-%")
            .order("entry_at", desc=True).limit(300).execute().data
        )
        return {"source": "live", "logs": logs}
    except Exception as exc:  # noqa: BLE001 - an empty log beats a broken page
        return {"source": "none", "logs": [], "error": str(exc)}


EXPORT_DAYS = {"day": 1, "week": 7, "month": 30, "all": None}
IST = timezone(timedelta(hours=5, minutes=30))


def _cell(v):
    """IST times, and no spreadsheet formulas from database text."""
    if isinstance(v, str) and re.match(r"\d{4}-\d\d-\d\dT", v):
        return datetime.fromisoformat(v).astimezone(IST).strftime("%Y-%m-%d %H:%M:%S")
    if isinstance(v, str) and v[:1] in ("=", "+", "-", "@"):
        return "'" + v
    return "" if v is None else v


def export_zip(days: int | None) -> bytes:
    """occupancy.csv (parking_snapshots) and vehicles.csv (cv_vehicle_log) for
    every lot, from Supabase, over the last `days` days (None = everything)."""
    global _client
    _client = _client or db.connect()
    since = None if days is None else (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()

    def rows(table, cols, ts):
        out = []
        while True:  # pages until empty, whatever the server's row cap is
            q = _client.table(table).select(cols).order(ts).order("id")
            page = (q.gte(ts, since) if since else q).range(len(out), len(out) + 999).execute().data
            if not page:
                return out
            out += page

    lots = {r["id"]: r["code"] for r in _client.table("parking_lots").select("id, code").execute().data}
    occ = [[r["captured_at"], lots.get(r["lot_id"], r["lot_id"]), r["occupied"], r["free"],
            r["moving"], r["detections"], r["registration_ok"]]
           for r in rows("parking_snapshots",
                         "id, captured_at, lot_id, occupied, free, moving, detections, registration_ok",
                         "captured_at")]
    veh = [[r["entry_at"], r["exit_at"], r["slot_code"], r["vehicle_type"], r["status"],
            r["dwell_seconds"], r["track_id"]]
           for r in rows("cv_vehicle_log",
                         "id, entry_at, exit_at, slot_code, vehicle_type, status, dwell_seconds, track_id",
                         "entry_at")]
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        for name, head, data in (
            ("occupancy.csv", ["time_ist", "lot", "occupied", "free", "moving", "detections",
                               "registration_ok"], occ),
            ("vehicles.csv", ["entry_ist", "exit_ist", "bay", "vehicle_type", "status",
                              "dwell_seconds", "track_id"], veh),
        ):
            s = io.StringIO()
            csv.writer(s).writerows([head] + [[_cell(v) for v in r] for r in data])
            z.writestr(name, s.getvalue().encode("utf-8-sig"))  # BOM: Excel reads it as UTF-8
    return buf.getvalue()


class Supervisor:
    """Keeps exactly the worker the current mode needs running, and pauses the
    live pipeline while the camera is being calibrated."""

    def __init__(self, cfg_path: str, cfg: dict, mode: str) -> None:
        self.cfg_path = cfg_path
        self.cfg = cfg
        self.mode = mode
        self.has_key = bool(os.environ.get("SUPABASE_SERVICE_KEY"))
        self.calibrating_until = 0.0
        self.generation = 0  # bumped to restart the worker on a new calibration
        self.proc: subprocess.Popen | None = None
        self.key = None
        self.error: str | None = None
        self.died_at = 0.0
        self.lock = threading.RLock()
        threading.Thread(target=self._watch, daemon=True).start()

    @property
    def lot_code(self) -> str:
        return self.cfg["lot_code"]

    def calibrated(self) -> bool:
        return all(Path(f"lots/{self.lot_code}.{ext}").exists() for ext in ("json", "jpg"))

    def calibrating(self) -> bool:
        return time.time() < self.calibrating_until

    def _wanted(self) -> list[str] | None:
        py = [sys.executable, "-u"]
        if self.mode == "recorded":
            # Without the service key nothing can be written; the clip still plays.
            return py + ["db_feeder.py"] if self.has_key else None
        if self.calibrating():
            return None  # calibration needs the camera to itself
        if not self.calibrated():
            # Nothing to count yet, but show the camera so it can be aimed.
            return py + ["camera.py", "--config", self.cfg_path]
        pipeline = py + ["pipeline.py", "--config", self.cfg_path]
        if self.has_key:
            return pipeline
        return pipeline + ["--dry-run", "--lot-file", f"lots/{self.lot_code}.json"]

    def sync(self) -> None:
        with self.lock:
            cmd = self._wanted()
            key = (tuple(cmd), self.generation) if cmd else None
            if key != self.key:
                self._stop()
                self.key, self.error = key, None
                if cmd:
                    self._start(cmd)
            elif self.proc and self.proc.poll() is not None:
                # It stopped on its own: show why, and try again every 30 s in
                # case it was something passing (Wi-Fi at startup, say).
                if self.error is None:
                    self.error = next((l for l in reversed(self.proc.log) if l.strip()), "stopped")
                    self.died_at = time.time()
                elif time.time() - self.died_at > 30:
                    self._start(cmd)
                    self.error = None

    def _start(self, cmd: list[str]) -> None:
        proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
        proc.log = deque(maxlen=5)
        self.proc = proc
        threading.Thread(target=self._pump, args=(proc, Path(cmd[2]).stem), daemon=True).start()

    @staticmethod
    def _pump(proc, name: str) -> None:
        """Worker output goes to this terminal, and its last lines are kept."""
        for line in proc.stdout:
            line = line.rstrip()
            if line.startswith("objc["):  # cv2 and av each bundle ffmpeg; harmless
                continue
            print(line if line.startswith("[") else f"[{name}] {line}", flush=True)
            proc.log.append(line)

    def _stop(self) -> None:
        proc, self.proc = self.proc, None
        if proc and proc.poll() is None:
            proc.terminate()  # both workers unwind cleanly on SIGTERM
            try:
                proc.wait(15)
            except subprocess.TimeoutExpired:
                proc.kill()

    def _watch(self) -> None:
        while True:
            self.sync()
            time.sleep(1)

    def set_mode(self, mode: str) -> None:
        with self.lock:
            self.mode = mode
            MODE_FILE.write_text(mode)
        self.sync()

    def calibrate_ping(self, seconds: float = 15) -> None:
        """The calibration page is open: keep live counting paused."""
        with self.lock:
            self.calibrating_until = time.time() + seconds
        self.sync()  # stops the pipeline now, so the camera is free

    def calibrate_done(self) -> None:
        with self.lock:
            self.calibrating_until = 0.0
            self.generation += 1
        self.sync()

    def shutdown(self) -> None:
        with self.lock:
            self.calibrating_until = float("inf")
            self.mode = None
            self._stop()

    def status(self) -> dict:
        return {
            "mode": self.mode,
            "lot_code": self.lot_code,
            "calibrated": self.calibrated(),
            "calibrating": self.calibrating(),
            "database": self.has_key,
            "worker": Path(self.key[0][2]).stem if self.key else None,
            "error": self.error,
        }


def latest_frame(black_level: float, max_age_s: float = 3.0):
    """The newest raw camera frame the running preview or pipeline published -
    what the live view was just showing - if it is recent and not black."""
    raw = LIVE_DIR / "raw.jpg"
    try:
        if time.time() - raw.stat().st_mtime > max_age_s:
            return None
    except FileNotFoundError:
        return None
    frame = cv2.imread(str(raw))
    return None if frame is None or is_blank(frame, black_level) else frame


def make_handler(sup: Supervisor, video_path: Path, cfg: dict):
    timeline = Path("occupancy_timeline.json").read_bytes()
    calib = {"frame": None}  # the frame being calibrated against, until it is saved
    black_level = (cfg.get("camera") or {}).get("black_level", 16)

    def reference():
        if calib["frame"] is not None:
            return calib["frame"]
        saved = Path(f"lots/{sup.lot_code}.jpg")
        return cv2.imread(str(saved)) if saved.exists() else None

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def _send(self, code, body, ctype, extra=None):
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            for k, v in (extra or {}).items():
                self.send_header(k, v)
            self.end_headers()
            if self.command != "HEAD":
                self.wfile.write(body)

        def _json(self, obj, code=200):
            self._send(code, json.dumps(obj).encode(), "application/json", {"Cache-Control": "no-store"})

        def _body(self) -> dict:
            n = int(self.headers.get("Content-Length", 0))
            return json.loads(self.rfile.read(n) or b"{}")

        def _send_video(self):
            size = video_path.stat().st_size
            rng = self.headers.get("Range")
            if not rng:
                with video_path.open("rb") as f:
                    body = f.read()
                return self._send(200, body, "video/mp4",
                                  {"Accept-Ranges": "bytes", "Content-Length": str(size)})
            m = re.match(r"bytes=(\d+)-(\d*)", rng)
            start = int(m.group(1))
            end = int(m.group(2)) if m.group(2) else size - 1
            end = min(end, size - 1)
            with video_path.open("rb") as f:
                f.seek(start)
                chunk = f.read(end - start + 1)
            self.send_response(206)
            self.send_header("Content-Type", "video/mp4")
            self.send_header("Accept-Ranges", "bytes")
            self.send_header("Content-Range", f"bytes {start}-{end}/{size}")
            self.send_header("Content-Length", str(len(chunk)))
            self.end_headers()
            self.wfile.write(chunk)

        def _stream(self):
            """MJPEG: push each new frame the pipeline publishes, until the tab closes."""
            self.send_response(200)
            self.send_header("Content-Type", "multipart/x-mixed-replace; boundary=frame")
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            frame, last, sent = LIVE_DIR / "frame.jpg", None, 0.0
            try:
                while True:
                    try:
                        stamp = frame.stat().st_mtime_ns
                        # Browsers show a part only once the next one starts, so a
                        # feed that stops would hide its last frame: repeat it each second.
                        if stamp != last or time.time() - sent > 1:
                            jpg, last, sent = frame.read_bytes(), stamp, time.time()
                            self.wfile.write(b"--frame\r\nContent-Type: image/jpeg\r\n"
                                             b"Content-Length: %d\r\n\r\n%b\r\n" % (len(jpg), jpg))
                    except FileNotFoundError:
                        pass  # pipeline not started yet
                    time.sleep(0.03)
            except (BrokenPipeError, ConnectionResetError):
                pass

        def _calibration_page(self):
            # A fresh visit (not a reload of an open calibration) starts from the
            # latest camera frame. Taken before the ping pauses the worker that
            # publishes it. With none (camera off, or nothing streaming) the page
            # waits for the next clear frame from the camera itself.
            if not sup.calibrating():
                calib["frame"] = latest_frame(black_level)
            sup.calibrate_ping()
            ref = calib["frame"]
            existing = Path(f"lots/{sup.lot_code}.json")
            w, h = (ref.shape[1], ref.shape[0]) if ref is not None else \
                map(int, cfg.get("camera", {}).get("size", "720x480").split("x"))
            page = build_page(
                Path("annotate_page.html").read_text(), lot_code=sup.lot_code, w=w, h=h,
                preload=json.loads(existing.read_text()) if existing.exists() else None,
                name="Camera lot", can_capture=True, has_frame=ref is not None,
            )
            self._send(200, page, "text/html; charset=utf-8")

        def do_HEAD(self):
            self.do_GET()

        def do_GET(self):
            path = self.path.split("?")[0]
            if path in ("/", "/index.html"):
                html = Path("dashboard.html").read_bytes()
                if sup.mode == "live":
                    html = html.replace(b"<body>", b'<body data-live="1">', 1)
                self._send(200, html, "text/html; charset=utf-8")
            elif path == "/status":
                self._json(sup.status())
            elif path == "/stream.mjpg":
                self._stream()
            elif path == "/state.json":
                try:
                    self._send(200, (LIVE_DIR / "state.json").read_bytes(), "application/json",
                               {"Cache-Control": "no-store"})
                except FileNotFoundError:
                    self._json({}, 503)
            elif path == "/video.mp4":
                self._send_video()
            elif path == "/timeline.json":
                self._send(200, timeline, "application/json")
            elif path == "/vehicles":
                self._json(load_vehicles(sup.lot_code if sup.mode == "live" else None))
            elif path == "/export":
                rng = parse_qs(urlparse(self.path).query).get("range", [""])[0]
                if rng not in EXPORT_DAYS:
                    return self._json({"error": f"range must be one of {list(EXPORT_DAYS)}"}, 400)
                try:
                    body = export_zip(EXPORT_DAYS[rng])
                except Exception as exc:  # noqa: BLE001 - shown on the button
                    return self._json({"error": str(exc)[:200]}, 502)
                self._send(200, body, "application/zip", {
                    "Content-Disposition": f'attachment; filename="parking-{rng}.zip"',
                    "Cache-Control": "no-store"})
            elif path == "/calibrate":
                self.send_response(301)
                self.send_header("Location", "/calibrate/")
                self.end_headers()
            elif path == "/calibrate/":
                self._calibration_page()
            elif path == "/calibrate/frame":
                ref = reference()
                if ref is None:
                    return self._send(404, b"no frame yet", "text/plain")
                ok, buf = cv2.imencode(".jpg", ref, [cv2.IMWRITE_JPEG_QUALITY, 92])
                self._send(200, buf.tobytes(), "image/jpeg", {"Cache-Control": "no-store"})
            elif path == "/calibrate/ping":
                sup.calibrate_ping()
                self._json({"ok": True})
            else:
                self._send(404, b"not found", "text/plain")

        def do_POST(self):
            path = self.path.split("?")[0]
            if path == "/mode":
                mode = self._body().get("mode")
                if mode not in MODES:
                    return self._json({"error": f"mode must be one of {MODES}"}, 400)
                sup.set_mode(mode)
                self._json(sup.status())
            elif path == "/calibrate/capture":
                sup.calibrate_ping(60)  # the camera must be free, and capture can take a while
                try:
                    frame = load_reference(cfg["source"], 0, cfg.get("camera"))
                except SystemExit as exc:  # no picture: camera off, unplugged, or not allowed
                    return self._json({"ok": False, "message": str(exc)})
                calib["frame"] = frame
                self._json({"ok": True, "w": frame.shape[1], "h": frame.shape[0]})
            elif path == "/calibrate/save":
                frame = reference()
                if frame is None:
                    return self._json({"ok": False, "message": "take a frame from the camera first"})
                result = save_lot(self._body(), frame, code=sup.lot_code, no_db=not sup.has_key)
                if result["ok"]:
                    calib["frame"] = None  # it is the saved reference now
                print(f"[calibrate] {result['message']}", flush=True)
                self._json(result)
            elif path == "/calibrate/exit":
                calib["frame"] = None
                sup.calibrate_done()
                self._json(sup.status())
            else:
                self._send(404, b"not found", "text/plain")

    return Handler


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--source", default=VIDEO_DEFAULT, help="the recorded clip")
    ap.add_argument("--port", type=int, default=8080)
    ap.add_argument("--mode", choices=MODES, help="start in this mode (default: the last one used)")
    ap.add_argument("--config", default="config.yaml")
    args = ap.parse_args()

    video_path = Path(args.source)
    if not video_path.exists():
        raise SystemExit(f"video not found: {video_path}")
    if not Path("occupancy_timeline.json").exists():
        raise SystemExit("occupancy_timeline.json missing - run dashboard_precompute.py first")

    db.load_env()  # the service key, for the workers and the live vehicle log
    cfg = yaml.safe_load(Path(args.config).read_text())
    saved = MODE_FILE.read_text().strip() if MODE_FILE.exists() else ""
    mode = args.mode or (saved if saved in MODES else "recorded")
    if args.mode:
        MODE_FILE.write_text(mode)
    sup = Supervisor(args.config, cfg, mode)

    # Closing the Terminal window (SIGHUP) or a kill (SIGTERM) must stop the
    # worker too - an orphaned pipeline would keep the camera and keep writing.
    def _stop(*_):
        raise KeyboardInterrupt

    signal.signal(signal.SIGTERM, _stop)
    signal.signal(signal.SIGHUP, _stop)
    server = ThreadingHTTPServer(("127.0.0.1", args.port), make_handler(sup, video_path, cfg))
    url = f"http://127.0.0.1:{args.port}/"
    print(f"[dashboard] {mode} mode; camera lot {sup.lot_code} "
          f"{'calibrated' if sup.calibrated() else 'not calibrated yet'}; "
          f"database {'on' if sup.has_key else 'off (no service key in .env)'}")
    print(f"[dashboard] open  {url}", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n[dashboard] stopping")
    finally:
        sup.shutdown()


if __name__ == "__main__":
    main()
