"""What a low-quality, shaking drone camera costs, and what each fix recovers.

Clean aerial footage is degraded to match the real drone camera *as measured*:
640x352, its MJPEG blocking plus sensor noise, and its frame-to-frame shake replayed
from a recorded clip. Both copies then go through the same detector, tracker
and speed chain as the live pipeline. The clean copy is the reference, so every
error reported is what the degradation costs and every recovery is what a fix
earns. The true shake is known exactly, so detections can be matched across
the two copies and the stabiliser can be scored against the truth.

    python3 bench_degraded.py                  # 600 frames: raw vs the live clean-up
    python3 bench_degraded.py --filters all    # every candidate filter (~6 min)
    python3 bench_degraded.py --frames 300 --no-calib
    python3 bench_degraded.py --evidence      # evidence images: before, cleaned, fused
    python3 bench_degraded.py --display       # what the operator sees, frame by frame
    python3 bench_degraded.py --timing        # speed error from Wi-Fi frame timing

Writes output/bench/report.md and report.json.
"""

import argparse
import json
import os
import time
from collections import defaultdict, deque

import cv2
import numpy as np

import speedcam as sc

CLEAN = "data/drone_video.mp4"                       # clean overhead traffic
REAL = "data/clip_rtsp_192_168_1_1_7070_webcam.mp4"  # the real drone camera
SIZE = (640, 352)                                    # the drone camera's frame
ZOOM = 1.15        # crop margin, so the replayed shake never exposes an edge
LEAK = 0.9         # bounds the replayed wander: the drone holds position
MODEL, CONF, IMGSZ = "models/visDrone.pt", 0.10, 1280  # live pipeline, drone mode
OUT = "output/bench"
CENTRE = (SIZE[0] / 2, SIZE[1] / 2)
PROBE = np.array([[40, 30], [600, 30], [40, 322], [600, 322], [320, 176]], float)


# ---------------------------------------------------------------- measuring
def blockiness(g):
    """Mean step across 8x8 block borders / mean step elsewhere (1.0 = none)."""
    dx = np.abs(np.diff(g.astype(np.float32), axis=1))
    return float(dx[:, 7::8].mean() / np.delete(dx, np.s_[7::8], axis=1).mean())


def similarity_about_centre(A):
    """2x3 similarity from estimateAffinePartial2D -> (dx, dy, deg, scale) with
    the translation taken about the frame centre, so it can be replayed."""
    s = float(np.hypot(A[0, 0], A[1, 0]))
    c = np.array(CENTRE)
    t = A[:, :2] @ c + A[:, 2] - c
    return t[0], t[1], float(np.degrees(np.arctan2(A[1, 0], A[0, 0]))), s


def real_profile(path, n=300):
    """Blockiness and per-frame shake of the real camera."""
    cap, prev, B, shake = cv2.VideoCapture(path), None, [], []
    for _ in range(n):
        ok, f = cap.read()
        if not ok:
            break
        g = cv2.cvtColor(cv2.resize(f, SIZE), cv2.COLOR_BGR2GRAY)
        B.append(blockiness(g))
        if prev is not None:
            p = cv2.goodFeaturesToTrack(prev, 400, 0.01, 8)
            q, st, _ = cv2.calcOpticalFlowPyrLK(prev, g, p, None)
            A, _ = cv2.estimateAffinePartial2D(p[st == 1], q[st == 1], method=cv2.RANSAC)
            if A is not None:
                shake.append(similarity_about_centre(A))
        prev = g
    cap.release()
    return float(np.mean(B)), np.array(shake)


# ---------------------------------------------------------------- degrading
def m3(M):
    return np.vstack([M, [0, 0, 1]])


def shake_matrices(shake, n):
    """Replay the real shake frame by frame (leaky, so it stays in the margin).
    Returns 3x3 maps: clean reference pixels -> shaken pixels."""
    pos, out = np.zeros(4), []
    for i in range(n):
        dx, dy, deg, s = shake[i % len(shake)]
        pos = LEAK * pos + [dx, dy, deg, np.log(s)]
        M = cv2.getRotationMatrix2D(CENTRE, pos[2], float(np.exp(pos[3])))
        M[:, 2] += pos[:2]
        out.append(m3(M))
    return out


ZM = m3(cv2.getRotationMatrix2D(CENTRE, 0, ZOOM))


def degrade(f, quality, blur, rng):
    if blur:
        f = cv2.GaussianBlur(f, (0, 0), blur)
    f = np.clip(f + rng.normal(0, 2.0, f.shape), 0, 255).astype(np.uint8)  # sensor noise
    ok, buf = cv2.imencode(".jpg", f, [cv2.IMWRITE_JPEG_QUALITY, quality])  # 4:2:0, like MJPEG
    return cv2.imdecode(buf, cv2.IMREAD_COLOR)


def fit_degradation(target_b, sample):
    """JPEG quality that reproduces the real camera's blocking. Only blocking is
    matched: detail depends on what is in view (the recorded clip includes a
    phone's bezel), so it cannot be compared across different footage."""
    rng, best = np.random.default_rng(0), None
    for q in (15, 20, 25, 30, 40, 50, 60, 70, 80):
        b = np.mean([blockiness(cv2.cvtColor(degrade(f, q, 0, rng), cv2.COLOR_BGR2GRAY))
                     for f in sample])
        err = abs(np.log((b - 1) / (target_b - 1)))
        if best is None or err < best[0]:
            best = (err, q, b)
    return best[1], 0, best[2]


