"""Log speeding vehicles, with an evidence frame, locally first and then to Postgres.

    python3 speedlog.py --init        create the table and storage bucket
    python3 speedlog.py --recent      show the most recent events

Wired into live_pipeline.py, which calls SpeedLogger.observe() once per tracked
vehicle per frame. Each event is saved to a local SQLite copy (output/speedlog.db),
which the dashboard reads, and a background thread syncs it to the cloud: the
drone takes the Wi-Fi, so the internet is a phone over USB, slow and sometimes
gone. Guards are only alerted once the cloud has the row. Everything here is
strictly additive: if logging is unconfigured the pipeline keeps measuring and
displaying exactly as before.

The database is the easy part. The hard part is not logging garbage -- see
`Candidate` for the rules a reading must pass before it can become a record.
"""

import argparse
import os
import queue
import sqlite3
import threading
import time
import uuid
from contextlib import closing
from dataclasses import dataclass, field
from datetime import datetime, timezone

import cv2
import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
try:
    from dotenv import load_dotenv
    load_dotenv(os.path.join(HERE, ".env"))
except Exception:                                   # dotenv is optional
    pass

# --- event qualification --------------------------------------------------
# A vehicle must stay over the limit continuously for this long before anything
# is written. Deliberately expressed in SECONDS, not frames: at 30 fps three
# consecutive readings span 0.086 s and share ~85% of the same 0.6 s velocity
# window, so they are one measurement counted three times rather than three
# independent confirmations. 0.7 s is slightly longer than a full window, so the
# evidence rests on genuinely fresh trajectory data -- and the rule behaves
# identically on a 24 fps drone clip and a 30 fps motorway feed.
MIN_DURATION_S = 0.7
UPDATE_MARGIN_KMH = 2.0    # re-upload a frame only if the peak beats this much
QUEUE_MAX = 256            # bounded: drop oldest rather than grow without limit
FLUSH_INTERVAL_S = 5.0
CROP_PAD = 0.4             # context around the vehicle box, as a fraction
BUCKET = "speeding"
LOCAL_DB = os.path.join(HERE, "output", "speedlog.db")
EVIDENCE_DIR = os.path.join(HERE, "output", "evidence")   # same names as in the bucket
SYNC_INTERVAL_S = 5.0      # cloud pull cadence, and the first retry when offline
SYNC_BACKOFF_MAX_S = 60.0
PULL_ROWS = 100            # cloud rows refreshed per pull: what the dashboard lists

SCHEMA = """
CREATE TABLE IF NOT EXISTS speeding_events (
    id            BIGSERIAL   PRIMARY KEY,
    detected_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
    camera        TEXT        NOT NULL,
    vehicle_id    INTEGER     NOT NULL,
    vehicle_type  TEXT        NOT NULL,
    speed_kmh     REAL        NOT NULL,
    speed_limit   REAL        NOT NULL,
    frame_url     TEXT,
    session_id    UUID        NOT NULL,
    lat           DOUBLE PRECISION,
    lon           DOUBLE PRECISION,
    UNIQUE (session_id, vehicle_id)
);
-- Guard dispatch (guards, dispatches, trigger) is added on top of this by
-- supabase/migrations/20261008_guard_dispatch.sql.
CREATE INDEX IF NOT EXISTS speeding_events_time_idx
    ON speeding_events (detected_at DESC);
CREATE INDEX IF NOT EXISTS speeding_events_camera_time_idx
    ON speeding_events (camera, detected_at DESC);
"""

# The row converges to the vehicle's PEAK speed. Logging only the first
# over-limit reading would record a car at 71 km/h that went on to do 95.
UPSERT = """
INSERT INTO speeding_events
    (detected_at, camera, vehicle_id, vehicle_type, speed_kmh, speed_limit,
     session_id, lat, lon)
VALUES (to_timestamp(%s), %s, %s, %s, %s, %s, %s, %s, %s)
ON CONFLICT (session_id, vehicle_id) DO UPDATE
   SET speed_kmh   = EXCLUDED.speed_kmh,
       detected_at = EXCLUDED.detected_at
 WHERE EXCLUDED.speed_kmh > speeding_events.speed_kmh;
"""

