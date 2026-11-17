"""Browser-based bay annotator with a local save-back server.

Marking ~80 parking bays by hand wants a real drawing surface - drag a rectangle,
zoom in, nudge a corner - which a click-per-corner OpenCV window does not give.
This serves that surface as a web page and, when you hit Save, writes the exact
lots/<code>.json the pipeline reads and (unless --no-db) pushes the lot to
Supabase. The frame you annotate becomes the lot's permanent reference image.

    python annotate_server.py --source istock.mp4 --lot-code ISTOCK --lot-name "Stock lot"
    python annotate_server.py --source "camera:AV TO USB2.0" --lot-code PARKING-A
    # then open the printed URL, draw the bays, set the scale, Save.

If lots/<code>.json already exists it is preloaded as an editable starting point,
so you can refine rather than redraw. Saving over an existing DB lot needs the
"replace on server" checkbox (it deletes and re-inserts the bays, which cascades
to their event history).
"""

from __future__ import annotations

import argparse
import json
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import cv2
import numpy as np
import yaml

import annotate  # reuse push() so the browser and CLI tools stay identical
from camera import PREFIX, Camera, is_blank
from occupancy import GROUND_PX_PER_M, ground_homography, to_ground


def grab_camera_frame(name: str, camera_cfg: dict | None = None,
                      settle_s: float = 2.0, timeout_s: float = 30.0):
    """A clear frame from the live camera: past the dongle's first frames (and
    the denoiser's warm-up), and not a black one from the transmitter being
    switched off. Same clean-up filters as the live feed, so the reference and
    the frames registered against it look alike."""
    cfg = camera_cfg or {}
    cam = Camera(name, cfg.get("size", "720x480"), cfg.get("fps", 25), cfg.get("filters"))
    start, frame = time.time(), None
    try:
        while time.time() - start < timeout_s:
            ok, f = cam.read()
            if ok and not is_blank(f) and time.time() - start >= settle_s:
                frame = f
                break
    finally:
        cam.release()
    if frame is None:
        raise SystemExit(f"no picture from {name!r} in {timeout_s:.0f}s - is the camera on?")
    return frame


def load_reference(source: str, frame_no: int, camera_cfg: dict | None = None):
    if source.startswith(PREFIX):
        return grab_camera_frame(source[len(PREFIX):], camera_cfg)
    cap = cv2.VideoCapture(source)
    if not cap.isOpened():
        raise SystemExit(f"cannot open source {source!r}")
    if frame_no:
        cap.set(cv2.CAP_PROP_POS_FRAMES, frame_no)
    ok, frame = cap.read()
    cap.release()
    if not ok:
        raise SystemExit(f"could not read frame {frame_no} from {source!r}")
    return frame


def build_page(html: str, *, lot_code, w, h, preload, name=None,
               can_capture=False, has_frame=True) -> bytes:
    """The marking page. can_capture adds Retake / Done for the dashboard's
    calibration screen; has_frame=False makes it grab a first frame on load."""
    out = (
        html.replace("__LOTCODE__", lot_code)
        .replace("__IMG_W__", str(w))
        .replace("__IMG_H__", str(h))
        .replace("__PRELOAD__", json.dumps(preload) if preload else "null")
        .replace("__LOTNAME__", json.dumps(name or (preload or {}).get("name") or lot_code))
        .replace("__CAN_CAPTURE__", json.dumps(can_capture))
        .replace("__HAS_FRAME__", json.dumps(has_frame))
    )
    return out.encode("utf-8")


