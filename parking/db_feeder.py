"""Replay the demo clip's occupancy into the database in real time, looping.

Our "camera" for the demo is a recorded clip that the dashboard loops. This
loops the same clip's occupancy into the database - each bay's state into
slot_status (the app's layout map) and the lot total into parking_snapshots -
so the mobile app sees live, changing data. In production pipeline.py writes
these same rows.

A row is written whenever the count changes, plus a heartbeat so the app never
mistakes a quiet lot for a dead camera (it greys a lot out after 2 minutes).

    python db_feeder.py             # writes to Supabase (service key in .env)
    python db_feeder.py --dry-run   # prints what it would write, touches nothing
"""

from __future__ import annotations

import argparse
import bisect
import json
import signal
import time
from pathlib import Path

import cv2

import db
from dashboard_server import VIDEO_DEFAULT


def clip_seconds(source: str, fps: float, last_frame: int) -> float:
    """Loop length: the real clip duration, or the last recorded change if the
    video isn't available."""
    cap = cv2.VideoCapture(source)
    frames = cap.get(cv2.CAP_PROP_FRAME_COUNT) if cap.isOpened() else 0
    cap.release()
    return (frames or last_frame + fps) / fps


def _stop(*_):
    raise KeyboardInterrupt


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--timeline", default="occupancy_timeline.json")
    ap.add_argument("--lot-code", default="PARKING-A")
    ap.add_argument("--source", default=VIDEO_DEFAULT, help="clip, used only for its length")
    ap.add_argument("--heartbeat", type=float, default=60, help="seconds between unchanged rows")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    tl = json.loads(Path(args.timeline).read_text())
    fps, capacity, codes = tl["fps"], tl["capacity"], tl["codes"]
    frames = [f for f, _ in tl["samples"]]
    states = [frozenset(occ) for _, occ in tl["samples"]]   # occupied bay indices
    loop = clip_seconds(args.source, fps, frames[-1])

    client = None if args.dry_run else db.connect()
    if client:
        lots = client.table("parking_lots").select("id, name").eq("code", args.lot_code).execute().data
        if not lots:
            raise SystemExit(f"no parking_lots row with code {args.lot_code!r}")
        lot_id, lot_name = lots[0]["id"], lots[0]["name"]
        bays = client.table("parking_slots").select("id, code").eq("lot_id", lot_id).execute().data
        slot_id = {b["code"]: b["id"] for b in bays}
        missing = [c for c in codes if c not in slot_id]
        if missing:
            raise SystemExit(f"{len(missing)} timeline bays not in the database, e.g. {missing[0]}")
        session_id = db.start_session(client, lot_id, drone_name="replay",
                                      source=Path(args.source).name, model_name="occupancy replay")
    else:
        lot_id, lot_name, session_id = "dry-run", args.lot_code, "dry-run"
        slot_id = {c: c for c in codes}
    writer = db.Writer(client, dry_run=args.dry_run)

    def write_bays(indices, occupied: frozenset, ts: float) -> None:
        """Per-bay state for the app's layout map, upserted before the lot total
        so a client refreshing on the snapshot event sees bays already updated."""
        for i in indices:
            writer.add("slot_status", {
                "slot_id": slot_id[codes[i]],
                "is_occupied": i in occupied,
                "since": db.iso(ts),
                "session_id": session_id,
                "updated_at": db.iso(ts),
            }, on_conflict="slot_id")

    print(f"[feeder] {lot_name}: replaying {len(frames)} occupancy states over a "
          f"{loop:.0f}s loop, heartbeat {args.heartbeat:.0f}s{'  (dry run)' if args.dry_run else ''}")
    signal.signal(signal.SIGTERM, _stop)   # the launcher stops us with SIGTERM
    t0, last_state, last_write = time.monotonic(), None, 0.0
    try:
        while True:
            frame = ((time.monotonic() - t0) % loop) * fps
            state = states[max(bisect.bisect_right(frames, frame) - 1, 0)]
            occupied = len(state)
            now = time.time()
            # Compare which bays, not how many: one car leaving while another
            # arrives elsewhere keeps the count but moves a bay on the map.
            changed = state != last_state
            if changed or now - last_write >= args.heartbeat:
                if last_state is None:
                    write_bays(range(len(codes)), state, now)       # full state on start
                elif changed:
                    write_bays(sorted(state ^ last_state), state, now)  # only bays that flipped
                writer.add("parking_snapshots", {
                    "lot_id": lot_id,
                    "session_id": session_id,
                    "captured_at": db.iso(now),
                    "occupied": occupied,
                    "free": capacity - occupied,
                    # the timeline records bay occupancy only; these aren't replayed
                    "moving": 0,
                    "detections": 0,
                    "registration_ok": True,
                })
                flipped = len(codes) if last_state is None else len(state ^ last_state)
                writer.flush()
                print(f"[{time.strftime('%H:%M:%S')}] {occupied}/{capacity} "
                      f"({100 * occupied // capacity}%) "
                      f"{f'change, {flipped} bay(s) written' if changed else 'heartbeat'}", flush=True)
                last_state, last_write = state, now
            time.sleep(0.25)
    except KeyboardInterrupt:
        print("\n[feeder] stopping")
    finally:
        writer.close()
        if client:
            db.end_session(client, session_id)


if __name__ == "__main__":
    main()
