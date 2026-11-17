"""Checks for the speeding-event qualification rules.

    python3 test_speedlog.py

No cloud database and no network (the local copy goes in a scratch folder): these exercise the decision logic that says whether
a reading deserves to become a permanent record. That logic is where a bug is
costly -- a false speeding record is much worse than a missed one -- and it is
pure state machine, so it can be tested exactly.
"""

import os
import shutil
import tempfile
import time
import types
from contextlib import closing
from datetime import datetime, timezone

import numpy as np

from speedlog import (MIN_DURATION_S, UPDATE_MARGIN_KMH, Candidate,
                      evidence_jpeg)

FPS = 30.0
LIMIT = 70.0


def feed(c, speeds, state="ok", t0=0.0, fps=FPS):
    """Feed a sequence of readings, returning the times at which it fired."""
    fired = []
    for i, s in enumerate(speeds):
        t = t0 + i / fps
        if c.update(t, state if not isinstance(s, tuple) else s[1],
                    s if not isinstance(s, tuple) else s[0], LIMIT):
            fired.append(t)
    return fired


def check_brief_spike_rejected():
    """A few frames over the limit must NOT create a record."""
    n = int(0.2 * FPS)                       # 0.2 s, well under the threshold
    fired = feed(Candidate(), [90.0] * n)
    assert not fired, f"a {n}-frame spike produced a record"

    # Even a spike just short of the threshold must be rejected.
    n = int((MIN_DURATION_S - 0.05) * FPS)
    fired = feed(Candidate(), [90.0] * n)
    assert not fired, f"a {MIN_DURATION_S - 0.05:.2f}s burst produced a record"
    print(f"ok  brief spikes rejected (<{MIN_DURATION_S}s over the limit)")


def check_sustained_fires_once():
    """Sustained speeding fires exactly one record, after the duration."""
    fired = feed(Candidate(), [90.0] * int(3 * FPS))
    assert len(fired) == 1, f"expected 1 record, got {len(fired)}"
    assert fired[0] >= MIN_DURATION_S - 1e-9, \
        f"fired at {fired[0]:.3f}s, before the {MIN_DURATION_S}s threshold"
    assert fired[0] < MIN_DURATION_S + 2 / FPS, "fired later than it should"
    print(f"ok  sustained speeding fires once, at {fired[0]:.2f}s")


def check_streak_resets():
    """Exceedance must be continuous, not cumulative."""
    half = int((MIN_DURATION_S * 0.6) * FPS)
    # over, then a dip below the limit, then over again -- neither run is enough
    seq = [90.0] * half + [50.0] * 3 + [90.0] * half
    assert not feed(Candidate(), seq), "a broken streak was accumulated"

    # a dip that is merely *unreliable* must also reset it
    c = Candidate()
    fired = []
    for i in range(half):
        c.update(i / FPS, "ok", 90.0, LIMIT)
    for i in range(3):
        c.update((half + i) / FPS, "unreliable", None, LIMIT)
    for i in range(half):
        if c.update((half + 3 + i) / FPS, "ok", 90.0, LIMIT):
            fired.append(i)
    assert not fired, "an UNRELIABLE gap did not reset the streak"
    print("ok  streak resets on a dip below the limit or an unreliable reading")


def check_unreliable_never_logs():
    """An UNRELIABLE reading must never become a record, however sustained."""
    c = Candidate()
    for i in range(int(5 * FPS)):
        assert not c.update(i / FPS, "unreliable", 200.0, LIMIT), \
            "an UNRELIABLE reading produced a speeding record"
    for i in range(int(5 * FPS)):
        assert not c.update(i / FPS, "warmup", 200.0, LIMIT), \
            "a warmup reading produced a speeding record"
    print("ok  unreliable and warmup readings never produce a record")


def check_under_limit_never_logs():
    c = Candidate()
    for i in range(int(5 * FPS)):
        assert not c.update(i / FPS, "ok", LIMIT - 0.1, LIMIT)
    assert not c.update(5.0, "ok", LIMIT, LIMIT), "exactly at the limit fired"
    print("ok  at or below the limit never produces a record")


