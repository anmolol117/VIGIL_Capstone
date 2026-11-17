"""Turn stabilized vehicle tracks into per-bay occupancy.

Everything here works in reference-frame pixels, where the camera is effectively
static. That is what makes "is this car parked?" a plain displacement test rather
than a heuristic: a parked car in this coordinate system does not move at all.

Distances are configured in metres and converted with the lot's calibrated
meters_per_pixel, so thresholds stay meaningful across lots flown at different
altitudes.

An angled camera (wall or pole) has no single meters_per_pixel: a metre near the
camera spans far more pixels than one at the back. Such a lot carries a ground
calibration instead - four image points around a ground rectangle of known size -
and every bay and vehicle position is mapped onto a true top-down plane before
any distance is measured.
"""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass, field

import cv2
import numpy as np

GROUND_PX_PER_M = 20.0  # the top-down ground plane: 5 cm per pixel


def ground_homography(ground: dict | None) -> np.ndarray | None:
    """Image -> top-down ground plane, from four image points going around a
    ground rectangle of width_m x length_m (e.g. a bay's painted corners)."""
    if not ground:
        return None
    w = ground["width_m"] * GROUND_PX_PER_M
    l = ground["length_m"] * GROUND_PX_PER_M
    src = np.asarray(ground["points"], dtype=np.float32)
    dst = np.float32([[0, 0], [w, 0], [w, l], [0, l]])
    return cv2.getPerspectiveTransform(src, dst)


def to_ground(H: np.ndarray, points) -> np.ndarray:
    pts = np.asarray(points, dtype=np.float32).reshape(-1, 1, 2)
    return cv2.perspectiveTransform(pts, H).reshape(-1, 2)


@dataclass(frozen=True)
class Observation:
    """One tracked vehicle for one sample, in reference-frame pixels."""

    track_id: int
    xyxy: tuple[float, float, float, float]
    vehicle_type: str
    raw_class: str


@dataclass(frozen=True)
class SlotEvent:
    slot_id: str
    event: str  # 'occupied' | 'vacated'
    at: float  # unix seconds
    track_id: int | None
    vehicle_type: str | None
    dwell_seconds: float | None


@dataclass(frozen=True)
class Counts:
    occupied: int
    free: int
    moving: int
    detections: int


@dataclass
class Track:
    track_id: int
    vehicle_type: str
    raw_class: str
    first_seen: float
    last_seen: float
    history: deque = field(default_factory=deque)  # (t, (x, y))
    is_parked: bool = False
    slot_id: str | None = None


@dataclass
class _SlotState:
    """Committed state plus the pending-change counter that debounces it."""

    occupied: bool = False
    since: float | None = None
    track_id: int | None = None
    vehicle_type: str | None = None
    pending: bool | None = None
    pending_count: int = 0
    pending_obs: Observation | None = None


