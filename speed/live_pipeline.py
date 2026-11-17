"""Live pipeline: calibrate a stream, then serve annotated video on localhost.

    python3 live_pipeline.py            # prompts for a stream URL

  1. resolves the live stream
  2. records a short clip and calibrates from it
  3. reconnects to the live edge and tracks vehicles
  4. serves the result at http://localhost:8000

Camera mode is switchable from the web page:

  Fixed / oblique   pole, overpass, CCTV. Runs Depth Pro once for focal length
                    and ground-plane tilt.
  Drone / overhead  straight down. Skips the depth model entirely -- looking
                    down at flat ground the depth gradient is ~zero, so there is
                    no tilt signal to recover, and only H/f is observable.

Switching modes recalibrates. The detector is released first so the depth model
never shares the GPU with it -- that overlap is what exhausts a 24 GB machine.
"""

import argparse
import json
import os
import re
import subprocess
import threading
import time
from collections import defaultdict, deque
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import cv2
import numpy as np

import calibrate as cal
import speedcam as sc
import speedlog

# Local overhead clip, so the demo works offline. The YouTube recording used
# before (Su5cUQq8dAY) was taken down. Relative to this folder: its cached drone
# calibration, calib/data_drone_video_mp4_drone.json, is keyed on this string.
DEFAULT_URL = "data/drone_video.mp4"
DRONE_URL = "rtsp://192.168.1.1:7070/webcam"   # also in "Start Vehicle Monitor.command"
PAGE_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "dashboard.html")

# Overhead footage needs an aerial-trained detector. A COCO model barely sees a
# car from directly above -- measured on the drone clip, yolo11s found 0 vehicles
# per frame at conf 0.25 and 1-2 at conf 0.10, while visDrone found 23-28. The
# camera mode therefore selects the detector, not just the calibration geometry.
FIXED_MODEL, FIXED_CONF = "models/yolo11s.pt", 0.25
DRONE_MODEL, DRONE_CONF = "models/visDrone.pt", 0.10


def model_for(mode, args):
    """(weights, conf) for a camera mode, honouring explicit CLI overrides."""
    if args.model or args.conf is not None:            # user chose: respect it
        return (args.model or (DRONE_MODEL if mode == "drone" else FIXED_MODEL),
                args.conf if args.conf is not None else
                (DRONE_CONF if mode == "drone" else FIXED_CONF))
    if mode == "drone" and os.path.exists(DRONE_MODEL):
        return DRONE_MODEL, DRONE_CONF
    return FIXED_MODEL, FIXED_CONF

# Shared between the inference loop and the HTTP threads.
STATE = {"jpeg": None, "stats": {}, "mode": "drone", "status": "starting",
         "want_mode": None, "want_source": None, "source": None,
         "overlay": False, "depth_kind": "none", "default_url": "", "title": "",
         "note": "", "feed": False, "calib": None, "camera": "", "speed_limit": 0.0}
LOCK = threading.Lock()


def publish(**kw):
    with LOCK:
        STATE.update(kw)


# =========================================================================
# Stream plumbing
# =========================================================================

def resolve_stream(url, height=1080):
    """Source -> (media url, title, w, h, is_live).

    Accepts a live stream, an ordinary uploaded video, or a local file. The
    is_live flag matters: a finite video decodes far faster than real time, so it
    must be timed by media position rather than by the wall clock.
    """
    if url.startswith(("rtsp://", "http://127.0.0.1", "http://localhost")):
        # A camera's own stream: RTSP straight from the drone or a CCTV camera,
        # or the drone_feed.py bridge. Already live; no resolver needed.
        # Retried: the drone's first RTSP attempt after joining its Wi-Fi can
        # fail ("Operation not permitted") and succeed seconds later.
        for attempt in range(4):
            cap = cv2.VideoCapture(url)
            ok, frame = cap.read()
            cap.release()
            if ok:
                break
            time.sleep(2)
        if not ok:
            raise RuntimeError(f"no video at {url} -- is the camera reachable?")
        return url, "Camera", frame.shape[1], frame.shape[0], True

    if os.path.exists(url):
        cap = cv2.VideoCapture(url)
        w = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
        h = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
        cap.release()
        return url, os.path.basename(url), w, h, False

    import yt_dlp
    # bv* (video-only) first, not "best". On YouTube "best" only considers
    # progressive muxed streams, which YouTube caps at 360p -- so an uploaded
    # 720p video silently arrives as 640x360 and every measurement is made on a
    # heavily downscaled frame. Audio is irrelevant here.
    fmt = (f"bv*[height<={height}]/bestvideo[height<={height}]/"
           f"best[height<={height}]/best")
    with yt_dlp.YoutubeDL({"quiet": True, "no_warnings": True,
                           "format": fmt}) as y:
        i = y.extract_info(url, download=False)
    return (i["url"], i.get("title", "stream"), i.get("width"), i.get("height"),
            bool(i.get("is_live")))


