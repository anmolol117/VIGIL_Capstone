"""One-shot scene calibration: focal length, ground-plane orientation, scale.

Runs offline, once per camera setup, then is never needed again. Each quantity
is taken from whichever source is actually trustworthy for it:

  focal length      Depth Pro's FOV head        (its trained task)
  plane orientation RANSAC on the point cloud   (a unit vector -- survives a
                                                 wrong global scale)
  absolute scale    p25 of car width vs 1.83 m  (a physical constant, not a
                                                 network prediction)

That split matters: Depth Pro is trained mostly on ground-level imagery, so its
absolute metric scale on a high nadir drone view is well out of distribution.
Nothing here depends on that scale being right.

Usage:
    python3 calibrate.py drone_video.mp4 --model visDrone.pt --out calib.json
"""

import argparse
import os

# Cap MPS allocation before torch initialises it. Depth Pro resizes every input
# to a fixed 1536x1536 and runs a multi-scale patch ViT over it, so peak memory
# is large and independent of the source resolution. Without a watermark an
# over-allocation consumes all unified memory and takes the machine down with
# it; with one, torch raises a catchable OOM instead.
# Both must be set together: the low watermark defaults to 1.4 and torch rejects
# a high watermark below it.
os.environ.setdefault("PYTORCH_MPS_LOW_WATERMARK_RATIO", "0.5")
os.environ.setdefault("PYTORCH_MPS_HIGH_WATERMARK_RATIO", "0.6")

# Keep model weights inside the project rather than in the user-wide HuggingFace
# cache, so everything the pipeline needs lives in one directory. Must be set
# before transformers is imported. Falls back to the default cache if the local
# copy is absent, in which case the model downloads into models/hf on first use.
os.environ.setdefault("HF_HOME", os.path.join(
    os.path.dirname(os.path.abspath(__file__)), "models", "hf"))

import cv2
import numpy as np
import torch

from speedcam import (CAR_WIDTH_M, VEHICLE_CENTROID_H, VEHICLE_NAMES,
                      box_centres, ground_points, load_calib, pixel_rays,
                      save_calib)

# The camera is static, so focal and plane orientation are identical every
# frame -- two frames is enough, and a single frame's road mask already yields
# ~10^5 points for the plane fit.
DEPTH_FRAMES = 2       # frames averaged for focal length / plane orientation
SCALE_FRAMES = 400     # frames of tracking used for the scale estimate
ROAD_DILATE = 41       # px; road band grown around vehicle boxes
PLANE_THRESH_REL = 0.01  # RANSAC inlier band, as a fraction of median depth
DEPTH_MODEL = "apple/DepthPro-hf"   # focal-length head is why; see estimate_geometry
MAX_CLOUD = 200_000    # point-cloud cap; RANSAC needs no more than this
HEADING_BASELINE = 5   # frames over which a vehicle's direction of travel is measured
MIN_TRAVEL_WIDTHS = 0.2  # vehicle must move this fraction of its own width
WIDTH_PERCENTILE = 25    # of the width samples; see estimate_scale for why not 50


class CalibrationError(RuntimeError):
    """Calibration could not be completed.

    A normal exception, not SystemExit: these helpers are library code, and the
    live pipeline has to survive a failed recalibration (revert to the previous
    mode) rather than have the whole server torn down under it.
    """


def depth_path(calib_file):
    """Where the calibration-time depth map lives, next to its calibration."""
    return os.path.splitext(calib_file)[0] + "_depth.npy"


def sample_frames(video, k, prefilter=None):
    cap = cv2.VideoCapture(video)
    total = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
    frames = []
    for i in np.linspace(0, max(total - 1, 0), k, dtype=int):
        cap.set(cv2.CAP_PROP_POS_FRAMES, int(i))
        ok, f = cap.read()
        if ok:
            frames.append(prefilter(f) if prefilter else f)
    cap.release()
    if not frames:
        raise CalibrationError(f"cannot read frames from {video}")
    return frames


def road_mask(shape, boxes):
    """Pixels that are road surface: a band around vehicles, minus the vehicles.

    Anchoring to where vehicles actually are keeps the plane fit off rooftops
    and tree canopy, which in the drone clip occupy more of the frame than the
    road does.
    """
    veh = np.zeros(shape[:2], np.uint8)
    for x1, y1, x2, y2 in np.atleast_2d(boxes).astype(int):
        cv2.rectangle(veh, (x1, y1), (x2, y2), 255, -1)
    band = cv2.dilate(veh, np.ones((ROAD_DILATE, ROAD_DILATE), np.uint8))
    return (band > 0) & (veh == 0)


