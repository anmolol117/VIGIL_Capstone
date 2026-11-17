"""Drone parking occupancy pipeline: register -> detect -> warp -> track -> count.

The ordering matters. A hovering drone drifts, so detections are warped onto the
lot's reference image *before* tracking. In that stabilized frame the camera is
effectively static, which is what lets ByteTrack's constant-velocity model work
and makes a parked car register as genuinely motionless.

    python pipeline.py --dry-run --show          # tune against recorded footage
    python pipeline.py                           # live, writing to Supabase

The source is a video file, an rtsp/rtmp URL, or "camera:<device name>" for the
analog camera's USB capture dongle (see camera.py). A black camera feed means the
transmitter is off, not that the lot emptied: bays hold their last state, nothing
is written, and after the feed returns bays stay held until parked cars have been
watched long enough to read as parked again.

Each processed frame, stabilized onto the reference, and the bay states are also
published to live/ for the dashboard (dashboard_server.py --live live).

Environment: SUPABASE_URL, SUPABASE_SERVICE_KEY (service_role - RLS has no policies).
"""

from __future__ import annotations

import argparse
import json
import os
import signal
import time
import warnings
from pathlib import Path

import cv2
import numpy as np
import yaml

warnings.filterwarnings("ignore", category=FutureWarning)
# The trackers Kalman implementation emits divide-by-zero/overflow RuntimeWarnings
# while a fresh tracklet's covariance settles over its first few updates. Checked
# across 600 frames of real footage: no tracked box ever comes back non-finite.
# Scoped to that one module so genuine numerical trouble elsewhere still surfaces.
warnings.filterwarnings(
    "ignore", category=RuntimeWarning, module=r"trackers\.utils\.kalman_filter"
)

import db
from camera import is_blank, open_source
from occupancy import Observation, OccupancyTracker, ground_homography
from stabilize import Stabilizer, warp_boxes

# Vehicle classes are picked by name, so the drone-trained VisDrone weights and
# a standard COCO model (often better for a wall or pole camera) both just work.
VEHICLE_NAMES = {"car", "van", "truck", "bus", "motor", "motorcycle"}


def vehicle_class_ids(names: dict) -> set[int]:
    return {i for i, n in names.items() if n in VEHICLE_NAMES}

# Normalised to the vocabulary public.vehicles already uses. A van is folded into
# 'car' because it occupies a car bay; raw_class keeps the original label so the
# call stays reversible.
NORMALISED = {"car": "car", "van": "car", "truck": "truck", "bus": "bus",
              "motor": "motorcycle", "motorcycle": "motorcycle"}


def to_observations(tracked, names) -> list[Observation]:
    """Build observations from tracked detections, dropping unconfirmed tracks.

    ByteTrackTracker marks unconfirmed tracks with -1, not None: tracker_id is a
    numpy int array and cannot hold None. An `is None` guard reads as correct here
    and silently funnels every unconfirmed detection into a single phantom track
    that teleports around the lot and can claim a bay.
    """
    observations = []
    for i, tid in enumerate(tracked.tracker_id):
        if tid < 0:
            continue
        label = names[int(tracked.class_id[i])]
        observations.append(
            Observation(
                track_id=int(tid),
                xyxy=tuple(float(v) for v in tracked.xyxy[i]),
                vehicle_type=NORMALISED.get(label, "other"),
                raw_class=label,
            )
        )
    return observations


def load_lot(args, cfg):
    """Lot geometry from a local JSON file, or from Supabase as the source of truth."""
    if args.lot_file:
        raw = json.loads(Path(args.lot_file).read_text())
        raw["boundary"] = np.asarray(raw.get("boundary") or [], dtype=np.float32)
        # Offline there are no database uuids yet, so bays are keyed by their code.
        raw["id"] = raw.get("id") or raw["code"]
        for slot in raw["slots"]:
            slot["polygon"] = np.asarray(slot["polygon"], dtype=np.float32)
            slot["id"] = slot.get("id") or slot["code"]
        return raw, None

    client = db.connect()
    lot = db.load_lot(client, cfg["lot_code"])
    # The bays are drawn on lots/<code>.jpg. If the marker saved them locally but
    # the database push was refused, the database still holds an older layout
    # that would be laid over this camera's picture and quietly count nonsense.
    local = Path(f"lots/{lot['code']}.json")
    if local.exists():
        marked = {s["code"]: s["polygon"] for s in json.loads(local.read_text())["slots"]}
        stored = {s["code"]: s["polygon"].tolist() for s in lot["slots"]}
        if marked.keys() != stored.keys() or any(
            not np.allclose(marked[c], stored[c], atol=0.5) for c in marked
        ):
            raise SystemExit(
                f"lots/{local.name} and the database disagree on {lot['code']}'s bays. "
                "Open the bay marker, tick 'replace on server' and Save again."
            )
    return lot, client