def check_peak_not_first():
    """The recorded speed must converge to the maximum, not the first reading."""
    c = Candidate()
    # accelerating: 75 -> 100 over three seconds
    speeds = list(np.linspace(75.0, 100.0, int(3 * FPS)))
    fired = feed(c, speeds)
    assert fired, "sustained speeding never fired"
    assert c.peak >= 99.0, f"peak tracked as {c.peak:.1f}, expected ~100"
    assert c.logged_at_peak >= 99.0 - UPDATE_MARGIN_KMH, \
        f"logged {c.logged_at_peak:.1f}, far below the {c.peak:.1f} peak"
    print(f"ok  converges to peak {c.peak:.1f} km/h, not the first reading "
          f"({speeds[0]:.1f})")


def check_update_margin():
    """A steady speed must not re-fire every frame."""
    c = Candidate()
    fired = feed(c, [90.0] * int(6 * FPS))
    assert len(fired) == 1, \
        f"steady speed re-fired {len(fired)} times — one upload per frame"

    # a materially higher peak *should* re-fire, a trivial one should not
    c2 = Candidate()
    feed(c2, [90.0] * int(2 * FPS))
    n_before = c2.logged_at_peak
    assert not c2.update(3.0, "ok", n_before + UPDATE_MARGIN_KMH * 0.5, LIMIT), \
        "a sub-margin increase re-fired"
    assert c2.update(3.1, "ok", n_before + UPDATE_MARGIN_KMH * 2, LIMIT), \
        "a materially higher peak did not re-fire"
    print(f"ok  re-fires only when the peak improves by >{UPDATE_MARGIN_KMH} km/h")


def check_frame_rate_independence():
    """The rule is a duration, so it must behave the same at any frame rate."""
    for fps in (24.0, 30.0, 60.0):
        c = Candidate()
        fired = feed(c, [90.0] * int(2 * fps), fps=fps)
        assert len(fired) == 1, f"{fps} fps: {len(fired)} records"
        assert abs(fired[0] - MIN_DURATION_S) < 2 / fps, \
            f"{fps} fps fired at {fired[0]:.3f}s, not ~{MIN_DURATION_S}s"
    print("ok  identical behaviour at 24 / 30 / 60 fps")


def check_wall_clock_timestamps():
    """Live mode stamps with time.time(); recorded mode with idx/fps.

    A float precision loss at 1e9 magnitude would silently stop every live event
    from ever firing, while recorded video kept working -- exactly the kind of
    mode-specific silent failure that is hard to notice in the field.
    """
    import time as _t
    for label, t0 in (("recorded", 0.0), ("live", _t.time())):
        c = Candidate()
        fired = [i / FPS for i in range(90)
                 if c.update(t0 + i / FPS, "ok", 90.0, LIMIT)]
        assert fired, f"{label}: never fired"
        assert abs(fired[0] - MIN_DURATION_S) < 0.05, \
            f"{label}: fired at {fired[0]:.3f}s, expected {MIN_DURATION_S}"
    now = _t.time()
    assert (now + 1 / FPS) - now > 0, "float64 cannot resolve a frame at 1e9"
    print("ok  wall-clock and media-position timestamps behave identically")


