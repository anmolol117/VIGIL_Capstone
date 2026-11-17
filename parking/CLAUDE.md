# Parking: campus parking lot vehicle tracking

Counts vehicles and free bays per lot from drone video or a fixed angled camera.
Read `README.md` for the full description; `APP_PLAN.md` covers the
Campus Parking app (in `../mobile/campus_parking/`).

## Pipeline: register → detect → warp → track → count

| Module | Role |
|---|---|
| `stabilize.py` | `Stabilizer`: ORB + RANSAC homography of each frame onto the lot's reference image (vehicles masked out). Keeps the last good H when a frame can't be registered and flags `registration_ok=false` |
| `pipeline.py` | Main loop (514 lines): detection with VisDrone YOLO (`visDrone.pt`, conf 0.10, imgsz 960, frame_skip 2), boxes warped into the reference frame, ByteTrack (`trackers` lib, `-1` = unconfirmed track), `LivePublisher` writes the frame and bay state for the dashboard |
| `occupancy.py` | `OccupancyTracker`: a vehicle is "parked" by a displacement test in the stabilized frame; bays commit only after K consistent samples (debounce); `ground_homography` maps an angled view to a top-down plane at 20 px/m |
| `camera.py` | `"camera:<device name>"` sources opened by name; ffmpeg clean-up filters from `config.yaml`; `is_blank` detects the camera-off black frames |
| `db.py` | Supabase client, `Writer` (bulk writes, spills to `.write_queue.jsonl` and drains on reconnect), evidence uploads |
| `dashboard_server.py` | Serves `dashboard.html` on 8080; `Supervisor` runs only the worker the current mode needs; `/export?range=` (day, week, month, all) zips `occupancy.csv` (`parking_snapshots`) and `vehicles.csv` (`cv_vehicle_log`) for all lots, IST, behind the header's Download menu |
| `dashboard_precompute.py` | Runs the pipeline over the demo clip once, producing `occupancy_timeline.json` and `data/cv_vehicle_log.json` |
| `db_feeder.py` | Replays the timeline into `parking_snapshots`/`slot_status` for lot `PARKING-A`, looping, with a 60 s heartbeat |
| `annotate.py`, `annotate_server.py` | Bay marking: an OpenCV clicker, and the browser annotator (port 8081) that the dashboard's Calibrate also uses |
| `test_pipeline.py` | `python3 test_pipeline.py`: homography round trip, registration, debounce, dropout, hold through camera outage, ground plane in metres |

All tuning lives in `config.yaml`: detection, tracking thresholds,
registration, `occupancy.anchor` (0.3 for angled cameras, 0.5 for top-down) and
the camera filter chain.

## Modes (dashboard header switch, remembered in `.mode`)

- **Recorded:** loops the demo clip
  `~/Downloads/istockphoto-1359195980-640_adpp_is.mp4` (an absolute path in
  `dashboard_server.py`, `dashboard_precompute.py` and `run.sh`) and replays its
  occupancy into `PARKING-A` (165 bays, from `lots/ISTOCK.json`).
- **Live camera:** runs `pipeline.py` on the analog camera into lot `CAMERA-1`,
  which isn't calibrated in this copy. **Calibrate** grabs a clear frame. You mark
  bays as drag boxes, then drag their 4 corners onto the paint, then mark one
  ground rectangle with real width and length in metres. "Replace saved bays"
  cascades to event history. The pipeline refuses to run if the local
  `lots/<code>.json` and the database disagree.
- Lots on disk: `lots/ISTOCK.*` (demo), `lots/TEST.*` (made from `drone_video.mp4`).
  A lot's reference image defines its pixel coordinate system permanently.

## Camera-off handling (the analog camera cools down every ~5 min)

A black frame means the camera is off, not an empty lot: bays hold their last
state and nothing is written. After 2 min the app shows "No live data". When
the picture returns, bays stay held for one parking window (~13 s) while tracks
re-form, so nothing reads as a departure. A dropped dongle is reopened automatically.

## Database writes (never per frame)

`slot_events` (debounced transitions with dwell and an evidence crop),
`parking_snapshots` (every ~20 s and immediately on any change),
`parking_tracks`, `slot_status` (current state, updated in place; reset to free
at session start), `cv_vehicle_log` (one row per parked→departed stay). Tracks
are anonymous with no link to RFID `vehicles`, deliberately.

`lot_occupancy` status levels (app-facing): Wide open 0–29% · Plenty of space
30–54 · Filling up 55–79 · Almost full 80–99 · Full 100 · No live data (> 2 min
old). The web dashboard still uses older names (very low … full); aligning them
is a known follow-up.

## Gotchas

- Bays must not overlap; a bay covered twice can never be claimed.
- `drone_video.mp4` and `visDrone.pt` are symlinks into
  `~/Downloads/Vehicle Location & Speed Tracking using drone footage/`.
- Changing the lens-correction filter changes the geometry, so recalibrate afterwards.
- `occupancy_timeline.json` must exist for recorded mode; the launcher builds it on first run (~1 min).