def save_lot(payload: dict, frame, *, code: str, name: str | None = None,
             no_db: bool = False) -> dict:
    """Persist the marked geometry. Bays arrive as four [x, y] image points each.

    With a ground rectangle (angled camera) each bay also gets its outline on the
    top-down ground plane, which is what the app draws as the bay map."""
    ground = payload.get("ground")
    H = ground_homography(ground)
    slots = []
    for i, quad in enumerate(payload["bays"]):
        slot = {"code": f"{code}-{i + 1:03d}", "polygon": quad}
        if H is not None:
            slot["map_polygon"] = to_ground(H, quad).round(1).tolist()
        slots.append(slot)
    lot = {
        "code": code,
        "name": (payload.get("name") or name or code).strip(),
        # On the ground plane the scale is fixed; otherwise the measured one.
        "meters_per_pixel": 1 / GROUND_PX_PER_M if H is not None else float(payload["mpp"]),
        "ground": ground,
        "boundary": payload.get("boundary") or [],
        "slots": slots,
    }

    Path("lots").mkdir(exist_ok=True)
    cv2.imwrite(f"lots/{code}.jpg", frame)
    Path(f"lots/{code}.json").write_text(json.dumps(lot, indent=2))

    msg = f"saved {len(slots)} bays to lots/{code}.json"
    if not no_db:
        try:
            lot["id"] = annotate.push(lot, frame, replace=bool(payload.get("replace")))
            Path(f"lots/{code}.json").write_text(json.dumps(lot, indent=2))
            msg += f" and pushed to Supabase (lot {lot['id']})"
        except SystemExit as exc:  # push refuses to clobber existing bays without replace
            return {"ok": False, "message": str(exc).strip()}
        except Exception as exc:  # noqa: BLE001 - report, don't kill the server
            return {"ok": False, "message": f"saved locally; Supabase push failed: {exc}"}
    return {"ok": True, "message": msg}


def make_handler(page: bytes, png: bytes, frame, args):
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *a):  # keep the console quiet
            pass

        def _send(self, code, body, ctype):
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self):
            if self.path in ("/", "/index.html"):
                self._send(200, page, "text/html; charset=utf-8")
            elif self.path.startswith("/frame"):
                self._send(200, png, "image/png")
            else:
                self._send(404, b"not found", "text/plain")

        def do_POST(self):
            if self.path != "/save":
                return self._send(404, b"not found", "text/plain")
            n = int(self.headers.get("Content-Length", 0))
            payload = json.loads(self.rfile.read(n) or b"{}")
            result = save_lot(payload, frame, code=args.lot_code, name=args.lot_name,
                              no_db=args.no_db)
            body = json.dumps(result).encode()
            self._send(200, body, "application/json")
            print(f"[save] {result['message']}")

    return Handler


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", required=True)
    parser.add_argument("--lot-code", required=True)
    parser.add_argument("--lot-name")
    parser.add_argument("--frame", type=int, default=0)
    parser.add_argument("--port", type=int, default=8081)   # 8000 is the speed dashboard
    parser.add_argument("--no-db", action="store_true", help="save locally only")
    args = parser.parse_args()

    cfg_path = Path("config.yaml")
    camera_cfg = (yaml.safe_load(cfg_path.read_text()) or {}).get("camera") if cfg_path.exists() else None
    frame = load_reference(args.source, args.frame, camera_cfg)
    h, w = frame.shape[:2]
    ok, buf = cv2.imencode(".png", frame)
    png = buf.tobytes()

    existing = Path(f"lots/{args.lot_code}.json")
    preload = json.loads(existing.read_text()) if existing.exists() else None

    html = Path("annotate_page.html").read_text()
    page = build_page(html, lot_code=args.lot_code, w=w, h=h, preload=preload, name=args.lot_name)

    handler = make_handler(page, png, frame, args)
    server = ThreadingHTTPServer(("127.0.0.1", args.port), handler)
    url = f"http://127.0.0.1:{args.port}/"
    print(f"[annotate] reference frame {args.frame} of {args.source} ({w}x{h})")
    print(f"[annotate] {'preloaded '+str(len(preload['slots']))+' existing bays' if preload else 'blank canvas'}")
    print(f"[annotate] open  {url}")
    print("[annotate] draw bays, set scale, Save. Ctrl-C here when done.")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n[annotate] stopped")


if __name__ == "__main__":
    main()