def check_evidence_frame():
    """The crop must be produced, annotated, and small enough to store."""
    frame = np.full((1080, 1920, 3), 90, np.uint8)
    box = np.array([900.0, 500.0, 960.0, 545.0])       # a small distant vehicle
    jpeg = evidence_jpeg(frame, box, 42, "Car", 96.4, 70, "Test Cam", 1.7e9)
    assert jpeg and jpeg[:2] == b"\xff\xd8", "no JPEG produced"
    assert len(jpeg) < 200_000, f"evidence frame is {len(jpeg)/1024:.0f} KB"
    import cv2
    img = cv2.imdecode(np.frombuffer(jpeg, np.uint8), cv2.IMREAD_COLOR)
    assert img is not None and img.shape[1] >= 420, "crop not upscaled for legibility"

    # a box at the frame edge must clamp, not throw
    assert evidence_jpeg(frame, np.array([-40.0, -30.0, 60.0, 50.0]),
                         1, "Car", 90, 70, "c", 1.7e9) is not None
    # a degenerate box must be declined, not crash
    assert evidence_jpeg(frame, np.array([10.0, 10.0, 12.0, 12.0]),
                         1, "Car", 90, 70, "c", 1.7e9) is None
    print(f"ok  evidence crop rendered ({len(jpeg)/1024:.0f} KB, "
          f"{img.shape[1]}x{img.shape[0]})")


class FakeCloud:
    """Stands in for the psycopg connection: records writes, answers the pull."""
    def __init__(self, pulled=(), down=False):
        self.sql, self.pulled, self.down = [], list(pulled), down

    def execute(self, sql, args=None):
        if self.down:
            raise OSError("no internet")
        self.sql.append((sql, args))
        return types.SimpleNamespace(fetchall=lambda: self.pulled)


def check_local_first_then_cloud():
    """Offline, an event is on the dashboard at once and marked unsent. Online,
    the row goes up before its images, the original image before the cleaned
    one, a higher peak goes up again, and guard responses come back."""
    import speedlog
    from speedlog import Event, SpeedLogger, _local, _pull, _push_evidence, _push_rows
    tmp = tempfile.mkdtemp()
    saved = (speedlog.LOCAL_DB, speedlog.EVIDENCE_DIR, speedlog._storage_upload,
             os.environ.get("DATABASE_URL"))
    speedlog.LOCAL_DB = os.path.join(tmp, "log.db")
    speedlog.EVIDENCE_DIR = os.path.join(tmp, "evidence")
    os.environ["DATABASE_URL"] = "postgresql://unused"
    sid = "0f8fad5b-d9cb-469f-a165-70867728950e"
    names = []

    def upload(jpeg, name):
        names.append((name, jpeg))
        if flaky and not name.endswith("_raw.jpg"):
            raise RuntimeError("network")
        return "https://store/" + name
    speedlog._storage_upload = upload
    try:
        lg = SpeedLogger("c", 40, enabled=False)
        lg._save([Event(time.time(), "c", 7, "Car", 60.0, 40.0, sid, b"raw", view=b"view")])
        d = speedlog.dashboard()
        assert d["total"] == 1 and d["pending"] == 1 and d["rows"][0]["pending"], d
        assert d["rows"][0]["frame"] == f"/evidence/{sid}/7.jpg"

        with closing(_local()) as db:             # offline: kept, still pending
            try:
                _push_rows(FakeCloud(down=True), db)
            except OSError:
                pass
        assert speedlog.dashboard()["pending"] == 1

        cloud, flaky = FakeCloud(), True
        with closing(_local()) as db:
            _push_rows(cloud, db)
            assert not names, "an image went up before the row"
            assert "INSERT INTO speeding_events" in cloud.sql[0][0]
            try:                                  # the cleaned view fails...
                _push_evidence(cloud, db)
            except RuntimeError:
                pass
            assert cloud.sql[-1][1][0] == f"https://store/{sid}/7_raw.jpg", "original not shown"
            flaky = False                         # ...so both go again next time
            names.clear()
            assert _push_evidence(cloud, db) is False
        assert [n for n, _ in names] == [f"{sid}/7_raw.jpg", f"{sid}/7.jpg"], names
        assert [j for _, j in names] == [b"raw", b"view"]
        assert speedlog.dashboard()["pending"] == 0

        lg._save([Event(time.time(), "c", 7, "Car", 72.0, 40.0, sid, b"raw2", view=b"view2")])
        d = speedlog.dashboard()
        assert d["pending"] == 1 and d["rows"][0]["kmh"] == 72.0, "higher peak not kept"
        with closing(_local()) as db:
            _push_rows(cloud, db)
        assert cloud.sql[-1][1][4] == 72.0 and speedlog.pending() == 0

        # the cloud's answer comes back; its older peak doesn't win
        now = datetime.now(timezone.utc)
        mine = (41, time.time(), "c", 7, "Car", 60.0, 40.0, f"https://store/{sid}/7.jpg",
                sid, None, None, "Asha", now, 3)
        other = (40, time.time() - 60, "Gate 2", 3, "Bus", 55.0, 40.0, None,
                 "1b4e28ba-2fa1-11d2-883f-0016d3cca427", None, None, None, None, 0)
        lg._save([Event(time.time(), "c", 7, "Car", 80.0, 40.0, sid, b"raw3")])
        with closing(_local()) as db:
            _pull(FakeCloud([mine, other]), db, full=True)
        d = speedlog.dashboard()
        r = {x["vehicle_id"]: x for x in d["rows"]}
        assert r[7]["responder"] == "Asha" and r[7]["alerted"] == 3 and r[7]["kmh"] == 80.0
        assert r[7]["frame"] == f"/evidence/{sid}/7.jpg", "this Mac's image not preferred"
        assert r[3]["frame"] is None and d["total"] == 2 and d["cameras"] == 2

        # a synced row the cloud no longer has goes; an unsent one stays
        with closing(_local()) as db:
            _pull(FakeCloud([]), db, full=True)
        assert [x["vehicle_id"] for x in speedlog.dashboard()["rows"]] == [7]
    finally:
        (speedlog.LOCAL_DB, speedlog.EVIDENCE_DIR, speedlog._storage_upload, dsn) = saved
        if dsn is None:
            del os.environ["DATABASE_URL"]
        else:
            os.environ["DATABASE_URL"] = dsn
        shutil.rmtree(tmp)
    print("ok  local first: shown offline, row before images, original first, responses come back")