def record_clip(stream_url, seconds, path):
    subprocess.run(["ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
                    "-i", stream_url, "-t", str(seconds), "-c", "copy", path],
                   check=True)
    return path


class LiveReader(threading.Thread):
    """Feeds frames to the inference loop, timed correctly for the source type.

    Live: keep only the newest frame and stamp it with the wall clock. Inference
    can be slower than the source, and without dropping, the capture buffer backs
    up so the "live" view drifts further into the past every second.

    Finite video (uploaded or local): the opposite problem. It decodes tens of
    times faster than real time, so wall-clock stamps would compress the whole
    clip into a second and report absurd speeds. Every frame is kept, stamped by
    media position (index / fps), and playback is paced so it looks natural. The
    stamps stay exact no matter how fast or slow decoding actually runs.
    """

    def __init__(self, url, live=True):
        super().__init__(daemon=True)
        self.url, self.cap, self.live = url, cv2.VideoCapture(url), live
        fps = self.cap.get(cv2.CAP_PROP_FPS)
        # An RTP stream can report its 90 kHz clock here before frames flow;
        # it sizes the speed window, so an absurd value must not get through.
        self.fps = fps if 1 <= fps <= 120 else 25.0   # ponytail: nominal fallback
        self.frame = self.stamp = self.arrived = None
        self.clock = sc.StreamClock()     # live: stamps from the camera's own clock
        self.running, self.dropped, self.idx = True, 0, 0
        self.t0 = time.time()
        self.loops = 0
        n = self.cap.get(cv2.CAP_PROP_FRAME_COUNT)
        self.duration = (n / self.fps) if n > 0 else 0.0
        # Live sources only. While the inference loop is busy calibrating, the
        # reader shows the raw video itself (passthrough) and records the
        # calibration clip, so the camera is opened once and never goes dark.
        self.passthrough = False
        self.rec_path, self.writer, self.rec_frames = None, None, 0
        self.rec_lock = threading.Lock()

    def run(self):
        while self.running:
            ok, f = self.cap.read()
            if not ok:
                if self.live:                       # transient hiccup: reconnect
                    time.sleep(0.5)
                    self.cap.release()
                    self.cap = cv2.VideoCapture(self.url)
                else:                               # end of video: play it again
                    self.cap.set(cv2.CAP_PROP_POS_FRAMES, 0)
                    self.idx, self.t0 = 0, time.time()
                    self.loops += 1
                continue

            if self.live:
                with self.rec_lock:
                    if self.rec_path:
                        if self.writer is None:
                            # ponytail: nominal fps; calibration reads the frame sequence, not timing
                            fps = self.fps if 1 <= self.fps <= 60 else 25
                            self.writer = cv2.VideoWriter(self.rec_path, cv2.VideoWriter_fourcc(*"mp4v"),
                                                          fps, (f.shape[1], f.shape[0]))
                        self.writer.write(f)
                        self.rec_frames += 1
                if self.passthrough:
                    ok, buf = cv2.imencode(".jpg", f, [cv2.IMWRITE_JPEG_QUALITY, 80])
                    if ok:
                        publish(jpeg=buf.tobytes())
                if self.frame is not None:
                    self.dropped += 1
                arrived = time.time()
                pts = self.cap.get(cv2.CAP_PROP_POS_MSEC) / 1000.0
                self.frame, self.stamp, self.arrived = f, self.clock.stamp(arrived, pts), arrived
                continue

            # Monotonic across replays: a stamp that jumped back to zero would
            # give tracks spanning the loop a near-zero dt and an absurd speed.
            stamp = self.loops * self.duration + self.idx / self.fps
            self.idx += 1
            target = self.t0 + self.idx / self.fps  # pace to real time
            while self.running and time.time() < target:
                time.sleep(0.002)
            while self.running and self.frame is not None:   # backpressure:
                time.sleep(0.002)                            # never drop a frame
            self.frame, self.stamp = f, stamp

    def record(self, path):
        with self.rec_lock:
            self.rec_path, self.writer, self.rec_frames = path, None, 0

    def end_recording(self):
        """Stop recording; returns how many frames were written."""
        with self.rec_lock:
            if self.writer is not None:
                self.writer.release()
            n, self.rec_path, self.writer = self.rec_frames, None, None
        return n

    @property
    def breaks(self):
        """Switches between timelines (camera clock / arrival): trajectories
        spanning one are not comparable."""
        return self.clock.breaks

    def take(self):
        """(frame, stamp, arrival time or None). Stamps space frames as the
        camera captured them; the arrival time is for measuring delay."""
        f, t, a = self.frame, self.stamp, self.arrived
        self.frame = None
        return f, t, a

    def stop(self):
        # Join before releasing. VideoCapture.release() while the reader thread
        # is inside cap.read() segfaults the interpreter -- no traceback, the
        # process simply vanishes. That is what killed the server on the first
        # mode switch.
        self.running = self.passthrough = False
        self.join(timeout=5)
        self.end_recording()
        self.cap.release()


