# Vehicle Speed & 3D Localization

Absolute 3D position, distance from camera, and speed for vehicles, from a fixed
camera at any angle. Replaces a single hand-tuned `METERS_PER_PIXEL` constant
with a calibrated projective model.

Calibration runs once per scene. At runtime there is no neural depth in the
loop — a vehicle's position is an exact ray/ground-plane intersection, so there
is no per-frame depth flicker to differentiate into the speed output.

Full design rationale — every choice and why, including the failures — is in
[METHODOLOGY.md](METHODOLOGY.md), and as a typeset 12-page report with the
mathematical formulation in [docs/Methodology.pdf](docs/Methodology.pdf).
The two calibration tracks (Depth Pro vs. overhead) are compared side by side in
[docs/Two_Tracks_Methodology.pdf](docs/Two_Tracks_Methodology.pdf), and their measured
accuracy in [docs/Accuracy_Comparison.pdf](docs/Accuracy_Comparison.pdf).

## Layout

```
speedcam.py        geometry helpers + offline runtime
calibrate.py       one-shot scene calibration (single entry point: calibrate_video)
live_pipeline.py   live stream -> calibration -> web UI on localhost
dashboard.html     the operator console (Kestrel), served by live_pipeline.py
bench_degraded.py  what the drone camera's quality and shake cost, per fix
test_speedcam.py   geometry checks + end-to-end verification

models/            yolo11n/s/m.pt, visDrone.pt
  hf/hub/          Depth Pro weights, 1.8 GB (HF_HOME points here)
data/              input videos and recorded calibration clips
calib/             calibration JSON + depth map, one per scene and mode
output/            rendered videos and frames
```

**Where the depth model lives.** Depth Pro is not a `.pt` file — it is a
HuggingFace repo, now stored inside the project at
`models/hf/hub/models--apple--DepthPro-hf` (1.8 GB). `calibrate.py` sets
`HF_HOME` to `models/hf` before importing transformers, so nothing reaches for
the user-wide `~/.cache/huggingface` cache. Verified by loading it with
`HF_HUB_OFFLINE=1`. The internal blob symlinks are relative, so the directory is
portable; if the weights are ever missing they simply re-download to the same
place.

## Use

**Any source, with web UI** — live stream, an ordinary uploaded video, or a
local file:

```bash
python3 live_pipeline.py
#  ==> http://localhost:8000
```

The server starts immediately and the **source is chosen in the page** (the
Source button: drone camera, demo footage, or any stream URL or file), not the
terminal. Sources can be swapped at any time without restarting — each gets its
own calibration, cached and reused. `--url <source>` preloads one.

Recorded sources play from the start and loop; live sources track the live edge,
and stay on screen while they calibrate. The page has a **depth-map overlay**
toggle (D), fullscreen (F), and a Recalibrate button for live cameras. The page
itself is `dashboard.html`, read on every request: edit it and refresh, no
restart (and so no recalibration) needed. Useful flags:
`--mode drone`, `--recalibrate`, `--port`, `--imgsz 960` (faster), `--altitude`.

**Recorded video:**

```bash
# oblique (pole, overpass, CCTV)
python3 calibrate.py data/car_video.mp4 --model models/yolo11s.pt --out calib/car.json
python3 speedcam.py  data/car_video.mp4 --calib calib/car.json --model models/yolo11s.pt

# overhead / drone -- skips the depth model entirely
python3 calibrate.py data/drone_video.mp4 --model models/visDrone.pt --conf 0.10 \
        --nadir --altitude 40 --out calib/drone.json
python3 speedcam.py  data/drone_video.mp4 --calib calib/drone.json \
        --model models/visDrone.pt --conf 0.10
```

`--altitude` is optional: speeds and ground positions are exact without it; it
only fixes the absolute *distance* readout. Add `--save out.mp4`, `--headless`.

## Speeding log (optional)

Logs each speeding vehicle **once** to Supabase Postgres with an annotated
evidence frame. Off unless `--speed-limit` is given.

```bash
cp .env.example .env          # fill in the two secret values
python3 speedlog.py --init    # only if the table/bucket do not exist yet
python3 live_pipeline.py --speed-limit 70 --camera "M6 J12 southbound"
python3 speedlog.py --recent  # inspect what was logged
```

Two Supabase gotchas, both of which cost a debugging round here:

- The pooler host is `aws-1-<region>` for newer projects; `aws-0-` returns
  `tenant/user not found`. Copy the exact string from the dashboard.
- Modern `sb_secret_...` keys are **opaque, not JWTs**. They go in the `apikey`
  header — sent only as `Authorization: Bearer`, storage rejects them with
  `Invalid Compact JWS`.

Row: `id, detected_at, camera, vehicle_id, vehicle_type, speed_kmh, speed_limit,
frame_url, session_id`, unique on `(session_id, vehicle_id)`.

Below the video the page shows a **violations ledger** (last hour, last 24 hours,
all time, fastest, average amount over the limit, breakdown by vehicle type) and a
**table** of the latest 100 events: speed against the limit, the guard response
(who is responding, or how many guards were alerted), and the evidence frame,
which opens full size. A new event flashes its row and shows a notice on the
video. It polls
`/events`, which reads a local SQLite copy (`output/speedlog.db`), never the
cloud: during a flight the drone has the Wi-Fi and the internet is a phone over
USB. Events are saved there first, with their images in `output/evidence/`, and
a background thread copies them to Supabase and pulls guard responses back
every 5 s. A row the cloud hasn't received yet (so no guard has been alerted)
shows "Not in the cloud yet", and the header counts what is waiting. The whole
section stays hidden unless `DATABASE_URL` is set.

**A record is deliberately harder to earn than an on-screen number:**

- only `state == "ok"` readings count — an `UNRELIABLE` reading must never
  become a ticket, which is the entire reason that state exists
- the vehicle must stay over the limit **continuously for 0.7 s**, expressed in
  seconds rather than frames: at 30 fps three consecutive readings span 0.086 s
  and share ~85% of the same 0.6 s velocity window, so they are one measurement
  counted three times, not three confirmations
- the row keeps the vehicle's **peak** speed, not its first over-limit reading

Measured on `car_video.mp4` at a 40 km/h limit: 44 vehicles crossed the limit at
some point, **20 were logged**, and the 24 suppressed had a longest continuous
exceedance of 0.37 s median / 0.67 s max — all genuinely momentary, against a
0.77 s minimum among those logged.

Evidence crops are cut from the frame **before** any annotation is drawn. Cutting
from the annotated frame bakes the previous vehicle's label into the corner, so a
crop captioned "104 km/h" can carry a stale "78 km/h" — which discredits the
evidence rather than supporting it.

Measured end to end on `car_video.mp4` at a 40 km/h limit: 54 writes collapsed to
**18 rows**, zero duplicate `(session_id, vehicle_id)`, all 18 with a resolving
frame URL, at 34-38 fps.

Logging never blocks or breaks the pipeline: events go through a bounded queue to
a background writer, and an unreachable database degrades to one warning while
the stream continues at full frame rate (measured: 39 fps in all cases).

### Drone camera (Wi-Fi)

The drone (KY UFO app, CooingDV camera) streams **standard RTSP**: MJPEG 640x352
at about 22 fps over UDP, at `rtsp://192.168.1.1:7070/webcam`. Nothing has to be
sent to the drone first, so no flight-control traffic is involved. Paste that
address into the dashboard, or set `DRONE=1` in `Start Vehicle Monitor.command`,
which waits up to 30 s for the stream and then opens it in drone mode. A live
drone recalibrates every flight, because its hover height changes; a fixed
camera's stream keeps its calibration.

`python3 drone_check.py` diagnoses the link without internet: it finds the drone
from the Wi-Fi connection, checks RTSP over UDP and TCP, and records a short
clip into `drone_check/`.

Other KY UFO drones use a Bouffalo BL618 camera at `192.168.169.1` with a
proprietary UDP protocol (fragmented JPEGs, per-frame acknowledgements that also
carry a "sticks centred" flight command). `drone_feed.py` bridges those to
`http://127.0.0.1:8090/feed.mjpg` and is tested against a fake drone
(`test_drone_feed.py`); it is not needed for the current drone.

**Camera shake.** Hovering, the drone's camera moves 2.6 px per frame (median;
9 px p95, measured on its own footage). Uncorrected, that gave 54% of parked
cars a speed of 5 km/h or more. In drone mode every frame is registered to one
reference view (`MotionCompensator` in `speedcam.py`): background points, with
vehicles masked out, are tracked from a keyframe and fitted with a
shift/rotation/scale model, 1.7 ms per frame. Speeds are measured in that
reference view; drawing and evidence crops still use the live frame. If the
view is lost the speed history restarts rather than mixing two views.
`--no-stabilize` turns it off for comparison.