# The evidence path is fixed per (session, vehicle) and re-uploads overwrite it,
# so the URL only ever needs setting once.
SET_URL = """
UPDATE speeding_events SET frame_url = %s
 WHERE session_id = %s AND vehicle_id = %s AND frame_url IS NULL;
"""


# =========================================================================
# Qualification — the part that decides what deserves a record
# =========================================================================

@dataclass
class Candidate:
    """Per-vehicle state machine deciding whether it has earned a record.

    Rules, all deliberately stricter than the on-screen display:

      * only `state == "ok"` readings count. An UNRELIABLE reading must never
        become a ticket -- that is the entire reason the state exists.
      * exceedance must be *continuous* for MIN_DURATION_S. Any reading below
        the limit, or unreliable, or still warming up, resets the streak.
      * after the first record, only a peak beating the stored one by
        UPDATE_MARGIN_KMH re-fires, so an accelerating vehicle does not upload
        a frame every frame.
    """
    since: float | None = None      # when the current over-limit streak began
    peak: float = 0.0               # best speed seen while over the limit
    logged_at_peak: float = 0.0     # peak already written to the database

    def update(self, t, state, kmh, limit):
        """Feed one reading. Returns True when a record should be written."""
        if state != "ok" or kmh is None or kmh <= limit:
            self.since = None                       # streak broken
            return False

        if self.since is None:
            self.since, self.peak = t, kmh
        else:
            self.peak = max(self.peak, kmh)

        if t - self.since < MIN_DURATION_S:         # not sustained yet
            return False
        if self.logged_at_peak == 0.0:              # first qualifying record
            self.logged_at_peak = self.peak
            return True
        if self.peak > self.logged_at_peak + UPDATE_MARGIN_KMH:
            self.logged_at_peak = self.peak         # a materially higher peak
            return True
        return False


@dataclass
class Event:
    ts: float
    camera: str
    vehicle_id: int
    vehicle_type: str
    speed_kmh: float
    speed_limit: float
    session_id: str
    jpeg: bytes | None = field(default=None, repr=False)
    lat: float | None = None        # fixed hover position of the drone
    lon: float | None = None
    view: bytes | None = field(default=None, repr=False)   # cleaned-up evidence, if any


# =========================================================================
# Evidence frame
# =========================================================================

def _annotate(crop, box, vehicle_id, vehicle_type, kmh, limit, camera, ts, note=None):
    """Box the vehicle, add the caption bar, encode. `box` is in `crop` pixels."""
    # Upscale small crops so the burnt-in caption stays legible. A distant
    # vehicle can be 40 px across; a caption on that is unreadable.
    scale = 1.0
    if crop.shape[1] < 420:
        scale = 420 / crop.shape[1]
        crop = cv2.resize(crop, (420, int(round(crop.shape[0] * scale))),
                          interpolation=cv2.INTER_CUBIC)
    x1, y1, x2, y2 = (np.asarray(box, float) * scale).astype(int)
    cv2.rectangle(crop, (x1, y1), (x2, y2), (0, 0, 255), 2)

    bar = 62 if note else 46
    out = cv2.copyMakeBorder(crop, 0, bar, 0, 0, cv2.BORDER_CONSTANT, value=(20, 20, 20))
    y0 = out.shape[0] - bar
    cv2.putText(out, f"{kmh:.1f} km/h  (limit {limit:.0f})", (8, y0 + 19),
                cv2.FONT_HERSHEY_SIMPLEX, 0.55, (0, 0, 255), 2)
    stamp = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(ts))
    cv2.putText(out, f"#{vehicle_id} {vehicle_type} | {camera} | {stamp}",
                (8, y0 + 37), cv2.FONT_HERSHEY_SIMPLEX, 0.42, (200, 200, 200), 1)
    if note:
        cv2.putText(out, note, (8, y0 + 54), cv2.FONT_HERSHEY_SIMPLEX, 0.4, (150, 200, 255), 1)
    ok, buf = cv2.imencode(".jpg", out, [cv2.IMWRITE_JPEG_QUALITY, 90])
    return buf.tobytes() if ok else None


