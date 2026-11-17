# RFID: VIGIL gate logging over LoRa

An ESP32 gate transmitter (MFRC522 + SX1276) sends encrypted scans over LoRa to
an ESP32 receiver, which relays them over USB serial to `gateway/gateway.py` on
the laptop. The gateway decrypts, commits to Supabase and sends back an ACK.
The success light means "committed to Postgres". `README.md` is the full
design; read it before touching the protocol.

## Invariants (security and correctness depend on these)

- AES-256-GCM, end to end from the ESP32 to the laptop. The receiver holds **no keys**.
- Frame: `version | type | device_id[4] | counter[8] | ciphertext | tag[16]`.
  The 14-byte header is the AAD. Nonce = type | device_id | counter's low 7
  bytes. The type byte keeps DATA and ACK nonces apart.
- The counter is a high-water mark in NVS that jumps +1000 on every boot, so a
  nonce is never reused. Retries reuse the same counter, and
  `unique (device_id, counter)` turns a duplicate into a 409, which becomes ACK `0x01`.
- An ACK counts only if its tag verifies **and** the echoed counter matches.
  Statuses: `0x00` committed, `0x01` already committed, `0x02` DB error (retry),
  `0x03` committed but unregistered card, `0x04` not logged (enroll mode).
- Stop-and-wait: 3 tries × 3 s, then a 30 s back-off while card reading
  continues. The queue holds 8 scans in RAM. Same-card debounce is 5 s
  (`UID_DEBOUNCE_MS`).
- `test_crypto.py` has a known-answer vector duplicated byte for byte in
  `transmitter.ino` `selfTest()`, which halts the board on mismatch. Change both
  together or not at all.

## Direction and vehicles

- **Two cards per vehicle** (entry and exit): `tags.direction` makes direction
  data. Deriving it from scan parity, with a weekly resync, was tried and
  rejected because one missed scan corrupts the card's direction forever. Don't
  reintroduce inference. A wrong tap is one wrong row, fixed with one UPDATE.
- The same card within 60 s gets `is_reread` (a trigger with a constant at the
  top of `vehicle_logs_flag_reread()`). Known misfire: scans drained from the
  queue after an outage get flagged, because frames carry no time.
- **Attendant tally cards** (a known unauthorised vehicle, counted by the guard)
  are separate from **unregistered cards** (unknown UID, a security anomaly).
  Tally cards: `unauthorised_tags` → `unauthorised_events`, with
  `unauthorised_summary` clamped at 0. Unknown cards go to `vehicle_logs` with
  `vehicle_id` and `direction` null. Keep the two apart, including in the paper's wording.
- Register a vehicle: create the `vehicles` row, run `python gateway.py --enroll`
  to read each card's UID, then add two `tags` rows (`in` and `out`).
  `vehicles.active` is not enforced; this system logs, it doesn't control access.

## Hardware

Shared VSPI bus: SCK 18, MISO 19, MOSI 23. Transmitter: LoRa NSS 4, RST 14,
DIO0 26, RFID SS 5, RST 22, LED 2. Receiver: LoRa NSS 5, RST 22, DIO0 21. Radio:
433 MHz (measured; 868 MHz didn't work with these modules), SF7, BW 125 kHz,
CR 4/5, sync 0x12, CRC on. Both sketches must match. Libraries: MFRC522, LoRa
(sandeepmistry), mbedtls from the ESP32 core.
Production sketches are also in `~/Documents/Arduino/VIGIL_LoRa_Transmitter_Final`
and `VIGIL_LoRa_Receiver_Final`. The Arduino IDE opens `.ino` files, so
`secrets.h` must sit next to the sketch there.

## Running and testing

- `Start VIGIL.command` finds the receiver by its "relay ready" banner and
  rewrites `SERIAL_PORT` in `gateway/.env`. USB paths change on replug.
  `start.sh` serves `dashboard/` on 8777 (or reuses a server already there) and
  runs the gateway.
- `cd gateway && python test_crypto.py` (no hardware);
  `python gateway.py --replay frames.hex` (full path, no radio).
- `dashboard/index.html` is static and reads Supabase directly with a
  service_role key kept in localStorage. Local only. Its Download menu builds
  one gate log CSV in the browser (IST) with a `source` column: registered tag,
  unregistered card, guard tally.
- `testing/` is the bench harness for the paper's Section 7. Its sketches are
  **generated** from production by `build_test_sketches.py` (patch lists).
  Bench boards use `DEVICE_ID 11`, so `delete … where device_id = 11` cleans up.
  Results are in `testing/results/` (`FINDINGS.md` plus CSVs). End to end is
  about 1.24 s per scan, of which 94% is the HTTPS round trips to Seoul;
  AES-GCM takes 0.1 ms.
- `make_paper.py`, `make_report.py` and `paper/*.py` generate PDFs and
  figures, written to fixed paths in `~/Downloads`.

## Known limits

The PSK sits in ESP32 flash and can be read over USB (eFuse flash encryption is
the upgrade). The RAM queue is lost on power loss. `schema.sql` predates the
`unauthorised_*` objects and the `vehicle_presence` view; the database is the truth.
