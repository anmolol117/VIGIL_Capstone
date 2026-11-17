"""Multi-angle vehicle speed + 3D localization.

Geometry helpers and the per-frame runtime. Calibration (focal length, ground
plane normal, camera height) is produced once by calibrate.py and read from a
JSON file -- no neural depth model runs here. Positions are exact ray/plane
intersections, so per-frame cost is arithmetic and there is no depth flicker to
differentiate into the speed output.

Camera convention is OpenCV: X right, Y down, Z forward, camera at the origin.
The ground plane is {P : n . P = -h}, where n is the unit up-vector expressed in
camera coordinates and h is the camera's height above the plane.
"""

import argparse
import json
from collections import deque, defaultdict

import cv2
import numpy as np

# --- physical constants / calibration knobs -------------------------------
# Real-world dimensions the scale estimate is anchored to. These are the tuning
# knobs for the whole system: if speeds read uniformly high or low, this is the
# number to adjust, not anything in the geometry.
CAR_WIDTH_M = 1.83        # median passenger-car body width
VEHICLE_CENTROID_H = 0.75 # height of a car's centroid above the road

# --- estimation parameters ------------------------------------------------
JITTER_FRAC = 0.006       # box-centre wobble as a fraction of box width
SIGMA_TO_REPORT = 2.0     # how many sigma a reading must clear to be claimed
SPEED_WINDOW_S = 0.6      # trajectory window the velocity fit runs over
MIN_SAMPLES = 4           # samples needed before any speed is reported
MAX_PLAUSIBLE_KMH = 200.0 # above this the reading is rejected, never zeroed
MAX_FIT_RESIDUAL_M = 1.5  # trajectory scatter above this means a bad track
STOPPED_KMH = 3.0         # below this the vehicle is reported as stopped

# Vehicle class names across both COCO (yolo11) and VisDrone label sets.
# Matching on name rather than index keeps this working when the model changes.
VEHICLE_NAMES = {"car", "van", "truck", "bus", "motor", "motorcycle"}
LABEL_MAP = {"motor": "Bike", "motorcycle": "Bike"}


# =========================================================================
# Geometry
# =========================================================================

def pixel_rays(K, pts):
    """Pixel coords (N,2) -> ray directions (N,3) in camera coords."""
    f, cx, cy = K
    pts = np.atleast_2d(np.asarray(pts, dtype=float))
    return np.stack([(pts[:, 0] - cx) / f,
                     (pts[:, 1] - cy) / f,
                     np.ones(len(pts))], axis=1)


def ground_points(K, n, h, pts, z=0.0):
    """Intersect rays through `pts` with the plane `z` metres above the ground.

    Returns (points_in_camera_frame (N,3), valid_mask (N,)). A ray is only valid
    if it actually travels downward toward the plane; rays at or above the
    horizon never intersect it.
    """
    d = pixel_rays(K, pts)
    denom = d @ n
    ok = denom < -1e-6
    t = np.where(ok, -(h - z) / np.where(ok, denom, -1.0), np.nan)
    return d * t[:, None], ok


def world_basis(n):
    """Orthonormal basis (right, fwd, up) with up = n."""
    for seed in (np.array([0.0, 0.0, 1.0]), np.array([0.0, 1.0, 0.0])):
        fwd = seed - seed.dot(n) * n
        if np.linalg.norm(fwd) > 1e-6:      # seed 1 degenerates in nadir views
            fwd /= np.linalg.norm(fwd)
            return np.cross(fwd, n), fwd, n
    raise ValueError("degenerate ground-plane normal")


def to_world(P, n, h):
    """Camera-frame points -> world frame: origin at camera nadir, +Z up.

    Ground points land at z=0 and the camera sits at (0, 0, h).
    """
    right, fwd, up = world_basis(n)
    P = np.atleast_2d(P)
    return np.stack([P @ right, P @ fwd, P @ up + h], axis=-1)


def box_centres(boxes):
    """xyxy boxes (N,4) -> centre pixels (N,2)."""
    boxes = np.atleast_2d(boxes)
    return np.stack([(boxes[:, 0] + boxes[:, 2]) / 2,
                     (boxes[:, 1] + boxes[:, 3]) / 2], axis=1)


# =========================================================================
# Calibration I/O
# =========================================================================

def save_calib(path, K, n, h, size):
    json.dump({"f": K[0], "cx": K[1], "cy": K[2],
               "n": list(map(float, n)), "h": float(h),
               "width": size[0], "height": size[1]},
              open(path, "w"), indent=2)


