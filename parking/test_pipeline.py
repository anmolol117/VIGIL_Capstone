"""Self-checks for the parts of the pipeline that can be wrong quietly.

Run: python test_pipeline.py

Registration and the occupancy state machine both fail in ways that still
produce plausible-looking numbers, so each gets a check that fails loudly if the
logic breaks. No framework, no fixtures - asserts and a synthetic lot.
"""

from __future__ import annotations

import cv2
import numpy as np

from occupancy import Observation, OccupancyTracker
from stabilize import Stabilizer, warp_boxes

SQUARE = np.array([[100, 100], [200, 100], [200, 200], [100, 200]], dtype=np.float32)


def synthetic_lot(w=640, h=480, seed=0):
    """Textured stand-in for asphalt: ORB needs corners to lock onto."""
    rng = np.random.default_rng(seed)
    img = np.full((h, w, 3), 60, dtype=np.uint8)
    for _ in range(120):
        x, y = int(rng.integers(0, w - 40)), int(rng.integers(0, h - 40))
        colour = tuple(int(c) for c in rng.integers(80, 255, 3))
        if rng.random() < 0.5:
            cv2.rectangle(img, (x, y), (x + int(rng.integers(8, 36)), y + int(rng.integers(8, 36))), colour, -1)
        else:
            cv2.circle(img, (x, y), int(rng.integers(4, 16)), colour, -1)
    return img


def test_warp_boxes_round_trip():
    """A box warped through H and back through H-inverse must come home."""
    H = np.array([[1.0, 0.0, 25.0], [0.0, 1.0, -12.0], [0.0, 0.0, 1.0]])
    boxes = np.array([[10, 20, 60, 80], [200, 150, 260, 210]], dtype=np.float32)

    there = warp_boxes(H, boxes)
    back = warp_boxes(np.linalg.inv(H), there)

    assert np.allclose(there[0], [35, 8, 85, 68], atol=1e-3), there[0]
    assert np.allclose(back, boxes, atol=1e-3), back


def test_warp_boxes_contains_rotated_corners():
    """Under rotation the AABB approximation must still enclose the true quad."""
    theta = np.deg2rad(12)
    H = np.array(
        [[np.cos(theta), -np.sin(theta), 0.0], [np.sin(theta), np.cos(theta), 0.0], [0, 0, 1.0]]
    )
    box = np.array([[10, 20, 60, 80]], dtype=np.float32)
    corners = np.array([[10, 20], [60, 20], [60, 80], [10, 80]], dtype=np.float32).reshape(-1, 1, 2)

    got = warp_boxes(H, box)[0]
    true_pts = cv2.perspectiveTransform(corners, H.astype(np.float32)).reshape(-1, 2)

    assert got[0] <= true_pts[:, 0].min() + 1e-3 and got[2] >= true_pts[:, 0].max() - 1e-3
    assert got[1] <= true_pts[:, 1].min() + 1e-3 and got[3] >= true_pts[:, 1].max() - 1e-3


def test_registration_recovers_known_translation():
    """A frame shifted by a known offset must register back onto the reference."""
    reference = synthetic_lot()
    dx, dy = 17.0, -11.0
    shifted = cv2.warpAffine(reference, np.float32([[1, 0, dx], [0, 1, dy]]), (640, 480))

    reg = Stabilizer(reference, min_inliers=15).register(shifted)

    assert reg.ok, f"registration failed: inliers={reg.inliers} ratio={reg.inlier_ratio:.2f}"
    # A feature at reference (a, b) sits at (a+dx, b+dy) in the shifted frame,
    # so H must undo exactly that.
    probe = np.array([[[300.0, 240.0]]], dtype=np.float32)
    got = cv2.perspectiveTransform(probe, reg.H.astype(np.float32))[0][0]
    assert np.allclose(got, [300 - dx, 240 - dy], atol=2.0), got


def test_registration_falls_back_on_garbage():
    """An unmatchable frame must reuse the last good H and flag itself, not crash."""
    stab = Stabilizer(synthetic_lot(), min_inliers=15)
    reg = stab.register(np.zeros((480, 640, 3), dtype=np.uint8))

    assert not reg.ok
    assert np.allclose(reg.H, np.eye(3)), "should have fallen back to the last good H"


def _tracker(**kw):
    defaults = dict(
        meters_per_pixel=0.05, park_window_s=1.0, park_threshold_m=1.5,
        slot_tolerance_m=0.5, debounce_k=3, track_ttl_s=5.0,
    )
    return OccupancyTracker([("slot-1", SQUARE)], **{**defaults, **kw})


