"""One-time lot setup: capture a reference frame and draw the bays on it.

Everything the pipeline knows about a lot's geometry starts here. The frame you
capture becomes that lot's permanent coordinate system - every polygon, and every
homography the pipeline later computes, is expressed relative to it. Capture it
on a clear day at the exact waypoint the drone will hold.

    python annotate.py --source drone_video.mp4 --lot-code LOT-A --lot-name "North Lot"

Stages: lot boundary -> parking bays (4 clicks each) -> scale calibration.
Keys: left click add - right click / u undo - ENTER next stage - ESC abort.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import cv2
import numpy as np

MAX_DISPLAY_W = 1400  # 4K drone frames do not fit on a laptop screen


class PolygonPicker:
    """Click points on a possibly-downscaled view, collect full-resolution coords."""

    def __init__(self, image, title="annotate"):
        self.image = image
        self.title = title
        self.scale = min(1.0, MAX_DISPLAY_W / image.shape[1])
        self.points: list[tuple[float, float]] = []
        self.done: list[list[tuple[float, float]]] = []
        cv2.namedWindow(title)
        cv2.setMouseCallback(title, self._on_mouse)

    def _on_mouse(self, event, x, y, flags, _param):
        if event == cv2.EVENT_LBUTTONDOWN:
            self.points.append((x / self.scale, y / self.scale))
        elif event == cv2.EVENT_RBUTTONDOWN:
            self._undo()

    def _undo(self):
        if self.points:
            self.points.pop()
        elif self.done:
            self.points = self.done.pop()

    def _render(self, banner):
        view = self.image.copy()
        for poly in self.done:
            pts = np.array(poly, dtype=np.int32).reshape(-1, 1, 2)
            cv2.polylines(view, [pts], True, (0, 200, 0), 2)
        if self.points:
            pts = np.array(self.points, dtype=np.int32).reshape(-1, 1, 2)
            cv2.polylines(view, [pts], False, (0, 200, 255), 2)
            for px, py in self.points:
                cv2.circle(view, (int(px), int(py)), 4, (0, 200, 255), -1)

        view = cv2.resize(view, None, fx=self.scale, fy=self.scale) if self.scale < 1 else view
        cv2.rectangle(view, (0, 0), (view.shape[1], 30), (0, 0, 0), -1)
        cv2.putText(view, banner, (8, 21), cv2.FONT_HERSHEY_SIMPLEX, 0.6, (255, 255, 255), 1)
        cv2.imshow(self.title, view)

    def collect(self, banner, *, close_at=None):
        """Run the click loop. close_at=N auto-finishes each polygon at N points."""
        while True:
            self._render(f"{banner}  [{len(self.done)} done]")
            key = cv2.waitKey(20) & 0xFF

            if close_at and len(self.points) == close_at:
                self.done.append(self.points)
                self.points = []
            if key == 27:
                raise KeyboardInterrupt("aborted")
            if key in (13, 10):  # ENTER
                if self.points and not close_at:
                    self.done.append(self.points)
                    self.points = []
                    continue
                return self.done
            if key == ord("u"):
                self._undo()


def grab_reference(source: str, frame_no: int = 0):
    """Freeze one frame to serve as the lot's canonical view."""
    cap = cv2.VideoCapture(source)
    if not cap.isOpened():
        raise RuntimeError(f"cannot open source {source!r}")
    if frame_no:
        cap.set(cv2.CAP_PROP_POS_FRAMES, frame_no)

    frame = None
    while True:
        ok, current = cap.read()
        if not ok:
            break
        frame = current
        scale = min(1.0, MAX_DISPLAY_W / frame.shape[1])
        view = cv2.resize(frame, None, fx=scale, fy=scale) if scale < 1 else frame.copy()
        cv2.putText(
            view, "SPACE = use this frame, ESC = abort", (10, 26),
            cv2.FONT_HERSHEY_SIMPLEX, 0.7, (0, 255, 255), 2,
        )
        cv2.imshow("reference", view)
        key = cv2.waitKey(30) & 0xFF
        if key == 32:
            break
        if key == 27:
            cap.release()
            cv2.destroyAllWindows()
            raise KeyboardInterrupt("aborted")

    cap.release()
    cv2.destroyWindow("reference")
    if frame is None:
        raise RuntimeError("no frames read from source")
    return frame