def load_calib(path):
    c = json.load(open(path))
    n = np.array(c["n"], dtype=float)
    return (c["f"], c["cx"], c["cy"]), n / np.linalg.norm(n), c["h"], (c["width"], c["height"])


# =========================================================================
# Velocity
# =========================================================================

def fit_velocity(hist):
    """Least-squares velocity over a trajectory window.

    `hist` is a sequence of (t, x, y) in seconds/metres. Returns
    (speed_kmh, heading_deg, max_residual_m, stderr_kmh) or None if too few
    samples.

    `stderr_kmh` is the standard error of the fitted slope, and it is what makes
    a far-field reading honest. Ground error grows as d^2/(h*f) per pixel of box
    jitter, so at 44 m on a 6.6 m-high camera one pixel is already ~0.19 m and
    the speed noise floor reaches ~10 km/h. Without this the pipeline reports a
    confident "0.0 km/h" for a parked car it genuinely cannot resolve.

    ponytail: a plain windowed line fit, not a Kalman filter. It handles
    irregular dt and is transparent to debug. Upgrade to a constant-velocity KF
    if occlusion gaps need bridging or the output needs to be smoother.
    """
    if len(hist) < MIN_SAMPLES:
        return None
    a = np.asarray(hist, dtype=float)
    t = a[:, 0] - a[0, 0]
    n = len(t)
    if t[-1] <= 0 or n < 3:
        return None
    (vx, x0), (vy, y0) = np.polyfit(t, a[:, 1], 1), np.polyfit(t, a[:, 2], 1)
    rx, ry = a[:, 1] - (vx * t + x0), a[:, 2] - (vy * t + y0)
    resid = np.hypot(rx, ry).max()

    # Slope standard error: sqrt( SSE / ((n-2) * Sxx) ), combined over both axes.
    sxx = ((t - t.mean()) ** 2).sum()
    if sxx <= 0:
        return None
    se = np.hypot(*(np.sqrt((r ** 2).sum() / max(n - 2, 1) / sxx) for r in (rx, ry)))
    return np.hypot(vx, vy) * 3.6, np.degrees(np.arctan2(vy, vx)), resid, se * 3.6


def speed_noise_kmh(dist_m, K, h, n_samples, span_s):
    """Resolution limit of a speed reading, from geometry alone.

    Linear in distance, not quadratic. The naive derivation says one pixel of
    box wobble maps to d^2/(h*f) of ground error, which is true for a *fixed*
    pixel jitter -- but detector jitter scales with the box, and a car's box
    shrinks as 1/d. The two effects cancel one power of d:

        sigma_px  ~ JITTER_FRAC * box_width = JITTER_FRAC * W * f / d
        sigma_pos = d^2 * sigma_px / (h*f)  = JITTER_FRAC * W * d / h

    Measured on 114 tracked vehicles spanning 20-160 m, the observed scatter
    scales as d^1.00 -- the quadratic form over-predicted by 5-20x and withheld
    most of a motorway.

    There is still no 1/sqrt(n) averaging term. Detection bias is *correlated*
    across neighbouring frames -- a box drifts coherently over a fraction of a
    second rather than rattling independently -- so more samples inside one short
    window buy no precision.
    """
    if K[0] <= 0 or h <= 0 or span_s <= 0 or n_samples < 2:
        return float("inf")
    sigma_pos = JITTER_FRAC * CAR_WIDTH_M * dist_m / h
    return 2.0 * sigma_pos / span_s * 3.6


def classify(fit, noise_kmh=0.0):
    """Velocity fit -> (state, speed_kmh). States: warmup / unreliable / ok.

    A rejected reading is never rewritten to zero: 'stopped' and 'measurement
    failed' have to stay distinguishable, which is exactly what the original
    `if speed > MAX: speed = 0` clamp destroyed.

    A reading is also rejected when its own standard error exceeds the stopped
    threshold -- at that point the measurement cannot tell a parked car from one
    doing STOPPED_KMH, so reporting either number would be inventing precision.
    """
    if fit is None:
        return "warmup", None
    kmh, _, resid, se = fit
    if kmh > MAX_PLAUSIBLE_KMH or resid > MAX_FIT_RESIDUAL_M:
        return "unreliable", None
    # Honest uncertainty is the worse of measured scatter and geometric limit.
    sigma = max(se, noise_kmh)
    if sigma > STOPPED_KMH and kmh < SIGMA_TO_REPORT * sigma:
        # Cannot separate this reading from stopped, so claim neither.
        # 2 sigma, not 3: "distinguishable from zero" is the ordinary bar, and
        # 3 sigma withheld most of a motorway. Measured against genuinely
        # stationary vehicles the predicted noise runs ~1.24x high in the
        # oblique regime, which is where BOX_JITTER_PX 3.0 -> 2.5 comes from.
        return "unreliable", None
    return "ok", kmh