def _obs(cx, cy, tid=1):
    return [Observation(tid, (cx - 20, cy - 20, cx + 20, cy + 20), "car", "car")]


def test_slot_assignment_inside_outside_and_tolerance():
    occ = _tracker()

    assert occ._nearest_slot((150, 150))[0] == "slot-1", "centre of the bay"
    assert occ._nearest_slot((100, 150))[0] == "slot-1", "exactly on the painted line"
    # 0.5 m tolerance at 0.05 m/px = 10 px of slack outside the line.
    assert occ._nearest_slot((205, 150))[0] == "slot-1", "just outside, within tolerance"
    assert occ._nearest_slot((260, 150))[0] is None, "out in the aisle"


def test_stationary_parks_and_crossing_does_not():
    occ = _tracker()
    for i in range(12):  # 1.2 s of history at 0.1 s steps
        occ.update(_obs(150, 150), i * 0.1)
    assert occ.tracks[1].is_parked, "a motionless vehicle must read as parked"

    crossing = _tracker()
    for i in range(12):  # traverses the bay at ~2 px per sample
        crossing.update(_obs(110 + i * 8, 150), i * 0.1)
    assert not crossing.tracks[1].is_parked, "a vehicle driving through must not read as parked"


def test_debounce_delays_commit_until_k_samples():
    """The bay must not flip on the first frame that looks occupied."""
    occ = _tracker(debounce_k=3)
    fired, parked_at = [], None
    for i in range(16):
        events, _ = occ.update(_obs(150, 150), i * 0.1)
        if parked_at is None and occ.tracks[1].is_parked:
            parked_at = i
        fired.extend((i, e.event) for e in events)

    assert parked_at is not None, "vehicle never registered as parked"
    assert [e for _, e in fired] == ["occupied"], fired
    assert fired[0][0] == parked_at + 2, (
        f"parked from sample {parked_at} but bay committed at {fired[0][0]}; "
        "a debounce_k of 3 must commit on the third agreeing sample"
    )


def test_single_sample_dropout_produces_no_event():
    """One missed detection must never generate a spurious vacated event."""
    occ = _tracker()
    events_seen = []

    for i in range(15):
        events, _ = occ.update(_obs(150, 150), i * 0.1)
        events_seen.extend(e.event for e in events)

    occ.update([], 1.5)  # the dropped frame
    for i in range(5):
        events, _ = occ.update(_obs(150, 150), 1.6 + i * 0.1)
        events_seen.extend(e.event for e in events)

    assert events_seen == ["occupied"], f"expected one occupied event, got {events_seen}"
    assert occ.status()["slot-1"].occupied, "bay must still read occupied"


def test_departure_emits_vacated_with_dwell():
    occ = _tracker()
    for i in range(15):
        occ.update(_obs(150, 150), i * 0.1)

    vacated = []
    for i in range(10):  # gone for good: past the 5 s TTL
        events, _ = occ.update([], 2.0 + i * 1.0)
        vacated.extend(e for e in events if e.event == "vacated")

    assert len(vacated) == 1, f"expected exactly one vacated event, got {len(vacated)}"
    assert vacated[0].dwell_seconds and vacated[0].dwell_seconds > 0, vacated[0]


def test_counts_reflect_parked_versus_moving():
    occ = _tracker()
    for i in range(15):
        parked = Observation(1, (130, 130, 170, 170), "car", "car")
        mover = Observation(2, (300 + i * 9, 300, 340 + i * 9, 340), "car", "car")
        events, counts = occ.update([parked, mover], i * 0.1)

    assert counts.occupied == 1, counts
    assert counts.free == 0, counts
    assert counts.moving == 1, "the vehicle crossing the lot is not occupying a bay"
    assert counts.detections == 2, counts


def test_unconfirmed_tracks_are_dropped():
    """ByteTrackTracker signals 'not yet a track' with -1, never with None.

    tracker_id is a numpy int array, so `tid is None` is false for every element.
    Guarding on None lets every unconfirmed detection through as one phantom
    track, which then wanders the lot and can claim a bay.
    """
    import types

    from pipeline import to_observations

    tracked = types.SimpleNamespace(
        tracker_id=np.array([-1, 0, 7, -1], dtype=np.int64),
        class_id=np.array([3, 3, 9, 4], dtype=np.int64),
        xyxy=np.array(
            [[0, 0, 10, 10], [20, 20, 60, 60], [80, 80, 120, 120], [1, 1, 5, 5]],
            dtype=np.float32,
        ),
    )
    got = to_observations(tracked, {3: "car", 4: "van", 9: "motor"})

    assert [o.track_id for o in got] == [0, 7], [o.track_id for o in got]
    assert got[1].vehicle_type == "motorcycle", "VisDrone 'motor' maps to motorcycle"
    assert not any(o.track_id < 0 for o in got), "unconfirmed tracks must not survive"


