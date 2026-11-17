# Campus Parking app — plan (Flutter, iOS + Android)

## Context

Objective 2: a simple mobile app where campus users sign in and see every parking lot's
live status — vehicles present, total capacity, and a five-level status — updating from
the database as the camera system writes to it. One lot today (Parking A, 165 bays),
several later.

What the database looks like right now (checked):

| Thing the app needs | State today |
|---|---|
| Lot + bay records (`parking_lots`, `parking_slots`) | **empty** — the lot was never pushed |
| Live occupancy (`parking_snapshots`, `slot_status`) | **empty** — the pipeline has only ever run in dry-run mode |
| Read access for signed-in users (RLS policies) | **none** — every table is locked |
| Realtime on any table | **none** |
| Users | none |

So this is three pieces of work, not one: a small **database layer**, a **live data feed**
into it, and the **Flutter app** on top.

Decisions locked with you: Campus Parking is **open to any email** (the @thapar.edu rule
applies to the Guard Alert app, which shares this Supabase project); install the Android
toolchain and **verify on both** iOS and Android simulators.

## Architecture

```
 camera system                       Supabase                           Flutter app
 ─────────────                       ────────                           ───────────
 pipeline.py (live)  ─┐                                                 sign in (@thapar.edu)
                      ├─► parking_snapshots ──► view lot_occupancy ──►  lots list
 db_feeder.py (demo) ─┘   one row per update     latest row per lot      live updates
                              │                   + capacity + status        ▲
                              └── Realtime INSERT event ─────────────────────┘ (triggers refetch)
```

- The app reads one view, `lot_occupancy`: one row per lot with name, occupied,
  capacity, free, % full, **status**, last-updated.
- **The status thresholds live in the SQL view**, so the app, the web dashboard and any
  future client always agree; changing a threshold is one SQL edit, no app release.
- Realtime is used as a *signal*: on any new snapshot the app refetches the view (one tiny
  query). Simpler than patching state from raw rows, and it self-heals missed events.

## Status levels (driver-facing names)

Status is purely a **percentage of each lot's own capacity**, so the same rules work for
a 10-bay lot and a 2000-bay lot. `pct_full` is rounded *down* and the status is derived
from that same number, so the % shown and the label can never disagree ("100%" only ever
means no free bay).

| Status | % full | Colour |
|---|---|---|
| Wide open | 0–29% | green |
| Plenty of space | 30–54% | teal |
| Filling up | 55–79% | amber |
| Almost full | 80–99% | orange |
| Full | 100% | red |
| No live data | last update older than 2 min | grey |

Verified across lot sizes 10, 165, 200 and 1000. Parking A at 75/165 (45%) shows
**Plenty of space**. The grey state matters: if the
camera system stops, the app says so instead of showing a stale number as if it were live.

## Phase 1 — Database (Supabase migrations)

1. **Seed the lot.** Insert `parking_lots` (code `PARKING-A`, name "Parking A" — replacing
   the demo name "Stock lot" so it matches the `PARKING-A-###` bay labels) and the 165
   hand-marked bays from `lots/ISTOCK.json`.