def plane_normal(P):
    """Least-squares plane fit for points P (N,3) -> (centroid, unit normal).

    Uses the 3x3 covariance rather than np.linalg.svd(P). numpy's svd defaults to
    full_matrices=True, which for an (N,3) cloud allocates a full (N,N) left
    factor: 14 GB at N=42k, 320 GB at N=200k. That -- not the depth model -- was
    what exhausted memory here. The covariance route is O(N) with a 3x3
    eigendecomposition, and eigh returns eigenvalues ascending, so the first
    eigenvector is the direction of least variance: the plane normal.
    """
    c = P.mean(0)
    return c, np.linalg.eigh(np.cov((P - c).T))[1][:, 0]


def ransac_plane(P, thresh=None, iters=400, seed=0):
    """Fit n.P + d = 0 to a point cloud. Returns (unit normal, d, n_inliers).

    The inlier band is a fraction of the cloud's own median depth rather than a
    fixed number of metres, so the fit still works when Depth Pro's absolute
    scale is off -- which it will be on a high nadir view.
    """
    rng = np.random.default_rng(seed)
    if thresh is None:
        thresh = PLANE_THRESH_REL * float(np.median(np.linalg.norm(P, axis=1)))
    best, best_cnt = None, 0
    for _ in range(iters):
        a, b, c = P[rng.choice(len(P), 3, replace=False)]
        nrm = np.cross(b - a, c - a)
        norm = np.linalg.norm(nrm)
        if norm < 1e-9:
            continue
        nrm = nrm / norm
        cnt = int((np.abs(P @ nrm + -nrm.dot(a)) < thresh).sum())
        if cnt > best_cnt:
            best, best_cnt = (nrm, -nrm.dot(a)), cnt
    if best is None:
        raise CalibrationError("plane fit failed: degenerate point cloud")
    # Refit on the inlier set -- RANSAC's 3-point sample is only a seed.
    nrm, d = best
    inl = P[np.abs(P @ nrm + d) < thresh]
    centroid, nrm = plane_normal(inl)
    d = -nrm.dot(centroid)
    if d < 0:                      # orient so camera height is positive
        nrm, d = -nrm, -d
    return nrm, d, len(inl)


def detect_roads(frames, detector):
    """Vehicle boxes per frame, collected before any depth model is loaded."""
    masks = []
    for frame in frames:
        det = detector(frame, verbose=False)[0]
        boxes = det.boxes.xyxy.cpu().numpy()
        names = [detector.names[int(c)] for c in det.boxes.cls.cpu().numpy()]
        boxes = boxes[[i for i, nm in enumerate(names) if nm in VEHICLE_NAMES]]
        masks.append(road_mask(frame.shape, boxes) if len(boxes) else None)
    return masks


