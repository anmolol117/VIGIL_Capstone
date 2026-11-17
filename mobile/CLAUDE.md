# Mobile apps (Flutter, iOS + Android)

Two separate apps, not part of the web page. Both use the shared Supabase
project with the publishable key and RLS, and share the same auth users. As of
2026-10-29 both pass `flutter analyze` and `flutter test` from this folder. The
combined copy left out the build caches; the first `flutter run` rebuilds them
(and `pod install` on iOS).

## campus_parking: Smart Parking app (goes with `../parking/`)

- Run: `flutter run --dart-define-from-file=env.json` (keys `SUPABASE_URL`,
  `SUPABASE_PUBLISHABLE_KEY`; template in `env.example.json`). Without the
  define, the app shows a screen explaining what's missing.
- One dependency, `supabase_flutter`. Package `com.campusparking.campus_parking`.
- `lots_screen.dart` reads the `lot_occupancy` view. A Realtime insert on
  `parking_snapshots` is only a signal to refetch. It also refreshes on a 30 s
  tick, on pull and on resume. `lot_layout_screen.dart` and `lot_painter.dart`
  draw the bay map from `parking_slots` (`map_polygon` when a ground
  calibration exists) and `slot_status`. `lot.dart` holds the model and the
  status → label/colour mapping, tested in `test/lot_test.dart`.
- Design: the VIGIL theme (`../REDESIGN_PLAN.md`), re-themed 2026-10-31. iOS
  grouped greys (`#F2F2F7` / black), system blue `#007AFF` / `#0A84FF` as the one
  accent, free green `#30D158`, an asphalt bay map `#2C2C2E` with white stall
  lines, and taken bays drawn as top-down cars. Status colours in `lot.dart`
  match the parking dashboard's levels. Lives in `lib/theme.dart`.
- Sign-up is open to any email; `sign_in_screen.dart` shows plain-English errors.
  History charts and notifications are deliberately left out.
- Forgot password (both apps, added 2026-11-17): `reset_password_screen.dart`
  here, `reset_password.dart` in guard_app. The user asks for a code, then types
  the code and a new password (`verifyOTP` type recovery, then `updateUser`). A
  code, not a link, so it works wherever the email is read and needs no deep
  link. It only works once the Supabase **Reset Password** email template shows
  `{{ .Token }}`; the default template has only a link. Supabase's built-in
  mailer only delivers to the project team's addresses, so real users need
  custom SMTP.

## guard_app: Guard Alert (goes with `../speed/`)

- Run: `flutter pub get && flutter run`. The Supabase URL and publishable key are
  in `lib/main.dart`. Bundle `edu.thapar.guardApp` / package `edu.thapar.guard_app`.
- Firebase is configured (`ios/Runner/GoogleService-Info.plist`,
  `android/app/google-services.json`); the Android build fails without the
  json. iOS uses CocoaPods, because Swift Package Manager is off in `pubspec.yaml`.
- Flow: a guard creates a login in the app (@thapar.edu only, checked by
  `isCampusEmail` in `login.dart`) or the admin creates one, then signs in.
  Alerts need a `guards` row, which the user adds with `speed/guards_admin.py add`
  (it reuses an existing login); until then the app shows "Not a guard account".
  The guard toggles on duty. Position is shared while on duty
  (geolocator). Alerts arrive over the private Realtime broadcast `guard:<uid>`,
  with `postgres_changes` as a fallback and duplicates ignored, plus FCM push
  for closed apps (Android only: iOS push needs a paid Apple Developer account,
  an APNs key in Firebase and the Push capability, none of which exist. On iOS
  an on-duty guard stays reachable while the app is backgrounded, because
  background location keeps it running, but not after a force-quit). `respond_to_event` claims the event; the others see "Taken by …".
  The app writes `delivered_at`/`opened_at`, so latency is measured.
- Design: the VIGIL theme (`lib/ui.dart`): iOS greys, blue
  `#007AFF` for everyday actions, red `#D70015` only for urgent things (speeds,
  on duty, the RESPONDING button, which sets its red in `alert.dart`). Dark mode
  (added 2026-10-31) follows the phone's setting with the iOS dark system
  colours (`#000` / `#1C1C1E` cards, red `#FF453A`, blue `#0A84FF`). Screens
  read colours with `Palette.of(context)`, never a constant, so both modes work.
- In the simulator, set a location first
  (`xcrun simctl location <udid> set 30.351778,76.370417`), or the app shows
  "Location stopped".

## Toolchain

Flutter (Homebrew), CocoaPods, JDK 17, Android SDK 35 and the `Pixel_8_API_35`
emulator. Xcode 27.0 (installed by 2026-10-31) builds both apps for the iOS
simulator: `flutter build ios --simulator --debug` (Campus Parking also needs
`--dart-define-from-file=env.json`). Don't run an emulator or Xcode build while
the ML pipelines are running (24 GB machine).
