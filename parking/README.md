# Drone-Based Parking Occupancy

Tracks how many vehicles are in each campus parking lot, and which specific bays
are free, from live aerial drone video. Detection is VisDrone YOLO, tracking is
ByteTrack, and state lands in the same Supabase project as the RFID gate system.

## Why it registers frames first

A hovering drone drifts - GPS wander, wind, yaw, altitude breathing - by metres
over a minute. Two things break if you ignore that:

- bay polygons drawn on frame 1 point at the wrong asphalt by frame 900
- ByteTrack's constant-velocity model reads global camera motion as every
  vehicle accelerating at once, and ID switches spike

So the pipeline is **register -> detect -> warp -> track -> count**. Each frame is
mapped onto the lot's fixed reference image before tracking. In that stabilized
frame the camera is effectively static, and a parked car is genuinely motionless -
which is what turns "is it parked?" into a displacement test instead of a guess.

## Setup

```bash
pip install ultralytics supervision trackers supabase numpy pyyaml
export SUPABASE_URL=https://<project>.supabase.co
export SUPABASE_SERVICE_KEY=<service_role key>
```

The **service_role** key is required: RLS is enabled on every table with no
policies, matching the rest of this project. The anon key can write nothing.

## 1. Annotate each lot, once

Fly to the waypoint the drone will hold, then:

```bash
python annotate.py --source rtsp://drone-1/live --lot-code LOT-A --lot-name "North Lot"
```

Click the lot boundary, then each bay's 4 corners, then two points a known
distance apart to calibrate metres-per-pixel. Writes `lots/LOT-A.{jpg,json}` and
pushes the lot to Supabase.

For marking many bays it is far quicker to use the **browser annotator**, which
draws drag-rectangles with zoom, undo, and live editing instead of clicking four
corners each:

```bash
python annotate_server.py --source istock.mp4 --lot-code ISTOCK --lot-name "Stock lot"
# open the printed URL, drag boxes, set scale (📏), Save
```

It preloads an existing `lots/<code>.json` as an editable starting point and, on
Save, writes the same file the pipeline reads (add credentials or the `--no-db`
default to control the Supabase push).

That reference image becomes the lot's permanent coordinate system. Capture it in
clear light at the exact hover waypoint. Re-annotating an existing lot needs
`--replace`, because deleting bays cascades to their event history.

**Bays must not overlap.** Two polygons covering the same asphalt means one of
them can never be claimed, and it will sit free forever.

## 2. Run

```bash
python pipeline.py                                   # live, writes to Supabase
python pipeline.py --dry-run --show                  # tune, writes nothing
```

Tune against recorded footage before trusting a live flight. A worked example is
checked in - `lots/TEST.json` was generated from parked cars in `drone_video.mp4`:

```bash
python pipeline.py --dry-run --lot-file lots/TEST.json --source drone_video.mp4
```

Watch the closing line. Registration stale on more than ~20% of frames means the
reference image is poor - glare, low texture, or too much altitude change - and
should be recaptured before the occupancy numbers mean anything.

## Live analog camera

The drone's analog camera reaches the Mac through a USB capture dongle that
macOS lists as **"AV TO USB2.0"** (720x480 at 25 fps). `config.yaml` points at it
with `source: "camera:AV TO USB2.0"`. It is opened by name, because device
numbers shift when an iPhone or other camera appears.

Double-click **Launch Parking System.command** (or `./run.sh`). The dashboard
opens at :8080. In its header:

- **Recorded / Live camera** switches the source. Recorded loops the demo clip
  and replays its occupancy into Parking A for the app. Live camera runs the
  pipeline on the camera and writes to the camera's own lot (`CAMERA-1`), so
  switching never overwrites either one. Only the selected source runs, and the
  choice is remembered between launches.
- **Calibrate** sets up the camera. Live counting pauses while it's open. It
  grabs a clear frame from the camera (**Retake frame** for a new one; black
  frames are skipped), and you set the lot name, mark the bays and the ground
  rectangle, then Save. **Done** returns to the dashboard and restarts live
  counting on the new calibration. Tick **Replace saved bays** when
  recalibrating: it replaces the old bays and clears their event history. The
  vehicle log is kept.

The first time it reads the camera, macOS asks whether Terminal may use it.
Click Allow.

### Mounting on a wall or pole (angled view)