def _region(box, w, h, pad=CROP_PAD):
    """Evidence window around `box`, clipped to a w x h image (ints)."""
    x1, y1, x2, y2 = np.asarray(box, dtype=float)
    px, py = (x2 - x1) * pad, (y2 - y1) * pad
    return (int(max(x1 - px, 0)), int(max(y1 - py, 0)),
            int(min(x2 + px, w)), int(min(y2 + py, h)))


def evidence_jpeg(frame, box, vehicle_id, vehicle_type, kmh, limit, camera, ts, note=None):
    """Annotated crop of the offending vehicle from one frame.

    A crop rather than the whole frame: padded for context it is both smaller
    and more useful as evidence than a 1080p still where the vehicle is 40 px.
    """
    cx1, cy1, cx2, cy2 = _region(box, frame.shape[1], frame.shape[0])
    if cx2 - cx1 < 16 or cy2 - cy1 < 16:
        return None
    return _annotate(frame[cy1:cy2, cx1:cx2].copy(), np.asarray(box, float) - [cx1, cy1, cx1, cy1],
                     vehicle_id, vehicle_type, kmh, limit, camera, ts, note)


# =========================================================================
# Backend
# =========================================================================

def _storage_upload(jpeg, name):
    """Upload to Supabase Storage over REST. Returns a public URL, or None."""
    url, key = os.getenv("SUPABASE_URL"), os.getenv("SUPABASE_SERVICE_KEY")
    if not (url and key and jpeg):
        return None
    import requests
    base = url.rstrip("/")
    # The key goes in `apikey`, not only Authorization. Modern Supabase keys
    # (sb_secret_...) are opaque, not JWTs: sent as a bare bearer token the
    # storage service tries to parse them as a JWT and fails with
    # "Invalid Compact JWS". The gateway reads `apikey` and synthesises the JWT.
    r = requests.post(f"{base}/storage/v1/object/{BUCKET}/{name}", data=jpeg,
                      headers={"apikey": key,
                               "Authorization": f"Bearer {key}",
                               "Content-Type": "image/jpeg",
                               "x-upsert": "true"}, timeout=20)
    if r.status_code not in (200, 201):
        raise RuntimeError(f"storage upload {r.status_code}: {r.text[:120]}")
    return f"{base}/storage/v1/object/public/{BUCKET}/{name}"


