"""Checks for the geometry and speed logic.

Run:  python3 test_speedcam.py              geometry + velocity (instant, no models)
      python3 test_speedcam.py <video>      also the parked-car check (needs calib.json)

The parked-car check is the one that matters end to end: a stationary vehicle
must read ~0 km/h. If calibration, drift correction or smoothing is broken, a
parked car acquires a phantom speed, so one assertion covers all three.
"""

import sys

import numpy as np

from speedcam import (MAX_PLAUSIBLE_KMH, STOPPED_KMH, classify, fit_velocity,
                      ground_points, to_world, world_basis)


def project(K, P):
    """Camera-frame point -> pixel. Inverse of pixel_rays, for round-tripping."""
    f, cx, cy = K
    return np.array([f * P[0] / P[2] + cx, f * P[1] / P[2] + cy])


def world_to_cam(n, h, w):
    right, fwd, up = world_basis(n)
    return w[0] * right + w[1] * fwd + (w[2] - h) * up


def check_geometry():
    """Known world points must survive projection and back-projection."""
    K = (1400.0, 960.0, 540.0)
    for name, n in [("oblique", np.array([0.0, -0.87, -0.5])),
                    ("nadir", np.array([0.0, 0.0, -1.0])),
                    ("rolled", np.array([0.2, -0.85, -0.49]))]:
        n = n / np.linalg.norm(n)
        h = 12.0
        for target in [(0.0, 30.0, 0.0), (-6.0, 45.0, 0.0),
                       (3.5, 25.0, 0.75), (8.0, 60.0, 0.0)]:
            P = world_to_cam(n, h, np.array(target))
            if P[2] <= 0:
                continue                        # behind the camera, not testable
            px = project(K, P)
            back, ok = ground_points(K, n, h, [px], z=target[2])
            assert ok[0], f"{name}: ray missed the plane for {target}"
            got = to_world(back, n, h)[0]
            assert np.allclose(got, target, atol=1e-6), \
                f"{name}: {target} round-tripped to {np.round(got, 4)}"
    print("ok  geometry round-trip (oblique / nadir / rolled)")


def check_scale_linearity():
    """Ground distances must scale linearly with camera height.

    The whole scale-inference step in calibrate.py rests on this: measure a car
    at h=1, divide the true width by it, get the true height.
    """
    K = (1400.0, 960.0, 540.0)
    n = np.array([0.0, -0.87, -0.5])
    n /= np.linalg.norm(n)
    px = [[900.0, 700.0], [1000.0, 700.0]]
    a, ok_a = ground_points(K, n, 1.0, px)
    b, ok_b = ground_points(K, n, 7.5, px)
    assert ok_a.all() and ok_b.all()
    w1 = np.linalg.norm(a[1] - a[0])
    w2 = np.linalg.norm(b[1] - b[0])
    assert np.isclose(w2 / w1, 7.5, rtol=1e-9), f"expected 7.5x, got {w2 / w1:.4f}"
    print("ok  ground distances scale linearly with camera height")


def check_horizon_rejected():
    """Rays at or above the horizon have no ground intersection.

    Camera held level (optical axis horizontal, up = -Y), so the horizon falls
    exactly on the centre row: everything above it is sky, below it is road.
    """
    K = (1400.0, 960.0, 540.0)
    n = np.array([0.0, -1.0, 0.0])
    _, ok = ground_points(K, n, 10.0, [[960.0, 100.0]])    # above centre: sky
    assert not ok[0], "a skyward ray was accepted as hitting the ground"
    _, ok = ground_points(K, n, 10.0, [[960.0, 900.0]])    # below centre: road
    assert ok[0], "a downward ray was rejected"
    print("ok  above-horizon rays rejected, below-horizon accepted")