def test_van_normalises_to_car_but_keeps_raw_label():
    import types

    from pipeline import to_observations

    tracked = types.SimpleNamespace(
        tracker_id=np.array([3], dtype=np.int64),
        class_id=np.array([4], dtype=np.int64),
        xyxy=np.array([[0, 0, 10, 10]], dtype=np.float32),
    )
    got = to_observations(tracked, {4: "van"})[0]

    assert got.vehicle_type == "car", "a van occupies a car bay"
    assert got.raw_class == "van", "the original label must stay recoverable"



def test_hold_keeps_bays_through_a_camera_outage():
    """Feed black for minutes, then back: tracks restart from nothing, and
    without the hold every parked car would 'leave' while it re-settles."""
    occ = _tracker(park_window_s=1.0, track_ttl_s=1.0)
    for i in range(15):
        occ.update(_obs(150, 150), i * 0.1)
    assert occ.status()["slot-1"].occupied

    events = []
    for i in range(30):  # camera back after 2 min; new track id, held 3 s
        ev, _ = occ.update(_obs(150, 150, tid=2), 120 + i * 0.1, hold=True)
        events += ev
    for i in range(10):  # hold released; the car is parked again by now
        ev, _ = occ.update(_obs(150, 150, tid=2), 123 + i * 0.1)
        events += ev
    assert events == [], [e.event for e in events]
    assert occ.status()["slot-1"].occupied

    for i in range(10):  # and a real departure still registers afterwards
        ev, _ = occ.update([], 124 + i * 1.0)
        events += ev
    assert [e.event for e in events] == ["vacated"], [e.event for e in events]


def test_black_frame_is_blank_but_a_dark_scene_is_not():
    from camera import is_blank
    rng = np.random.default_rng(0)
    assert is_blank(None)
    assert is_blank(np.zeros((480, 720, 3), np.uint8))
    assert is_blank(rng.integers(0, 6, (480, 720, 3), dtype=np.uint8)), "analog noise floor"
    night = rng.integers(0, 40, (480, 720, 3), dtype=np.uint8)  # dark, but textured
    assert not is_blank(night)
    assert not is_blank(synthetic_lot())


def test_ground_plane_measures_true_metres_near_and_far():
    """Angled camera: the back of a 2.5 x 5 m bay looks 3x narrower than the
    front. On the ground plane a metre must be a metre at both ends."""
    from occupancy import ground_homography, to_ground

    quad = [[300, 200], [340, 200], [380, 400], [260, 400]]  # back edge far, front near
    H = ground_homography({"points": quad, "width_m": 2.5, "length_m": 5})
    assert np.allclose(to_ground(H, quad), [[0, 0], [50, 0], [50, 100], [0, 100]], atol=0.01)

    one_m_near = to_ground(H, [[260, 400], [260 + 120 / 2.5, 400]])  # 1 m along the front
    one_m_far = to_ground(H, [[300, 200], [300 + 40 / 2.5, 200]])    # 1 m along the back
    for seg in (one_m_near, one_m_far):
        metres = np.linalg.norm(seg[1] - seg[0]) / 20.0
        assert abs(metres - 1.0) < 0.01, metres

    # and a parked car standing in that perspective bay claims it
    occ = OccupancyTracker([("bay", np.float32(quad))], meters_per_pixel=999,
                           ground_H=H, anchor=0.3, park_window_s=1.0, debounce_k=3)
    car = [Observation(1, (280, 230, 360, 390), "car", "car")]
    for i in range(16):
        occ.update(car, i * 0.1)
    assert occ.status()["bay"].occupied, "car in a perspective bay must claim it"

if __name__ == "__main__":
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    failed = 0
    for test in tests:
        try:
            test()
            print(f"  PASS  {test.__name__}")
        except AssertionError as exc:
            failed += 1
            print(f"  FAIL  {test.__name__}: {exc}")
    print(f"\n{len(tests) - failed}/{len(tests)} passed")
    raise SystemExit(1 if failed else 0)