# =========================================================================
# Calibration
# =========================================================================

def source_tag(url):
    """Filesystem-safe identifier for a source."""
    return re.sub(r"\W+", "_", url)[-32:].strip("_")


def calib_path(args, mode):
    """One calibration file per (stream, mode) -- they are not interchangeable."""
    return os.path.join(args.calibdir, f"{source_tag(args.url)}_{mode}.json")


def clip_path(args):
    """One recorded calibration clip per source.

    This was a single shared filename, and the logic is "reuse the clip if it
    exists" -- so only the first source ever recorded one and every later video
    was calibrated from that first video's footage. Three unrelated clips came
    out with byte-identical calibrations and depth maps before this was keyed
    by source.
    """
    return os.path.join(args.workdir, f"clip_{source_tag(args.url)}.mp4")


def build_depth_overlay(path, K, n, h, size):
    """Prepare the depth image shown under the toggle.

    Prefers the map Depth Pro produced at calibration; falls back to the
    geometric ground-plane depth, which is what overhead mode has (no network
    depth map is made there) and is in any case the depth the measurements use.
    """
    npy = cal.depth_path(path)
    if os.path.exists(npy):
        return sc.colourize_depth(np.load(npy), size), "depth-pro"
    return sc.colourize_depth(sc.ground_depth_map(K, n, h, size), size), "geometric"


def record_live(reader, seconds, path):
    """Record the calibration clip from the running reader, keeping the video on screen."""
    reader.record(path)
    end = time.time() + seconds
    while time.time() < end:
        publish(status=f"recording {seconds}s to calibrate",
                calib={"left": int(end - time.time()), "total": seconds})
        time.sleep(1)
    if reader.end_recording() == 0:
        raise RuntimeError("no video arrived while recording -- is the camera still connected?")


def cleanup_for(mode, args):
    """The frame clean-up in force, or None. Calibration and live detection must
    agree: vehicle widths measured on a different picture bias the height."""
    return sc.clean_frame if mode == "drone" and not args.no_cleanup else None