class SpeedLogger:
    """Queues qualifying events and saves them to the local copy from a
    background thread; the sync thread takes them on to the cloud.

    Never blocks the inference loop, and never raises into it: a failed save
    degrades to a warning and a count, not a broken pipeline.
    """

    def __init__(self, camera, limit, enabled=True, lat=None, lon=None):
        self.camera, self.limit = camera, limit
        self.lat, self.lon = lat, lon    # without these, events are logged but no guard is dispatched
        self.dsn = os.getenv("DATABASE_URL")
        self.enabled = bool(enabled and self.dsn)
        self.session_id = str(uuid.uuid4())
        self.cands = {}
        self.q = queue.Queue(maxsize=QUEUE_MAX)
        self.written = self.dropped = self.failed = 0
        self._stop = threading.Event()
        self._warned = False
        if enabled and not self.dsn:
            print("  speed logging: DATABASE_URL not set -- logging disabled")
        if self.enabled:
            self._t = threading.Thread(target=self._run, daemon=True)
            self._t.start()

    def new_session(self):
        """Tracker ids only mean something within a session; reset on source change."""
        self.session_id = str(uuid.uuid4())
        self.cands.clear()

    def observe(self, t, tid, state, kmh, vehicle_type, frame=None, box=None, view=None):
        """One reading for one vehicle. Enqueues an event only if it qualifies.

        `frame` is the camera frame as recorded; `view`, if given, is the same
        frame after the pipeline's clean-up, used for the image people look at.
        """
        if not self.enabled:
            return False
        c = self.cands.get(tid)
        if c is None:
            c = self.cands[tid] = Candidate()
        if not c.update(t, state, kmh, self.limit):
            return False

        jpeg = None
        if frame is not None and box is not None:
            try:
                jpeg = evidence_jpeg(frame, box, tid, vehicle_type, c.peak,
                                     self.limit, self.camera, time.time())
            except Exception:
                jpeg = None                          # evidence is best-effort
        shown = None
        if view is not None and box is not None:
            try:
                shown = evidence_jpeg(view, box, tid, vehicle_type, c.peak, self.limit,
                                      self.camera, time.time(),
                                      note="Noise-reduced view. The original frame is kept.")
            except Exception:
                shown = None
        ev = Event(time.time(), self.camera, int(tid), vehicle_type, c.peak,
                   self.limit, self.session_id, jpeg, lat=self.lat, lon=self.lon, view=shown)
        try:
            self.q.put_nowait(ev)
        except queue.Full:
            try:                                     # bounded: shed the oldest
                self.q.get_nowait()
                self.q.put_nowait(ev)
            except queue.Empty:
                pass
            self.dropped += 1
        return True

    # -- writer thread ----------------------------------------------------

    def _run(self):
        while not self._stop.is_set():
            batch = []
            try:
                batch.append(self.q.get(timeout=FLUSH_INTERVAL_S))
            except queue.Empty:
                continue
            while len(batch) < 32:
                try:
                    batch.append(self.q.get_nowait())
                except queue.Empty:
                    break
            try:
                self._save(batch)
            except Exception as e:
                self.failed += len(batch)
                if not self._warned:                 # warn once, keep running
                    print(f"  speed logging failed ({type(e).__name__}: "
                          f"{str(e)[:100]}) -- pipeline continues")
                    self._warned = True

    def _save(self, batch):
        """Images to disk, then the rows, so a row never points at a missing
        image. Milliseconds, with or without the internet."""
        with closing(_local()) as db:
            for ev in batch:
                if ev.jpeg:
                    # The cleaned view is shown as <id>.jpg, with the frame as
                    # recorded beside it as <id>_raw.jpg.
                    _write(_evidence_path(ev.session_id, f"{ev.vehicle_id}.jpg"), ev.view or ev.jpeg)
                    if ev.view:
                        _write(_evidence_path(ev.session_id, f"{ev.vehicle_id}_raw.jpg"), ev.jpeg)
                db.execute(LOCAL_UPSERT, (ev.session_id, ev.vehicle_id, ev.ts, ev.camera,
                                          ev.vehicle_type, ev.speed_kmh, ev.speed_limit,
                                          ev.lat, ev.lon, int(bool(ev.jpeg))))
            db.commit()
        self.written += len(batch)
        _kick.set()

    def close(self, timeout=10):
        """Save what is queued, then give the cloud a moment to take it.
        Whatever it doesn't get now is sent on the next start."""
        if not self.enabled:
            return
        deadline = time.time() + timeout
        while not self.q.empty() and time.time() < deadline:
            time.sleep(0.2)
        self._stop.set()
        self._t.join(timeout=FLUSH_INTERVAL_S + 1)   # let an in-flight save finish
        _kick.set()
        while SYNC["error"] is None and pending() and time.time() < deadline:
            time.sleep(0.2)                          # offline: don't hold up the exit


# =========================================================================
# Local copy: what the dashboard reads, and the queue for the cloud
# =========================================================================
#
# Each local write bumps `rev`. The sync thread pushes a row while
# rev > row_rev, then its images while row_rev > img_rev, and copies the
# cloud's columns (guard response, cloud id) back. Rows pulled from the
# cloud arrive with all three at 0: nothing to send.

