# VIGIL Capstone: context for Claude

Final-year capstone at Thapar Institute of Engineering and Technology, Patiala,
by Anmol Mangat (CSE), with Ishita Vaid (ECE) among the co-authors, supervised
by Dr. Sudhanshu Tyagi (ECE). Five parts, built from Jul to Oct 2026 as three
independent systems, then copied into this folder on 2026-10-29. The originals
were left unchanged, and changes made here don't flow back:
`~/Downloads/Drone Based Smart Parking and Logistics`,
`~/Vehicle Monitoring and Speed Estimation` and `~/RFID_LORA_Vehicle_Logging  copy`
(two spaces before "copy"; the folder without "copy" is empty).

Each system folder has its own CLAUDE.md with the detail. Read it before you change that system.

| Folder | Part | Dashboard | Own launcher |
|---|---|---|---|
| `parking/` | Campus parking lot vehicle tracking: drone or pole camera, bay occupancy | "Campus Parking", http://127.0.0.1:8080 | `Launch Parking System.command`, `run.sh` |
| `speed/` | Vehicle speed tracking and estimation from the drone, speeding log | "Kestrel", http://localhost:8000 | `Start Vehicle Monitor.command`, `start.sh` |
| `rfid/` | RFID + LoRa gate logging ("VIGIL"): ESP32 firmware, laptop gateway | "VIGIL — Gate Log", http://localhost:8777 | `Start VIGIL.command`, `start.sh` |
| `mobile/campus_parking/` | Smart Parking app for drivers (part of parking) | Flutter app | — |
| `mobile/guard_app/` | Guard Alert app, overspeed alerts (part of speed) | Flutter app | — |
| `web/` | Combined page: a slider switches between the three dashboards | http://localhost:8070 | `Start VIGIL Capstone.command` |

## Running

`Start VIGIL Capstone.command` serves `web/` on port 8070 and the RFID dashboard
on 8777. It then starts the three systems' own launchers in the background, with
`NO_BROWSER=1` so they don't open their own tabs, and logs each to
`logs/<system>.log`. A `kill 0` trap stops the whole process group. The RFID
launcher exits at once when no ESP32 boards are plugged in. That is expected,
and the gate log still works.

The first tab, **VIGIL**, is `web/home.html`: an Apple-product-page-style guide
in plain language (hero film that opens on scroll, auto-advancing highlights,
Gate / Road / Lot story sections with stats, the two apps, "A day on campus",
a light privacy and consoles zone). Its media in `web/media/` is real output:
- `hero-plain.mp4` / `hero-tagged.mp4`: 2–14 s of `speed/data/drone_video.mp4`,
  plain and through `speedcam.py` (1080p render, frame-aligned, offset 0).
  `road.mp4` is 21–33 s of the same render. The offline runner has no speed
  limit flag, so renders use its fixed 30/40 colour bands (the live console
  uses 32/40); the Road legend follows the render.
- `parking.mp4`: the demo lot with the dashboard's bay overlay (old colours);
  the source is an iStock preview with a watermark, so license it before any
  public use. `drone_video.mp4`'s source is unrecorded; confirm it too.
- `parking-sign.webp`: the dashboard's entrance board (headless Chrome, 2×).
- App screenshots: iPhone 16 Pro simulator, light mode, 2026-10-31, after the
  re-theme. `road-evidence.webp` is the evidence photo from a real demo alert.
- `gate.svg`: an animated sketch, not a recording (the RFID tables are empty).
  Swap in a gate-log screenshot once there are scans.
The paper's circuit diagram shows the receiver's LoRa pins wrong (it should be
GPIO 5, 22 and 21), so it isn't used.

## Design (VIGIL theme)

