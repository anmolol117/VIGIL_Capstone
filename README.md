# VIGIL Capstone

One final project made of five parts, built as independent systems. They share
one Supabase project and are otherwise separate: each keeps its own code,
launcher and README.

| Folder | Part | Web page |
|---|---|---|
| `parking/` | Campus parking lot vehicle tracking (drone video, bay occupancy) | http://127.0.0.1:8080 |
| `speed/` | Vehicle speed tracking and speed estimation | http://localhost:8000 |
| `rfid/` | RFID + LoRa vehicle logging at the gate (ESP32 firmware, gateway) | http://localhost:8777 |
| `mobile/campus_parking/` | Smart Parking app, for drivers (goes with `parking/`) | phone app |
| `mobile/guard_app/` | Guard Alert app, overspeed alerts for guards (goes with `speed/`) | phone app |
| `web/` | The combined page: a slider with a VIGIL guide page (`home.html`) and the three systems | http://localhost:8070 |

## Run

Double-click **Start VIGIL Capstone.command**. It starts all three systems and
opens http://localhost:8070. The slider at the top switches between Parking,
Speed and RFID gate (arrow keys work too). A green dot means that system is
answering. A stopped system shows how to start it, and the page picks it up
again on its own.

- Each system's output goes to `logs/<system>.log`.
- Speed waits up to 30 s for the drone's camera. Without it, choose a source in its page.
- The RFID gate log works without hardware. The gateway that writes new entries
  starts only when both ESP32 boards are plugged in.
- Closing the Terminal window stops everything.

Each system still runs on its own with its launcher, for example
`speed/Start Vehicle Monitor.command`. Settings such as the speed limit live in
those launchers.

## Mobile apps

The two Flutter apps are separate from the web page. Run them from their folders:
`mobile/campus_parking/README.md` and `mobile/guard_app/README.md` have the details.

## Database

All parts use the same Supabase project. The schema lives with the part that
owns it: `rfid/schema.sql`, `speed/supabase/` (guard dispatch migration and the
`dispatch` edge function) and `parking/README.md` (parking tables). Changing a
shared table, sign-in or access rule can affect the other parts.

## Where this came from

Copied on 2026-10-29 from three folders, which were left unchanged:
`~/Downloads/Drone Based Smart Parking and Logistics`,
`~/Vehicle Monitoring and Speed Estimation` and `~/RFID_LORA_Vehicle_Logging  copy`.
Changes made while combining them:

- The parking launchers no longer stop the speed system's `live_pipeline.py`
  when they clear old parking processes.
- The three launchers skip opening their own browser tab when the combined
  launcher starts them.
- Flutter build caches were left out. They rebuild on the next `flutter run`.