LOCAL_SCHEMA = """
CREATE TABLE IF NOT EXISTS speeding_events (
    id              INTEGER PRIMARY KEY,         -- local; the dashboard's row id
    session_id      TEXT    NOT NULL,
    vehicle_id      INTEGER NOT NULL,
    detected_at     REAL    NOT NULL,            -- unix seconds
    camera          TEXT    NOT NULL,
    vehicle_type    TEXT    NOT NULL,
    speed_kmh       REAL    NOT NULL,
    speed_limit     REAL    NOT NULL,
    lat             REAL,
    lon             REAL,
    evidence        INTEGER NOT NULL DEFAULT 0,  -- images saved under EVIDENCE_DIR
    frame_url       TEXT,
    cloud_id        INTEGER,
    claimed_by_name TEXT,
    claimed_at      TEXT,
    alerted         INTEGER NOT NULL DEFAULT 0,
    rev             INTEGER NOT NULL DEFAULT 1,
    row_rev         INTEGER NOT NULL DEFAULT 0,
    img_rev         INTEGER NOT NULL DEFAULT 0,
    UNIQUE (session_id, vehicle_id)
);
CREATE INDEX IF NOT EXISTS speeding_events_time_idx ON speeding_events (detected_at DESC);
"""

# Same peak rule as the cloud's UPSERT.
LOCAL_UPSERT = """
INSERT INTO speeding_events
    (session_id, vehicle_id, detected_at, camera, vehicle_type, speed_kmh,
     speed_limit, lat, lon, evidence)
VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
ON CONFLICT (session_id, vehicle_id) DO UPDATE
   SET speed_kmh = excluded.speed_kmh, detected_at = excluded.detected_at,
       evidence = max(evidence, excluded.evidence), rev = rev + 1
 WHERE excluded.speed_kmh > speeding_events.speed_kmh
"""

PULL = """
SELECT e.id, extract(epoch FROM e.detected_at), e.camera, e.vehicle_id, e.vehicle_type,
       e.speed_kmh, e.speed_limit, e.frame_url, e.session_id::text, e.lat, e.lon,
       e.claimed_by_name, e.claimed_at,
       (SELECT count(*) FROM dispatches d WHERE d.event_id = e.id)
FROM speeding_events e ORDER BY e.id DESC LIMIT %s
"""

PULL_UPSERT = """
INSERT INTO speeding_events
    (cloud_id, detected_at, camera, vehicle_id, vehicle_type, speed_kmh, speed_limit,
     frame_url, session_id, lat, lon, claimed_by_name, claimed_at, alerted, rev)
VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 0)
ON CONFLICT (session_id, vehicle_id) DO UPDATE
   SET cloud_id = excluded.cloud_id,
       frame_url = coalesce(excluded.frame_url, frame_url),
       claimed_by_name = excluded.claimed_by_name, claimed_at = excluded.claimed_at,
       alerted = excluded.alerted,
       -- a higher peak still waiting to go up beats the cloud's older one
       speed_kmh = CASE WHEN rev > row_rev THEN speed_kmh ELSE excluded.speed_kmh END,
       detected_at = CASE WHEN rev > row_rev THEN detected_at ELSE excluded.detected_at END
"""

SYNC = {"error": None, "at": None}   # last failure (None once it works again), last success
_kick = threading.Event()            # set when there is something new to send
_syncer = None


def _local():
    """A connection to the local copy. One per use: they are cheap, and a
    sqlite3 connection must not cross threads."""
    os.makedirs(os.path.dirname(LOCAL_DB), exist_ok=True)
    db = sqlite3.connect(LOCAL_DB, timeout=10)
    db.execute("PRAGMA journal_mode=WAL")            # the page reads while events are written
    db.executescript(LOCAL_SCHEMA)
    return db