def check_scale_recovery():
    """Scale inference must recover a known camera height from a known car.

    Regression guard: the box's left/right extremities sit on the car body ~0.75m
    up, not on the road, so back-projecting them to z=0 overestimates the
    footprint. Omitting the +z_body term silently underestimates camera height
    (it cost a 16% scale error on real footage), and nothing else in the pipeline
    would flag it -- every distance and speed would just be uniformly low.
    """
    from speedcam import CAR_WIDTH_M, VEHICLE_CENTROID_H

    K = (1589.0, 960.0, 540.0)
    n = np.array([0.0, -0.919, -0.393])
    n /= np.linalg.norm(n)
    h_true, z_body = 8.0, VEHICLE_CENTROID_H

    for across in (18.0, 30.0, 45.0):        # a car at several distances
        # Body extremities: CAR_WIDTH_M apart, at z_body above the road.
        left = np.array([-CAR_WIDTH_M / 2, across, z_body])
        right = np.array([CAR_WIDTH_M / 2, across, z_body])
        px = [project(K, world_to_cam(n, h_true, p)) for p in (left, right)]

        # Scale inference: back-project to the road plane at unit height.
        P, ok = ground_points(K, n, 1.0, px, z=0.0)
        assert ok.all()
        w1 = np.linalg.norm(P[1] - P[0])
        recovered = CAR_WIDTH_M / w1 + z_body
        assert np.isclose(recovered, h_true, rtol=1e-6), \
            f"at {across}m: recovered h={recovered:.3f}, expected {h_true}"

        naive = CAR_WIDTH_M / w1          # the buggy form, for contrast
        assert naive < h_true - 0.7, "the omitted term should bias h low"
    print(f"ok  scale inference recovers camera height {h_true} m (naive form "
          f"under-reads by {h_true - naive:.2f} m)")


def check_velocity():
    """A synthetic constant-velocity track must recover its own speed."""
    fps, v = 25.0, np.array([12.0, 5.0])          # m/s -> 46.8 km/h
    hist = [(i / fps, *(v * (i / fps))) for i in range(15)]
    kmh, heading, resid, se = fit_velocity(hist)
    assert np.isclose(kmh, np.linalg.norm(v) * 3.6, rtol=1e-9), kmh
    assert np.isclose(heading, np.degrees(np.arctan2(v[1], v[0])), rtol=1e-9)
    assert resid < 1e-9 and se < 1e-9
    assert fit_velocity(hist[:2]) is None, "reported a speed from 2 samples"
    print(f"ok  velocity fit recovers {kmh:.1f} km/h from a synthetic track")


def check_states_distinct():
    """'stopped' and 'measurement failed' must not collapse into each other."""
    stationary = [(i / 25.0, 0.004 * i, 0.0) for i in range(15)]
    state, kmh = classify(fit_velocity(stationary))
    assert state == "ok" and kmh < STOPPED_KMH, (state, kmh)

    state, kmh = classify((MAX_PLAUSIBLE_KMH + 150, 0.0, 0.1, 0.1))
    assert state == "unreliable" and kmh is None, \
        "an implausible reading was laundered into a number"

    state, kmh = classify((50.0, 0.0, 9.0, 0.1))  # scattered track, sane speed
    assert state == "unreliable", "a high-residual track was reported as good"

    state, kmh = classify((0.4, 0.0, 0.1, 8.0))   # far car: noise floor 8 km/h
    assert state == "unreliable", \
        "a reading whose own stderr exceeds the stopped threshold was called ok"

    assert classify(None)[0] == "warmup"
    print("ok  stopped / unreliable / warmup stay distinguishable")