`python3 bench_degraded.py` measures this. It degrades clean overhead footage to
match the real camera (its MJPEG blocking plus noise, and its recorded shake
replayed frame by frame), runs both copies through the live chain, and scores
the degraded one against the clean one (600 frames, about 70 s):

| speed chain | moving: median error | moving: p90 error | parked cars given >= 5 km/h | usable readings |
|---|---|---|---|---|
| no correction | 3.3 km/h | 11.7 km/h | 54% | 89% |
| DriftCorrector (offline tool) | 2.5 km/h | 9.2 km/h | 32% | 67% |
| **MotionCompensator** | **0.6 km/h** | **3.6 km/h** | **5%** | **91%** |

The stabiliser itself is accurate to 0.03 px median (0.09 px worst) against the
known shake.

**Frame clean-up.** In drone mode each frame gets an edge-preserving (bilateral)
smoothing before detection, 1.4 ms per frame, and calibration applies the same
filter so vehicle widths are measured on the same picture. `--no-cleanup`
turns it off. Measured with the stabiliser on:

| frames | detection recall | precision | moving: p90 error | calibrated height |
|---|---|---|---|---|
| raw | 76% | 94% | 3.6 km/h | -4.1% |
| **clean-up** | **84%** | 92% | **3.1 km/h** | **-1.0%** |

Dedicated 8x8 deblocking and colour smoothing were tested on top
(`bench_degraded.py --filters all`) and added nothing measurable, nor anything
visible on the real camera's frames, so they are not used. A stronger bilateral
setting found 1 point more vehicles at the cost of 3 points of precision.

**Display.** What the operator sees also gets the still background averaged
over time, in the stabilised view so shake does not smear it (`TemporalDenoiser`,
about 4 ms per frame). Where anything changes, and inside every detected
vehicle, the pixel is this frame's own, so traffic can neither lag nor leave a
ghost. It runs after speeds and evidence are taken, so it changes neither.
Against the clean copy (`bench_degraded.py --display`):

| display | background PSNR | background flicker | vehicles PSNR |
|---|---|---|---|
| as recorded | 34.8 dB | 3.33 | 31.8 dB |
| cleaned | 35.8 dB | 2.66 | 32.5 dB |
| **cleaned + temporal** | **36.9 dB** | **0.94** | 32.5 dB |

Flicker is the background's frame-to-frame change once the true shake is
undone; the clean copy itself measures 1.02 (real changes such as shadows and
pedestrians), so the display is now as steady as clean footage.

**Frame timing.** A speed is distance over time, and the time between frames
was taken from when each frame *arrived*. Over Wi-Fi that is late and uneven:
frames come in clumps after a stall, a receive buffer can hold them back for
half a second, and right after a stream opens OpenCV hands over buffered frames
0.8 ms apart that were captured 41.7 ms apart. Live frames are now stamped from
the camera's own timestamps (`StreamClock` in `speedcam.py`), carried in RTP.
They are trusted only after 3 s of agreeing with the arrivals, because some
streams have none: for HTTP MJPEG, OpenCV invents them at a nominal 25 fps,
which would read speeds 22% high. Those are refused for good; a one-time step
in the path delay is not mistaken for them. Switching timelines restarts the
speed histories. Arrival time still measures the on-screen delay.

Tested through a relay that gives an RTP stream Wi-Fi-like delay, jitter and
150 ms stalls, into the real `LiveReader` (`bench_degraded.py --timing`
replays that timing on the same detections):

| frame times | spacing error p95 / worst | speed error median / p90 / p99 |
|---|---|---|
| arrival (before) | 40.9 ms / 478 ms | 0.50 / 3.94 / 11.92 km/h |
| **camera clock** | **0.02 ms / 0.02 ms** | **0.02 / 0.02 / 1.16 km/h** (p99: the first 3 s, before it is trusted) |

The relay's timing is simulated. `drone_check.py` now records the real drone's
(`drone_check/timing.csv`), and `--timing` uses that file when it exists.