def evidence_jpg(frame, H_inv, polygon, pad=40):
    """Crop the bay's neighbourhood out of the raw frame for an event record."""
    pts = cv2.perspectiveTransform(polygon.reshape(-1, 1, 2).astype(np.float32), H_inv).reshape(-1, 2)
    h, w = frame.shape[:2]
    x1 = max(0, int(pts[:, 0].min()) - pad)
    y1 = max(0, int(pts[:, 1].min()) - pad)
    x2 = min(w, int(pts[:, 0].max()) + pad)
    y2 = min(h, int(pts[:, 1].max()) + pad)
    if x2 <= x1 or y2 <= y1:
        return None
    ok, buf = cv2.imencode(".jpg", frame[y1:y2, x1:x2], [cv2.IMWRITE_JPEG_QUALITY, 80])
    return buf.tobytes() if ok else None


class LivePublisher:
    """Latest frame + bay states as files the dashboard serves. Written with an
    atomic rename so the server never reads a half-written frame."""

    def __init__(self, live_dir: str, lot: dict, ref_size: tuple[int, int]) -> None:
        self.dir = Path(live_dir)
        self.dir.mkdir(exist_ok=True)
        self.ids = [s["id"] for s in lot["slots"]]
        self.static = {
            "name": lot.get("name") or lot["code"],
            "code": lot["code"],
            "width": ref_size[0],
            "height": ref_size[1],
            "capacity": len(lot["slots"]),
            "codes": [s["code"] for s in lot["slots"]],
            "rects": [[float(v) for v in (*s["polygon"].min(axis=0), *s["polygon"].max(axis=0))]
                      for s in lot["slots"]],
            "polys": [s["polygon"].round(1).tolist() for s in lot["slots"]],
        }

    def _write(self, name: str, data: bytes) -> None:
        tmp = self.dir / f".{name}"
        tmp.write_bytes(data)
        os.replace(tmp, self.dir / name)

    def publish(self, view, occ, *, signal: bool, held: bool, raw=None) -> None:
        """view is the stabilized frame the dashboard shows. raw is the camera's
        own frame, kept so Calibrate can take the latest one as the reference."""
        ok, buf = cv2.imencode(".jpg", view, [cv2.IMWRITE_JPEG_QUALITY, 80])
        if ok:
            self._write("frame.jpg", buf.tobytes())
        if raw is not None:
            ok, buf = cv2.imencode(".jpg", raw, [cv2.IMWRITE_JPEG_QUALITY, 92])
            if ok:
                self._write("raw.jpg", buf.tobytes())
        status = occ.status()
        state = {**self.static, "at": time.time(), "signal": signal, "held": held,
                 "occupied": [i for i, sid in enumerate(self.ids) if status[sid].occupied]}
        self._write("state.json", json.dumps(state).encode())