# =========================================================================
# Drift correction (hovering drone / nominally static camera)
# =========================================================================

class DriftCorrector:
    """Maps current-frame pixels back to the calibration reference frame.

    A hovering drone drifts a few pixels; at 24 fps that alone invents a speed
    for parked cars. Homography is estimated on background only (vehicle boxes
    masked out) so moving traffic cannot drag the estimate.
    """

    def __init__(self, ref_gray, every=10):
        self.orb = cv2.ORB_create(1500)
        self.matcher = cv2.BFMatcher(cv2.NORM_HAMMING, crossCheck=True)
        self.kp, self.des = self.orb.detectAndCompute(ref_gray, None)
        self.every = every
        self.H = None

    def update(self, gray, boxes, frame_idx):
        if self.des is None or frame_idx % self.every:
            return
        mask = np.full(gray.shape, 255, np.uint8)
        for x1, y1, x2, y2 in np.atleast_2d(boxes).astype(int):
            mask[max(y1, 0):y2, max(x1, 0):x2] = 0
        kp, des = self.orb.detectAndCompute(gray, mask)
        if des is None or len(des) < 12:
            return
        matches = self.matcher.match(des, self.des)
        if len(matches) < 12:
            return
        src = np.float32([kp[m.queryIdx].pt for m in matches]).reshape(-1, 1, 2)
        dst = np.float32([self.kp[m.trainIdx].pt for m in matches]).reshape(-1, 1, 2)
        H, inliers = cv2.findHomography(src, dst, cv2.RANSAC, 3.0)
        if H is None or inliers is None or inliers.sum() < 10:
            return
        # Gate on how far H actually moves the frame. Below ~1px the camera is
        # genuinely static and applying a fitted homography only injects match
        # noise into the speeds; above ~50px this is a bad match, not drift.
        h_img, w_img = gray.shape
        probe = np.float32([[0, 0], [w_img, 0], [0, h_img], [w_img, h_img],
                            [w_img / 2, h_img / 2]]).reshape(-1, 1, 2)
        shift = np.median(np.linalg.norm(
            cv2.perspectiveTransform(probe, H) - probe, axis=2))
        self.H = H if 1.0 <= shift <= 50.0 else None

    def apply(self, pts):
        if self.H is None:
            return pts
        return cv2.perspectiveTransform(
            np.asarray(pts, np.float32).reshape(-1, 1, 2), self.H).reshape(-1, 2)


