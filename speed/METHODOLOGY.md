# Methodology

How the pipeline works and why each choice was made. Empirical figures quoted
here were measured on the clips in `data/`; see **§14** for how they were
checked. For usage see [README.md](README.md).

---

## 1. The problem with the original approach

- **`METERS_PER_PIXEL = 0.05`** collapsed all of projective geometry into one
  constant. Valid only when the ground is fronto-parallel at a fixed distance —
  i.e. a nadir drone shot at known altitude.
- **It fails on oblique views** because metres-per-pixel varies across the frame:
  a car near the horizon covers several times more ground per pixel than one in
  the foreground.
- **Speed came from a 3-frame pixel difference**, which differentiates tracker
  jitter straight into the output.
- **Over-speed readings were silently zeroed** (`if speed > MAX: speed = 0`),
  making a tracker glitch indistinguishable from a parked car.
- The constant was also *wrong even for nadir*: calibration measured
  **0.034 m/px** against the hand-tuned 0.05 — a ~47% overestimate.

## 2. Core decision: geometry, not per-frame depth

- **Speed is a derivative.** Per-frame depth networks flicker, and differencing
  amplifies that noise. Depth-based speed methods land near 3.6 km/h RMSE;
  geometric calibration reaches ~1.1 km/h.
- **So: recover scene geometry once, then use exact projective math every
  frame.** Vehicles sit on a road; the road is a plane. Given intrinsics and pose
  relative to that plane, a pixel back-projects to an exact 3D point.
- **Consequence:** no neural depth in the runtime loop, no flicker, and residual
  error is a *systematic bias* you can calibrate out rather than random noise you
  cannot.
- **Distance from camera needs no depth model at all** — it falls out of the
  ray/plane intersection as `‖P‖`.

## 3. Camera model and the plane

- **OpenCV convention**: X right, Y down, Z forward, camera at origin.
- **Ground plane** is `{P : n·P = −h}` — `n` the unit up-vector in camera
  coordinates, `h` the camera height above it.
- **Ray for a pixel**: `d = K⁻¹[u,v,1]`.
- **Intersection**: `t = −(h−z)/(n·d)`, valid only when `n·d < 0` — rays at or
  above the horizon never meet the plane and are rejected rather than producing
  garbage.
- **World frame**: origin at camera nadir, +Z up, so ground points land at `z=0`
  and the camera sits at `(0,0,h)`.

## 4. Calibration — fixed / oblique camera

Each quantity comes from whichever source is actually trustworthy for it.

- **Focal length ← Depth Pro's FOV head.** This is the *only* reason the large
  model is used. Not its metric depth.
- **I tried to replace it and failed.** `solve_focal()` searched for the focal
  that makes the road planar. It is structurally broken: as `f→∞` every point
  collapses onto a line, which is trivially planar, so the residual falls
  monotonically and the search runs away. On a clip of known focal 1589 px it
  returned the 5760 px search ceiling and a 7–8° tilt against a 23.75°
  vanishing-point truth. Kept in the code, explicitly labelled unreliable,
  rather than deleted — the negative result is worth recording.
- **Plane orientation ← RANSAC on the depth point cloud.** Only the *unit
  normal* is kept. A normalized vector is invariant to a wrong global scale,
  which matters because Depth Pro's absolute scale is out of distribution on
  aerial or unusual views.
- **Road mask is anchored to vehicles** — a dilated band around detected boxes,
  minus the boxes. Fitting to "where cars actually are" keeps the plane off
  rooftops and tree canopy.
- **Plane fit uses the 3×3 covariance, not `np.linalg.svd(P)`.** numpy's SVD
  defaults to `full_matrices=True`, which for an (N,3) cloud allocates an (N,N)
  factor — **14 GB at N=42k, 320 GB at N=200k**. This, not the depth model, was
  what crashed the machine twice.
- **Two frames are enough.** The camera is static, so focal and tilt are
  identical every frame, and one frame's road mask already yields ~10⁵ points.

## 5. Calibration — drone / nadir camera

- **The normal is known exactly**: `n = [0,0,−1]`. Nothing to estimate.
- **No depth model is loaded at all** — and running one would be *worse than
  useless*. Plane tilt is recovered from the depth *gradient*, which is ~zero
  looking straight down, so a fit has no signal and converts the model's own
  error into tilt that is not there. Amplified by `f/(W/2)` ≈ 1.67×, so a 3%
  depth error becomes ~3° of false tilt.
- **Depth is genuinely constant.** `P = H·[(u−cx)/f, (v−cy)/f, 1]` — the Z
  component is `H` for *every* pixel. Verified: 65.20 m at frame centre,
  65.20 m at the corners; only slant range varies (to 70.94 m).
- **Focal is degenerate with altitude.** Ground offset is `H·(u−cx)/f`, so only
  the ratio `H/f` is observable. Fixing `f` arbitrarily and solving `h` from car
  width matches that ratio — ground positions and speeds are exact for *any* `f`.
- **This is why a single metres-per-pixel constant is correct overhead** — and
  only overhead. The original approach worked there for a real reason.