def ensure_calibration(stream_url, args, mode, force=False, reader=None):
    """Return (K, n, h) for `mode`, calibrating if there is no cached file."""
    path = calib_path(args, mode)
    # A live drone keeps the same stream address but hovers at a new height
    # every flight, so neither its calibration nor its recorded clip may be
    # reused. A fixed camera's stream, or a recorded video, keeps its cache.
    fresh = mode == "drone" and not os.path.exists(args.url)
    if os.path.exists(path) and not (force or fresh):
        K, n, h, _ = sc.load_calib(path)
        print(f"reusing {path}: f={K[0]:.0f}px height={h:.2f}m")
        return K, n, h

    if args.clip:
        clip = args.clip
    elif os.path.exists(args.url):
        clip = args.url               # a local video calibrates from itself
    else:
        clip = clip_path(args)
    if fresh and os.path.exists(clip):
        os.remove(clip)
    if not os.path.exists(clip):
        print(f"recording {args.calib_seconds}s -> {clip}")
        if reader is not None:
            record_live(reader, args.calib_seconds, clip)
        else:
            publish(status=f"recording {args.calib_seconds}s to calibrate")
            record_clip(stream_url, args.calib_seconds, clip)

    publish(status=f"calibrating ({mode})", calib=None)
    t0 = time.time()
    weights, conf = model_for(mode, args)
    print(f"  detector: {weights} (conf {conf})")
    K, n, h, _ = cal.calibrate_video(
        clip, path, model=weights, nadir=(mode == "drone"),
        altitude=args.altitude, device=args.device, imgsz=args.imgsz,
        conf=conf, prefilter=cleanup_for(mode, args))
    print(f"  calibration took {time.time() - t0:.0f}s")
    return K, n, h


# =========================================================================
# Web page (stdlib only -- one MJPEG endpoint needs no framework)
# =========================================================================

# The page lives in dashboard.html, read on each request: editing it needs a
# browser refresh, not a pipeline restart (which would mean recalibrating).