def estimate_geometry(frames, masks, device, model_id=DEPTH_MODEL, log=print):
    """Metric depth -> focal length and ground-plane normal.

    Depth Pro is the default because of its focal-length head, not its metric
    depth -- absolute scale never comes from here, it comes from car width
    against a 1.83 m constant. What the depth map must get right is the road's
    *shape* (is it planar, and how does it tilt), not its absolute range.

    A smaller metric model (~25M params, ~1.2GB vs ~950M, ~7.3GB) works for the
    plane fit but predicts no focal length, and recovering focal from planarity
    alone does not work: as f grows every point collapses toward a line, which is
    trivially planar, so any such search runs away to infinity. Measured against
    a clip of known focal 1589px, that approach returned the 5760px search
    ceiling and a 7-8 degree tilt versus a 23.75 degree vanishing-point truth.

    The two models are never resident at once: detection has already finished
    and handed over plain masks.
    """
    from transformers import AutoImageProcessor, AutoModelForDepthEstimation

    # Depth Pro carries a focal-length head; small metric models do not.
    is_pro = "depthpro" in model_id.lower()
    dtype = torch.float16 if device == "mps" else torch.float32
    proc = AutoImageProcessor.from_pretrained(model_id)
    model = AutoModelForDepthEstimation.from_pretrained(
        model_id, dtype=dtype if is_pro else torch.float32).to(device).eval()
    focals = []
    rng = np.random.default_rng(0)
    per_frame = MAX_CLOUD // max(len(frames), 1)

    uvs, ds, last_depth = [], [], None
    try:
        for frame, m in zip(frames, masks):
            if m is None:
                continue
            h, w = frame.shape[:2]
            img = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
            inputs = proc(images=img, return_tensors="pt").to(device)
            if is_pro:
                inputs = inputs.to(dtype)
            with torch.inference_mode():
                out = model(**inputs)
            if is_pro:
                post = proc.post_process_depth_estimation(
                    out, target_sizes=[(h, w)])[0]
                focals.append(float(post["focal_length"]))
                depth = post["predicted_depth"].float().cpu().numpy()
                del post
            else:
                depth = torch.nn.functional.interpolate(
                    out.predicted_depth[None].float(), size=(h, w),
                    mode="bicubic", align_corners=False)[0, 0].cpu().numpy()
            # Release device-side state before the next frame: the caching
            # allocator otherwise carries activations forward.
            del out, inputs
            if device == "mps":
                torch.mps.empty_cache()

            vs, us = np.nonzero(m)
            if len(vs) > per_frame:                 # subsample now, not later
                sel = rng.choice(len(vs), per_frame, replace=False)
                vs, us = vs[sel], us[sel]
            # Keep one full map for the overlay. The camera is fixed, so a map
            # made at calibration stays valid for the whole session.
            last_depth = depth.astype(np.float16)
            good = np.isfinite(depth[vs, us]) & (depth[vs, us] > 0)
            uvs.append(np.stack([us[good], vs[good]], axis=1))
            ds.append(depth[vs[good], us[good]])
            del depth
    finally:
        del model, proc
        if device == "mps":
            torch.mps.empty_cache()

    if not uvs:
        raise CalibrationError("no vehicles detected -- cannot locate the road surface")

    pts_uv = np.concatenate(uvs).astype(float)
    depth = np.concatenate(ds).astype(float)
    h_img, w_img = frames[0].shape[:2]

    if not focals:
        raise CalibrationError(
            f"{model_id} does not predict focal length, and nothing else here "
            "recovers it. Use apple/DepthPro-hf (its FOV head is the reason it is "
            "the default), or --nadir, where focal is degenerate with altitude.")
    focal, src = float(np.median(focals)), "model FOV head"

    P = pixel_rays((focal, w_img / 2, h_img / 2), pts_uv) * depth[:, None]
    P = P[np.isfinite(P).all(1)]
    n, _, inliers = ransac_plane(P)
    log(f"  focal {focal:.0f}px via {src}"
        f"   plane inliers {inliers}/{len(P)}   normal {np.round(n, 3)}")
    return focal, n, last_depth