From an angle, bays are slanted shapes, and a metre near the camera spans many
more pixels than a metre at the back. Marking handles both:

1. **Bay:** drag a box over each bay, then drag its four corners onto the
   painted lines. **D** copies the selected bay to the next one along the row.
2. **Ground:** click the four corners of one real rectangle on the ground, in
   order around it. A bay's painted corners work well. Then type its width
   (corner 1→2) and length (corner 2→3) in metres.
3. Tick **replace on server** if the lot already exists, then **Save**.

The ground rectangle maps the image to a true top-down plane:

- Movement and distance thresholds are measured in real metres everywhere.
- The app's bay map shows a proper overhead layout, not the camera's view.
- `occupancy.anchor: 0.3` places each car at its footprint rather than its
  roof, which hangs over the bay behind.

Frame alignment stays on and corrects pole sway. Mount the camera as high as
you can, because a car parked in front hides the bays behind it.

If the bays in `lots/<code>.json` differ from those in the database (for
example, Save without "replace" was refused), the pipeline stops and says so.
It won't count against the wrong layout.

Every frame is cleaned up by ffmpeg before anything uses it. The steps, all
listed in `camera.filters` in `config.yaml`:

- analog line filters
- denoising
- **lens correction** for the 170-degree fisheye. It's derived from the
  camera's spec sheet (REES52 RS2967: 1.8 mm, 3.5964 x 2.7084 mm sensor) and
  checked on its own footage, where straight edges now bend 55% less.
- sharpening
- a highlight-dimming tone curve

Changing the lens correction changes the picture's geometry, so recalibrate
afterwards.

**The camera is switched off for a few minutes every ~5 minutes to cool down,
so the feed goes black.** The pipeline handles this as follows:

- A black frame means the camera is off, not that the lot is empty.
  `camera.black_level` sets the cut-off.
- While the feed is black, every bay holds its last state and nothing is written.
- If a gap lasts over 2 minutes, the app shows the lot as "No live data".
- When the picture returns, bays stay held for one parking window (~13 s) while
  cars are re-tracked, so nothing reads as a departure.
- If the dongle drops out, it is reopened automatically.
- The dashboard shows "Camera off · holding last known bays" during a gap.

## Dashboard (looping-video demo)

A local dashboard shows the video on a loop with each bay drawn free/occupied,
lot occupancy stats, and the CV vehicle log read from the database.

```bash
python dashboard_precompute.py     # run the pipeline once -> occupancy_timeline.json + data/cv_vehicle_log.json
python dashboard_server.py         # open the printed URL (default :8080)
```

Occupancy is precomputed rather than inferred live in the browser, so the overlay
stays exactly in step with the looping feed and no model runs at view time. The
vehicle-log panel shows the drone system's own record — the `cv_vehicle_log`
table: one row per observed parking stay (tracked vehicle, type, tracking id,
parked&rarr;departed timing). It reads live from Supabase when `SUPABASE_URL` +
`SUPABASE_SERVICE_KEY` are set, otherwise from `data/cv_vehicle_log.json`.
This is **separate from** the RFID `vehicle_logs` gate system. Occupancy levels
are very low / low / medium / high / full by fraction of capacity filled.

The feed autoplays (muted) in a normal browser; clicking it toggles play/pause.

## What gets written

Never per frame. 30 fps across several lots is millions of rows a day nobody reads.

| table | granularity |
|---|---|
| `slot_events` | one row per bay transition, with dwell time and an evidence crop |
| `parking_snapshots` | one row per lot per ~20 s - the time series |
| `parking_tracks` | one row per track per session, upserted |
| `slot_status` | current state per bay, updated in place - for the dashboard |

Failed writes spill to `.write_queue.jsonl` and drain on reconnect, so a campus
wifi drop delays data rather than losing it.

## Deliberate limitation

Plates are unreadable at drone altitude, so **tracks are anonymous** and carry no
foreign key to `public.vehicles`. Identity lives in the RFID gate tables. Matching
the two by timing is guesswork as soon as two cars enter together, so it is left
out rather than allowed to corrupt occupancy. Aggregate comparisons (gate entries
vs. lot fill rate) still work and stay honest.

## Tests

```bash
python test_pipeline.py
```

Covers the parts that fail quietly: homography round-trip, registration against a
known translation, bay assignment at the painted line, the debounce state machine,
and the tracker's -1 sentinel for unconfirmed tracks.