class Handler(BaseHTTPRequestHandler):
    # Deliberately HTTP/1.0. An MJPEG response is an endless multipart body with
    # no Content-Length; under HTTP/1.1 a client expects a length or chunked
    # encoding, gets neither, and treats the response as malformed -- the image
    # simply fails to load.
    def log_message(self, *a):
        pass                                # keep the console for pipeline output

    def _send(self, body, ctype):
        self.send_response(200)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        # Match on the path only: the stream URL carries a cache-busting query
        # so each reconnect opens a fresh connection, and an exact-string
        # comparison would 404 on it.
        route = self.path.split("?", 1)[0]
        if route == "/":
            with open(PAGE_FILE, "rb") as fh:
                self._send(fh.read(), "text/html; charset=utf-8")
        elif self.path.startswith("/mode"):
            m = "drone" if "drone" in self.path else "fixed"
            with LOCK:
                if m != STATE["mode"]:
                    STATE["want_mode"] = m       # main loop picks this up
                    STATE["status"] = f"switching to {m}..."
                else:
                    STATE["status"] = "running"  # clear any failure banner
            self._send(b'{"ok":true}', "application/json")
        elif self.path.startswith("/source"):
            from urllib.parse import urlparse, parse_qs, unquote
            q = parse_qs(urlparse(self.path).query)
            url = unquote(q.get("url", [""])[0]).strip()
            if url:
                with LOCK:
                    STATE["want_source"] = url
                    STATE["status"] = "loading source..."
            self._send(b'{"ok":true}', "application/json")
        elif route == "/recalibrate":
            with LOCK:                       # reloading a live drone recalibrates it
                if STATE["source"]:
                    STATE["want_source"] = STATE["source"]
                    STATE["status"] = "loading source..."
                    STATE["note"] = ""
            self._send(b'{"ok":true}', "application/json")
        elif self.path.startswith("/overlay"):
            with LOCK:
                STATE["overlay"] = "1" in self.path
            self._send(b'{"ok":true}', "application/json")
        elif route == "/events":
            data = speedlog.dashboard()
            body = json.dumps(data if data is not None else {"disabled": True}).encode()
            self._send(body, "application/json")
        elif route == "/export":
            from urllib.parse import urlparse, parse_qs
            rng = parse_qs(urlparse(self.path).query).get("range", [""])[0]
            if rng not in speedlog.EXPORT_DAYS:
                return self.send_error(400, "range must be day, week, month or all")
            self._send(speedlog.export_csv(speedlog.EXPORT_DAYS[rng]), "text/csv; charset=utf-8")
        elif route.startswith("/evidence/"):
            # this Mac's copy of an event's images, so the log doesn't wait on the phone
            m = re.fullmatch(r"/evidence/([0-9a-f-]{36})/(\d+(?:_raw)?\.jpg)", route)
            path = m and os.path.join(speedlog.EVIDENCE_DIR, m[1], m[2])
            if path and os.path.exists(path):
                with open(path, "rb") as f:
                    self._send(f.read(), "image/jpeg")
            else:
                self.send_error(404)
        elif route == "/stats":
            with LOCK:
                body = json.dumps({**STATE["stats"], "mode": STATE["mode"],
                                   "status": STATE["status"],
                                   "overlay": STATE["overlay"],
                                   "depth_kind": STATE["depth_kind"],
                                   "source": STATE["source"],
                                   "note": STATE["note"], "feed": STATE["feed"],
                                   "calib": STATE["calib"], "camera": STATE["camera"],
                                   "limit": STATE["speed_limit"], "drone_url": DRONE_URL,
                                   "default_url": STATE["default_url"]}).encode()
            self._send(body, "application/json")
        elif route == "/frame.jpg":
            with LOCK:
                buf = STATE["jpeg"]
            if buf is None:
                self.send_response(204)
                self.end_headers()
            else:
                self._send(buf, "image/jpeg")
        elif route == "/stream.mjpg":
            self.send_response(200)
            self.send_header("Content-Type",
                             "multipart/x-mixed-replace; boundary=frame")
            self.end_headers()
            # Send only genuinely new frames, and let any socket error end the
            # thread. Previously a departed viewer could leave a handler looping
            # forever, and those accumulate across reloads.
            last = None
            try:
                while True:
                    with LOCK:
                        buf = STATE["jpeg"]
                    if buf is None or buf is last:
                        time.sleep(0.02)
                        continue
                    last = buf
                    self.wfile.write(b"--frame\r\nContent-Type: image/jpeg\r\n"
                                     b"Content-Length: " + str(len(buf)).encode()
                                     + b"\r\n\r\n" + buf + b"\r\n")
                    self.wfile.flush()
            except (BrokenPipeError, ConnectionResetError, OSError, ValueError):
                pass                        # viewer went away: end this handler
        else:
            self.send_error(404)


# =========================================================================
# Main
# =========================================================================

def load_source(url, args, mode):
    """Resolve a source, calibrate it, and return everything the loop needs."""
    publish(status=f"resolving {url}", source=url, stats={}, note="", feed=False, calib=None)
    print(f"\nresolving {url}")
    stream_url, title, sw, sh, is_live = resolve_stream(url)
    print(f"  {title}  ({sw}x{sh})  {'LIVE' if is_live else 'recorded video'}")

    args.url = url                      # calib_path keys off this
    if not os.path.exists(calib_path(args, mode)):
        need = not (args.clip and os.path.exists(args.clip)) and \
               not os.path.exists(url) and not os.path.exists(clip_path(args))
        eta = (args.calib_seconds if need else 0) + (10 if mode == "drone" else 30)
        print(f"calibrating ({mode}, ~{eta // 60}m{eta % 60:02d}s)")

    # A live camera shows its video from the start, calibration included.
    reader = None
    if is_live:
        reader = LiveReader(stream_url, live=True)
        reader.passthrough = True
        reader.start()
        publish(feed=True, title=title)
    try:
        K, n, h = ensure_calibration(stream_url, args, mode, reader=reader)
        _, _, _, size = sc.load_calib(calib_path(args, mode))
        depth_img, depth_kind = build_depth_overlay(calib_path(args, mode), K, n, h, size)
    except cal.CalibrationError as e:
        if not is_live:
            raise
        # A live camera with no moving cars in view yet (indoors, or before the
        # drone is in position) can't calibrate. Keep its video up so it can be
        # positioned, and calibrate again on demand.
        print(f"  not calibrated yet: {e}")
        reader.passthrough = False
        publish(mode=mode, depth_kind="none", status="preview", title=title, source=url,
                is_live=True, feed=False, note="Not calibrated: no moving cars seen. "
                "Position the camera over traffic, then press Recalibrate.")
        return reader, None, None, None, None, None, True, title
    except BaseException:
        if reader:
            reader.stop()
        publish(feed=False)
        raise
    if reader is None:
        reader = LiveReader(stream_url, live=is_live)
        reader.start()
    reader.passthrough = False
    publish(mode=mode, depth_kind=depth_kind, status="running", title=title,
            source=url, is_live=is_live, note="", feed=False)
    return reader, K, n, h, depth_img, size, is_live, title