def check_export_ranges():
    """The Download menu's CSV: the range filters by time, times are IST, and
    database text can't become a spreadsheet formula."""
    import speedlog
    tmp, saved = tempfile.mkdtemp(), speedlog.LOCAL_DB
    speedlog.LOCAL_DB = os.path.join(tmp, "log.db")
    try:
        now = time.time()
        with closing(speedlog._local()) as db:
            db.executemany("INSERT INTO speeding_events (session_id, vehicle_id, detected_at, camera,"
                           " vehicle_type, speed_kmh, speed_limit) VALUES ('s', ?, ?, ?, 'Car', 55, 40)",
                           [(1, now - 2 * 86400, "Gate"), (2, now - 60, "=HYPERLINK()"),
                            (3, 1784000000.0, "Fixed")])      # 2026-07-14 03:33:20 UTC
            db.commit()
        day = speedlog.export_csv(1).decode("utf-8-sig").splitlines()
        assert len(day) == 2 and ",'=HYPERLINK()," in day[1], day     # one row, formula neutralised
        everything = speedlog.export_csv(None)
        rows = everything.decode("utf-8-sig").splitlines()
        assert len(rows) == 4 and rows[1].startswith("2026-07-14 09:03:20,"), rows   # +5:30
        assert everything.startswith(b"\xef\xbb\xbf"), "no BOM for Excel"
    finally:
        speedlog.LOCAL_DB = saved
        shutil.rmtree(tmp)
    print("ok  export: range filters, IST times, no formulas")


if __name__ == "__main__":
    check_export_ranges()
    check_brief_spike_rejected()
    check_sustained_fires_once()
    check_streak_resets()
    check_unreliable_never_logs()
    check_under_limit_never_logs()
    check_peak_not_first()
    check_update_margin()
    check_frame_rate_independence()
    check_wall_clock_timestamps()
    check_evidence_frame()
    check_local_first_then_cloud()
    print("\nall speed-log checks passed")