`REDESIGN_PLAN.md` holds the token set. Since 2026-11-07 the web pages use its
**Thapar palette** (section 2b): a darkish Thapar red (`#B3141C` light, `#D42F35`
dark, from the logo's `#ED2024`) for controls only, warm dark greys instead of
black, beige instead of white in light mode; system colours for data, the Mac's
own SF Pro via `-apple-system` (no web fonts, so pages work offline). The apps
still use the earlier Apple palette (blue `#007AFF`). Every web page carries
its own copy of the tokens, because each dashboard also runs on its own. The
three dashboards and both apps were re-themed only (colours, fonts, shapes);
their layouts are unchanged and were checked by measuring element positions.
Guard Alert follows the phone's light/dark setting (`Palette` in `lib/ui.dart`). The parking
dashboard keeps the Doto LED font for its entrance board.

`web/index.html` shows each dashboard in an iframe. It checks each system every
4 s with a `HEAD` request in `no-cors` mode, loads each iframe on first view and
keeps it alive afterwards. The URL hash (`#parking`, `#speed`, `#rfid`) and
`localStorage['vigil-tab']` remember the selected system.

Ports: 8070 combined page · 8080 parking dashboard · 8081 parking standalone
annotator (`annotate_server.py`) · 8000 speed dashboard · 8777 RFID dashboard ·
8090 `speed/drone_feed.py` (the old BL618 bridge, unused) · 8801/8800 BL618 drone protocol.
Keep new services off these ports.

The parking launchers clean up with `pkill -f`. Their patterns are anchored
(`(^|[ /])pipeline\.py`) so they don't kill speed's `live_pipeline.py`. Keep it
that way when you edit them.

## Shared Supabase project

All parts use one project: `grknwhtsoginetxkmeto` ("anmolol117's Project"),
region ap-northeast-2 (Seoul), Postgres 17. The Supabase MCP is connected; use
it for read-only inspection. Apply migrations only when the user asks.

| Owner | Tables / objects |
|---|---|
| RFID | `vehicles`, `tags`, `vehicle_logs` (+ `vehicle_logs_flag_reread` trigger), `unauthorised_tags`, `unauthorised_events`; views `vehicle_movements`, `vehicle_presence`, `unauthorised_summary` |
| Speed + Guard Alert | `speeding_events`, `guards`, `dispatches`; functions `dispatch_speeding_event` (trigger), `claim_push_batch`, `respond_to_event`, `gc_distance_m`, `gc_bearing_deg`; edge function `dispatch` (FCM push); a Storage bucket for evidence frames; Realtime broadcast topic `guard:<uid>` |
| Parking + Campus Parking app | `parking_lots`, `parking_slots`, `drone_sessions`, `parking_tracks`, `slot_events`, `parking_snapshots`, `slot_status`, `cv_vehicle_log`; view `lot_occupancy` (the status thresholds live in this SQL) |
| Shared auth | `allowed_email_domains` + `is_allowed_email()`: only @thapar.edu accounts can be guards |

Rules:
- **Additive only.** Each system owns its tables. Never alter another system's
  tables, and check the other systems when you touch shared auth, RLS or Realtime.
- RLS is on everywhere. Backend writers use the **service_role** key from their
  `.env`. The apps use the publishable key, with read-only policies.
- Auth users are shared by both apps. Campus Parking sign-up is open to any
  email. Guard access is a row in `public.guards`, which the **user** creates
  with `speed/guards_admin.py`. Never create accounts or enter passwords.
- **Two separate vehicle logs, on purpose:** RFID `vehicle_logs` holds identity
  at the gate; parking `cv_vehicle_log` holds anonymous drone tracks. Plates
  can't be read from the air, and matching the two by timing is guesswork, so
  never link or merge them. The user explicitly wants them kept apart.
- Migration SQL in the repo is incomplete. Parking's migrations live only in the
  database. `rfid/schema.sql` lacks the `unauthorised_*` objects. Only the guard
  migrations are in `speed/supabase/migrations/`. Run `list_migrations` to see
  the real history (20 migrations as of 2026-10-29).
- Rows as of 2026-10-29: `speeding_events` 461, `parking_snapshots` about 16.3k
  (grows without limit; pruning was never built), `parking_slots` 165 (Parking
  A), RFID tables 0, `guards` 1.
- Supabase gotchas: the pooler host is `aws-1-<region>` (`aws-0` fails);
  `sb_secret_...` keys are opaque, so send them in the `apikey` header, not only
  as a Bearer token.

## Secrets: never print, commit or send

`parking/.env`, `speed/.env`, `rfid/gateway/.env` (holds the PSK and service
key), `rfid/transmitter/secrets.h`, `rfid/testing/VIGIL_TX_Test/secrets.h`,
`mobile/campus_parking/env.json`, the Firebase config files in
`mobile/guard_app`, and the `FCM_SERVICE_ACCOUNT` edge-function secret. The RFID
dashboard keeps a service_role key in browser localStorage, so it must stay
local and never be hosted. `.gitignore` covers the files above; this folder is
not a git repo yet.

## Machine

- MacBook Pro M4 Pro, 24 GB unified memory, MPS (no CUDA). The disk is nearly
  full (6.8 GB free on 2026-10-29). Copy big files as APFS clones (`cp -c`).
- Python: `/Users/anmol117/miniconda3/bin/python3` (3.13: torch, ultralytics,
  transformers, cv2). Launchers put it first on PATH. Leave
  `/usr/local/bin/python3.11` alone.
- **The laptop has hard-crashed** from GPU memory exhaustion. Run one heavy
  vision model at a time: Depth Pro takes about 7 GB in fp16. Set
  `PYTORCH_MPS_LOW_WATERMARK_RATIO=0.5` and `PYTORCH_MPS_HIGH_WATERMARK_RATIO=0.6`
  together, free each model before loading the next, and don't run the Android
  emulator or Xcode alongside the ML pipelines. Be cautious with long jobs.
- Flutter is at `/opt/homebrew/bin/flutter`, with JDK 17, the Android SDK and the
  `Pixel_8_API_35` emulator. Xcode 27.0 is installed (2026-10-31), so iOS
  simulator builds work again (`flutter build ios --simulator`).
- Arduino uploads must run at 115200 baud; the CH340 adapters drop at 921600.

## Hardware

- **Gate (RFID):** two ESP32s with SX1276 433 MHz (RA-01/02) radios; the
  transmitter also has an MFRC522 reader. They appear as
  `/dev/cu.usbserial-*`, and the path changes on replug.
- **Speed drone:** KY UFO with a CooingDV camera. RTSP at
  `rtsp://192.168.1.1:7070/webcam`, MJPEG 640x352 at about 22 fps, UDP only.
  It hovers at 30.351778 N, 76.370417 E. The Mac's single Wi-Fi can't be on the
  drone and the internet at once, so the speeding log and guard alerts need a
  USB-C Ethernet adapter or a travel router. The user rejected iPhone tethering
  as unprofessional. Tests on the real drone are offline scripts the user runs.
- **Parking live camera:** REES52 RS2967 analog camera with a 170° fisheye,
  through an "AV TO USB2.0" dongle (720x480 at 25 fps), opened by name. It
  switches off for a few minutes about every 5 minutes to cool down.

## How the user works

- Plans first for big features ("make a plan"), then step by step ("go ahead with step 3").
- UI: minimal and clean, no emojis or filler text. It should look like a
  finished software product, not a demo, with no generic AI-style design. The
  parking dashboard is dark themed.
- Show times in IST. Parking bays are labelled `PARKING-A-###`.
- Every system has a double-click `.command` launcher; "start/stop the system"
  means that launcher.
- **Measured, not estimated:** numbers in the docs come from real runs, and
  papers must never invent results.
- Short, direct answers when asked something quick.
- Research papers (the user edits .tex in Overleaf; change only what is asked
  and return the full .tex):
  - RFID: "VIGIL: A Secure and Reliable RFID–LoRa Architecture for Vehicle
    Identification and Movement Logging", aimed at Wiley *International Journal
    of Communication Systems* (NJD template). Drafts are `~/Downloads/VIGIL_main-*.tex`,
    the latest seen being `VIGIL_main-6.tex` (2026-09-15). Its measured results
    are in `rfid/testing/results/FINDINGS.md`.
  - Speed: "Self-Calibrating Vehicle Speed Estimation from Overhead UAV Video",
    a conference paper of at most 6 pages on the drone method only, at
    `speed/paper/main.tex`. Paused until ground-truth experiments exist.

## Where the deep docs are

- `parking/README.md`: pipeline, camera, calibration, tables. `parking/APP_PLAN.md`: Campus Parking app plan and status levels.
- `speed/README.md` (measured results), `speed/METHODOLOGY.md` (every design
  decision, including the failures), `speed/docs/*.pdf`, `speed/output/bench/*.md`.
- `rfid/README.md` (protocol, wiring, design), `rfid/testing/README.md`,
  `rfid/testing/results/FINDINGS.md`, `rfid/VIGIL_Methodology.pdf`.
- `mobile/guard_app/README.md`: alert flow and Firebase setup.