- **`--altitude` resolves the remaining freedom.** Rescaling `f` and `h` together
  leaves `H/f` untouched, so speeds do not change but the absolute distance
  readout becomes real.

## 6. Scale — the physical anchor

- **Absolute scale never comes from a network.** It comes from car width against
  **1.83 m**, a physical constant.
- **Measurement**: back-project each tracked car's box bottom edge to the ground
  at `h=1`. Ground distances scale linearly with height, so `h = W_true / w₁`.
- **Plus the body-height term.** The box's left/right extremities sit on the car
  *body*, ~0.75 m up, not on the road. Rays through them extended to `z=0` land
  further apart by `h/(h−z_body)`, giving `w₁ = W/(h−z_body)`, hence:

  ```
  h = CAR_WIDTH_M / w₁ + z_body
  ```

  Omitting that second term cost a **16% scale error**; restoring it moved
  measured lane width from 3.18 m to 3.59 m against a 3.7 m reference.
- **25th percentile, not median.** For a car whose heading makes angle φ with the
  image horizontal, the axis-aligned box spans `L·|cos φ| + W·|sin φ| ≥ W`, with
  equality only at φ=90°. The error is **one-sided** — length contamination can
  only inflate the measurement. So the samples have a hard floor at the truth and
  a contaminated tail above it, and the median sits inside the contamination.
- **The perpendicularity gate alone cannot fix it**: since `L ≫ W`, even 85° off
  admits `4.5(0.087) + 1.83(0.996) = 2.21 m`, still 21% over.
- **But the raw minimum is far worse** — detection jitter corrupts the bottom
  tail. Scored against lane markings: min +140%, p5 +12%, **p25 +2%**,
  median −6%.
- **Only `car`-class detections are used** — trucks and buses have a much wider
  size distribution.

## 7. Runtime measurement chain

- **Ray through the box *centre*, intersected with a plane at `z=0.75 m`**
  (vehicle centroid height) — not the box bottom. In an oblique view the box
  bottom is the tyre contact line, but in a nadir view the box is the *roof*
  outline and its "bottom" is meaningless. Centroid-height intersection is
  approximately correct in both regimes with no branch.
- **Velocity from a windowed least-squares fit** over 0.6 s of ground positions —
  not frame differencing. Handles irregular `dt`, gives heading for free, and is
  transparent to debug. A constant-velocity Kalman filter is the upgrade path if
  occlusion bridging is ever needed.
- **Timestamps come from a real clock or media position, never a frame counter**
  — the live path drops frames to stay current, so a counter would not measure
  elapsed time.

## 8. Uncertainty — the part that keeps it honest

- **Precision falls off linearly with distance, not quadratically.** The naive
  derivation gives `d²/(h·f)` per pixel of wobble, true for a *fixed* pixel
  jitter. But detector jitter scales with box size, and a car's box shrinks as
  `1/d`, cancelling one power:

  ```
  σ_px  ≈ JITTER_FRAC · W · f / d
  σ_pos = d²·σ_px/(h·f) = JITTER_FRAC · W · d / h
  ```

- **Measured on 114 tracked vehicles spanning 20–160 m, scatter scales as
  d^1.00.** The quadratic form over-predicted noise by **5–20×** and withheld
  most of a motorway — 35% of readings reported versus **92%** once corrected.
- **No `1/√n` averaging term.** Detection bias is *correlated* across
  neighbouring frames — a box drifts coherently rather than rattling
  independently — so extra samples inside one short window buy no precision.
  Assuming independence underestimated real noise threefold.
- **A reading must clear 2σ to be claimed.** 2σ is the ordinary "distinguishable
  from zero" bar; 3σ was measurably too strict.
- **Three deliberately distinct output states:**

  | State | Meaning |
  |---|---|
  | `N km/h` | measured and resolvable |
  | `0.0 km/h (stopped)` | genuinely stationary — a **valid reading**, not an error |
  | `UNRELIABLE` | implausible, high-residual, or below the noise floor — **no claim made** |

- **Collapsing the third into the second was the original bug.** A parked car and
  a tracker glitch must stay tellable apart.

## 9. Detection

- **The camera mode selects the detector, not just the geometry.** A COCO model
  barely recognises a car from directly overhead — measured on the drone clip,
  yolo11s found **0** vehicles/frame at conf 0.25 and 1–2 at conf 0.10, while
  visDrone found **23–28**.
- **yolo11s over yolo11m for oblique** — benchmarked on real footage it is
  **2× faster (34 vs 16 fps) *and* finds more vehicles (15 vs 10)**. The larger
  model is more conservative on distant, low-contrast frames. Not a tradeoff.
- **Class filtering matches on *name*, not index**, so it survives a model swap.
- **ByteTrack with `persist=True`** supplies the stable IDs the trajectory
  history is keyed on.

## 10. Ego-motion

- **Drift correction for hovering drones**: ORB + RANSAC homography from the
  current frame back to the calibration reference, computed on **background
  only** (vehicle boxes masked out) so moving traffic cannot drag the estimate.
- **Gated in both directions**: skipped below ~1 px (a genuinely static camera,
  where fitting only injects match noise) and rejected above ~50 px (a bad match,
  not drift).