def draw_overlay(frame, H_inv, lot, occ, tracked_boxes, reg):
    """Bays and tracks projected back into the live view, for tuning by eye."""
    status = occ.status()
    for slot in lot["slots"]:
        pts = cv2.perspectiveTransform(
            slot["polygon"].reshape(-1, 1, 2).astype(np.float32), H_inv
        ).astype(np.int32)
        occupied = status[slot["id"]].occupied
        cv2.polylines(frame, [pts], True, (0, 0, 255) if occupied else (0, 200, 0), 2)

    for tid, box in tracked_boxes:
        x1, y1, x2, y2 = map(int, box)
        cv2.rectangle(frame, (x1, y1), (x2, y2), (255, 200, 0), 2)
        cv2.putText(frame, str(tid), (x1, y1 - 6), cv2.FONT_HERSHEY_SIMPLEX, 0.5, (255, 200, 0), 2)

    colour = (0, 200, 0) if reg.ok else (0, 0, 255)
    cv2.putText(
        frame,
        f"reg {'OK' if reg.ok else 'STALE'} inliers={reg.inliers} ratio={reg.inlier_ratio:.2f}",
        (10, 26),
        cv2.FONT_HERSHEY_SIMPLEX,
        0.7,
        colour,
        2,
    )
    return frame


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", default="config.yaml")
    parser.add_argument("--source", help="override config source (file or rtsp/rtmp url)")
    parser.add_argument("--lot-file", help="read lot geometry from local JSON instead of Supabase")
    parser.add_argument("--reference", help="reference image (default lots/<lot_code>.jpg)")
    parser.add_argument("--dry-run", action="store_true", help="process but write nothing")
    parser.add_argument("--show", action="store_true", help="render the overlay window")
    parser.add_argument("--live-dir", default="live", help="where the dashboard's live view is published")
    parser.add_argument(
        "--timebase",
        choices=["auto", "media", "wall"],
        default="auto",
        help="auto: media time for files, wall clock for streams",
    )
    args = parser.parse_args()

    cfg = yaml.safe_load(Path(args.config).read_text())
    det_cfg, trk_cfg = cfg["detection"], cfg["tracking"]
    reg_cfg, occ_cfg, w_cfg = cfg["registration"], cfg["occupancy"], cfg["writes"]

    source = args.source or cfg["source"]
    lot, client = load_lot(args, cfg)

    ref_path = args.reference or f"lots/{lot['code']}.jpg"
    reference = cv2.imread(ref_path)
    if reference is None:
        raise RuntimeError(f"reference image {ref_path!r} not found - run annotate.py first")

    # Dwell times must be measured in the footage's own timebase. Wall clock on a
    # recorded file would compress a 30s clip into however fast the GPU chews it.
    is_file = Path(source).exists()
    use_media_time = args.timebase == "media" or (args.timebase == "auto" and is_file)

    # The dashboard stops us with SIGTERM: unwind like Ctrl-C, so the session is
    # closed, queued rows are flushed and the camera is released.
    def _stop(*_):
        raise KeyboardInterrupt

    signal.signal(signal.SIGTERM, _stop)
    cap, fps = open_source(source, cfg.get("camera"))
    is_camera = not hasattr(cap, "isOpened")
    black_level = (cfg.get("camera") or {}).get("black_level", 16)
    ref_size = (reference.shape[1], reference.shape[0])
    live = LivePublisher(args.live_dir, lot, ref_size)
    # Imported here rather than at module scope so --help stays instant.
    import supervision as sv
    from trackers import ByteTrackTracker
    from ultralytics import YOLO

    model = YOLO(det_cfg["weights"])
    vehicle_ids = vehicle_class_ids(model.names)
    stabilizer = Stabilizer(reference, **reg_cfg)
    tracker = ByteTrackTracker(frame_rate=fps / max(1, det_cfg["frame_skip"]), **trk_cfg)
    occ = OccupancyTracker(
        [(s["id"], s["polygon"]) for s in lot["slots"]],
        lot["meters_per_pixel"],
        lot_boundary=lot["boundary"],
        ground_H=ground_homography(lot.get("ground")),
        **occ_cfg,
    )

    slot_codes = {s["id"]: s["code"] for s in lot["slots"]}
    slot_polys = {s["id"]: s["polygon"] for s in lot["slots"]}

    session_id = None
    if not args.dry_run:
        session_id = db.start_session(
            client,
            lot["id"],
            drone_name=cfg.get("drone_name"),
            source=source,
            model_name=det_cfg["weights"],
        )
        print(f"[pipeline] session {session_id} on lot {lot['code']}")

    writer = db.Writer(client, queue_path=w_cfg["queue_path"], dry_run=args.dry_run)

    # Every bay starts free; occupancy events then mark the taken ones. Without
    # this reset, a bay left "occupied" by an earlier session would stay red on
    # the app's layout map forever, since a free bay never emits an event.
    if session_id:
        started = db.iso(time.time())
        for slot in lot["slots"]:
            writer.add("slot_status", {"slot_id": slot["id"], "is_occupied": False, "since": None,
                                       "session_id": session_id, "updated_at": started},
                       on_conflict="slot_id")
        writer.flush()

    t0 = time.time()
    frame_idx = 0
    last_snapshot = last_flush = None
    prev_boxes: np.ndarray = np.empty((0, 4), dtype=np.float32)
    stale_frames = 0
    signal_lost = False
    outages = 0
    hold_until = 0.0  # bays stay as they are until this time

    try:
        while True:
            ok, frame = cap.read()
            if not ok and not is_camera:
                break
            frame_idx += 1
            if frame_idx % det_cfg["frame_skip"]:
                continue

            now = t0 + frame_idx / fps if use_media_time else time.time()
            if last_snapshot is None:
                last_snapshot = last_flush = now

            # Transmitter off (black feed) or no frames at all: hold every bay and
            # write nothing. If the outage outlasts 2 min the app greys the lot
            # out as "No live data", which is the truth.
            if is_camera and is_blank(frame, black_level):
                if not signal_lost:
                    signal_lost, outages = True, outages + 1
                    print(f"[{time.strftime('%H:%M:%S')}] camera feed lost - holding bay states")
                    writer.flush()
                view = frame if frame is not None else np.zeros((ref_size[1], ref_size[0], 3), np.uint8)
                live.publish(view, occ, signal=False, held=True, raw=frame)
                continue
            if signal_lost:
                signal_lost = False
                # Old tracks are gone; parked cars need a full park window before
                # they claim their bays again, plus the debounce on top.
                hold_until = now + occ_cfg["park_window_s"] + 3.0
                print(f"[{time.strftime('%H:%M:%S')}] camera feed back - bays held "
                      f"{hold_until - now:.0f}s while cars re-settle")

            reg = stabilizer.register(frame, prev_boxes)
            stale_frames += 0 if reg.ok else 1

            result = model.predict(
                frame,
                imgsz=det_cfg["imgsz"],
                conf=det_cfg["conf"],
                device=det_cfg["device"],
                verbose=False,
            )[0]

            raw = result.boxes.xyxy.cpu().numpy()
            classes = result.boxes.cls.cpu().numpy().astype(int)
            scores = result.boxes.conf.cpu().numpy()

            keep = np.array([c in vehicle_ids for c in classes], dtype=bool)
            raw, classes, scores = raw[keep], classes[keep], scores[keep]
            prev_boxes = raw  # next frame masks these out when matching features

            observations: list[Observation] = []
            tracked_boxes: list[tuple[int, np.ndarray]] = []

            if len(raw):
                detections = sv.Detections(
                    xyxy=warp_boxes(reg.H, raw).astype(np.float32),
                    confidence=scores.astype(np.float32),
                    class_id=classes.astype(int),
                )
                tracked = tracker.update(detections)
                observations = to_observations(tracked, model.names)

                if args.show:
                    H_inv = np.linalg.inv(reg.H)
                    tracked_boxes = [
                        (o.track_id, warp_boxes(H_inv, np.array([o.xyxy], dtype=np.float32))[0])
                        for o in observations
                    ]

            held = now < hold_until
            events, counts = occ.update(observations, now, hold=held)
            H_inv = np.linalg.inv(reg.H)
            live.publish(cv2.warpPerspective(frame, reg.H, ref_size), occ, signal=True, held=held,
                         raw=frame)

            for ev in events:
                frame_url = None
                if w_cfg["evidence_frames"] and not args.dry_run:
                    jpg = evidence_jpg(frame, H_inv, slot_polys[ev.slot_id])
                    if jpg:
                        frame_url = db.upload_frame(
                            client,
                            f"{lot['code']}/{session_id}/{slot_codes[ev.slot_id]}_{int(ev.at)}.jpg",
                            jpg,
                        )

                writer.add(
                    "slot_events",
                    {
                        "slot_id": ev.slot_id,
                        "session_id": session_id,
                        "event": ev.event,
                        "at": db.iso(ev.at),
                        "track_id": ev.track_id,
                        "vehicle_type": ev.vehicle_type,
                        "dwell_seconds": ev.dwell_seconds,
                        "frame_url": frame_url,
                    },
                )
                state = occ.status()[ev.slot_id]
                writer.add(
                    "slot_status",
                    {
                        "slot_id": ev.slot_id,
                        "is_occupied": state.occupied,
                        "since": db.iso(state.since) if state.since else None,
                        "track_id": state.track_id,
                        "vehicle_type": state.vehicle_type,
                        "session_id": session_id,
                        "updated_at": db.iso(ev.at),
                    },
                    on_conflict="slot_id",
                )
                # cv_vehicle_log: the drone system's own vehicle record. 'occupied'
                # opens a stay (entry), 'vacated' closes it (exit); entry on close is
                # recovered from the dwell so one upsert row spans the whole stay.
                # ponytail: keyed by (session, track, slot) - a track re-parking in the
                # SAME bay overwrites its earlier stay; fine at this scale.
                parked = ev.event == "occupied"
                entry_at = ev.at if parked else ev.at - (ev.dwell_seconds or 0)
                writer.add(
                    "cv_vehicle_log",
                    {
                        "session_id": session_id,
                        "track_id": ev.track_id,
                        "vehicle_type": ev.vehicle_type,
                        "slot_id": ev.slot_id,
                        "slot_code": slot_codes[ev.slot_id],
                        "entry_at": db.iso(entry_at),
                        "exit_at": None if parked else db.iso(ev.at),
                        "dwell_seconds": ev.dwell_seconds,
                        "status": "parked" if parked else "departed",
                        "updated_at": db.iso(ev.at),
                    },
                    on_conflict="session_id,track_id,slot_id",
                )
                print(
                    f"[{time.strftime('%H:%M:%S', time.localtime(ev.at))}] "
                    f"{slot_codes[ev.slot_id]} {ev.event}"
                    + (f" after {ev.dwell_seconds:.0f}s" if ev.dwell_seconds else "")
                )

            # A bay changing state writes a snapshot (and flushes) immediately, so the
            # mobile app updates within a second or two; otherwise one per interval.
            if events or now - last_snapshot >= w_cfg["snapshot_interval_s"]:
                writer.add(
                    "parking_snapshots",
                    {
                        "lot_id": lot["id"],
                        "session_id": session_id,
                        "captured_at": db.iso(now),
                        "occupied": counts.occupied,
                        "free": counts.free,
                        "moving": counts.moving,
                        "detections": counts.detections,
                        "registration_ok": reg.ok,
                    },
                )
                for track in occ.tracks.values():
                    writer.add(
                        "parking_tracks",
                        {
                            "session_id": session_id,
                            "track_id": track.track_id,
                            "vehicle_type": track.vehicle_type,
                            "raw_class": track.raw_class,
                            "slot_id": track.slot_id,
                            "is_parked": track.is_parked,
                            "first_seen": db.iso(track.first_seen),
                            "last_seen": db.iso(track.last_seen),
                            "dwell_seconds": track.last_seen - track.first_seen,
                        },
                        on_conflict="session_id,track_id",
                    )
                print(
                    f"[snapshot] occupied={counts.occupied} free={counts.free} "
                    f"moving={counts.moving} reg_ok={reg.ok}"
                )
                last_snapshot = now

            if events or now - last_flush >= w_cfg["flush_interval_s"]:
                writer.flush()
                last_flush = now

            if args.show:
                cv2.imshow(
                    "parking", draw_overlay(frame, H_inv, lot, occ, tracked_boxes, reg)
                )
                if cv2.waitKey(1) == 27:
                    break

    except KeyboardInterrupt:
        print("\n[pipeline] interrupted")
    finally:
        cap.release()
        if args.show:
            cv2.destroyAllWindows()
        writer.close()
        if session_id:
            db.end_session(client, session_id)
        processed = frame_idx // max(1, det_cfg["frame_skip"])
        pct = 100.0 * stale_frames / processed if processed else 0.0
        print(f"[pipeline] {processed} frames processed, {writer.written} rows written")
        print(f"[pipeline] registration stale on {stale_frames} ({pct:.1f}%) of frames")
        if outages:
            print(f"[pipeline] camera feed went black {outages} time(s); bays were held each time")
        if pct > 20:
            print("[pipeline] WARNING: high stale rate - recapture the reference image")


if __name__ == "__main__":
    main()