class MotionCompensator:
    """Maps every frame back to one fixed reference view (hovering drone).

    The real drone camera shakes 2.6 px per frame (median; 8.6 px p95) while
    holding position. At overhead scale that alone reads as ~30 km/h on every
    vehicle, parked ones included. DriftCorrector above re-fits only every 10th
    frame and ignores shifts under 1 px, which suits slow drift, not shake.

    Each frame is registered directly to a keyframe -- not chained frame to
    frame, so errors do not accumulate -- with a 4-DOF similarity (shift,
    rotation, scale) fitted by RANSAC to background points tracked with
    pyramidal Lucas-Kanade. Vehicle boxes are masked out so traffic cannot drag
    the fit. A new keyframe is taken only when too few points survive; error
    accumulates only across those rare switches.
    """

    MIN_POINTS = 25         # surviving inliers below this: take a new keyframe
    MAX_POINTS = 400
    MASK_PAD = 6            # px of margin kept clear around each vehicle

    def __init__(self):
        self.key = self.kpts = None
        self.key_to_ref = np.eye(3)     # keyframe pixels -> reference pixels
        self.A = np.eye(3)              # current pixels -> keyframe pixels
        self.M = np.eye(3)              # current pixels -> reference pixels
        self.centre = np.zeros(2)

    def _mask(self, shape, boxes):
        m = np.full(shape, 255, np.uint8)
        p = self.MASK_PAD
        for x1, y1, x2, y2 in np.atleast_2d(boxes).astype(int) if len(boxes) else []:
            m[max(y1 - p, 0):y2 + p, max(x1 - p, 0):x2 + p] = 0
        return m

    def _new_keyframe(self, gray, boxes, key_to_ref):
        pts = cv2.goodFeaturesToTrack(gray, self.MAX_POINTS, 0.01, 8,
                                      mask=self._mask(gray.shape, boxes))
        self.key, self.kpts = gray, pts
        self.key_to_ref, self.A = key_to_ref, np.eye(3)
        return pts is not None and len(pts) >= self.MIN_POINTS

    def update(self, gray, boxes):
        """Register `gray` (uint8). Returns False when registration failed and
        the reference was reset: positions before and after are not
        comparable, so the caller must drop its trajectory history."""
        if self.key is None:
            self.centre = np.array(gray.shape[::-1], float) / 2
            self._new_keyframe(gray, boxes, np.eye(3))
            self.M = np.eye(3)
            return True
        if self.kpts is None or len(self.kpts) < self.MIN_POINTS:
            return self._reset(gray, boxes)
        # Start each point where the last estimate predicts it: shake up to
        # tens of px stays within reach of the pyramid.
        inv = np.linalg.inv(self.A)
        guess = (self.kpts.reshape(-1, 2) @ inv[:2, :2].T + inv[:2, 2]).astype(np.float32)
        cur, st, _ = cv2.calcOpticalFlowPyrLK(
            self.key, gray, self.kpts, guess.reshape(-1, 1, 2), winSize=(21, 21), maxLevel=3,
            flags=cv2.OPTFLOW_USE_INITIAL_FLOW,
            criteria=(cv2.TERM_CRITERIA_EPS | cv2.TERM_CRITERIA_COUNT, 20, 0.03))
        ok = st.ravel() == 1
        c, k = cur.reshape(-1, 2)[ok], self.kpts.reshape(-1, 2)[ok]
        if len(boxes):                               # drop points now on a vehicle
            b = np.atleast_2d(boxes)
            inside = ((c[:, None, 0] >= b[:, 0]) & (c[:, None, 0] <= b[:, 2]) &
                      (c[:, None, 1] >= b[:, 1]) & (c[:, None, 1] <= b[:, 3])).any(1)
            c, k = c[~inside], k[~inside]
        if len(c) < 8:
            return self._reset(gray, boxes)
        A, inl = cv2.estimateAffinePartial2D(c, k, method=cv2.RANSAC,
                                             ransacReprojThreshold=1.5)
        if A is None or inl.sum() < 8:
            return self._reset(gray, boxes)
        self.A = np.vstack([A, [0, 0, 1]])
        self.M = self.key_to_ref @ self.A
        if inl.sum() < self.MIN_POINTS:              # running low: re-key here
            self._new_keyframe(gray, boxes, self.M)
        return True

    def _reset(self, gray, boxes):
        self._new_keyframe(gray, boxes, np.eye(3))
        self.M = np.eye(3)
        return False

    def apply(self, boxes):
        """Boxes (N,4) in current pixels -> reference pixels. Only box centres
        are used downstream, and an affine map keeps midpoints, so mapping the
        two corners is exact for them even under rotation."""
        b = np.asarray(boxes, np.float64).reshape(-1, 2)
        return (b @ self.M[:2, :2].T + self.M[:2, 2]).reshape(-1, 4)

    @property
    def offset_px(self):
        """How far the view has moved from the reference, at the frame centre."""
        c = self.centre
        return float(np.linalg.norm(self.M[:2, :2] @ c + self.M[:2, 2] - c))