def check_motion_compensation():
    """A known shake (shift, rotation, scale) must be undone to a fraction of a
    pixel, and a vehicle moving the other way must not drag the estimate."""
    import cv2
    from speedcam import MotionCompensator
    rng = np.random.default_rng(3)
    ground = cv2.GaussianBlur(rng.integers(0, 255, (352, 640)).astype(np.uint8), (0, 0), 2.5)
    ground = cv2.normalize(ground, None, 0, 255, cv2.NORM_MINMAX)
    car = np.array([300, 150, 360, 180])                      # bright "vehicle"
    def frame(M, car_dx):
        f = cv2.warpAffine(ground, M, (640, 352), borderMode=cv2.BORDER_REFLECT)
        x1, y1, x2, y2 = car + [car_dx, 0, car_dx, 0]
        f[y1:y2, x1:x2] = 250
        return f, np.array([[x1, y1, x2, y2]], float)
    mc = MotionCompensator()
    f0, b0 = frame(np.float32([[1, 0, 0], [0, 1, 0]]), 0)
    assert mc.update(f0, b0)
    worst = 0.0
    for i, (dx, dy, deg, s) in enumerate([(3, -2, .3, 1.0), (7.3, -4.1, 1.5, 1.02),
                                          (-9, 6, -1, .985), (14, 9, .8, 1.01)]):
        M = cv2.getRotationMatrix2D((320, 176), deg, s)
        M[:, 2] += (dx, dy)
        f, b = frame(M, 25 * (i + 1))                          # the car keeps driving
        assert mc.update(f, b), "registration failed on a modest shake"
        truth = cv2.invertAffineTransform(M)                    # current -> reference
        probe = np.array([[50, 40], [600, 40], [50, 320], [600, 320], [320, 176]], float)
        want = probe @ truth[:, :2].T + truth[:, 2]
        got = mc.apply(np.c_[probe, probe])[:, :2]              # points as zero-size boxes
        worst = max(worst, float(np.abs(got - want).max()))
    assert worst < 0.3, f"shake undone only to {worst:.2f} px"
    assert not mc.update(np.zeros((352, 640), np.uint8), np.zeros((0, 4))), \
        "a featureless frame must reset, not report a bogus registration"
    print(f"ok  camera shake undone to {worst:.2f} px; moving vehicle ignored")


def check_temporal_denoiser():
    """Display filter: a static noisy background comes out cleaner, while a
    moving vehicle is shown exactly as captured and leaves no ghost behind."""
    import cv2
    from speedcam import TemporalDenoiser
    rng = np.random.default_rng(4)
    scene = cv2.GaussianBlur(rng.integers(40, 200, (352, 640, 3)).astype(np.uint8), (0, 0), 4)
    td, M = TemporalDenoiser(), np.eye(3)

    def step(k, detected):
        f = np.clip(scene + rng.normal(0, 6, scene.shape), 0, 255).astype(np.uint8)
        x = 100 + 12 * k                                   # the vehicle drives right
        f[150:180, x:x + 40] = 245
        box = np.array([[x, 150, x + 40, 180]], float) if detected else np.zeros((0, 4))
        return f, td.apply(f, M, box), x
    for k in range(29):
        f, out, x = step(k, detected=k % 3 != 0)          # the detector misses some frames
    bg = (slice(250, 340), slice(20, 600))
    noise_in = np.abs(f[bg].astype(float) - scene[bg]).mean()
    noise_out = np.abs(out[bg].astype(float) - scene[bg]).mean()
    assert noise_out < 0.6 * noise_in, f"background noise {noise_in:.1f} -> {noise_out:.1f}"
    assert np.array_equal(out[150:180, x:x + 40], f[150:180, x:x + 40]), "detected vehicle altered"
    trail = out[150:180, x - 40:x - 12].astype(float)       # where it was a moment ago
    assert trail.mean() < 200, f"ghost of the vehicle left behind ({trail.mean():.0f})"
    f, out, x = step(29, detected=False)                    # missed by the detector
    miss = np.abs(out[155:175, x + 5:x + 35].astype(float) - f[155:175, x + 5:x + 35]).mean()
    assert miss < 3, f"an undetected moving vehicle was averaged away ({miss:.1f})"
    print(f"ok  display filter: background noise {noise_in:.1f} -> {noise_out:.1f}, "
          "vehicles shown as captured (detected or not), no ghost")