def _evidence_path(session_id, name):
    return os.path.join(EVIDENCE_DIR, session_id, name)


def _write(path, data):
    """Write-then-rename, so the page never shows half an image."""
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path + ".tmp", "wb") as f:
        f.write(data)
    os.replace(path + ".tmp", path)


def _read(path):
    try:
        with open(path, "rb") as f:
            return f.read()
    except OSError:
        return None


def pending():
    """Events saved here that the cloud doesn't have yet (or not their latest peak)."""
    with closing(_local()) as db:
        return db.execute("SELECT count(*) FROM speeding_events WHERE rev > row_rev").fetchone()[0]


def _push_rows(conn, db):
    """Every row changed here since its last push. Rows go before any image:
    the INSERT is what alerts guards, so it must not wait behind an upload."""
    for eid, rev, *row in db.execute(
            "SELECT id, rev, detected_at, camera, vehicle_id, vehicle_type, speed_kmh,"
            " speed_limit, session_id, lat, lon FROM speeding_events"
            " WHERE rev > row_rev ORDER BY id").fetchall():
        conn.execute(UPSERT, row)        # safe to repeat: a second push is an UPDATE, no second alert
        db.execute("UPDATE speeding_events SET row_rev = ? WHERE id = ?", (rev, eid))
        db.commit()


def _push_evidence(conn, db):
    """Upload the images of one event the cloud already has. True if more wait:
    one at a time, so a new event's row never queues behind a run of uploads."""
    todo = db.execute("SELECT id, row_rev, session_id, vehicle_id FROM speeding_events"
                      " WHERE evidence AND row_rev > img_rev ORDER BY id LIMIT 2").fetchall()
    if not todo:
        return False
    eid, rev, sid, vid = todo[0]
    base, url = f"{sid}/{vid}", None
    try:
        # The original first: it is the record, the cleaned view is extra.
        raw = _read(_evidence_path(sid, f"{vid}_raw.jpg"))
        if raw:
            url = _storage_upload(raw, base + "_raw.jpg")
        url = _storage_upload(_read(_evidence_path(sid, f"{vid}.jpg")), base + ".jpg") or url
    finally:
        if url:                          # what made it up is shown, even if the rest failed
            conn.execute(SET_URL, (url, sid, vid))
    db.execute("UPDATE speeding_events SET img_rev = ? WHERE id = ?", (rev, eid))
    db.commit()
    return len(todo) > 1


def _pull(conn, db, full):
    """Copy the cloud back: guard responses, other cameras' events, and on the
    first pull the whole history. The cloud is the record, so on that pull a
    synced row it no longer has (deleted there) goes from here too."""
    rows = conn.execute(PULL, (None if full else PULL_ROWS,)).fetchall()
    if full:
        db.execute("UPDATE speeding_events SET cloud_id = NULL WHERE rev = row_rev")
    db.executemany(PULL_UPSERT, [(r[0], float(r[1]), *r[2:12],
                                  r[12].isoformat() if r[12] else None, r[13])
                                 for r in reversed(rows)])   # oldest first: local ids keep the order
    if full:
        db.execute("DELETE FROM speeding_events WHERE rev = row_rev AND cloud_id IS NULL")
    db.commit()


def _sync_loop():
    conn, wait, last_pull, full = None, 0.0, 0.0, True
    while True:
        _kick.wait(wait)
        _kick.clear()
        try:
            if conn is None or conn.closed:
                import psycopg
                # One connection held open: a fresh TLS handshake over the phone
                # costs more than the queries. No prepared statements, which the
                # transaction pooler can't keep.
                conn = psycopg.connect(os.getenv("DATABASE_URL"), connect_timeout=15,
                                       autocommit=True, prepare_threshold=None)
            with closing(_local()) as db:
                _push_rows(conn, db)
                more = _push_evidence(conn, db)
                if full or time.time() - last_pull >= SYNC_INTERVAL_S:
                    _pull(conn, db, full)
                    last_pull, full = time.time(), False
            SYNC.update(error=None, at=time.time())
            wait = 0.0 if more else SYNC_INTERVAL_S
        except Exception as e:
            SYNC["error"] = f"{type(e).__name__}: {str(e)[:120]}"
            if conn is not None:
                conn.close()
            conn = None
            wait = min(max(2 * wait, SYNC_INTERVAL_S), SYNC_BACKOFF_MAX_S)