- **Why it matters**: 2 px of hover drift at 24 fps manufactures a phantom speed
  for every parked car.

## 11. Input handling — the layer that actually broke

Every failure in this project came from here, not from the geometry. All were
silent: the pipeline ran, produced confident, plausible-looking numbers, and
nothing announced that an input assumption had been violated.

- **YouTube's `best` format is 360p.** It only considers *progressive* muxed
  streams, which YouTube caps at 640×360. A 720p video silently arrived
  downscaled, placing the horizon *above* the frame and reporting motorway
  traffic at **12–16 km/h**. Fixed by requesting video-only (`bv*`) — audio is
  irrelevant here.
- **Live vs recorded need opposite treatment.** Live: keep the newest frame, drop
  stale ones, stamp with the wall clock. Recorded: keep every frame with
  backpressure, stamp by media position, pace to real time. A finite video
  decodes **~42× faster than real time**, so wall-clock stamps would compress the
  whole clip into a moment.
- **Replay needs monotonic stamps and history clearing.** Without both, tracks
  straddling the loop boundary get a near-zero `dt` and an absurd speed —
  observed at 97 km/h for a car doing about 46.
- **Calibration clips are keyed per source.** They were a single shared filename
  with "reuse if it exists" logic, so only the first source ever recorded one and
  **every later video was calibrated from the first one's footage** — three
  unrelated clips came out with byte-identical calibrations and depth maps.

## 12. Resource discipline

- **One model on the GPU at a time.** The detector is explicitly freed before the
  depth model loads. That overlap is what exhausts a 24 GB unified-memory
  machine.
- **MPS watermarks set before torch initialises** (`LOW=0.5`, `HIGH=0.6` — and
  both must be set, since the low default of 1.4 must not exceed the high) so
  torch raises a catchable OOM instead of taking the OS down.
- **fp16 on MPS is not a compromise**: against a CPU fp32 reference the focal
  matched to 0.06% with identical depth percentiles, at 5× the speed and half the
  memory.
- **Depth Pro's input is fixed at 1536×1536** (`patch_size=384` × ratios
  `[0.25,0.5,1]`) — not reducible, so the cost is ~7 GB regardless of source
  resolution.
- **Weights live in the project** (`models/hf/`), with `HF_HOME` set before
  importing transformers; verified loadable with `HF_HUB_OFFLINE=1`.

## 13. Serving

- **stdlib `http.server` + MJPEG multipart** — one endpoint needs no framework.
- **Deliberately HTTP/1.0**: an MJPEG body is endless with no `Content-Length`;
  under HTTP/1.1 a client expects a length or chunked encoding, gets neither, and
  treats the response as malformed.
- **The stream element self-heals.** An MJPEG `<img>` never reconnects on its own
  — a server restart leaves it blank forever — so it attaches only while visible
  and re-arms on `error`. Reconnect URLs carry a cache-buster, which means routes
  must match on **path**, not the whole request string.
- **Reader threads join before releasing the capture.** Calling
  `VideoCapture.release()` while another thread is inside `read()` segfaults the
  interpreter with no traceback.

## 14. Validation method

- **Never validate against intuition; validate against a measurable reference.**
  An early assumption that one clip was a "60–110 km/h highway" was wrong and
  sent the investigation hunting a non-existent bug for a long time. It was an
  urban arterial.
- **References used**: the lane-marking vanishing point (tilt agreed to 0.6°),
  lane width against 3.7 m / 3.65 m standards (+2%), the visible horizon row
  versus the calibrated `horizon_row` (206 vs ~199), and parked cars that must
  read ~0.
- **Cross-validation across independent paths**: two cameras, two calibration
  methods (depth-based oblique and model-free nadir), converging on the same
  physical answer.
- **The end-to-end check reports `NOT EXERCISED`** when a clip contains no
  stationary vehicle, rather than passing vacuously.
- **Ground truth is taken from each track's own trajectory**, never from the
  speed the pipeline reported — otherwise the check merely confirms its own
  answer.

## 15. Known limits

- **Depth Pro's focal head is a single point of failure with no cross-check.**
  Every silent failure was diagnosed by hand-comparing `horizon_row` to the
  visible horizon. Automating that comparison is the highest-value remaining
  addition.
- **Single flat plane** — hills, banked curves and multi-level junctions all
  violate it.
- **No lens distortion model** — wide-angle and security cameras carry systematic
  error toward the frame edges.
- **The camera must not move**; any pan/tilt/zoom silently invalidates the
  calibration and nothing detects it.
- **Detection is the real floor** — night footage found almost nothing regardless
  of how good the geometry was.
- **Empirically tuned constants**: `WIDTH_PERCENTILE = 25`,
  `JITTER_FRAC = 0.006`, `SIGMA_TO_REPORT = 2.0`. The reasoning behind each is
  principled, but the specific values come from a handful of clips. They are
  exposed as named constants rather than buried.
- **At 110–160 m the noise model sits at 0.66×** — slightly optimistic at extreme
  range.
- **Evidence base is four scenes, all fixed cameras** (three oblique, one nadir).
  That is not a broad sample.