class StreamClock:
    """Frame times from the camera's own clock, once it has proved honest.

    Stamping a frame when it arrives puts every network delay into the speed:
    Wi-Fi delivers late and in clumps, a receive buffer can hold frames back
    for half a second, and right after a stream opens OpenCV hands over its
    buffered frames 0.8 ms apart although they were captured 41.7 ms apart.
    The stream's presentation timestamps (set at capture, carried in RTP)
    space frames correctly, and spacing is all a speed needs.

    Not every stream has real timestamps: for HTTP MJPEG, OpenCV invents them
    at a nominal rate (40 ms apart on a stream delivering every 49 ms), which
    would read every speed 22% high. So arrival times are used until VERIFY_S
    of timestamps agree with the arrivals: the best-case delay per second must
    stay level (real clocks drift by parts per million; invented ones by the
    rate mismatch). Disagreement must persist for SUSTAIN_S before timestamps
    are given up for good: a path delay that steps once (measured: +250 ms
    when the source looped) tilts the estimate for a few seconds only.

    Trusted timestamps are shifted by an offset that starts at a frame's
    arrival delay and creeps (at most SLEW per frame) toward the smallest
    recent delay, following the camera clock's drift against the Mac's. It
    must creep: any change in it lands in one frame interval. Stamps track the
    Mac's clock only loosely, so measure latency from arrival times.

    `breaks` counts discontinuities (switching timeline, or the stream's
    timestamps restarting): trajectories spanning one are not comparable.
    """

    SLEW = 2e-5            # s per frame: 0.05% of a 41.7 ms interval
    VERIFY_S = 3.0         # agreement needed before timestamps are trusted
    MAX_DRIFT = 0.02       # s of best-case delay per s; invented timestamps give ~0.2
    SUSTAIN_S = 6.0        # how long drift must last before timestamps are given up

    def __init__(self, window_s=10.0):
        self.window_s = window_s
        self.recent = deque()          # (arrival, arrival - pts)
        self.offset = self._last_pts = self._drifting_since = None
        self.trusted = None            # None until verified; False sticks
        self.using_camera = self.started = False
        self.breaks = 0
        self.late = deque(maxlen=300)  # each frame's delay beyond the best recent one, s

    def _emit(self, camera, value):
        if self.started and camera != self.using_camera:
            self.breaks += 1
        self.using_camera, self.started = camera, True
        return value

    def _drift(self):
        """Trend of the best-case delay, s per s: median of the pairwise slopes
        between complete one-second buckets, so a stall that empties one
        bucket of prompt frames cannot swing it. The current second is left
        out: it is incomplete, and during a stall holds only late frames."""
        t0, best = self.recent[0][0], {}
        for a, d in self.recent:
            k = int(a - t0)
            best[k] = min(d, best.get(k, d))
        best.pop(int(self.recent[-1][0] - t0), None)
        if len(best) < 3:
            return None
        k = sorted(best)
        return float(np.median([(best[k[j]] - best[k[i]]) / (k[j] - k[i])
                                for i in range(len(k)) for j in range(i + 1, len(k))]))

    def stamp(self, arrival, pts):
        """`pts` in seconds (None or negative if the stream has none)."""
        if self.trusted is False or pts is None or not np.isfinite(pts) or pts < 0:
            return self._emit(False, arrival)
        if self._last_pts is not None and not 0 < pts - self._last_pts < 2.0:
            # timestamps restarted (reconnect) or jumped: re-anchor
            self.recent.clear()
            self.offset = None
            if self.using_camera:
                self.breaks += 1
        self._last_pts = pts
        d = arrival - pts
        self.recent.append((arrival, d))
        while arrival - self.recent[0][0] > self.window_s:
            self.recent.popleft()
        best = min(x for _, x in self.recent)
        self.late.append(d - best)
        drift = self._drift() if arrival - self.recent[0][0] >= self.VERIFY_S else None
        drifting = drift is not None and abs(drift) > self.MAX_DRIFT
        if not drifting:
            self._drifting_since = None
        elif self._drifting_since is None:
            self._drifting_since = arrival
        elif arrival - self._drifting_since >= self.SUSTAIN_S:
            self.trusted = False       # invented, or not in seconds: never again
            return self._emit(False, arrival)
        if self.trusted is None:
            if drift is None or drifting:
                return self._emit(False, arrival)   # not verified (yet)
            self.trusted = True
        if self.offset is None:
            self.offset = d
        self.offset += float(np.clip(best - self.offset, -self.SLEW, self.SLEW))
        return self._emit(True, pts + self.offset)

    @property
    def jitter_ms(self):
        """95th percentile of how late frames arrive versus the best recent
        one: the timing error arrival stamps would put into speeds."""
        return 1000 * float(np.percentile(self.late, 95)) if self.late and self.trusted else None