def write_clips(n, quality, blur, J):
    """Clean reference and degraded copies, frame-aligned. Lossless (FFV1):
    OpenCV's MJPEG writer ignores its quality setting and would compress the
    clean reference too."""
    cap, rng = cv2.VideoCapture(CLEAN), np.random.default_rng(1)
    fps = cap.get(cv2.CAP_PROP_FPS)
    four = cv2.VideoWriter_fourcc(*"FFV1")
    wc = cv2.VideoWriter(f"{OUT}/clean.avi", four, fps, SIZE)
    wd = cv2.VideoWriter(f"{OUT}/degraded.avi", four, fps, SIZE)
    for i in range(n):
        ok, f = cap.read()
        if not ok:
            raise SystemExit(f"{CLEAN} has only {i} frames")
        base = cv2.resize(f, SIZE, interpolation=cv2.INTER_AREA)
        wc.write(cv2.warpAffine(base, ZM[:2], SIZE, flags=cv2.INTER_LINEAR))
        shaken = cv2.warpAffine(base, (J[i] @ ZM)[:2], SIZE, flags=cv2.INTER_LINEAR,
                                borderMode=cv2.BORDER_REFLECT_101)
        wd.write(degrade(shaken, quality, blur, rng))
    for x in (cap, wc, wd):
        x.release()
    return fps