**Evidence images.** Two images are stored per violation: the frame as recorded
(`<id>_raw.jpg`, uploaded first, because it is the record) and the same crop
from the cleaned frame (`<id>.jpg`, the one shown, captioned as noise-reduced).
The dashboard's evidence viewer switches between them. Scored against the 1080p
source the degraded clip was made from (`bench_degraded.py --evidence`, 117
moving vehicles, inside the vehicle box):

| evidence image | SSIM (median) | better than as recorded | cost |
|---|---|---|---|
| as recorded | 0.616 | - | - |
| **cleaned (shipped)** | **0.637** | **98%** | none (the frame is cleaned anyway) |
| 12-frame fusion, optical-flow aligned | 0.634 | 79% | 124 ms per event |
| 12-frame fusion + clean-up | 0.641 | 81% | 125 ms per event |

Multi-frame fusion was the plan and does not earn its place. Aligning on box
centres, translation ECC, rotation-aware ECC and a brute-force shift search all
made evidence *worse* than a single frame: boxes jitter 1-2 px, and the
camera's rotation plus the vehicle's own change in appearance defeat rigid
alignment. Only dense optical flow made it work, and then it beats the free
option by 0.004 SSIM for 125 ms and ~60 lines. Its code stays in the benchmark.

**Internet on the drone's Wi-Fi.** One Wi-Fi radio joins one network, so the Mac
needs a second link for the speeding log and guard alerts: a USB-C Ethernet
adapter (put Ethernet above Wi-Fi in the network service order), or a travel
router that joins the drone's Wi-Fi and connects to the Mac by cable.

### Guard alerts

Each event can page the 3 nearest on-duty guards through the Flutter app in
[mobile/guard_app/](../mobile/guard_app/README.md). Pass the drone's fixed hover position, or set
`LAT`/`LON` in `Start Vehicle Monitor.command`:

```bash
python3 live_pipeline.py --speed-limit 40 --camera "Main Gate" --lat 30.351778 --lon 76.370417
python3 guards_admin.py add --email name@thapar.edu --name "Guard Name"
```

Without `--lat/--lon`, events are still logged but nobody is notified. Dispatch
runs in the database (`supabase/migrations/`), so it happens the instant the row
is written. The event row is written before the evidence image upload, over one
held connection: about 0.2 s, against 1.8 s for a fresh connection.

### Two different averages — don't confuse them

| metric | where | over what |
|---|---|---|
| **traffic avg** / 85th percentile | telemetry panel | every reliable reading from *moving* vehicles, rolling 60 s |
| **average over the limit** | violations ledger | only vehicles logged as speeding: how far above the limit they were |

Measured simultaneously on the same clip at a 45 km/h limit: traffic average
**33.7 km/h**, offender average **65.3 km/h**. Nearly double — which is why the
ledger reports the amount *over* the limit, never an "average speed" that could be
mistaken for the traffic's.
Stationary vehicles are excluded from the traffic average, or a car park in frame
would drag it toward zero and say nothing about the traffic.

The 85th percentile is the figure traffic engineers conventionally set limits
from, so it is surfaced alongside.

## Camera modes

| | Fixed / oblique | Drone / overhead |
|---|---|---|
| **detector** | **yolo11s, conf 0.25** | **visDrone, conf 0.10** |
| ground normal | recovered from depth | known exactly, `[0,0,−1]` |
| focal length | Depth Pro FOV head | degenerate with altitude |
| depth model | required (~7 GB, 13 s) | **not used at all** |
| calibration time | ~30 s | ~10 s |

The mode selects the **detector** as well as the geometry. A COCO-trained model
barely recognises a car seen from directly above: measured on the drone clip,
yolo11s found **0** vehicles per frame at conf 0.25 and 1-2 at conf 0.10, where
visDrone found **23-28**. `--model` / `--conf` override this if needed.

**Why overhead needs no depth model.** Looking straight down at flat ground,
`P = H·[(u−cx)/f, (v−cy)/f, 1]` — the Z component is `H` for *every* pixel, so
depth is constant and there is nothing for a network to estimate. Plane tilt is
recovered from the depth *gradient*, which is ~zero here, so a depth fit has no
signal and merely converts its own error into tilt that isn't there (amplified
by `f/(W/2)`, ≈1.67×, so a 3 % depth error becomes ~3° of false tilt). And since
ground offset is `H·(u−cx)/f`, only the ratio `H/f` is observable — focal and
altitude are degenerate. A single metres-per-pixel constant is genuinely the
correct model for this one case, which is why the original approach worked
overhead and collapsed obliquely.