class TemporalDenoiser:
    """Display only: a calm, clean background without touching the traffic.

    The background is averaged over time in the stabilised reference view
    (MotionCompensator.M), so shake does not smear it. Each output pixel is the
    averaged background where nothing is changing, and this frame's pixel
    where something is: a smoothed brightness difference between T0 and T1
    grey levels blends from one to the other. Detected vehicles are always
    shown exactly as captured and never learned into the background, so a
    vehicle can lag or leave a ghost nowhere. Runs after detection, speed and
    evidence, so it cannot affect any of them.
    """

    ALPHA = 0.15             # background learning rate (~12 frames of averaging)
    T0, T1 = 6.0, 18.0       # grey-level change: below T0 static, above T1 moving
    PAD = 6                  # px kept live around each vehicle

    def __init__(self):
        self.bg = None

    def reset(self):
        self.bg = None

    def apply(self, frame, M, boxes):
        """frame: this frame (BGR uint8); M: its pixels -> reference pixels (3x3)."""
        h, w = frame.shape[:2]
        veh = np.zeros((h, w), np.uint8)
        for x1, y1, x2, y2 in (np.atleast_2d(boxes).astype(int) if len(boxes) else []):
            veh[max(y1 - self.PAD, 0):y2 + self.PAD, max(x1 - self.PAD, 0):x2 + self.PAD] = 255
        to_ref = dict(dsize=(w, h), flags=cv2.INTER_LINEAR, borderMode=cv2.BORDER_REPLICATE)
        cur = cv2.warpAffine(frame, M[:2], **to_ref).astype(np.float32)
        if self.bg is None:
            self.bg = cur
            return frame
        # learn the background everywhere except under vehicles
        learn = cv2.warpAffine(255 - veh, M[:2], (w, h), flags=cv2.INTER_NEAREST, borderValue=0)
        cv2.accumulateWeighted(cur, self.bg, self.ALPHA, mask=learn)
        Minv = np.linalg.inv(M)
        bg = cv2.warpAffine(self.bg, Minv[:2], **to_ref)
        inside = cv2.warpAffine(np.full((h, w), 255, np.uint8), Minv[:2], (w, h),
                                flags=cv2.INTER_NEAREST, borderValue=0)
        f = frame.astype(np.float32)
        d = cv2.GaussianBlur(cv2.absdiff(cv2.cvtColor(f, cv2.COLOR_BGR2GRAY),
                                         cv2.cvtColor(bg, cv2.COLOR_BGR2GRAY)), (0, 0), 1.5)
        live = np.clip((d - self.T0) / (self.T1 - self.T0), 0, 1)
        live = np.maximum(live, cv2.GaussianBlur(veh, (0, 0), 2).astype(np.float32) / 255)
        live[inside == 0] = 1                # outside the reference view: nothing to average
        return (f + (1 - live[..., None]) * (bg - f)).clip(0, 255).astype(np.uint8)


# =========================================================================
# Runtime
# =========================================================================

def vehicles_from(result, names_map):
    """Tracked Results -> (boxes, ids, names), vehicles only.

    Shared by the file-based iterator and the live pipeline so both apply the
    same class filter and the same "no tracker ids yet" handling.
    """
    if result.boxes is None or result.boxes.id is None:
        return np.empty((0, 4)), np.empty(0, dtype=int), []
    boxes = result.boxes.xyxy.cpu().numpy()
    ids = result.boxes.id.cpu().numpy().astype(int)
    names = [names_map[int(c)] for c in result.boxes.cls.cpu().numpy()]
    keep = [i for i, nm in enumerate(names) if nm in VEHICLE_NAMES]
    return boxes[keep], ids[keep], [names[i] for i in keep]


def iter_tracks(model, video, conf, imgsz, device):
    """Yield (frame_idx, frame, boxes, ids, names) for frames with detections."""
    stream = model.track(source=video, imgsz=imgsz, conf=conf, device=device,
                         tracker="bytetrack.yaml", stream=True, persist=True,
                         verbose=False)
    for idx, r in enumerate(stream):
        yield (idx, r.orig_img, *vehicles_from(r, model.names))


def colour(state, kmh, limit=None):
    # Two greys, not one: "unreliable" and "warmup" are different claims, and the
    # labels ("UNRELIABLE" vs "--") only read at close range, so the boxes need
    # to differ too. Unreliable is the lighter of the pair.
    if state == "warmup":
        return (95, 95, 95)
    if state == "unreliable":
        return (155, 155, 155)
    # Red means "over the limit" -- the same threshold that logs an event -- so
    # what the eye flags and what the database records agree. Yellow is the
    # approaching band (80-100% of the limit). With no limit set (logging off)
    # fall back to a fixed 30/40 scale. Proportional keeps the bands sensible at
    # any limit: 24-30 red>30, or 56-70 red>70.
    red = limit if limit else 40.0
    yellow = 0.8 * limit if limit else 30.0
    return (0, 0, 255) if kmh > red else (0, 255, 255) if kmh > yellow else (0, 255, 0)