def main():
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--url", help="preload a source; otherwise paste one in the page")
    ap.add_argument("--mode", choices=("fixed", "drone"), default="drone")
    ap.add_argument("--recalibrate", action="store_true")
    ap.add_argument("--clip", help="reuse this clip instead of recording")
    ap.add_argument("--calib-seconds", type=int, default=90)
    ap.add_argument("--altitude", type=float, help="known altitude (drone mode)")
    ap.add_argument("--no-stabilize", action="store_true",
                    help="drone mode: measure without camera-shake compensation")
    ap.add_argument("--no-cleanup", action="store_true",
                    help="drone mode: detect on the raw camera frames")
    # yolo11s, not 11m: benchmarked on real footage it runs 2x faster (34 vs 16
    # fps at 1280) *and* finds more vehicles (15 vs 10) -- the larger model is
    # more conservative on distant, low-contrast frames.
    ap.add_argument("--model", help=f"override; default {FIXED_MODEL}, "
                                    f"or {DRONE_MODEL} in drone mode")
    ap.add_argument("--conf", type=float, help="override; default 0.25 fixed / 0.10 drone")
    ap.add_argument("--imgsz", type=int, default=1280)
    ap.add_argument("--device", default="mps")
    ap.add_argument("--port", type=int, default=8000)
    ap.add_argument("--workdir", default="data")
    ap.add_argument("--calibdir", default="calib")
    ap.add_argument("--speed-limit", type=float,
                    help="km/h; vehicles sustained above this are logged to Postgres")
    ap.add_argument("--camera", default="unnamed camera",
                    help="location label stored with each speeding event")
    ap.add_argument("--lat", type=float, help="drone hover latitude (decimal degrees)")
    ap.add_argument("--lon", type=float, help="drone hover longitude (decimal degrees)")
    args = ap.parse_args()
    if (args.lat is None) != (args.lon is None):
        ap.error("--lat and --lon go together")
    os.makedirs(args.workdir, exist_ok=True)
    os.makedirs(args.calibdir, exist_ok=True)

    from ultralytics import YOLO

    # Serve immediately: the page is where a source gets pasted, so it has to be
    # up before there is anything to show.
    server = ThreadingHTTPServer(("127.0.0.1", args.port), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    print(f"\n  ==> http://localhost:{args.port}   (ctrl-c to stop)\n")
    publish(status="waiting for a source", default_url=DEFAULT_URL,
            camera=args.camera, speed_limit=args.speed_limit or 0.0)

    logger = speedlog.SpeedLogger(args.camera, args.speed_limit or 0.0,
                                  enabled=args.speed_limit is not None,
                                  lat=args.lat, lon=args.lon)
    if logger.enabled:
        print(f"  logging vehicles over {args.speed_limit:.0f} km/h "
              f"as camera '{args.camera}'")
        print(f"  guards dispatched to {args.lat:.6f}, {args.lon:.6f}" if args.lat is not None
              else "  no --lat/--lon: events are logged but no guard is notified")
    speedlog.start_sync()                # saved here first, then copied to the cloud

    mode, model = args.mode, None
    reader = K = n = h = depth_img = size = None
    is_live, title = False, ""
    hist, window = defaultdict(list), 24
    stab = cleanup = calm = None
    traffic = sc.TrafficStats()
    fps_ema, last_report, seen_loops, seen_breaks = 0.0, time.time(), 0, 0
    if args.url:
        publish(want_source=args.url)

    try:
        while True:
            with LOCK:
                want_src = STATE.pop("want_source", None)
                want_mode = STATE.pop("want_mode", None)
                STATE["want_source"] = STATE["want_mode"] = None

            if want_src or (want_mode and want_mode != mode):
                url = want_src or STATE.get("source")
                if not url:
                    continue
                if reader:
                    reader.stop()
                if model is not None:
                    del model                # free before the depth model loads
                    model = None
                    if args.device == "mps":
                        import torch
                        torch.mps.empty_cache()
                new_mode = want_mode or mode
                try:
                    (reader, K, n, h, depth_img, size,
                     is_live, title) = load_source(url, args, new_mode)
                    mode = new_mode
                    # Tracker ids restart with the source, so identity must too.
                    logger.new_session()
                except (cal.CalibrationError, Exception) as e:
                    if isinstance(e, KeyboardInterrupt):
                        raise
                    print(f"  failed to load: {e}")
                    # Keep the failed URL as the source so "Try again" can reload it.
                    publish(status=f"failed: {str(e)[:120]}", source=url, jpeg=None, stats={},
                            note=f"Could not load the source: {str(e)[:140]}")
                    reader = None
                weights, conf = model_for(mode, args)
                model = YOLO(weights)
                window = max(sc.MIN_SAMPLES, int(sc.SPEED_WINDOW_S * reader.fps)) \
                    if reader else 24
                hist = defaultdict(lambda: deque(maxlen=window))
                # A hovering drone shakes; measure in one fixed reference view.
                stab = sc.MotionCompensator() if mode == "drone" and not args.no_stabilize else None
                cleanup = cleanup_for(mode, args)
                # display only: averages the static background over time
                calm = sc.TemporalDenoiser() if stab is not None and cleanup is not None else None
                traffic = sc.TrafficStats()     # new scene, new traffic average
                seen_loops = seen_breaks = 0
                continue

            if reader is None:
                time.sleep(0.1)              # idle until a source is chosen
                continue

            frame, stamp, arrived = reader.take()
            if frame is None:
                time.sleep(0.01)
                continue
            raw = frame                      # evidence crops stay unprocessed
            if cleanup is not None:
                frame = cleanup(frame)
            if reader.loops != seen_loops:
                # Video restarted: positions jump discontinuously, so every
                # in-flight trajectory is meaningless. Start them over.
                seen_loops = reader.loops
                hist.clear()
                if stab is not None:
                    stab = sc.MotionCompensator()
                if calm is not None:
                    calm.reset()
                print(f"  video restarted (loop {seen_loops})")
            if reader.breaks != seen_breaks:
                # The stream's timestamps vanished, reset or came back: times
                # before and after are on different clocks.
                seen_breaks = reader.breaks
                hist.clear()
            t0 = time.time()

            r = model.track(frame, imgsz=args.imgsz, conf=conf,
                            device=args.device, tracker="bytetrack.yaml",
                            persist=True, verbose=False)[0]
            boxes, ids, names = sc.vehicles_from(r, model.names)

            calibrated = K is not None
            # Speeds are measured in the stabilised reference view; `boxes`
            # stay in this frame's pixels for drawing and evidence crops.
            mboxes = boxes
            if stab is not None:
                if not stab.update(cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY), boxes):
                    hist.clear()             # view lost: positions not comparable
                    if calm is not None:
                        calm.reset()
                mboxes = stab.apply(boxes) if len(boxes) else boxes
            results = list(sc.measure(K, n, h, mboxes, ids, hist, stamp)) if calibrated else []

            # Log BEFORE anything is drawn. Evidence crops are cut from this
            # frame, and an overlay baked into one shows the previous vehicle's
            # label over the offender -- a crop captioned "104 km/h" with a
            # stale "78 km/h" burnt into the corner discredits the evidence.
            for i, tid, state, kmh, dist in results:
                traffic.add(stamp, state, kmh)
                logger.observe(stamp, tid, state, kmh,
                               sc.LABEL_MAP.get(names[i], names[i].title()),
                               raw, boxes[i], view=frame if cleanup is not None else None)

            # Everything measured and logged is done: from here on the frame is
            # only for display, so the background can be averaged over time.
            if calm is not None:
                frame = calm.apply(frame, stab.M, boxes)

            with LOCK:
                show_depth = STATE["overlay"]
            if show_depth and depth_img is not None:
                # Blend before drawing, so boxes and labels stay crisp on top of
                # the overlay rather than being washed out by it.
                if depth_img.shape[:2] != frame.shape[:2]:
                    depth_img = cv2.resize(depth_img, (frame.shape[1], frame.shape[0]))
                frame = cv2.addWeighted(frame, 0.55, depth_img, 0.45, 0)

            # Small feeds (the drone's is 640x352) are drawn at 2x so labels stay
            # crisp when the browser scales the video up to fill the screen.
            up = 2 if frame.shape[0] < 600 else 1
            if up > 1:
                frame = cv2.resize(frame, None, fx=up, fy=up, interpolation=cv2.INTER_LINEAR)
            for i, tid, state, kmh, dist in results:
                sc.draw_vehicle(frame, boxes[i] * up, tid, names[i], state, kmh, dist,
                                limit=logger.limit or None)
            if not calibrated:            # preview: detections only, no speeds
                for b, tid, nm in zip(boxes, ids, names):
                    sc.draw_label(frame, b * up, f"{sc.LABEL_MAP.get(nm, nm.title())}  #{tid}",
                                  (155, 155, 155))

            dt = time.time() - t0
            fps_ema = 0.9 * fps_ema + 0.1 / dt if fps_ema else 1.0 / dt
            ok, buf = cv2.imencode(".jpg", frame, [cv2.IMWRITE_JPEG_QUALITY, 80])
            if ok:
                publish(jpeg=buf.tobytes(), stats={
                    "fps": fps_ema, "tracked": int(len(boxes)),
                    "latency_ms": int((time.time() - arrived) * 1000) if is_live and arrived else 0,
                    "clock": ("camera" if reader.clock.using_camera else "arrival") if is_live else None,
                    "jitter_ms": round(reader.clock.jitter_ms, 1)
                                 if is_live and reader.clock.jitter_ms is not None else None,
                    "live": is_live, "position_s": None if is_live else round(stamp, 1),
                    "height": h, "focal": K[0] if calibrated else None, "title": title,
                    "tilt": float(np.degrees(np.arcsin(-n[2]))) if calibrated else None,
                    "traffic": traffic.summary(stamp),
                    "logged": logger.written, "log_on": logger.enabled,
                    "log_failed": logger.failed,
                    "speed_limit": logger.limit,
                    "stab_px": round(stab.offset_px, 1) if stab is not None else None,
                    "cleanup": cleanup is not None,
                })
            if time.time() - last_report > 15:
                print(f"  {fps_ema:5.1f} fps | {len(boxes):3d} vehicles | "
                      f"{reader.dropped} dropped")
                last_report = time.time()
    except KeyboardInterrupt:
        print("\nstopping")
    finally:
        if reader:
            reader.stop()
        logger.close()
        if logger.enabled:
            left = speedlog.pending()
            print(f"  logged {logger.written} speeding events"
                  + (f", {logger.failed} failed" if logger.failed else "")
                  + (f", {logger.dropped} dropped" if logger.dropped else "")
                  + (f", {left} not in the cloud yet (sent on the next start)" if left else ""))
        server.shutdown()


if __name__ == "__main__":
    main()