def start_sync():
    """Start the cloud sync thread, once. Runs with logging off too: the
    dashboard still shows the cloud's history and guard responses."""
    global _syncer
    if _syncer is None and os.getenv("DATABASE_URL"):
        _syncer = threading.Thread(target=_sync_loop, daemon=True)
        _syncer.start()


# =========================================================================
# Read side — powers the dashboard
# =========================================================================

def dashboard(limit=100):
    """Summary + recent rows for the web UI, from the local copy: instant, and
    still there when the internet isn't. None if logging is unconfigured."""
    if not os.getenv("DATABASE_URL"):
        return None
    now = time.time()
    try:
        with closing(_local()) as db:
            s = db.execute("""
                SELECT count(*), coalesce(max(speed_kmh), 0), coalesce(avg(speed_kmh), 0),
                       coalesce(avg(speed_kmh - speed_limit), 0),
                       coalesce(sum(detected_at > ?), 0), count(DISTINCT camera),
                       coalesce(sum(detected_at > ?), 0), coalesce(sum(rev > row_rev), 0)
                FROM speeding_events""", (now - 3600, now - 86400)).fetchone()
            by_type = db.execute("SELECT vehicle_type, count(*) FROM speeding_events"
                                 " GROUP BY 1 ORDER BY 2 DESC").fetchall()
            rows = db.execute("""
                SELECT id, detected_at, camera, vehicle_id, vehicle_type, speed_kmh,
                       speed_limit, frame_url, claimed_by_name, claimed_at, alerted,
                       session_id, rev > row_rev
                FROM speeding_events ORDER BY detected_at DESC LIMIT ?""", (limit,)).fetchall()
    except Exception as e:
        return {"error": f"{type(e).__name__}: {str(e)[:120]}",
                "total": 0, "rows": [], "by_type": []}

    def frame(sid, vid, url):
        # this Mac's copy when it has one: no round trip through the phone
        mine = os.path.exists(_evidence_path(sid, f"{vid}.jpg"))
        return f"/evidence/{sid}/{vid}.jpg" if mine else url

    return {
        "total": s[0], "fastest": s[1], "avg": s[2], "avg_over": s[3],
        "last_hour": s[4], "cameras": s[5], "last_24h": s[6],
        "pending": s[7], "sync_error": SYNC["error"], "synced_at": SYNC["at"],
        "by_type": [{"type": t, "n": n} for t, n in by_type],
        "rows": [{"id": r[0], "at": datetime.fromtimestamp(r[1], timezone.utc).isoformat(),
                  "camera": r[2], "vehicle_id": r[3], "type": r[4],
                  "kmh": round(r[5], 1), "limit": r[6], "over": round(r[5] - r[6], 1),
                  "frame": frame(r[11], r[3], r[7]),
                  # guard response: who took it, else how many were paged
                  "responder": r[8], "claimed_at": r[9], "alerted": r[10],
                  "pending": bool(r[12])} for r in rows],
    }


EXPORT_DAYS = {"day": 1, "week": 7, "month": 30, "all": None}


