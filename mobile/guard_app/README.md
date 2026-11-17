# Guard Alert

Flutter app (Android + iOS) that alerts the nearest on-duty guards when the
drone pipeline logs a speeding vehicle.

```
lib/main.dart     start-up, auth gate
lib/backend.dart  alerts, duty toggle, location sharing, live updates
lib/push.dart     push notifications + in-app alert sound
lib/home.dart     duty card, active and earlier alerts
lib/alert.dart    alert detail: Responding / Navigate / Can't respond
lib/login.dart    email + password sign-in
lib/ui.dart       colours and formatting helpers
```

Backend: `../../speed/supabase/` (database migration and the `dispatch` edge function).
Guard accounts: `python3 ../../speed/guards_admin.py add --email x@thapar.edu --name "..."`.

## How an alert reaches a guard

1. The drone pipeline inserts a row into `speeding_events` with the drone's coordinates.
2. A database trigger picks the 3 nearest on-duty guards and writes `dispatches`.
3. Apps that are open get the alert at once: the trigger broadcasts to the guard's private
   Realtime channel (`guard:<id>`), and the app plays a sound and opens the alert screen.
   Measured from dispatch to device: median 0.23 s (0.14–1.26 s). A table-change
   subscription is kept as a fallback; it was slower (median 1.1 s, up to 3.3 s).
4. The `dispatch` edge function sends a push to the same guards for closed apps.
5. The first guard to tap **Responding** claims it; the others see "Taken by …".

Without Firebase configured the app still works, but only while it is open.

## Run

```bash
flutter pub get
flutter run            # pick a connected device or emulator
flutter test
```

iOS builds need an Xcode version that supports the installed macOS. Xcode 16.2
cannot compile asset catalogs on macOS 27.

Swift Package Manager is switched off in `pubspec.yaml`, because Firebase's
Swift package needs Xcode 16.3 or later; CocoaPods is used instead.

## Push notifications (one-time setup)

1. In the [Firebase console](https://console.firebase.google.com), create a
   project (Analytics is not needed).
2. Add an **Android** app with package `edu.thapar.guard_app`. Download
   `google-services.json` into `android/app/`.
3. Add an **iOS** app with bundle ID `edu.thapar.guardApp`. Download
   `GoogleService-Info.plist` into `ios/Runner/`.
4. Go to Project settings → Service accounts → Generate new private key. Paste the
   whole JSON into the Supabase dashboard → Edge Functions → Secrets as
   `FCM_SERVICE_ACCOUNT`. Never commit it or share it.
5. iOS only: create an APNs key in the Apple Developer account (paid) and upload
   it under Firebase → Project settings → Cloud Messaging.

The Android Gradle plugin for Google services is wired in once
`google-services.json` exists; the build fails without the file.