**What Depth Pro is actually for.** Its focal-length head — not its metric
depth. Absolute scale never comes from a network; it comes from car width against
1.83 m (see the note on which statistic, below). Recovering focal from planarity alone does not work: as `f` grows
every point collapses toward a line, which is trivially planar, so such a search
runs away to infinity (measured: 5760 px ceiling and 7–8° tilt against a 23.75°
vanishing-point truth on a clip whose true focal is 1589 px).

## A trap: YouTube's default format is 360p

`yt_dlp` format `best` only considers **progressive** (muxed audio+video) streams,
and YouTube caps those at **640x360**. An uploaded 720p or 4K video therefore
arrives silently downscaled, and every measurement is made on that small frame.
Observed on a 1280x720 clip: it calibrated to a 640x360 frame, placed the horizon
*above* the top of the image, and reported motorway traffic at **12-16 km/h**.
Re-fetched at native resolution the same clip calibrates to a horizon row of 206
(the visible treeline sits at ~199) and reports 88 km/h.

The selector is therefore `bv*` (video-only) first -- audio is irrelevant here.
If speeds look wildly low, check the resolution the pipeline actually received;
it is printed on load.

## Live streams vs recorded video

Both work, but they are timed differently and the distinction is not cosmetic:

| | Live stream | Recorded video / local file |
|---|---|---|
| frames | newest only, stale ones dropped | every frame, with backpressure |
| timestamp | wall clock | media position (`index / fps`) |
| at the end | reconnect | replay from the start |
| calibration clip | records ~90 s from the live edge | the video calibrates from itself |

A finite video decodes **tens of times faster than real time** (measured: 24 s of
footage read in 0.6 s, ~42x). Wall-clock stamps would therefore compress the
whole clip into a moment and report absurd speeds, so recorded sources are timed
by media position instead — exact regardless of how fast decoding runs.

Replay also needs care: stamps are made monotonic across loops and trajectory
history is cleared when the video restarts. Without both, tracks straddling the
loop boundary get a near-zero `dt` and a wild speed (observed: 97 km/h for a car
actually doing about 46).

Verified equivalent to the offline path on the same clip: near-field median
39.5 km/h (offline) vs 38.2 km/h (live).

## Depth-map overlay

The web page can blend a colourised depth map under the annotations (red near,
blue far). Two sources, chosen automatically:

- **Depth Pro map** — saved at calibration next to the JSON as `*_depth.npy`.
  The camera is fixed, so a map made once stays valid for the session; the model
  is never run per-frame.
- **Geometric ground plane** — per-pixel ray/plane distance from the calibration
  alone, no network. This is the depth the measurements actually use, and it is
  what overhead mode shows, since nadir calibration produces no learned map (and
  a learned map there would be a nearly flat field anyway: measured 68.0–78.4 m
  across the frame, versus 10.6–143.4 m for the oblique camera).

## Scale: why the 25th percentile, not the median

Boxes are axis-aligned, so for a car whose heading makes angle `phi` with the
image horizontal the box spans

```
extent = L·|cos phi| + W·|sin phi|   >=  W,   equality only at phi = 90°
```

The measurement can only ever be inflated by the car's *length*, never deflated:
the samples have a hard floor at the true width and a contaminated tail above it,
so the median sits inside the contamination and under-reads camera height. The
perpendicularity gate cannot fix this on its own — since `L >> W`, even 85° off
still admits `4.5(0.087) + 1.83(0.996) = 2.21 m`, 21 % over.

The extreme minimum is far worse, though, because detection jitter corrupts the
bottom tail. Scored against lane markings on real footage:

| statistic | h | lane width error |
|---|---|---|
| min | 16.63 m | +140 % |
| p5 | 7.77 m | +12 % |
| **p25** | **7.04 m** | **+2 %** |
| median | 6.52 m | −6 % |

p25 balances the two error sources and is 3× better than the median.

## Accuracy and its limits

Precision falls off with distance, but **linearly, not quadratically**. The naive
derivation says one pixel of box wobble maps to `d²/(h·f)` of ground error — true
for a *fixed* pixel jitter, but detector jitter scales with the box, and a car's
box shrinks as `1/d`. The two cancel one power of `d`:

```
σ_px  ≈ JITTER_FRAC · box_width = JITTER_FRAC · W · f / d
σ_pos = d² · σ_px / (h·f)       = JITTER_FRAC · W · d / h
```

Measured on 114 tracked vehicles spanning 20–160 m, observed scatter scales as
**d^1.00**. The quadratic form over-predicted noise by 5–20× and withheld most of
a motorway — 35% of readings reported, versus 92% once corrected.

A reading must clear `SIGMA_TO_REPORT` (2σ) to be claimed; below that it is
`UNRELIABLE` rather than a confident number. 2σ is the ordinary
"distinguishable from zero" bar — 3σ was measurably too strict.

The model still omits any `1/√n` averaging: detection bias is correlated across
neighbouring frames, so extra samples inside one window buy no precision.

Three deliberately distinct output states:

| State | Meaning |
|---|---|
| `N km/h` | measured, resolvable |
| `0.0 km/h (stopped)` | genuinely stationary — a valid reading, not an error |
| `UNRELIABLE` | implausible, or below the noise floor — no claim made |

Collapsing the third into the second was the bug in the original
`if speed > MAX: speed = 0` clamp: it made a tracker glitch indistinguishable
from a parked car.

## Verified

```bash
python3 test_speedcam.py                                   # geometry only, instant
python3 test_speedcam.py <video> <model> <calib> [conf]     # + end-to-end
```

- **Oblique** (overpass): tilt agrees with the lane-marking vanishing point to
  **0.6°**; lane width reads 3.85/3.67 m against a 3.7 m reference (**+2 %**).
- **Overhead** (drone): parked cars tracked for **29 s** move under 0.3 m and
  read **0.05–0.38 km/h**; moving traffic median **41.2 km/h**.
- **Live** (traffic circle): recovered f=1256 px → hFOV 59.8°, horizon row 239
  against a visible treeline at ~239, height 11.0 m. Re-running with a different
  detector changed the height by 0.5 %.

Two cameras and two independent calibration paths converge on the same physical
answer, which is the strongest evidence here that the geometry is right.

The end-to-end check reports `NOT EXERCISED` when a clip contains no stationary
vehicle, rather than passing vacuously — `car_video.mp4` has none.

## Performance

Benchmarked on real footage (Apple M4 Pro, MPS):

| model | imgsz | detect fps | vehicles found |
|---|---|---|---|
| yolo11m | 1280 | 15.9 | 10 |
| **yolo11s** | **1280** | **34.1** | **15** |
| yolo11s | 960 | 53.1 | 14 |
| yolo11n | 1280 | 64.0 | 12 |

yolo11s is the default: on this footage it is both faster *and* finds more
vehicles than yolo11m, which is more conservative on distant, low-contrast
frames. Live pipeline sustains ~25 fps end-to-end (tracking, geometry,
annotation and JPEG encode add ~20 %) at <100 ms latency.

Calibration, measured: road detection 1 s, Depth Pro 13 s, scale inference 14 s
— **~30 s total**, plus 90 s if a clip must be recorded first. Cached
afterwards, so later runs start in ~5 s.

## Memory (Apple Silicon)

Depth Pro's input is fixed at 1536×1536 (`patch_size=384` × ratios
`[0.25,0.5,1]`) and peaks around **7 GB** in fp16 on MPS. On a 24 GB machine run
it alone; `calibrate.py` sets both MPS watermark env vars so torch raises a
catchable OOM instead of exhausting unified memory, and the detector is always
freed before the depth model loads.

Two traps worth knowing, both of which cost real crashes here:

- `np.linalg.svd(P)` on an (N,3) cloud defaults to `full_matrices=True` and
  allocates an (N,N) factor — **14 GB at N=42k, 320 GB at N=200k**. Use the 3×3
  covariance instead (`plane_normal()`).
- Releasing a `cv2.VideoCapture` while another thread is inside `read()`
  segfaults the interpreter with no traceback. Join the reader first.
- An MJPEG `<img>` never reconnects by itself, and in Safari it stayed black
  after a recalibration until the tab was reloaded. The page now fetches one
  JPEG per request from `/frame.jpg` instead; `/stream.mjpg` remains for external
  viewers. Routes match on path, not on the whole request string, because
  clients add cache-busting queries.