# ---------------------------------------------------------------- measuring speed
def deblock(y, alpha=20, beta=6):
    """Candidate: smooth 8x8 JPEG block borders where the step across is small
    and both sides are flat (an artefact; real edges are left alone), using
    the H.264 strong-filter taps."""
    f = y.astype(np.int16)
    for axis in (1, 0):
        g = np.moveaxis(f, axis, 0)
        n = (g.shape[0] // 8 - 1) * 8
        k = np.arange(8, n + 1, 8)
        p2, p1, p0, q0, q1, q2 = (g[k + d] for d in (-3, -2, -1, 0, 1, 2))
        m = (np.abs(p0 - q0) < alpha) & (np.abs(p1 - p0) < beta) & (np.abs(q1 - q0) < beta)
        g[k - 1] = np.where(m, (p2 + 2 * p1 + 2 * p0 + 2 * q0 + q1 + 4) >> 3, p0)
        g[k - 2] = np.where(m, (p2 + p1 + p0 + q0 + 2) >> 2, p1)
        g[k] = np.where(m, (p1 + 2 * p0 + 2 * q0 + 2 * q1 + q2 + 4) >> 3, q0)
        g[k + 1] = np.where(m, (p0 + q0 + q1 + q2 + 2) >> 2, q1)
    return f.clip(0, 255).astype(np.uint8)


def ycc_filter(bgr, luma=True, chroma=True):
    """Candidate: deblock brightness and/or smooth the quarter-resolution colour."""
    y, cr, cb = cv2.split(cv2.cvtColor(bgr, cv2.COLOR_BGR2YCrCb))
    if luma:
        y = deblock(y)
    if chroma:
        cr, cb = cv2.GaussianBlur(cr, (0, 0), 1.5), cv2.GaussianBlur(cb, (0, 0), 1.5)
    return cv2.cvtColor(cv2.merge([y, cr, cb]), cv2.COLOR_YCrCb2BGR)


FILTERS = {
    "none": None,
    "live clean-up": sc.clean_frame,               # what live_pipeline.py uses
    # candidates that did not earn a place (see README):
    "deblock": lambda f: ycc_filter(f, chroma=False),
    "chroma smoothing": lambda f: ycc_filter(f, luma=False),
    "deblock + chroma": ycc_filter,
    "bilateral strong": lambda f: cv2.bilateralFilter(f, 7, 40, 7),
    "deblock + bilateral": lambda f: sc.clean_frame(ycc_filter(f, chroma=False)),
    "deblock + chroma + bilateral": lambda f: sc.clean_frame(ycc_filter(f)),
}


def detections(path, filt=None):
    from ultralytics import YOLO
    model, cap = YOLO(MODEL), cv2.VideoCapture(path)
    while True:
        ok, f = cap.read()
        if not ok:
            break
        if filt:
            f = filt(f)
        r = model.track(f, imgsz=IMGSZ, conf=CONF, device="mps", tracker="bytetrack.yaml",
                        persist=True, verbose=False)[0]
        yield f, sc.vehicles_from(r, model.names)
    cap.release()


def window_hist(fps):
    w = max(sc.MIN_SAMPLES, int(sc.SPEED_WINDOW_S * fps))
    return defaultdict(lambda: deque(maxlen=w))


def run_clean(calib, fps):
    """Reference speeds, plus the clean footage's own slow drift (it was shot
    from a real drone too), which the stabiliser is right to follow."""
    K, n, h, _ = calib
    hist, out, mc, drift = window_hist(fps), [], sc.MotionCompensator(), []
    for i, (f, (boxes, ids, _)) in enumerate(detections(f"{OUT}/clean.avi")):
        res = sc.measure(K, n, h, boxes, ids, hist, i / fps)
        out.append((boxes, {j: (st, v) for j, _, st, v, _ in res}))
        mc.update(cv2.cvtColor(f, cv2.COLOR_BGR2GRAY), boxes)
        drift.append(mc.M.copy())                  # clean frame i -> clean frame 0
    return out, drift


def run_degraded(calib, fps, J, drift, filt=None):
    """One detection pass (after the clean-up `filt`), measured three ways: raw,
    the offline tool's DriftCorrector, and the new MotionCompensator."""
    K, n, h, _ = calib
    variants = ("no correction", "DriftCorrector (offline tool)", "MotionCompensator")
    hist = {v: window_hist(fps) for v in variants}
    out, mc, dc, ref, reg_err, cost, resets = [], sc.MotionCompensator(), None, 0, [], [], 0
    for i, (f, (boxes, ids, _)) in enumerate(detections(f"{OUT}/degraded.avi", filt)):
        g = cv2.cvtColor(f, cv2.COLOR_BGR2GRAY)
        t0 = time.perf_counter()
        if not mc.update(g, boxes):
            hist["MotionCompensator"].clear()
            ref, resets = i, resets + (i > 0)
        cost.append(time.perf_counter() - t0)
        # current -> clean -> (source drift) -> clean ref frame -> degraded ref frame
        truth = J[ref] @ np.linalg.inv(drift[ref]) @ drift[i] @ np.linalg.inv(J[i])
        want = PROBE @ truth[:2, :2].T + truth[:2, 2]
        reg_err.append(np.abs(mc.apply(np.c_[PROBE, PROBE])[:, :2] - want).max())
        if dc is None:
            dc = sc.DriftCorrector(g)
        dc.update(g, boxes, i)
        mapped = {
            "no correction": boxes,
            "DriftCorrector (offline tool)":
                np.c_[dc.apply(boxes[:, :2]), dc.apply(boxes[:, 2:])] if len(boxes) else boxes,
            "MotionCompensator": mc.apply(boxes) if len(boxes) else boxes,
        }
        row = {}
        for v in variants:
            res = sc.measure(K, n, h, mapped[v], ids, hist[v], i / fps)
            row[v] = {j: (st, kmh) for j, _, st, kmh, _ in res}
        out.append((boxes, row))
    stab = {"median_px": float(np.median(reg_err)), "p95_px": float(np.percentile(reg_err, 95)),
            "max_px": float(np.max(reg_err)), "ms_per_frame": 1000 * float(np.median(cost)),
            "resets": resets}
    return out, variants, stab


def iou(a, b):
    x1, y1 = np.maximum(a[:2], b[:2])
    x2, y2 = np.minimum(a[2:], b[2:])
    inter = max(0.0, x2 - x1) * max(0.0, y2 - y1)
    return inter / ((a[2] - a[0]) * (a[3] - a[1]) + (b[2] - b[0]) * (b[3] - b[1]) - inter + 1e-9)


def match(clean_boxes, deg_boxes, Jinv):
    """Greedy IoU matching after mapping degraded boxes back by the true shake."""
    if not len(clean_boxes) or not len(deg_boxes):
        return []
    d = np.asarray(deg_boxes, float).reshape(-1, 2) @ Jinv[:2, :2].T + Jinv[:2, 2]
    d = d.reshape(-1, 4)
    pairs = sorted(((iou(c, x), ci, di) for ci, c in enumerate(clean_boxes)
                    for di, x in enumerate(d)), reverse=True)
    used_c, used_d, out = set(), set(), []
    for score, ci, di in pairs:
        if score < 0.3:
            break
        if ci not in used_c and di not in used_d:
            used_c.add(ci), used_d.add(di), out.append((ci, di))
    return out


def score(clean, degraded, variants, J):
    det = {"clean": 0, "degraded": 0, "matched": 0}
    m = {v: {"moving_err": [], "parked_kmh": [], "parked_phantom": 0, "parked_n": 0,
             "ok": 0, "ref_ok": 0} for v in variants}
    for i, ((cb, cres), (db, drow)) in enumerate(zip(clean, degraded)):
        pairs = match(cb, db, np.linalg.inv(J[i]))
        det["clean"] += len(cb)
        det["degraded"] += len(db)
        det["matched"] += len(pairs)
        for ci, di in pairs:
            if ci not in cres or cres[ci][0] != "ok":
                continue
            ref_kmh = cres[ci][1]
            for v in variants:
                st, kmh = drow[v].get(di, ("missing", None))
                r = m[v]
                r["ref_ok"] += 1
                r["ok"] += st == "ok"
                if ref_kmh < sc.STOPPED_KMH:           # parked in the clean copy
                    r["parked_n"] += 1
                    if st == "ok":
                        r["parked_kmh"].append(kmh)
                        r["parked_phantom"] += kmh >= 5.0
                elif ref_kmh >= 5.0 and st == "ok":
                    r["moving_err"].append(abs(kmh - ref_kmh))
    rows = {}
    for v in variants:
        r = m[v]
        e = np.array(r["moving_err"] or [np.nan])
        rows[v] = {
            "moving_err_median_kmh": float(np.median(e)),
            "moving_err_p90_kmh": float(np.percentile(e, 90)),
            "parked_phantom_share": r["parked_phantom"] / max(r["parked_n"], 1),
            "parked_median_kmh": float(np.median(r["parked_kmh"])) if r["parked_kmh"] else 0.0,
            "usable_share": r["ok"] / max(r["ref_ok"], 1),
            "readings": r["ref_ok"],
        }
    det["recall"] = det["matched"] / max(det["clean"], 1)
    det["precision"] = det["matched"] / max(det["degraded"], 1)
    return det, rows


# ---------------------------------------------------------------- evidence
def ssim(a, b):
    a, b = a.astype(np.float64), b.astype(np.float64)
    blur = lambda x: cv2.GaussianBlur(x, (11, 11), 1.5)
    ma, mb = blur(a), blur(b)
    va, vb, cab = blur(a * a) - ma ** 2, blur(b * b) - mb ** 2, blur(a * b) - ma * mb
    c1, c2 = (0.01 * 255) ** 2, (0.03 * 255) ** 2
    return float((((2 * ma * mb + c1) * (2 * cab + c2)) /
                  ((ma ** 2 + mb ** 2 + c1) * (va + vb + c2))).mean())


def psnr(a, b):
    return float(10 * np.log10(255 ** 2 / max(np.mean((a.astype(float) - b.astype(float)) ** 2), 1e-9)))


FUSE_FRAMES, UP = 12, 4


def trail_crop(frame, box, pad=0.8):
    """(crop, box_in_crop) around a vehicle: evidence window plus alignment slack."""
    x1, y1, x2, y2 = np.asarray(box, float)
    px, py = (x2 - x1) * pad, (y2 - y1) * pad
    cx1, cy1 = int(max(x1 - px, 0)), int(max(y1 - py, 0))
    cx2, cy2 = int(min(x2 + px, frame.shape[1])), int(min(y2 + py, frame.shape[0]))
    return frame[cy1:cy2, cx1:cx2].copy(), np.array([x1 - cx1, y1 - cy1, x2 - cx1, y2 - cy1])


_dis = None


def fuse_flow(trail, reduce=np.median):
    """Candidate (rejected): multi-frame evidence fusion. Each of the vehicle's
    recent crops is upsampled, pre-aligned on box centres, then aligned per
    pixel with dense optical flow (DIS) and the median taken. Simpler
    alignments were tried first and made evidence worse: box centres jitter
    1-2 px, and translation-only or rotation-aware ECC cannot absorb the
    camera's rotation and the vehicle's own change in appearance."""
    global _dis
    _dis = _dis or cv2.DISOpticalFlow_create(cv2.DISOPTICAL_FLOW_PRESET_MEDIUM)
    ref, rbox = trail[-1]
    R = cv2.resize(ref, None, fx=UP, fy=UP, interpolation=cv2.INTER_CUBIC)
    h, w = R.shape[:2]
    Rg = cv2.cvtColor(R, cv2.COLOR_BGR2GRAY)
    rc = (rbox[:2] + rbox[2:]) / 2 * UP
    gx, gy = np.meshgrid(np.arange(w, dtype=np.float32), np.arange(h, dtype=np.float32))
    stack = [R.astype(np.float32)]
    for crop, box in trail[:-1]:
        U = cv2.resize(crop, None, fx=UP, fy=UP, interpolation=cv2.INTER_CUBIC)
        g = (box[:2] + box[2:]) / 2 * UP - rc
        P = cv2.warpAffine(U, np.float32([[1, 0, g[0]], [0, 1, g[1]]]), (w, h),
                           flags=cv2.INTER_CUBIC | cv2.WARP_INVERSE_MAP, borderMode=cv2.BORDER_REPLICATE)
        flow = _dis.calc(Rg, cv2.cvtColor(P, cv2.COLOR_BGR2GRAY), None)
        stack.append(cv2.remap(P, gx + flow[..., 0], gy + flow[..., 1], cv2.INTER_CUBIC,
                               borderMode=cv2.BORDER_REPLICATE).astype(np.float32))
    return reduce(np.stack(stack), axis=0).clip(0, 255).astype(np.uint8)


def evidence_study(J):
    """Evidence images scored against the truth: the 1080p source the degraded
    clip was made from, sampled on the same 4x grid. Moving vehicles only,
    scored inside the vehicle box."""
    up = UP
    big_cap = cv2.VideoCapture(CLEAN)
    sx = big_cap.get(cv2.CAP_PROP_FRAME_WIDTH) / SIZE[0]
    sy = big_cap.get(cv2.CAP_PROP_FRAME_HEIGHT) / SIZE[1]
    to_big = np.array([[sx, 0, 0.5 * sx - 0.5], [0, sy, 0.5 * sy - 0.5], [0, 0, 1]])
    cand = {
        "single frame, as recorded (before)": lambda t: t[-1][0],
        "single frame, cleaned (live)": lambda t: sc.clean_frame(t[-1][0]),
        "12-frame fusion, optical flow": fuse_flow,
        "12-frame fusion + clean-up": lambda t: sc.clean_frame(fuse_flow(t)),
    }
    trails, done, rows, shots = defaultdict(lambda: deque(maxlen=FUSE_FRAMES)), set(), [], []
    for i, (f, (boxes, ids, _)) in enumerate(detections(f"{OUT}/degraded.avi")):
        big = big_cap.read()[1]
        for b, tid in zip(boxes, ids):
            tr = trails[tid]
            tr.append((trail_crop(f, b), np.asarray(b, float)))
            if len(tr) < FUSE_FRAMES or tid in done:
                continue
            c0, c1 = (tr[0][1][:2] + tr[0][1][2:]) / 2, (tr[-1][1][:2] + tr[-1][1][2:]) / 2
            if np.hypot(*(c1 - c0)) < 4:                 # parked: the easy case
                continue
            done.add(tid)
            trail = [c for c, _ in tr]
            (ref, rbox), absbox = trail[-1], tr[-1][1]
            o = absbox[:2] - rbox[:2]
            grid = np.array([[1 / up, 0, o[0] + 0.5 / up - 0.5], [0, 1 / up, o[1] + 0.5 / up - 0.5], [0, 0, 1]])
            M = to_big @ np.linalg.inv(J[i] @ ZM) @ grid
            gt = cv2.warpAffine(big, M[:2], (ref.shape[1] * up, ref.shape[0] * up),
                                flags=cv2.INTER_CUBIC | cv2.WARP_INVERSE_MAP)
            rb = rbox * up
            x1, y1 = np.maximum(rb[:2].astype(int), 0)
            x2, y2 = rb[2:].astype(int)
            if x2 - x1 < 16 or y2 - y1 < 16:
                continue
            Y = lambda im: cv2.cvtColor(im[y1:y2, x1:x2], cv2.COLOR_BGR2GRAY)
            row, imgs = {}, {}
            for name, fn in cand.items():
                t0 = time.perf_counter()
                im = fn(trail)
                if im.shape[0] != gt.shape[0]:           # single frames are at camera scale
                    im = cv2.resize(im, None, fx=up, fy=up, interpolation=cv2.INTER_CUBIC)
                row[name] = (psnr(Y(im), Y(gt)), ssim(Y(im), Y(gt)), 1000 * (time.perf_counter() - t0))
                imgs[name] = im
            rows.append(row)
            if len(shots) < 6 and (x2 - x1) * (y2 - y1) > 60 * 30:
                cut = lambda im: cv2.resize(im[max(y1 - 20, 0):y2 + 20, max(x1 - 20, 0):x2 + 20], (240, 160))
                shots.append(np.hstack([cut(imgs["single frame, as recorded (before)"]),
                                        cut(imgs["single frame, cleaned (live)"]), cut(gt)]))
    big_cap.release()
    if shots:
        grid_img = np.vstack(shots)
        for k, label in enumerate(("as recorded", "cleaned (live)", "truth (1080p)")):
            cv2.putText(grid_img, label, (k * 240 + 6, 16), cv2.FONT_HERSHEY_SIMPLEX, 0.45,
                        (255, 255, 255), 1, cv2.LINE_AA)
        cv2.imwrite(f"{OUT}/evidence_bench.png", grid_img)
    return rows, list(cand)


def evidence_real(n_frames=240, keep=4):
    """Before/after on the real drone camera, through the live code path. No
    truth exists for it, so this is a picture for the eye, not a score."""
    import speedlog as sl
    from ultralytics import YOLO
    model, cap = YOLO(MODEL), cv2.VideoCapture(REAL)
    seen, out = {}, []
    dec = lambda j: cv2.imdecode(np.frombuffer(j, np.uint8), cv2.IMREAD_COLOR)
    for _ in range(n_frames):
        ok, f = cap.read()
        if not ok or len(out) >= keep:
            break
        view = sc.clean_frame(f)
        r = model.track(view, imgsz=IMGSZ, conf=CONF, device="mps", tracker="bytetrack.yaml",
                        persist=True, verbose=False)[0]
        for b, tid, nm in zip(*sc.vehicles_from(r, model.names)):
            seen[tid] = seen.get(tid, 0) + 1
            if seen[tid] != 15 or (b[2] - b[0]) * (b[3] - b[1]) < 300:
                continue
            a = sl.evidence_jpeg(f, b, tid, nm.title(), 0, 0, "real drone", time.time())
            c = sl.evidence_jpeg(view, b, tid, nm.title(), 0, 0, "real drone", time.time(),
                                 note="Noise-reduced view. The original frame is kept.")
            if a and c:
                a, c = dec(a), dec(c)
                hgt = max(a.shape[0], c.shape[0])
                padv = lambda im: cv2.copyMakeBorder(im, 0, hgt - im.shape[0], 0, 0,
                                                     cv2.BORDER_CONSTANT, value=(245, 245, 245))
                out.append(np.hstack([padv(a), np.full((hgt, 8, 3), 245, np.uint8), padv(c)]))
    cap.release()
    if out:
        cv2.imwrite(f"{OUT}/evidence_real.png", np.vstack(out))
    return len(out)


# ---------------------------------------------------------------- display
def display_study(J, warmup=15):
    """The displayed picture, scored per frame against the clean copy brought
    into the same pixels (the true shake is known). Background and vehicles
    are scored apart: the temporal filter may only change the background.
    Flicker: frame-to-frame change of the background, after undoing the true
    shake -- lower is calmer; the clean copy's own value is the floor."""
    def temporal(alpha):
        td = sc.TemporalDenoiser()
        td.ALPHA = alpha
        return lambda raw, clean, M, boxes: td.apply(clean, M, boxes)
    variants = {
        "as recorded": lambda raw, clean, M, boxes: raw,
        "cleaned (step 3)": lambda raw, clean, M, boxes: clean,
        "cleaned + temporal (live)": temporal(sc.TemporalDenoiser.ALPHA),
        "cleaned + temporal, slower (0.08)": temporal(0.08),
        "cleaned + temporal, faster (0.30)": temporal(0.30),
    }
    stats = {v: {"bg": [], "veh": [], "bg_ssim": [], "flicker": [], "ms": []} for v in variants}
    stats["truth"] = {"flicker": []}
    prev = {}
    raw_cap, clean_cap = cv2.VideoCapture(f"{OUT}/degraded.avi"), cv2.VideoCapture(f"{OUT}/clean.avi")
    mc, m = sc.MotionCompensator(), 24
    for i, (clean_f, (boxes, ids, _)) in enumerate(detections(f"{OUT}/degraded.avi", sc.clean_frame)):
        raw, ref = raw_cap.read()[1], clean_cap.read()[1]
        if not mc.update(cv2.cvtColor(clean_f, cv2.COLOR_BGR2GRAY), boxes):
            prev.clear()
        truth = cv2.warpAffine(ref, J[i][:2], SIZE, flags=cv2.INTER_LINEAR)
        veh = np.zeros(SIZE[::-1], bool)
        for x1, y1, x2, y2 in (np.atleast_2d(boxes).astype(int) if len(boxes) else []):
            veh[max(y1 - 4, 0):y2 + 4, max(x1 - 4, 0):x2 + 4] = True
        inner = np.zeros_like(veh)
        inner[m:-m, m:-m] = True
        bgm, vm = inner & ~veh, inner & veh
        back = np.linalg.inv(J[i])[:2]                     # shaken pixels -> clean pixels
        Yt = cv2.cvtColor(truth, cv2.COLOR_BGR2GRAY)
        for v, fn in variants.items():
            t0 = time.perf_counter()
            out = fn(raw, clean_f, mc.M, boxes)
            stats[v]["ms"].append(1000 * (time.perf_counter() - t0))
            Y = cv2.cvtColor(out, cv2.COLOR_BGR2GRAY)
            steady = cv2.warpAffine(Y, back, SIZE, flags=cv2.INTER_LINEAR).astype(np.float32)
            if i >= warmup:
                st = stats[v]
                st["bg"].append(psnr(Y[bgm], Yt[bgm]))
                if vm.any():
                    st["veh"].append(psnr(Y[vm], Yt[vm]))
                st["bg_ssim"].append(ssim(np.where(bgm, Y, Yt), Yt))
                if v in prev:
                    st["flicker"].append(float(np.abs(steady - prev[v])[m:-m, m:-m][~veh[m:-m, m:-m]].mean()))
            prev[v] = steady
        steady_t = cv2.cvtColor(ref, cv2.COLOR_BGR2GRAY).astype(np.float32)
        if i >= warmup and "truth" in prev:
            stats["truth"]["flicker"].append(float(np.abs(steady_t - prev["truth"])[m:-m, m:-m].mean()))
        prev["truth"] = steady_t
        if i == 300:                                       # a picture for the eye
            outs = [fn(raw, clean_f, mc.M, boxes) for fn in list(variants.values())[:3]]
            cut = lambda im: cv2.resize(im[60:240, 120:440], None, fx=2, fy=2, interpolation=cv2.INTER_NEAREST)
            tiles = [cut(o) for o in outs] + [cut(truth)]
            for t_img, label in zip(tiles, ("as recorded", "cleaned (step 3)", "cleaned + temporal", "truth")):
                cv2.putText(t_img, label, (8, 22), cv2.FONT_HERSHEY_SIMPLEX, 0.6, (255, 255, 255), 2, cv2.LINE_AA)
            cv2.imwrite(f"{OUT}/display_compare.png", np.vstack([np.hstack(tiles[:2]), np.hstack(tiles[2:])]))
    return stats


# ---------------------------------------------------------------- timing
def timing_study(trace, fps):
    """Speed error from frame timing alone. The clean copy's detections are
    measured three times: with true capture times, with arrival times (each
    frame late by what the trace shows), and with the stream clock over those
    arrivals. Only the time stamps differ. The live reader also drops frames
    when it falls behind; that is not modelled."""
    tr = np.loadtxt(trace, delimiter=",", skiprows=1)
    keep = np.r_[True, np.diff(tr[:, 1]) > 0]
    d = (tr[keep, 0] - tr[keep, 1])
    late = d - d.min()                                     # beyond the best case
    calib = sc.load_calib(f"{OUT}/clean_calib.json")
    K, n, h, _ = calib
    variants = ("true capture times", "arrival times (before)", "stream clock (live)")
    hist = {v: window_hist(fps) for v in variants}
    clock, res = sc.StreamClock(), []
    for i, (f, (boxes, ids, _)) in enumerate(detections(f"{OUT}/clean.avi")):
        t = i / fps
        arrival = t + late[i % len(late)]
        stamps = {variants[0]: t, variants[1]: arrival, variants[2]: clock.stamp(arrival, t)}
        res.append({v: {j: (st, kmh) for j, _, st, kmh, _ in sc.measure(K, n, h, boxes, ids, hist[v], stamps[v])}
                    for v in variants})
    rows = {}
    for v in variants[1:]:
        err, parked, parked_n, ok, ref_ok = [], 0, 0, 0, 0
        for r in res:
            for j, (st, kmh) in r[variants[0]].items():
                if st != "ok":
                    continue
                vs, vk = r[v].get(j, ("missing", None))
                ref_ok += 1
                ok += vs == "ok"
                if kmh < sc.STOPPED_KMH:
                    parked_n += 1
                    parked += vs == "ok" and vk >= 5
                elif kmh >= 5 and vs == "ok":
                    err.append(abs(vk - kmh))
        rows[v] = (np.median(err), np.percentile(err, 90), np.percentile(err, 99),
                   parked / max(parked_n, 1), ok / max(ref_ok, 1))
    spacing = np.abs(np.diff(tr[keep, 0]) - np.diff(tr[keep, 1]))
    return rows, spacing


# ---------------------------------------------------------------- main
def calibrate(path, name, prefilter=None):
    import calibrate as cal
    out = f"{OUT}/{name.replace(' ', '').replace('+', '_')}_calib.json"
    cal.calibrate_video(path, out, model=MODEL, nadir=True, imgsz=IMGSZ, conf=CONF,
                        log=lambda *a, **k: None, prefilter=prefilter)
    return sc.load_calib(out)


def centre_shift(M):
    c = np.array(CENTRE)
    return float(np.linalg.norm(M[:2, :2] @ c + M[:2, 2] - c))


def filter_cost(fn, path, n=40):
    """ms per frame, and blockiness after the filter."""
    cap = cv2.VideoCapture(path)
    fr = [cap.read()[1] for _ in range(n)]
    cap.release()
    t = time.perf_counter()
    out = [fn(f) for f in fr] if fn else fr
    ms = 1000 * (time.perf_counter() - t) / n if fn else 0.0
    return ms, float(np.mean([blockiness(cv2.cvtColor(o, cv2.COLOR_BGR2GRAY)) for o in out]))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--frames", type=int, default=600)
    ap.add_argument("--filters", default="none,live clean-up",
                    help=f"comma-separated, or 'all': {', '.join(FILTERS)}")
    ap.add_argument("--no-calib", action="store_true",
                    help="skip calibrating each degraded variant (~20 s each)")
    ap.add_argument("--evidence", action="store_true",
                    help="only the evidence-image study")
    ap.add_argument("--display", action="store_true",
                    help="only the display study: what the operator sees")
    ap.add_argument("--timing", action="store_true",
                    help="only the frame-timing study (uses drone_check/timing.csv if present)")
    a = ap.parse_args()
    filters = list(FILTERS) if a.filters == "all" else [f.strip() for f in a.filters.split(",")]
    for f in filters:
        if f not in FILTERS:
            raise SystemExit(f"unknown filter {f!r}; choose from {list(FILTERS)}")
    os.makedirs(OUT, exist_ok=True)
    t_start = time.time()

    tb, shake = real_profile(REAL)
    jit = np.hypot(shake[:, 0], shake[:, 1])
    print(f"real camera: blockiness {tb:.2f}x  "
          f"shake {np.median(jit):.2f} px/frame median, {np.percentile(jit, 95):.2f} p95")

    cap = cv2.VideoCapture(CLEAN)
    sample = []
    for _ in range(12):
        ok, f = cap.read()
        sample.append(cv2.warpAffine(cv2.resize(f, SIZE, interpolation=cv2.INTER_AREA),
                                     ZM[:2], SIZE))
        for _ in range(20):
            cap.grab()
    cap.release()
    q, blur, b = fit_degradation(tb, sample)
    print(f"degradation fitted: JPEG quality {q}  ->  blockiness {b:.2f}x")

    J = shake_matrices(shake, a.frames)
    fps = write_clips(a.frames, q, blur, J)
    if a.timing:
        measured = os.path.exists("drone_check/timing.csv")
        trace = "drone_check/timing.csv" if measured else f"{OUT}/relay_timing.csv"
        if not os.path.exists(f"{OUT}/clean_calib.json"):
            calibrate(f"{OUT}/clean.avi", "clean")
        rows, spacing = timing_study(trace, fps)
        L = [f"# Frame timing ({a.frames} frames)", "",
             f"Trace: {trace} ({'measured on the drone' if measured else 'simulated Wi-Fi through a relay; run drone_check.py on the drone to measure the real one'}).",
             f"Arrival vs camera frame spacing in the trace: median {1000 * np.median(spacing):.1f} ms, "
             f"p95 {1000 * np.percentile(spacing, 95):.1f} ms, worst {1000 * spacing.max():.0f} ms.", "",
             "Error against the same detections measured with true capture times:", "",
             "| time stamps | moving: median error | p90 | p99 | parked given >= 5 km/h | usable readings |",
             "|---|---|---|---|---|---|"]
        for v, (m50, m90, m99, ph, us) in rows.items():
            L.append(f"| {v} | {m50:.2f} km/h | {m90:.2f} km/h | {m99:.2f} km/h | {ph:.1%} | {us:.0%} |")
        open(f"{OUT}/timing.md", "w").write("\n".join(L) + "\n")
        print("\n" + "\n".join(L))
        return
    if a.display:
        st = display_study(J)
        L = [f"# Display ({a.frames} frames)", "",
             "Scored against the clean copy in the same pixels. PSNR on luma; flicker is the mean",
             "frame-to-frame change of the background after undoing the true shake (lower is calmer).", "",
             "| display | background PSNR | background SSIM | vehicles PSNR | background flicker | cost |",
             "|---|---|---|---|---|---|"]
        for v, r in st.items():
            if v == "truth":
                continue
            L.append(f"| {v} | {np.median(r['bg']):.2f} dB | {np.median(r['bg_ssim']):.3f} | "
                     f"{np.median(r['veh']):.2f} dB | {np.median(r['flicker']):.2f} | {np.median(r['ms']):.1f} ms |")
        L += [f"| clean copy (floor) | - | - | - | {np.median(st['truth']['flicker']):.2f} | - |", "",
              f"Picture: {OUT}/display_compare.png"]
        open(f"{OUT}/display.md", "w").write("\n".join(L) + "\n")
        print("\n" + "\n".join(L))
        return
    if a.evidence:
        rows, names = evidence_study(J)
        shown = evidence_real()
        base = np.array([r[names[0]][1] for r in rows])
        L = [f"# Evidence images ({len(rows)} moving vehicles, {a.frames} frames)", "",
             "Scored against the 1080p source, inside the vehicle box, on a 4x grid.", "",
             "| evidence image | PSNR (median) | SSIM (median) | better than before (SSIM) | cost |",
             "|---|---|---|---|---|"]
        for n in names:
            v = np.array([r[n] for r in rows])
            L.append(f"| {n} | {np.median(v[:, 0]):.2f} dB | {np.median(v[:, 1]):.3f} | "
                     f"{np.mean(v[:, 1] > base):.0%} | {np.median(v[:, 2]):.1f} ms |")
        L += ["", f"Pictures: {OUT}/evidence_bench.png (as recorded | cleaned | truth), "
              f"{OUT}/evidence_real.png ({shown} vehicles from the real drone, as recorded | cleaned)."]
        open(f"{OUT}/evidence.md", "w").write("\n".join(L) + "\n")
        print("\n" + "\n".join(L))
        return
    calib = calibrate(f"{OUT}/clean.avi", "clean")
    clean_b = filter_cost(None, f"{OUT}/clean.avi")[1]
    print(f"clean copy: blockiness {clean_b:.2f}x, calibrated height {calib[2]:.1f} m")
    print("detecting on the clean copy ...")
    clean, drift = run_clean(calib, fps)

    results, stab_rows, stab = {}, None, None
    for name in filters:
        fn = FILTERS[name]
        ms, fb = filter_cost(fn, f"{OUT}/degraded.avi")
        h = None if a.no_calib else calibrate(f"{OUT}/degraded.avi", f"degraded {name}", fn)[2]
        print(f"[{name}] {ms:.2f} ms/frame, blockiness {fb:.2f}x"
              + (f", calibrated height {h:.1f} m" if h else "") + "; detecting ...")
        degraded, variants, st = run_degraded(calib, fps, J, drift, fn)
        det, rows = score(clean, degraded, variants, J)
        results[name] = {"ms_per_frame": ms, "blockiness": fb, "height_m": h,
                         "detection": det, "speed": rows["MotionCompensator"]}
        if name == "none":
            stab_rows, stab = rows, st
            stab["source_drift_px"] = centre_shift(drift[-1])

    report = {"frames": a.frames,
              "real_camera": {"blockiness": tb, "shake_median_px": float(np.median(jit)),
                              "shake_p95_px": float(np.percentile(jit, 95))},
              "degradation": {"jpeg_quality": q, "noise_sigma": 2.0, "blockiness": b},
              "clean": {"blockiness": clean_b, "height_m": calib[2]},
              "stabiliser": stab, "stabilisation": stab_rows, "filters": results}
    json.dump(report, open(f"{OUT}/report.json", "w"), indent=2)

    L = [f"# Degraded-camera benchmark ({a.frames} frames, {time.time() - t_start:.0f} s)", "",
         f"Real camera: blockiness {tb:.2f}x, shake {np.median(jit):.2f} px/frame median "
         f"({np.percentile(jit, 95):.2f} p95). Replayed as JPEG quality {q} plus sensor noise "
         f"(blockiness {b:.2f}x). Clean copy: blockiness {clean_b:.2f}x, calibrated height {calib[2]:.1f} m.", ""]
    if stab:
        L += ["## Camera shake", "",
              f"MotionCompensator registration error vs the true shake: median {stab['median_px']:.2f} px, "
              f"p95 {stab['p95_px']:.2f} px, max {stab['max_px']:.2f} px; {stab['ms_per_frame']:.1f} ms/frame; "
              f"{stab['resets']} resets (truth includes the source footage's own drift, "
              f"{stab['source_drift_px']:.1f} px).", "",
              "| speed chain | moving: median error | moving: p90 error | parked cars given >= 5 km/h | usable readings |",
              "|---|---|---|---|---|"]
        for v, r in stab_rows.items():
            L.append(f"| {v} | {r['moving_err_median_kmh']:.1f} km/h | {r['moving_err_p90_kmh']:.1f} km/h | "
                     f"{r['parked_phantom_share']:.0%} | {r['usable_share']:.0%} |")
        L.append("")
    L += ["## Clean-up filters (all with MotionCompensator)", "",
          "| filter | cost | blockiness | detection recall | precision | moving: median error | "
          "moving: p90 error | parked >= 5 km/h | usable | calibrated height |",
          "|---|---|---|---|---|---|---|---|---|---|"]
    for name, r in results.items():
        sp, d, h = r["speed"], r["detection"], r["height_m"]
        L.append(f"| {name} | {r['ms_per_frame']:.2f} ms | {r['blockiness']:.2f}x | {d['recall']:.1%} | "
                 f"{d['precision']:.1%} | {sp['moving_err_median_kmh']:.2f} km/h | "
                 f"{sp['moving_err_p90_kmh']:.2f} km/h | {sp['parked_phantom_share']:.1%} | "
                 f"{sp['usable_share']:.0%} | "
                 + (f"{h:.1f} m ({100 * (h / calib[2] - 1):+.1f}%)" if h else "-") + " |")
    open(f"{OUT}/report.md", "w").write("\n".join(L) + "\n")
    print("\n" + "\n".join(L))


if __name__ == "__main__":
    main()