2. **View `lot_occupancy`** with `security_invoker = on` (so RLS applies to whoever queries
   it — avoids Supabase's "security definer view" hole). Joins each active lot to its
   latest snapshot via the existing `(lot_id, captured_at desc)` index; capacity comes from
   the snapshot (`occupied + free`), so the app never needs bay geometry.
3. **Read-only RLS.** `SELECT` for `authenticated` on `parking_lots` and `parking_snapshots`
   only. Everything else stays locked: bay geometry, `cv_vehicle_log`, and the separate RFID
   tables (owner names, plates) are invisible to the app. No write policies exist, so the
   app cannot change anything.
4. **Realtime:** add `parking_snapshots` to the `supabase_realtime` publication.
5. **Campus-email rule, guards only.** Auth users are shared with Guard Alert, so the
   @thapar.edu rule (`allowed_email_domains` + `is_allowed_email`) is enforced where guard
   access is granted: a trigger on `public.guards` (only campus accounts can be guards) and
   one on `auth.users` email changes (a guard can't switch to a non-campus email). Campus
   Parking sign-up is open to any email; a parking account gets no guard access.
6. Run Supabase's security advisor afterwards; fix anything new.

## Phase 2 — Live data feed

- **Production (no new code):** `pipeline.py` already writes a snapshot every 20 s. Two
  small changes: write a snapshot *immediately* whenever a bay changes state (app updates
  in ~1–2 s instead of up to 20 s), and point `config.yaml`'s `lot_code` at `PARKING-A`.
- **Demo (`db_feeder.py`, ~60 lines):** our "camera" is the looping video, so this replays
  `occupancy_timeline.json` into `parking_snapshots` in real time, looping like the
  dashboard — a row on every occupancy change plus a 60 s heartbeat (keeps the app out of
  the grey state). Reuses `db.connect`, `db.start_session`, `db.Writer` from `db.py`.
- **Credentials:** the feeder writes, so it needs the **service_role key** — a secret.
  You paste it into a local `.env` (I'll create `.env.example`); it never goes in the app.
- **Launcher:** `Launch Parking System.command` and `run.sh` start the feeder alongside the
  dashboard when `.env` exists, and warn (instead of failing) when it doesn't.

## Phase 3 — Flutter app (`mobile/`, package `campus_parking`)

- **One dependency:** `supabase_flutter` — auth with persisted sessions, queries,
  realtime. No state-management or routing packages; two screens don't need them.
- **Config:** project URL + **publishable (anon) key** via
  `--dart-define-from-file=env.json` (`env.example.json` committed). The publishable key is
  designed to ship inside client apps — RLS is what protects the data.

| File | Purpose |
|---|---|
| `lib/main.dart` | init Supabase, light/dark theme, auth gate (signed in → lots, else sign-in) |
| `lib/sign_in_screen.dart` | email + password, sign in / create account toggle, @thapar.edu check, plain-English errors (wrong password, email not confirmed, domain not allowed) |
| `lib/lots_screen.dart` | lot cards, pull-to-refresh, realtime subscription, 30 s refresh tick, refresh on app resume, sign-out |
| `lib/lot.dart` | `Lot` model from the view row + status → label/colour |
| `test/lot_test.dart` | status mapping and parsing (the logic that can be quietly wrong) |

**Lot card:** name · large `76 / 165` · "89 spaces free" · bar coloured by status · status
chip · "Updated 12 s ago" (or the grey "No live data"). Empty state when no lots exist;
error banner that keeps the last good numbers on screen if the network drops.

**Platform setup:** Android `INTERNET` permission in the main manifest (release builds
need it). No deep links required — email/password doesn't use them.

## Phase 4 — Toolchain install (approved; Homebrew)

| Install | Why | Approx. size |
|---|---|---|
| Flutter SDK (`--cask flutter`) | the framework | 2.5 GB |
| CocoaPods | iOS plugin dependencies | small |
| JDK 17 (Temurin) | Android Gradle builds | 0.3 GB |
| Android command-line tools → SDK platform 35, build-tools, platform-tools, emulator, arm64 system image | build + run Android | 6–8 GB |
| Emulator `Pixel_8_API_35` | Android test device | (sparse) |

About 10–12 GB of your 44 GB free. **Accepting the Android SDK licences needs your explicit
OK** when I reach that step (or you run `flutter doctor --android-licenses` yourself).
Finish line: `flutter doctor` green for iOS and Android.

## What only you can do

1. **Paste the service_role key into `.env`** (Supabase Dashboard → Project Settings → API).
2. **Email confirmation (important).** Supabase's built-in email sender only delivers to
   members of your Supabase organisation, so real @thapar.edu students won't receive
   confirmation emails until you add custom SMTP (Dashboard → Authentication → SMTP).
   Without confirmation, the domain check proves the address *string*, not that the person
   owns it. For first testing: keep confirmation on and add your thapar.edu address as an
   org member, or temporarily turn confirmation off.
3. **Create your own account** in the app when I hand you the simulator — I don't create
   accounts or enter passwords on your behalf.
4. **OK the Android licence acceptance.**

## Verification

- **Database:** query the view as the `authenticated` role → returns Parking A; as `anon`
  → returns nothing; `is_allowed_email('x@gmail.com')` false, `…@thapar.edu` true;
  security advisor clean.
- **Feeder:** run 2 min → snapshots arrive; latest occupied moves 75 → 78 in step with the
  clip; heartbeat keeps `updated_at` fresh.
- **App:** `flutter analyze` clean; `flutter test` passes.
- **iOS simulator:** build, launch, screenshot the sign-in screen → you sign in → screenshot
  the lot card → with the feeder running, a second screenshot shows the count changed with
  no manual refresh (proves realtime). A @gmail.com sign-up shows the friendly rejection.
- **Android emulator:** same flow.

## Added after v1: live bay map

Tapping a lot opens a map built from the real, hand-marked bay geometry — every
bay where it actually is, green free / red occupied, pinch to zoom, tap a bay
for its number. Bay states come from `slot_status` (writers update it before the
lot snapshot, so the app refreshes on the snapshot event and sees bays already
current). Signed-in users can read active bays and four `slot_status` columns
only (`slot_id, is_occupied, since, updated_at`). `pipeline.py` resets every bay
to free at session start so no bay stays red from an earlier run.

## Design

The lot as the drone sees it. Concrete-grey ground (`#EEF0F2`), an asphalt map
(`#3A3F47`) with white painted stall lines, road-marking yellow (`#F2B91D`) as the
one accent (buttons, selected bay), and bay-free green (`#2FCB7D`). On the map,
taken bays show a top-down car parked nose-in. Free bays are lit with a green
lamp, so free and taken differ by shape as well as colour. Stall lines are drawn
as real ones are, open on the lane side. A wide lot is turned a quarter on a
tall phone when that shows it bigger. The lot cards use the same asphalt strip
with stall ticks for "% full", and the sign-in screen shows a small stretch of
lot drawn by the same painter. Colours and the theme live in `lib/theme.dart`;
the map painter is in `lib/lot_painter.dart`.

## Deliberately left out (easy follow-ups)

Password reset (needs deep links), a per-lot detail screen with an occupancy-history
chart (the data already exists in `parking_snapshots`), favourites, "notify me when a lot
frees up", maps/directions, app-store release (signing, icons, privacy policy), pruning
old snapshots, and aligning the web dashboard's status names with the app's.