def estimate_scale(video, tracker_model, K, n, device, imgsz, conf, log=print, prefilter=None):
    """Camera height from apparent car width, measured at h=1.

    Ground distances scale linearly with camera height, so measuring a car's
    width with h=1 and dividing the true width by it recovers the true height.
    Only the box's bottom edge is used, and only when it lies across the
    direction of travel -- otherwise the box spans the car's length, not width.
    """
    from collections import deque, defaultdict

    tracks, widths = defaultdict(lambda: deque(maxlen=HEADING_BASELINE + 1)), []
    track = dict(imgsz=imgsz, conf=conf, device=device, tracker="bytetrack.yaml",
                 persist=True, verbose=False)
    if prefilter is None:
        stream = tracker_model.track(source=video, stream=True, **track)
    else:
        # Frame by frame, so calibration sees exactly what the live detector
        # sees after clean-up; otherwise vehicle widths are measured on a
        # different picture and the height comes out biased.
        def filtered():
            cap = cv2.VideoCapture(video)
            while True:
                ok, f = cap.read()
                if not ok:
                    break
                yield tracker_model.track(prefilter(f), **track)[0]
            cap.release()
        stream = filtered()
    for idx, r in enumerate(stream):
        if idx >= SCALE_FRAMES:
            break
        if r.boxes is None or r.boxes.id is None:
            continue
        boxes = r.boxes.xyxy.cpu().numpy()
        ids = r.boxes.id.cpu().numpy().astype(int)
        names = [tracker_model.names[int(c)] for c in r.boxes.cls.cpu().numpy()]
        keep = [i for i, nm in enumerate(names) if nm == "car"]   # tightest size spread
        if not keep:
            continue
        boxes, ids = boxes[keep], ids[keep]

        centres, ok_c = ground_points(K, n, 1.0, box_centres(boxes))
        left, ok_l = ground_points(K, n, 1.0, boxes[:, [0, 3]])    # bottom-left
        right, ok_r = ground_points(K, n, 1.0, boxes[:, [2, 3]])   # bottom-right

        for i, tid in enumerate(ids):
            if not (ok_c[i] and ok_l[i] and ok_r[i]):
                continue
            hist = tracks[tid]
            hist.append(centres[i])
            if len(hist) <= HEADING_BASELINE:
                continue
            edge = right[i] - left[i]
            w = float(np.linalg.norm(edge))
            if w < 1e-9:
                continue
            # Heading over a multi-frame baseline: one frame of displacement is
            # comparable to detection jitter. Requiring the car to cover a set
            # fraction of its own width keeps this free of absolute scale.
            travel = centres[i] - hist[0]
            d = float(np.linalg.norm(travel))
            if d < MIN_TRAVEL_WIDTHS * w:
                continue                                   # parked or crawling
            if abs(travel.dot(edge)) / (d * w) < 0.5:      # edge across travel
                widths.append(w)

    if len(widths) < 20:
        raise CalibrationError(
            f"only {len(widths)} usable width samples (need 20). Either the clip "
            "has too few moving cars, or the camera mode is wrong -- a nadir fit "
            "on an oblique view rejects almost every sample.")
    # The box's left/right extremities lie on the car *body*, ~0.75 m up, not on
    # the road. Rays through them, extended to z=0, land further apart than the
    # true footprint by h/(h - z_body), so the width measured at unit height is
    #     w1 = W_true / (h - z_body)   =>   h = W_true/w1 + z_body
    # Dropping that second term is a pure underestimate of h -- it accounted for
    # a 16% scale error here, and restoring it brought the measured lane width
    # from 3.18 m to 3.59 m against a 3.7 m reference.
    # A low percentile, not the median. The box is axis-aligned, so for a car
    # whose heading makes angle phi with the image horizontal its box spans
    #     extent = L*|cos phi| + W*|sin phi|   >=  W,  equality only at phi=90
    # -- the measurement can only ever be inflated by the car's *length*, never
    # deflated. So the samples have a hard floor at the true width and a
    # contaminated tail above it, and the median sits inside the contamination.
    # The perpendicularity gate alone cannot fix this: L >> W, so even 85 degrees
    # off admits 4.5*0.087 + 1.83*0.996 = 2.21 m, still 21% over.
    #
    # The extreme minimum is worse than the median, though -- detection jitter
    # corrupts the bottom tail. Measured against lane markings on real footage:
    # min +140%, p5 +12%, p25 +2%, median -6%. p25 balances the two error sources.
    h = CAR_WIDTH_M / float(np.percentile(widths, WIDTH_PERCENTILE)) + VEHICLE_CENTROID_H
    log(f"  {len(widths)} width samples   camera height {h:.2f}m")
    if len(widths) < 100:
        # A sound fit on real traffic yields many hundreds. A trickle usually
        # means the mode is wrong for this camera -- a nadir fit on an oblique
        # view rejects nearly every sample, then produces a confident-looking
        # height from the few that slip through.
        log(f"  WARNING: only {len(widths)} samples -- treat this calibration as "
            "suspect; check the camera mode matches the actual viewpoint")
    return h


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("video")
    ap.add_argument("--model", default="models/yolo11s.pt")
    ap.add_argument("--out", default="calib/scene.json")
    ap.add_argument("--device", default="mps")
    ap.add_argument("--depth-device", default="mps")
    ap.add_argument("--depth-model", default=DEPTH_MODEL,
                    help="metric-depth model for the ground-plane fit "
                         "(~1.2GB, 0.7s/frame at the default)")
    ap.add_argument("--nadir", action="store_true",
                    help="camera points straight down (drone/overhead). Skips the "
                         "depth model entirely -- see estimate_scale docstring")
    ap.add_argument("--altitude", type=float,
                    help="true camera altitude in metres (nadir mode). Speeds are "
                         "already exact without it; this fixes the absolute "
                         "distance readout, which otherwise carries the assumed "
                         "focal length's error")
    ap.add_argument("--imgsz", type=int, default=1280)
    ap.add_argument("--conf", type=float, default=0.25)
    ap.add_argument("--frames", type=int, default=DEPTH_FRAMES)
    args = ap.parse_args()

    try:
        calibrate_video(args.video, args.out, model=args.model, nadir=args.nadir,
                        altitude=args.altitude, device=args.device,
                        depth_device=args.depth_device,
                        depth_model=args.depth_model,
                        imgsz=args.imgsz, conf=args.conf, frames=args.frames)
    except CalibrationError as e:
        raise SystemExit(f"calibration failed: {e}")


