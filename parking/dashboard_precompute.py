"""Precompute a per-frame occupancy timeline for the dashboard.

The dashboard plays the video in a loop and needs each shown frame's occupancy
to line up with it exactly. Running YOLO live in the browser is impossible and
running it live in a server is heavy and non-deterministic, so we do it once here
and record the result. The dashboard then just plays the video and looks up the
recorded state - perfectly synced, loops cleanly, no inference at view time.

This reuses the real pipeline pieces (Stabilizer, OccupancyTracker, ByteTrack,
YOLO), so the recorded occupancy is what the system actually decides, not a
separate re-implementation. Two demo-oriented differences from a live run:

  * a short park window, so a car visible from the first frame is called
    "parked" in a couple of seconds rather than ten;
  * a warm-up back-fill, so the very start of the loop already shows the steady
    state instead of filling in from empty on every repeat.

    python dashboard_precompute.py            # -> occupancy_timeline.json
"""

from __future__ import annotations

import argparse
import json
import warnings
from datetime import datetime, timedelta
from pathlib import Path

import cv2
import numpy as np
import yaml

warnings.filterwarnings("ignore", category=FutureWarning)
warnings.filterwarnings("ignore", category=RuntimeWarning, module=r"trackers\.utils\.kalman_filter")

from occupancy import Observation, OccupancyTracker, ground_homography
from pipeline import NORMALISED, to_observations, vehicle_class_ids
from stabilize import Stabilizer, warp_boxes


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--config", default="config.yaml")
    ap.add_argument("--lot-file", default="lots/ISTOCK.json")
    ap.add_argument("--reference", default="lots/ISTOCK.jpg")
    ap.add_argument("--source", default="/Users/anmol117/Downloads/istockphoto-1359195980-640_adpp_is.mp4")
    ap.add_argument("--out", default="occupancy_timeline.json")
    ap.add_argument("--park-window", type=float, default=2.0, help="seconds still => parked (short for demo)")
    ap.add_argument("--debounce", type=int, default=2)
    ap.add_argument("--warmup", type=float, default=3.0, help="back-fill the first N seconds to steady state")
    ap.add_argument("--vehicle-log", default="data/cv_vehicle_log.json")
    ap.add_argument("--session-start", default="2026-08-19T09:15:00+00:00",
                    help="wall-clock anchor for entry/exit timestamps")
    args = ap.parse_args()

    cfg = yaml.safe_load(Path(args.config).read_text())
    det = cfg["detection"]

    lot = json.loads(Path(args.lot_file).read_text())
    slots = lot["slots"]
    codes = [s["code"] for s in slots]
    polys = [np.asarray(s["polygon"], dtype=np.float32) for s in slots]
    rects = [[float(p[:, 0].min()), float(p[:, 1].min()),
              float(p[:, 0].max()), float(p[:, 1].max())] for p in polys]
    idx_of = {c: i for i, c in enumerate(codes)}

    reference = cv2.imread(args.reference)
    if reference is None:
        raise SystemExit(f"reference {args.reference!r} not found")

    cap = cv2.VideoCapture(args.source)
    if not cap.isOpened():
        raise SystemExit(f"cannot open {args.source!r}")
    fps = cap.get(cv2.CAP_PROP_FPS) or 24.0
    total = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))

    from trackers import ByteTrackTracker
    from ultralytics import YOLO

    model = YOLO(det["weights"])
    vehicle_ids = vehicle_class_ids(model.names)
    stab = Stabilizer(reference, **cfg["registration"])
    tracker = ByteTrackTracker(frame_rate=fps / max(1, det["frame_skip"]), **cfg["tracking"])
    occ = OccupancyTracker(
        [(s["id"] if "id" in s else s["code"], np.asarray(s["polygon"], dtype=np.float32)) for s in slots],
        lot["meters_per_pixel"],
        lot_boundary=np.asarray(lot.get("boundary") or [], dtype=np.float32),
        anchor="centroid",  # the recorded clip looks straight down; config is set for the angled camera
        park_window_s=args.park_window,
        park_threshold_m=cfg["occupancy"]["park_threshold_m"],
        slot_tolerance_m=cfg["occupancy"]["slot_tolerance_m"],
        debounce_k=args.debounce,
        track_ttl_s=cfg["occupancy"]["track_ttl_s"],
        ground_H=ground_homography(lot.get("ground")),
    )

    samples: list[list] = []   # [frameIdx, [occupied slot indices]]
    prev_key = None
    prev_boxes = np.empty((0, 4), dtype=np.float32)
    frame_idx = 0
    t0 = 0.0
    stale = 0
    open_stays: dict = {}   # slot_code -> stay opened by an 'occupied' event
    finished: list = []     # stays closed by a 'vacated' event

    print(f"[precompute] {args.source} ({total} frames @ {fps:.2f}) x {len(slots)} bays")
    while True:
        ok, frame = cap.read()
        if not ok:
            break
        frame_idx += 1
        if frame_idx % det["frame_skip"]:
            continue
        now = t0 + frame_idx / fps

        reg = stab.register(frame, prev_boxes)
        stale += 0 if reg.ok else 1
        r = model.predict(frame, imgsz=det["imgsz"], conf=det["conf"], device=det["device"], verbose=False)[0]
        b = r.boxes.xyxy.cpu().numpy()
        c = r.boxes.cls.cpu().numpy().astype(int)
        s = r.boxes.conf.cpu().numpy()
        keep = np.array([x in vehicle_ids for x in c], dtype=bool)
        b, c, s = b[keep], c[keep], s[keep]
        prev_boxes = b

        obs = []
        if len(b):
            import supervision as sv
            det_sv = sv.Detections(xyxy=warp_boxes(reg.H, b).astype(np.float32),
                                   confidence=s.astype(np.float32), class_id=c)
            obs = to_observations(tracker.update(det_sv), model.names)
        events, _ = occ.update(obs, now)
        for ev in events:   # occupied = a vehicle entered a bay; vacated = it left
            if ev.event == "occupied":
                open_stays[ev.slot_id] = {"track_id": ev.track_id, "vehicle_type": ev.vehicle_type,
                                          "slot_code": ev.slot_id, "entry_frame": frame_idx}
            else:
                st = open_stays.pop(ev.slot_id, None)
                if st:
                    st["exit_frame"] = frame_idx
                    finished.append(st)

        occupied = sorted(idx_of[cid] for cid, st in
                          zip(codes, occ.status().values()) if st.occupied)
        key = tuple(occupied)
        if key != prev_key:               # store only on change - keeps the file tiny
            samples.append([frame_idx, occupied])
            prev_key = key

        if frame_idx % 200 == 0:
            print(f"  frame {frame_idx}/{total}  occupied={len(occupied)}")
    cap.release()

    # Warm-up back-fill: replace everything in the first `warmup` seconds with the
    # state as it stands at `warmup`, so the loop starts already filled.
    warm_frame = int(args.warmup * fps)
    baseline = []
    for f, occd in samples:
        if f <= warm_frame:
            baseline = occd
        else:
            break
    samples = [[0, baseline]] + [s for s in samples if s[0] > warm_frame]

    timeline = {
        "video": Path(args.source).name,
        "fps": fps,
        "frame_skip": det["frame_skip"],
        "width": reference.shape[1],
        "height": reference.shape[0],
        "capacity": len(slots),
        "codes": codes,
        "rects": rects,
        "samples": samples,
    }
    Path(args.out).write_text(json.dumps(timeline))
    processed = frame_idx // det["frame_skip"]
    print(f"[precompute] {len(samples)} state changes over {processed} frames "
          f"-> {args.out} ({Path(args.out).stat().st_size/1024:.0f} KB)")
    print(f"[precompute] registration stale {100*stale/max(1,processed):.1f}%  "
          f"baseline occupied={len(baseline)}/{len(slots)}")

    # --- vehicle log: one row per observed parking stay (entry = parked, exit = left) ---
    base = datetime.fromisoformat(args.session_start)
    end_frame = frame_idx
    ts = lambda f: (base + timedelta(seconds=f / fps)).isoformat()
    log = []
    for st in finished:                       # departed during the clip
        ef, xf = st["entry_frame"], st["exit_frame"]
        log.append({"track_id": st["track_id"], "vehicle_type": st["vehicle_type"],
                    "slot_code": st["slot_code"], "entry_at": ts(ef), "exit_at": ts(xf),
                    "dwell_seconds": round((xf - ef) / fps, 1), "status": "departed"})
    for st in open_stays.values():            # still parked at end of the clip
        ef = st["entry_frame"]
        log.append({"track_id": st["track_id"], "vehicle_type": st["vehicle_type"],
                    "slot_code": st["slot_code"], "entry_at": ts(ef), "exit_at": None,
                    "dwell_seconds": round((end_frame - ef) / fps, 1), "status": "parked"})
    log.sort(key=lambda r: r["exit_at"] or r["entry_at"], reverse=True)

    Path(args.vehicle_log).parent.mkdir(exist_ok=True)
    Path(args.vehicle_log).write_text(json.dumps(
        {"generated_at": ts(0), "source": "cv", "session_start": args.session_start,
         "count": len(log), "logs": log}, indent=1))
    departed = sum(1 for r in log if r["status"] == "departed")
    print(f"[precompute] vehicle log: {len(log)} stays ({departed} departed, "
          f"{len(log)-departed} still parked) -> {args.vehicle_log}")


if __name__ == "__main__":
    main()