class OccupancyTracker:
    def __init__(
        self,
        slots: list[tuple[str, np.ndarray]],
        meters_per_pixel: float,
        *,
        lot_boundary: np.ndarray | None = None,
        anchor: str = "centroid",
        park_window_s: float = 10.0,
        park_threshold_m: float = 1.5,
        slot_tolerance_m: float = 0.5,
        debounce_k: int = 3,
        track_ttl_s: float = 5.0,
        ground_H: np.ndarray | None = None,
    ) -> None:
        """Slots, boundary and observations arrive in reference-image pixels.
        With ground_H they are all moved onto the top-down ground plane, where
        meters_per_pixel is fixed and true everywhere."""
        if ground_H is not None:
            meters_per_pixel = 1.0 / GROUND_PX_PER_M
        if meters_per_pixel is None or meters_per_pixel <= 0:
            raise ValueError("meters_per_pixel must be calibrated and positive")
        if anchor not in ("centroid", "bottom") and not 0 <= float(anchor) <= 1:
            raise ValueError(f"anchor must be 'centroid', 'bottom' or 0..1, got {anchor!r}")

        self.ground_H = ground_H
        place = (lambda p: to_ground(ground_H, p)) if ground_H is not None else (lambda p: p)
        self.slots = [(sid, place(np.asarray(poly, dtype=np.float32))) for sid, poly in slots]
        self.lot_boundary = (
            None if lot_boundary is None or len(lot_boundary) < 3
            else place(np.asarray(lot_boundary, dtype=np.float32))
        )
        self.anchor = anchor
        self.park_window_s = park_window_s
        self.debounce_k = debounce_k
        self.track_ttl_s = track_ttl_s

        # Metres are the honest unit; pixels are a per-lot accident of altitude.
        self.park_threshold_px = park_threshold_m / meters_per_pixel
        self.slot_tolerance_px = slot_tolerance_m / meters_per_pixel

        self.tracks: dict[int, Track] = {}
        self._slot_state: dict[str, _SlotState] = {sid: _SlotState() for sid, _ in self.slots}

    def update(
        self, observations: list[Observation], now: float, *, hold: bool = False
    ) -> tuple[list[SlotEvent], Counts]:
        """hold=True keeps every bay as it is while tracks rebuild - used after
        the camera feed returns, when parked cars have not yet been watched long
        enough to read as parked again and would otherwise all 'leave'."""
        self._update_tracks(observations, now)
        self._prune(now)

        occupants = self._claim_slots()

        events: list[SlotEvent] = []
        for sid, _ in self.slots:
            if hold:
                st = self._slot_state[sid]
                st.pending, st.pending_count, st.pending_obs = None, 0, None
            else:
                self._debounce(sid, occupants.get(sid), now, events)

        occupied = sum(1 for st in self._slot_state.values() if st.occupied)
        in_lot = [t for t in self.tracks.values() if self._in_lot(t)]
        counts = Counts(
            occupied=occupied,
            free=len(self.slots) - occupied,
            moving=sum(1 for t in in_lot if not t.is_parked),
            detections=len(observations),
        )
        return events, counts

    def anchor_point(self, xyxy) -> tuple[float, float]:
        """Where a vehicle 'is' on the ground, in image pixels.

        A near-nadir aerial view has no foreshortening, so the box centroid is
        right. From an angle the top of the box is roof, hanging over the bay
        behind; the car's footprint is the lower part of the box. A number
        is the height above the box bottom as a fraction of the box (0.5 is
        the centroid, 0 the bottom edge); ~0.3 suits a wall or pole camera.
        """
        x1, y1, x2, y2 = xyxy
        up = {"centroid": 0.5, "bottom": 0.0}.get(self.anchor, self.anchor)
        return ((x1 + x2) / 2.0, float(y2) - float(up) * (y2 - y1))

    def _update_tracks(self, observations, now):
        for obs in observations:
            track = self.tracks.get(obs.track_id)
            if track is None:
                track = Track(obs.track_id, obs.vehicle_type, obs.raw_class, now, now)
                self.tracks[obs.track_id] = track

            track.last_seen = now
            track.vehicle_type = obs.vehicle_type
            track.raw_class = obs.raw_class
            point = self.anchor_point(obs.xyxy)
            if self.ground_H is not None:
                point = tuple(to_ground(self.ground_H, [point])[0])
            track.history.append((now, point))

            # Keep one sample straddling the window edge. Dropping everything
            # older than the window leaves the oldest survivor strictly younger
            # than it, so the full-coverage test below could never be satisfied.
            while len(track.history) > 1 and now - track.history[1][0] > self.park_window_s:
                track.history.popleft()

            track.is_parked = self._is_parked(track, now)

    def _is_parked(self, track: Track, now: float) -> bool:
        """Parked = observed for a full window and barely moved across it.

        Requiring full window coverage is what stops a car that has only just
        appeared from being called parked before there is evidence either way.
        """
        if not track.history:
            return False
        if now - track.history[0][0] < self.park_window_s:
            return False

        points = np.array([p for _, p in track.history], dtype=np.float32)
        spread = np.linalg.norm(points - points[-1], axis=1).max()
        return bool(spread <= self.park_threshold_px)

    def _prune(self, now):
        for tid in [t for t, tr in self.tracks.items() if now - tr.last_seen > self.track_ttl_s]:
            del self.tracks[tid]

    def _claim_slots(self) -> dict[str, Track]:
        """Assign each parked track to at most one bay, best claim wins ties."""
        claims: dict[str, tuple[float, Track]] = {}
        for track in self.tracks.values():
            track.slot_id = None
            if not track.is_parked or not track.history:
                continue

            sid, depth = self._nearest_slot(track.history[-1][1])
            if sid is None:
                continue

            track.slot_id = sid
            best = claims.get(sid)
            if best is None or depth > best[0]:
                if best is not None:
                    best[1].slot_id = None
                claims[sid] = (depth, track)
            else:
                track.slot_id = None

        return {sid: track for sid, (_, track) in claims.items()}

    def _nearest_slot(self, point) -> tuple[str | None, float]:
        """Bay containing the point, or the nearest one it sits just outside of.

        The tolerance covers the common real case of a centroid landing a few
        centimetres over a painted line - not a car in the driving aisle.
        """
        best_id, best_dist = None, None
        pt = (float(point[0]), float(point[1]))
        for sid, poly in self.slots:
            dist = cv2.pointPolygonTest(poly, pt, True)
            if best_dist is None or dist > best_dist:
                best_id, best_dist = sid, dist

        if best_dist is None or best_dist < -self.slot_tolerance_px:
            return None, 0.0
        return best_id, best_dist

    def _debounce(self, sid, occupant, now, events):
        """Commit a bay change only after K consecutive agreeing samples."""
        state = self._slot_state[sid]
        observed = occupant is not None

        if observed == state.occupied:
            state.pending, state.pending_count, state.pending_obs = None, 0, None
            if observed:  # keep the occupant's identity fresh
                state.track_id = occupant.track_id
                state.vehicle_type = occupant.vehicle_type
            return

        if state.pending == observed:
            state.pending_count += 1
        else:
            state.pending, state.pending_count = observed, 1
        if observed:
            state.pending_obs = occupant

        if state.pending_count < self.debounce_k:
            return

        if observed:
            state.occupied = True
            state.since = now
            state.track_id = occupant.track_id
            state.vehicle_type = occupant.vehicle_type
            events.append(SlotEvent(sid, "occupied", now, state.track_id, state.vehicle_type, None))
        else:
            dwell = None if state.since is None else now - state.since
            events.append(SlotEvent(sid, "vacated", now, state.track_id, state.vehicle_type, dwell))
            state.occupied = False
            state.since = None
            state.track_id = None
            state.vehicle_type = None

        state.pending, state.pending_count, state.pending_obs = None, 0, None

    def _in_lot(self, track: Track) -> bool:
        if self.lot_boundary is None or not track.history:
            return True
        x, y = track.history[-1][1]
        return cv2.pointPolygonTest(self.lot_boundary, (float(x), float(y)), False) >= 0

    def status(self) -> dict[str, _SlotState]:
        return self._slot_state