def calibrate_video(video, out, model="models/yolo11s.pt", nadir=False,
                    altitude=None, device="mps", depth_device="mps",
                    depth_model=DEPTH_MODEL, imgsz=1280, conf=0.25,
                    frames=DEPTH_FRAMES, log=print, prefilter=None):
    """Calibrate one video and write the JSON. The single calibration entry point.

    Both the CLI and the live pipeline call this, so the two cannot drift apart.
    Previously each kept its own copy of the sample/detect/depth/scale sequence,
    and only one of them freed the detector before the depth model loaded --
    exactly the overlap that exhausts memory.
    """
    from ultralytics import YOLO

    sampled = sample_frames(video, frames, prefilter)
    h_img, w_img = sampled[0].shape[:2]

    if nadir:
        # Straight-down view: the ground plane is perpendicular to the optical
        # axis, so the normal is known exactly and no depth model is needed.
        #
        # Running one anyway would be worse than useless. Plane tilt is driven by
        # the depth *gradient* across the frame, which is ~zero here, so the fit
        # has no signal to lock onto and merely converts the depth model's error
        # into tilt that is not there -- amplified by f/(W/2), about 1.67x at
        # these sizes, so a 3% depth error becomes ~3 degrees of false tilt.
        #
        # Focal length is unnecessary too: ground offset is H*(u-cx)/f, so only
        # the ratio H/f is observable -- f and altitude are degenerate. Fixing f
        # arbitrarily and solving for h from car width matches that ratio, making
        # ground positions and speeds exact for ANY f. Only the reported slant
        # distance depends on f being right, which --altitude resolves.
        #
        # This is also why a single METERS_PER_PIXEL constant works overhead: for
        # this one case it is the mathematically correct model.
        focal, n, depth_map = float(w_img), np.array([0.0, 0.0, -1.0]), None
        log(f"[1/2] nadir: normal fixed at {n}, f fixed at {focal:.0f}px "
            "(degenerate with height) -- depth model skipped")
    else:
        log(f"[1/3] locating road surface in {len(sampled)} frames")
        detector = YOLO(model)
        masks = detect_roads(sampled, detector)
        del detector                      # one model on the GPU at a time
        if device == "mps":
            torch.mps.empty_cache()

        log("[2/3] depth bootstrap: focal length + ground plane")
        focal, n, depth_map = estimate_geometry(sampled, masks, depth_device,
                                                depth_model, log=log)

    K = (focal, w_img / 2, h_img / 2)
    log(f"[{'2/2' if nadir else '3/3'}] scale inference from car width")
    h = estimate_scale(video, YOLO(model), K, n, device, imgsz, conf, log=log,
                       prefilter=prefilter)

    if altitude:
        # Only h/f is observable from a nadir view, so a known altitude resolves
        # the remaining degree of freedom. Rescaling both together leaves h/f --
        # and therefore every ground position and speed -- untouched, while
        # making the reported slant distance real.
        f_true = K[0] * altitude / h
        log(f"  altitude {altitude:.1f}m given -> true focal {f_true:.0f}px "
            f"(was {K[0]:.0f}); ground positions and speeds unchanged")
        K, h = (f_true, K[1], K[2]), altitude

    if depth_map is not None:
        np.save(depth_path(out), depth_map)
        log(f"  depth map saved -> {depth_path(out)}")
    save_calib(out, K, n, h, (w_img, h_img))
    horizon = K[2] + K[0] * (-n[2] / n[1]) if abs(n[1]) > 1e-9 else float("nan")
    log(f"  f={K[0]:.0f}px  height={h:.2f}m  "
        f"tilt={np.degrees(np.arcsin(-n[2])):.1f}deg  horizon_row={horizon:.0f}")
    log(f"  wrote {out}")
    return load_calib(out)


if __name__ == "__main__":
    main()