def calibrate_scale(image) -> float:
    """Metres per pixel from one known real-world distance.

    This replaces the hardcoded METERS_PER_PIXEL = 0.05 in the speed-tracking
    prototype. Pick something you can actually measure - a bay's length, or the
    gap between two lamp posts.
    """
    picker = PolygonPicker(image, "calibrate")
    pts = picker.collect("Click TWO points a known distance apart, then ENTER", close_at=2)
    cv2.destroyWindow("calibrate")
    if not pts:
        raise RuntimeError("scale calibration needs two points")

    (x1, y1), (x2, y2) = pts[0]
    pixels = float(np.hypot(x2 - x1, y2 - y1))
    metres = float(input(f"Real-world distance between those points in metres ({pixels:.1f} px): "))
    if metres <= 0:
        raise ValueError("distance must be positive")
    return metres / pixels


def push(lot, reference, *, replace: bool):
    import db

    client = db.connect()

    existing = client.table("parking_lots").select("id").eq("code", lot["code"]).execute().data
    if existing:
        lot_id = existing[0]["id"]
        bays = (
            client.table("parking_slots").select("id", count="exact")
            .eq("lot_id", lot_id).execute()
        )
        if bays.data and not replace:
            raise SystemExit(
                f"{lot['code']} already has {len(bays.data)} saved bays. Saving deletes them "
                "along with their event history - tick 'Replace saved bays' (or pass "
                "--replace) if that is what you want."
            )
        if bays.data:
            client.table("parking_slots").delete().eq("lot_id", lot_id).execute()
    else:
        lot_id = None

    ok, buf = cv2.imencode(".jpg", reference, [cv2.IMWRITE_JPEG_QUALITY, 92])
    ref_path = f"{lot['code']}/reference.jpg"
    if ok:
        db.upload_frame(client, ref_path, buf.tobytes())

    row = {
        "code": lot["code"],
        "name": lot["name"],
        "reference_image_path": ref_path,
        "meters_per_pixel": lot["meters_per_pixel"],
        "boundary": lot["boundary"],
        "ground": lot.get("ground"),
    }
    if lot_id:
        client.table("parking_lots").update(row).eq("id", lot_id).execute()
    else:
        lot_id = client.table("parking_lots").insert(row).execute().data[0]["id"]

    slots = [
        {"lot_id": lot_id, "code": s["code"], "polygon": s["polygon"],
         "map_polygon": s.get("map_polygon")}
        for s in lot["slots"]
    ]
    client.table("parking_slots").insert(slots).execute()
    print(f"[annotate] pushed lot {lot['code']} ({lot_id}) with {len(slots)} bays")
    return lot_id


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", required=True)
    parser.add_argument("--lot-code", required=True)
    parser.add_argument("--lot-name")
    parser.add_argument("--frame", type=int, default=0, help="seek to this frame (files only)")
    parser.add_argument("--no-db", action="store_true", help="save locally only")
    parser.add_argument("--replace", action="store_true", help="delete existing bays for this lot")
    args = parser.parse_args()

    reference = grab_reference(args.source, args.frame)

    picker = PolygonPicker(reference, "boundary")
    boundary = picker.collect("Click the LOT BOUNDARY, then ENTER")
    cv2.destroyWindow("boundary")

    picker = PolygonPicker(reference, "bays")
    bays = picker.collect("Click each BAY's 4 corners (auto-closes), ENTER when done", close_at=4)
    cv2.destroyWindow("bays")
    if not bays:
        raise SystemExit("no bays annotated - nothing to do")

    mpp = calibrate_scale(reference)
    cv2.destroyAllWindows()

    lot = {
        "code": args.lot_code,
        "name": args.lot_name or args.lot_code,
        "meters_per_pixel": mpp,
        "boundary": [[round(x, 1), round(y, 1)] for x, y in (boundary[0] if boundary else [])],
        "slots": [
            {"code": f"{args.lot_code}-{i + 1:03d}", "polygon": [[round(x, 1), round(y, 1)] for x, y in poly]}
            for i, poly in enumerate(bays)
        ],
    }

    Path("lots").mkdir(exist_ok=True)
    cv2.imwrite(f"lots/{args.lot_code}.jpg", reference)
    Path(f"lots/{args.lot_code}.json").write_text(json.dumps(lot, indent=2))
    print(
        f"[annotate] {len(lot['slots'])} bays, {mpp:.4f} m/px "
        f"-> lots/{args.lot_code}.jpg + lots/{args.lot_code}.json"
    )

    if not args.no_db:
        lot["id"] = push(lot, reference, replace=args.replace)
        Path(f"lots/{args.lot_code}.json").write_text(json.dumps(lot, indent=2))


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        cv2.destroyAllWindows()
        print("\naborted, nothing saved")