def export_csv(days=None) -> bytes:
    """Speeding events from the local copy (the whole table after the first
    sync) over the last `days` days, None = all. Times in IST."""
    import csv, io
    from datetime import timedelta
    ist = timezone(timedelta(hours=5, minutes=30))

    def t(v):
        if v is None:
            return ""
        dt = datetime.fromtimestamp(v, timezone.utc) if isinstance(v, float) else datetime.fromisoformat(v)
        return dt.astimezone(ist).strftime("%Y-%m-%d %H:%M:%S")

    def cell(v):  # no spreadsheet formulas from database text
        return "'" + v if isinstance(v, str) and v[:1] in ("=", "+", "-", "@") else v

    since = 0 if days is None else time.time() - days * 86400
    with closing(_local()) as db:
        rows = db.execute("""
            SELECT detected_at, speed_kmh, speed_limit, vehicle_type, camera, lat, lon,
                   claimed_by_name, claimed_at, frame_url, cloud_id, session_id, vehicle_id
            FROM speeding_events WHERE detected_at >= ? ORDER BY detected_at""", (since,)).fetchall()
    out = io.StringIO()
    w = csv.writer(out)
    w.writerow(["time_ist", "speed_kmh", "limit_kmh", "over_kmh", "vehicle_type", "camera", "lat", "lon",
                "responded_by", "responded_ist", "evidence_url", "event_id", "session_id", "track_id"])
    for r in rows:
        w.writerow([t(r[0]), round(r[1], 1), r[2], round(r[1] - r[2], 1), cell(r[3]), cell(r[4]), r[5], r[6],
                    cell(r[7]) or "", t(r[8]), r[9] or "", r[10] or "", r[11], r[12]])
    return out.getvalue().encode("utf-8-sig")  # BOM: Excel reads it as UTF-8


# =========================================================================
# CLI
# =========================================================================

def init_db():
    import psycopg
    dsn = os.getenv("DATABASE_URL")
    if not dsn:
        raise SystemExit("DATABASE_URL not set — copy .env.example to .env first")
    with psycopg.connect(dsn, connect_timeout=20) as conn:
        conn.execute(SCHEMA)
        conn.commit()
    print("table speeding_events ready")

    url, key = os.getenv("SUPABASE_URL"), os.getenv("SUPABASE_SERVICE_KEY")
    if url and key:
        import requests
        r = requests.post(f"{url.rstrip('/')}/storage/v1/bucket",
                          json={"name": BUCKET, "id": BUCKET, "public": True},
                          headers={"Authorization": f"Bearer {key}",
                                   "Content-Type": "application/json"}, timeout=20)
        print(f"bucket '{BUCKET}': "
              + ("created" if r.status_code in (200, 201) else
                 "already exists" if "exist" in r.text.lower() else
                 f"HTTP {r.status_code} {r.text[:80]}"))
    else:
        print("SUPABASE_URL / SUPABASE_SERVICE_KEY not set — frames will be skipped")


def recent(n=20):
    import psycopg
    dsn = os.getenv("DATABASE_URL")
    if not dsn:
        raise SystemExit("DATABASE_URL not set")
    with psycopg.connect(dsn, connect_timeout=20) as conn:
        rows = conn.execute(
            "SELECT id, detected_at, camera, vehicle_id, vehicle_type, "
            "speed_kmh, speed_limit, frame_url FROM speeding_events "
            "ORDER BY detected_at DESC LIMIT %s", (n,)).fetchall()
    if not rows:
        print("no events logged yet")
        return
    print(f"{'id':>5} {'when':<20} {'camera':<18} {'veh':>5} {'type':<7} "
          f"{'km/h':>6} {'limit':>6}  frame")
    for r in rows:
        print(f"{r[0]:>5} {r[1]:%Y-%m-%d %H:%M:%S}  {r[2][:17]:<18} {r[3]:>5} "
              f"{r[4][:7]:<7} {r[5]:>6.1f} {r[6]:>6.0f}  "
              f"{'yes' if r[7] else '-'}")


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--init", action="store_true", help="create table + bucket")
    ap.add_argument("--recent", nargs="?", type=int, const=20,
                    help="show the most recent events")
    a = ap.parse_args()
    if a.init:
        init_db()
    elif a.recent is not None:
        recent(a.recent)
    else:
        ap.print_help()


if __name__ == "__main__":
    main()