def ground_depth_map(K, n, h, size, step=4):
    """Per-pixel distance to the ground plane, from calibration alone.

    This is the depth the measurements actually use -- an exact ray/plane
    intersection, no network involved -- so it is defined in both camera modes.
    Overhead views have no learned depth map at all (and would show a flat field,
    since Z-depth there is constant), which is why this exists as the fallback.

    Pixels above the horizon never meet the plane and come back NaN.
    """
    w_img, h_img = size
    us, vs = np.meshgrid(np.arange(0, w_img, step), np.arange(0, h_img, step))
    pts = np.stack([us.ravel(), vs.ravel()], axis=1).astype(float)
    P, ok = ground_points(K, n, h, pts)
    d = np.where(ok, np.linalg.norm(np.nan_to_num(P), axis=1), np.nan)
    return d.reshape(us.shape)


def colourize_depth(depth, size):
    """Depth map -> BGR heat image at `size`, robust to outliers and NaNs."""
    d = np.asarray(depth, dtype=np.float32)
    finite = np.isfinite(d)
    if not finite.any():
        return None
    lo, hi = np.percentile(d[finite], [2, 98])
    norm = np.clip((d - lo) / max(hi - lo, 1e-6), 0, 1)
    norm = np.nan_to_num(norm, nan=1.0)                 # unreachable = far end
    img = cv2.applyColorMap((255 * (1 - norm)).astype(np.uint8), cv2.COLORMAP_TURBO)
    img[~finite] = (40, 40, 40)                         # above horizon: flat grey
    return cv2.resize(img, size, interpolation=cv2.INTER_LINEAR)


class TrafficStats:
    """Rolling average of ALL reliable readings, not just offenders.

    Distinct from the dashboard's average, which is taken over logged speeding
    events and is therefore always above the limit by construction. This is the
    average speed of traffic actually passing the camera, which is a different
    and usually more interesting number.

    Stationary vehicles are excluded: a car park in frame would otherwise drag
    the figure toward zero and say nothing about the traffic.
    """

    def __init__(self, window_s=60.0):
        self.window_s = window_s
        self.samples = deque()          # (t, kmh) for moving, resolvable vehicles

    def add(self, t, state, kmh):
        if state == "ok" and kmh is not None and kmh >= STOPPED_KMH:
            self.samples.append((t, kmh))

    def summary(self, now):
        while self.samples and now - self.samples[0][0] > self.window_s:
            self.samples.popleft()
        if not self.samples:
            return None
        v = np.fromiter((s for _, s in self.samples), float)
        return {"mean": float(v.mean()), "p85": float(np.percentile(v, 85)),
                "n": len(v)}


def measure(K, n, h, boxes, ids, hist, t):
    """Boxes at time `t` -> [(index, track_id, state, kmh, distance_m)].

    The single place the per-frame chain lives: ground projection, history
    update, velocity fit and the noise-floor decision. Offline playback, the
    live pipeline and the tests all call this, so none of them can drift into
    measuring things slightly differently.

    `t` is seconds and must come from a real clock or frame index -- never a
    plain counter, since the live path drops frames to stay current.
    """
    if not len(boxes):
        return []
    P, valid = ground_points(K, n, h, box_centres(boxes), z=VEHICLE_CENTROID_H)
    W = to_world(P, n, h)
    out = []
    for i, tid in enumerate(ids):
        if not valid[i]:
            continue
        hist[tid].append((t, W[i, 0], W[i, 1]))
        dist = float(np.linalg.norm(P[i]))
        span = hist[tid][-1][0] - hist[tid][0][0]
        state, kmh = classify(fit_velocity(hist[tid]),
                              speed_noise_kmh(dist, K, h, len(hist[tid]), span))
        out.append((i, int(tid), state, kmh, dist))
    return out


# =========================================================================
# Frame clean-up (drone camera: low-quality MJPEG)
# =========================================================================

def clean_frame(bgr):
    """Edge-preserving smoothing before detection, in drone mode.

    Chosen on bench_degraded.py (clean footage degraded to the real camera's
    MJPEG blocking, noise and shake): detection recall 76% -> 84%, calibrated
    height bias -4.1% -> -1.0%, 1.4 ms/frame. Dedicated 8x8 deblocking and
    chroma smoothing were tried on top and added nothing measurable, or
    visible on the real camera's frames. It smooths; it never invents detail.
    """
    return cv2.bilateralFilter(bgr, 5, 25, 5)


