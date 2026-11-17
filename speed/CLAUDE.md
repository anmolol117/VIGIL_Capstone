# Speed: vehicle speed tracking and estimation

Metric speed and position of vehicles from a calibrated camera, with a speeding
log in Supabase and guard dispatch. It replaced a prototype with a hand-set
`METERS_PER_PIXEL = 0.05`
(`~/Downloads/Vehicle Location & Speed Tracking using drone footage/vehicle_speed_location_tracking.py`).
`README.md` has the measured results, and `METHODOLOGY.md` explains why every
choice was made, including what failed. Read both before changing the geometry.

## Modules

| Module | Role |
|---|---|
| `speedcam.py` | Geometry (rays to ground plane), `fit_velocity` (least squares over a ~0.6 s window), `classify` → `warmup`/`unreliable`/`ok`, `MotionCompensator` (registers drone frames to a reference view, 1.7 ms), `StreamClock` (uses the camera's RTP timestamps once they've agreed with arrival times for 3 s), `TemporalDenoiser` (display only), `clean_frame` (bilateral filter in drone mode), offline runner |
| `calibrate.py` | `calibrate_video` is the single calibration entry point. Oblique: Depth Pro focal head plus ground normal from depth. Nadir (`--nadir`): no depth model at all |
| `live_pipeline.py` | Server on :8000. The source is chosen in the page (drone, demo, URL, file); calibration is cached per (source, mode) in `calib/`; serves `/frame.jpg`, `/stream.mjpg`, `/stats`, `/events`, `/evidence/<session>/<id>.jpg`, `/export?range=` (day, week, month, all: CSV of the local log, IST, behind the header's Download menu); re-reads `dashboard.html` on every request |
| `speedlog.py` | `Candidate` state machine, `SpeedLogger` (background queue into the local SQLite copy), the cloud sync thread, evidence crops, `--init`/`--recent` (cloud) |
| `guards_admin.py` | The **user** adds, lists and removes guard accounts |
| `drone_check.py` | Offline diagnosis of the drone link; records `drone_check/timing.csv` |
| `drone_feed.py` | Bridge for BL618 KY UFO drones (port 8090). Not used by the current drone |
| `bench_degraded.py` | Measures what drone camera quality and shake cost (`--filters`, `--display`, `--timing`, `--evidence`) |
| tests | `test_speedcam.py` (geometry; add `<video> <model> <calib>` for end to end), `test_speedlog.py`, `test_drone_feed.py` |

Models live in `models/`: `yolo11s.pt` (oblique, conf 0.25), `visDrone.pt`
(drone, conf 0.10), and Depth Pro at `models/hf/hub` (1.8 GB; `HF_HOME` points
here). Inputs are in `data/`, calibrations in `calib/`, renders in `output/`.

## Core geometry (don't break these)

- **Overhead / drone:** depth is the same everywhere, so only H/f is observable,
  which makes metres-per-pixel the correct model. Scale comes from car width
  (1.83 m) at the **25th percentile** of perpendicular box extents: axis-aligned
  boxes only ever inflate width, and the median under-reads height.
- **Oblique:** Depth Pro's focal-length head gives f, and the ground plane comes
  from the depth gradient. Absolute scale never comes from a network. Use
  `plane_normal()` (3×3 covariance), never `np.linalg.svd` on the point cloud,
  which allocates N×N (14 GB at N=42k).
- Noise grows **linearly** with distance (σ_pos = JITTER_FRAC·W·d/h). A reading
  must clear 2σ (`SIGMA_TO_REPORT`), or it is `UNRELIABLE`. Keep three distinct
  states: a number, `0.0 (stopped)`, and `UNRELIABLE`. Never clamp a glitch to 0.
- Recorded video is timed by media position; live streams by wall clock or
  camera clock. Timestamps stay monotonic across loops, and history is cleared
  on restart.
- `yt_dlp` must select `bv*`: plain `best` silently caps at 360p and wrecks the speeds.

## Deployment and UI

- The UI runs **drone mode only**; the user removed the fixed/oblique switch on
  2026-08-19. The oblique path still exists in the code and on the command line.
- `Start Vehicle Monitor.command` sets LIMIT=40, CAMERA "Overhead Cam",
  LAT/LON 30.351778/76.370417 and DRONE=1 (it waits up to 30 s for RTSP, then
  falls back to choosing a source in the page). It warns when the default route
  goes out through the drone's Wi-Fi.
- Speed colours: green below 80% of the limit, yellow up to the limit, red above
  it (`speedcam.py` around line 558).
- A live drone recalibrates every flight; a fixed camera keeps its calibration.
- "Play demo footage" uses `DEFAULT_URL = "data/drone_video.mp4"`, local so it
  works offline. The previous YouTube demo (Su5cUQq8dAY) was taken down
  (2026-10-29). The relative path is what its cached calibration is keyed on.

## Speeding log rules

A record must be earned: state `ok` only, over the limit **continuously for
0.7 s**, and the row keeps the **peak** speed. The table is unique on
`(session_id, vehicle_id)`. Each event stores two images, `<id>_raw.jpg` (the
record, uploaded first) and `<id>.jpg` (cleaned). Crops are cut **before** any
annotation is drawn. Logging never blocks the frame loop.

**Local first, then the cloud.** The drone takes the Wi-Fi, so the internet is a
phone over USB: slow, sometimes gone. Each event is saved to
`output/speedlog.db` (SQLite) with its images in `output/evidence/<session>/`,
and `/events` reads only that file. The sync thread (`start_sync`, started by
`live_pipeline.py` even with logging off) pushes changed rows first, then their
images one event at a time, then pulls the latest 100 cloud rows every 5 s (the
whole table on the first pull) for guard responses and other cameras' events.
On that first pull, synced rows the cloud no longer has are deleted locally.
Guards are only alerted once the cloud has the row, so the page marks unsent rows
"Not in the cloud yet". Unsent rows survive a restart. An event sent hours late
still alerts guards when it lands: the trigger doesn't check its age.

## Guard dispatch (backend in `supabase/`)

On INSERT into `speeding_events` with lat/lon, the trigger
`dispatch_speeding_event` picks the 3 nearest on-duty guards whose position is
under 30 min old. It writes `dispatches`, broadcasts to `guard:<uid>` (median
0.23 s to the device) and calls the `dispatch` edge function through pg_net for
FCM push. `respond_to_event` gives the event to the first responder atomically;
the others see "taken:<name>". A dispatch failure never loses the event.