def check_stream_clock():
    """Frame spacing must follow the camera, not Wi-Fi -- once its timestamps
    have proved honest -- and invented or broken timestamps must never be used."""
    from speedcam import StreamClock
    rng = np.random.default_rng(6)

    def wifi(n, burst=True, fps=24.0):
        t = np.arange(n) / fps                               # true capture times
        a = t + 0.030 + rng.exponential(0.004, n)            # delay and jitter
        if burst:
            a[:12] = t[11] + 0.030 + np.arange(12) * 0.0008  # buffered on opening
        for s0 in range(120, n, 120):                        # stalls: 150 ms, then a clump
            held = (t >= t[s0]) & (t < t[s0] + 0.150)
            a[held] = t[s0] + 0.180 + np.arange(held.sum()) * 0.001
        return t, np.maximum.accumulate(a)

    t, arrival = wifi(900)
    pts = 5000.0 + t * (1 + 50e-6)                           # own epoch, clock 50 ppm off
    clock = StreamClock()
    stamps = np.array([clock.stamp(a, p) for a, p in zip(arrival, pts)])
    settled = int(24 * (StreamClock.VERIFY_S + 0.5))         # verified by now
    err_clk = np.abs(np.diff(stamps) - np.diff(t))[settled:]
    err_arr = np.abs(np.diff(arrival) - np.diff(t))[settled:]
    assert clock.trusted and clock.using_camera and clock.breaks == 1, (clock.trusted, clock.breaks)
    assert err_clk.max() < 0.0005, f"spacing error {1000 * err_clk.max():.2f} ms"
    assert err_arr.max() > 0.04, "test scenario lost its jitter"
    # long run: the camera's 50 ppm drift is followed, not accumulated
    t, arrival = wifi(4000, burst=False)
    c = StreamClock()
    lag = np.array([a - c.stamp(a, 5000.0 + x * (1 + 50e-6)) for a, x in zip(arrival, t)])
    assert np.abs(np.median(lag[-500:]) - np.median(lag[200:700])) < 0.002, "offset ran away"
    # a trusted stream that restarts its timestamps (reconnect): one break, still trusted
    restart = c.stamp(arrival[-1] + 0.5, 0.0)
    assert c.trusted and c.breaks == 2 and abs(restart - (arrival[-1] + 0.5)) < 1e-9
    # an honest stream whose path delay steps up once (+250 ms) stays trusted
    t, arrival = wifi(1500, burst=False)
    arrival[700:] += 0.25
    c = StreamClock()
    stamps = np.array([c.stamp(a, 5000.0 + x) for a, x in zip(arrival, t)])
    assert c.trusted and c.using_camera, "a one-time delay step made the clock give up"
    assert np.abs(np.diff(stamps) - np.diff(t))[200:].max() < 0.0005, "the step leaked into spacing"
    # timestamps that must never be used
    t, arrival = wifi(300, burst=False, fps=20.5)            # stream delivers 20.5 fps...
    c = StreamClock()
    out = [c.stamp(a, i * 0.040) for i, a in enumerate(arrival)]   # ...OpenCV invents 25
    assert c.trusted is False and not c.using_camera and c.breaks == 0
    assert np.array_equal(out, arrival), "invented timestamps were used"
    c = StreamClock()
    out = [c.stamp(a, 1000 * (a - arrival[0])) for a in arrival]   # in ms, not s
    assert np.array_equal(out, arrival) and c.breaks == 0, "timestamps in ms accepted as s"
    c = StreamClock()
    assert c.stamp(1.0, None) == 1.0 and not c.using_camera
    print(f"ok  stream clock: frame spacing error {1000 * err_clk.max():.2f} ms "
          f"(arrival times: up to {1000 * err_arr.max():.0f} ms); invented timestamps refused")