def draw_label(frame, box, text, c):
    """Box plus a solid plate in the box colour carrying `text`.

    Sized to the frame, not fixed: a label sized for 1080p covers a quarter of a
    360p frame, and on distant clusters the labels overlap into a smear.
    """
    x1, y1, x2, y2 = np.asarray(box).astype(int)
    k = frame.shape[0] / 1080.0
    fs, th = max(0.36, 0.52 * k), max(1, int(round(1.5 * k)))
    pad = max(2, int(round(5 * k)))
    cv2.rectangle(frame, (x1, y1), (x2, y2), c, max(1, int(round(2 * k))), cv2.LINE_AA)
    if not text:
        return
    (tw, tht), _ = cv2.getTextSize(text, cv2.FONT_HERSHEY_SIMPLEX, fs, th)
    ph = tht + 2 * pad
    top = y1 - ph if y1 - ph >= 0 else y2          # no room above: hang it below
    cv2.rectangle(frame, (x1, top), (x1 + tw + 2 * pad, top + ph), c, -1)
    ink = (255, 255, 255) if c in ((0, 0, 255), (95, 95, 95)) else (20, 20, 20)   # white on red / dark grey
    cv2.putText(frame, text, (x1 + pad, top + pad + tht), cv2.FONT_HERSHEY_SIMPLEX,
                fs, ink, th, cv2.LINE_AA)


def draw_vehicle(frame, box, tid, name, state, kmh, dist, limit=None):
    """Standard overlay: speed first, then track id (the id the speeding log uses).
    Stopped vehicles get the box alone: a car park full of "stopped" plates
    buries the moving traffic the operator is watching."""
    txt = ("..." if state == "warmup" else
           "unreliable" if state == "unreliable" else
           None if kmh < STOPPED_KMH else f"{kmh:.0f} km/h")
    draw_label(frame, box, txt and f"{txt}  #{tid}", colour(state, kmh or 0.0, limit))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("video")
    ap.add_argument("--calib", default="calib.json")
    ap.add_argument("--model", default="yolo11m.pt")
    ap.add_argument("--conf", type=float, default=0.25)
    ap.add_argument("--imgsz", type=int, default=960)
    ap.add_argument("--device", default="mps")
    ap.add_argument("--no-drift", action="store_true")
    ap.add_argument("--save", help="write annotated video here")
    ap.add_argument("--headless", action="store_true")
    args = ap.parse_args()

    from ultralytics import YOLO

    K, n, h, _ = load_calib(args.calib)
    cap = cv2.VideoCapture(args.video)
    fps = cap.get(cv2.CAP_PROP_FPS) or 30.0
    ok, ref = cap.read()
    cap.release()
    if not ok:
        raise SystemExit(f"cannot read {args.video}")

    drift = None if args.no_drift else DriftCorrector(cv2.cvtColor(ref, cv2.COLOR_BGR2GRAY))
    window = max(MIN_SAMPLES, int(SPEED_WINDOW_S * fps))
    hist = defaultdict(lambda: deque(maxlen=window))
    writer = None

    print(f"calib: f={K[0]:.1f}px  camera height={h:.2f}m  fps={fps:.2f}")

    for idx, frame, boxes, ids, names in iter_tracks(
            YOLO(args.model), args.video, args.conf, args.imgsz, args.device):
        if len(boxes) and drift:
            drift.update(cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY), boxes, idx)
            boxes = boxes.copy()
            boxes[:, :2] = drift.apply(boxes[:, :2])
            boxes[:, 2:] = drift.apply(boxes[:, 2:])
        for i, tid, state, kmh, dist in measure(K, n, h, boxes, ids, hist, idx / fps):
            draw_vehicle(frame, boxes[i], tid, names[i], state, kmh, dist)

        if args.save:
            if writer is None:
                writer = cv2.VideoWriter(args.save, cv2.VideoWriter_fourcc(*"mp4v"),
                                         fps, (frame.shape[1], frame.shape[0]))
            writer.write(frame)
        if not args.headless:
            cv2.imshow("speedcam", frame)
            if cv2.waitKey(1) == 27:
                break

    if writer:
        writer.release()
    cv2.destroyAllWindows()


if __name__ == "__main__":
    main()