def check_parked_cars(video, model_path="yolo11m.pt", calib="calib.json", conf=0.25):
    """End-to-end: stationary vehicles must read ~0 km/h, moving ones must not.

    Tracks are split by total displacement rather than by speed, so the check
    never assumes its own answer. Both halves matter: a pipeline that reports
    zero for everything would pass a stationary-only assertion.
    """
    import cv2
    from collections import defaultdict, deque

    from ultralytics import YOLO

    import speedcam as sc

    K, n, h, _ = sc.load_calib(calib)
    cap = cv2.VideoCapture(video)
    fps = cap.get(cv2.CAP_PROP_FPS) or 30.0
    cap.release()

    window = max(sc.MIN_SAMPLES, int(sc.SPEED_WINDOW_S * fps))
    hist = defaultdict(lambda: deque(maxlen=window))
    track = defaultdict(list)

    for idx, frame, boxes, ids, _ in sc.iter_tracks(YOLO(model_path), video, conf, 960, "mps"):
        if idx > 700:
            break
        if not len(boxes):
            continue
        # Record every sample, not just the confident ones: whether a vehicle
        # moved has to be judged from its full trajectory, and "was rejected as
        # unreliable" is itself a result worth checking.
        for i, tid, state, kmh, dist in sc.measure(K, n, h, boxes, ids, hist, idx / fps):
            x, y = hist[tid][-1][1], hist[tid][-1][2]
            track[tid].append((idx / fps, x, y, kmh, state))

    # Ground truth comes from each track's own trajectory, never from the speed
    # the pipeline reported -- otherwise the check just confirms its own answer.
    # A vehicle counts as parked only if watched long enough that real motion
    # would be unmistakable: 3 s at even 10 km/h covers 8 m, so a car that
    # travels under 2 m in that time was genuinely standing still.
    MIN_STILL_S, STILL_DISP_M, MOVING_DISP_M = 3.0, 2.0, 5.0
    moving, parked = [], []
    for v in track.values():
        if len(v) < 30:
            continue
        t = np.array([r[0] for r in v], float)
        xy = np.array([[r[1], r[2]] for r in v], float)
        kmh = np.array([r[3] for r in v], float)
        ok = np.array([r[4] == "ok" for r in v])
        dt = t[-1] - t[0]
        if dt <= 0:
            continue
        disp = float(np.hypot(*(xy[-1] - xy[0])))
        if disp > MOVING_DISP_M:
            if ok.any():
                moving.append(float(np.median(kmh[ok])))
        elif dt >= MIN_STILL_S and disp <= STILL_DISP_M:
            parked.append((float(np.median(kmh[ok])) if ok.any() else None,
                           float(ok.mean()), disp, dt))

    assert moving, "no moving vehicle was tracked -- check is vacuous"
    confident = [p for p in parked if p[0] is not None]
    if parked:
        print(f"    parked  n={len(parked)} ({len(confident)} with confident "
              "readings, rest rejected as unresolvable)")
        for kmh, frac, disp, dt in parked:
            print(f"      moved {disp:4.2f}m in {dt:4.1f}s -> "
                  + (f"{kmh:5.2f} km/h ({frac:.0%} confident)" if kmh is not None
                     else "UNRELIABLE (no confident reading)"))
    else:
        # Not a failure and not a pass. This clip simply contains no vehicle
        # standing still long enough to test against, so say so rather than
        # letting a vacuous check masquerade as a green one.
        print("    parked  n=0 -- clip has no stationary vehicle; "
              "parked-car property NOT EXERCISED here")
    print(f"    moving  n={len(moving)}: median {np.median(moving):.2f} km/h")

    # The property that matters: a car that did not move must never be handed a
    # confident non-zero speed. Reporting ~0 is right; declining to answer is
    # right; "10 km/h" for a parked car is the failure being guarded against.
    for kmh, frac, disp, dt in confident:
        assert kmh < STOPPED_KMH, (
            f"a vehicle that moved {disp:.2f}m in {dt:.1f}s was confidently "
            f"reported at {kmh:.1f} km/h -- calibration, drift correction or "
            "the noise floor is wrong")
    assert np.median(moving) > 10, \
        "moving traffic reads near zero -- the pipeline is reporting everything stopped"
    print("ok  parked vehicles never confidently reported as moving"
          if parked else "ok  moving traffic sane (no parked vehicles in this clip)")


if __name__ == "__main__":
    check_geometry()
    check_scale_linearity()
    check_horizon_rejected()
    check_scale_recovery()
    check_velocity()
    check_states_distinct()
    check_motion_compensation()
    check_temporal_denoiser()
    check_stream_clock()
    if len(sys.argv) > 1:
        check_parked_cars(*sys.argv[1:4],
                          conf=float(sys.argv[4]) if len(sys.argv) > 4 else 0.25)
    print("\nall checks passed")
