# RFID + LoRa Vehicle Logging

Campus gate logging over LoRa, with a stop-and-wait acknowledged protocol and
AES-256-GCM. The transmitter's success light means *"the row is committed to
Postgres"*, not *"the radio transmitted something"*.

```
[Transmitter ESP32]              [Receiver ESP32]      [Laptop]        [Supabase]
  RFID read
  encrypt ─────── LoRa DATA ────►  relay ── serial ──►  decrypt/verify
                                                        INSERT ──────────►
                                                                  ◄─────── 201
                                                        seal ACK
  verify ACK ◄─── LoRa ACK ─────  relay ◄─ serial ────
```

The receiver **holds no keys and does no crypto**. Payloads are encrypted
end-to-end from the ESP32 to the laptop and stay opaque to the middle hop.

## Wiring

Both boards share the VSPI bus: **SCK 18, MISO 19, MOSI 23**.

| | Transmitter | Receiver |
|---|---|---|
| LoRa NSS | 4 | 5 |
| LoRa RST | 14 | 22 |
| LoRa DIO0 | 26 | 21 |
| RFID SS | 5 | — |
| RFID RST | 22 | — |
| Status LED | 2 (built-in) | — |

No manual chip-select handling is needed even though RFID and LoRa share the
bus: MFRC522 and LoRa each drive their own CS inside `SPI.beginTransaction()`,
and both parts are SPI mode 0.

## Setup

**1. Generate a key** — the same value goes in both places:

```bash
openssl rand -hex 32
```

```bash
cp transmitter/secrets.h.example transmitter/secrets.h   # paste as PSK_DATA bytes, set DEVICE_ID
cp gateway/.env.example gateway/.env                     # paste as PSK_DATA hex
```

Both are gitignored. Put your **service_role** key in `.env` — RLS is enabled
with no policies, so the anon key can read and write nothing.

**2. Arduino libraries:** `MFRC522`, `LoRa` (sandeepmistry). AES comes from
`mbedtls`, already in the ESP32 core.

**3. Python:** `pip install pyserial cryptography httpx`

**4. Flash** `transmitter/transmitter.ino` and `receiver/receiver.ino`, then:

```bash
cd gateway && python gateway.py
```

## Registering a vehicle

People register first and are issued cards later, so a `vehicles` row can exist
with no `tags` rows at all.

1. Create the vehicle in the Supabase table editor — `plate_number`,
   `owner_name`, `owner_role`, `vehicle_type`.
2. Run `python gateway.py --enroll` and scan the **entry** card. Copy the UID.
3. Add a `tags` row: that `rfid_uid`, the `vehicle_id`, `direction = 'in'`.
4. Repeat with the **exit** card, `direction = 'out'`.

Scans from unknown cards are still logged, with `vehicle_id` and `direction`
null and ACK status `0x03` (three blinks) so the guard knows to check manually.

## Protocol

DATA frames are `version | type | device_id[4] | counter[8] | ciphertext | tag[16]`.
The 14-byte header is AAD: authenticated but readable, because the receiver
must route on `type` and the laptop needs `counter` to derive the nonce.

```
nonce[0]     = type (0x01 DATA / 0x02 ACK)
nonce[1..4]  = device_id
nonce[5..11] = counter, low 7 bytes
```

The type byte is what stops a DATA frame and its ACK — same key, same counter —
from colliding on a nonce. **GCM nonce reuse under one key leaks the keystream
and permits tag forgery**, which is why the counter is persisted in NVS as a
high-water mark that jumps forward `1000` on every boot: an unclean power cut
can never replay a nonce.

ACK statuses: `0x00` committed, `0x01` already committed, `0x02` DB error
(retry), `0x03` committed but tag unregistered, `0x04` acknowledged but
deliberately not logged (enroll mode). `0x04` is separate from `0x03` on
purpose — an ACK that reports "committed" when nothing was written makes the
whole acknowledgement scheme worthless.

An ACK is accepted only if the GCM tag verifies **and** the echoed counter
matches the frame in flight — otherwise a replayed old ACK could convince the
gate a scan was logged when it never reached the database.

Serial between receiver and laptop is line-oriented hex — `R <hex> <rssi> <snr>`
in, `A <hex>` out — so the whole protocol is visible in a plain serial monitor.

## Reliability

Stop-and-wait, one frame in flight. Three tries at a 3s timeout, then the scan
stays queued and backs off 30s while card reading continues. Retries reuse the
original counter, so `unique (device_id, counter)` in Postgres turns a
lost-ACK retry into a `409` rather than a duplicate row — the database is the
single source of truth for idempotency, with no local ledger to drift out of sync.

Because you chose the strict single ACK chain, the transmitter blocks for the
full round trip: roughly 400ms–1s per scan to `ap-northeast-2`, so about one
vehicle per second. A network outage delays scans rather than losing them.

## Unauthorised vehicles

Vehicles with no registered tag are counted by the guard, who holds two cards:
one tapped per unauthorised vehicle entering, one per vehicle leaving. Those two
UIDs live in `unauthorised_tags`, and their scans land in `unauthorised_events`.

**Why a separate table?** These rows count *anonymous* vehicles. Putting them in
`vehicle_logs` would break per-vehicle presence, since every row would share the
same two tag UIDs and look like one vehicle entering hundreds of times.

Occupancy is a running tally — `+1` on an entry scan, `-1` on an exit — exposed
by `unauthorised_summary`. It's **clamped at zero**: more exits than entries means
a guard missed an entry scan, not negative occupancy, and the dashboard shows a
warning when that happens rather than silently displaying 0.

The gateway checks `tags` first and only consults `unauthorised_tags` on a miss,
so a registered vehicle still costs two round trips.

**Note the 5-second firmware debounce.** Two unauthorised vehicles arriving less
than 5s apart mean the guard's second tap of the same card is dropped at the
transmitter and never sent. Lower `UID_DEBOUNCE_MS` if that's a real problem at
your gate. There is deliberately no re-read trigger on `unauthorised_events` —
two taps in a row are two genuine vehicles, not a duplicate read.

The dashboard distinguishes this from **unknown cards** — a card nobody
registered at all, which is a security anomaly rather than an operational count.

## Dashboard

```bash
cd dashboard && python3 -m http.server 8777
# then open http://localhost:8777
```

First load asks for your project URL and **service_role** key. They're kept in
browser `localStorage` — nothing is written to disk in this repo, so there's no
secret to accidentally commit. "change credentials" clears them.

> **Local only.** The service_role key bypasses every RLS policy. Never host this
> directory, never put it behind a public URL. If you need a shared dashboard,
> that needs Supabase Auth and read policies instead — a different piece of work.

Shows: on/off campus counts with an occupancy donut, today's traffic, a
24-hour unregistered-scan count (a security signal — unknown cards at your
gate), time since last scan (a liveness signal — if the gateway dies, this stops
advancing), who is currently on campus and since when, and a searchable
movements table filterable by direction, role, unregistered, and re-read.
Refreshes every 10 seconds, so it tracks scans as they happen.

## Testing

```bash
cd gateway
python test_crypto.py                 # 10 checks, no hardware needed
python gateway.py --replay frames.hex # full decrypt -> DB -> ACK path, no radio
```

`test_crypto.py` includes a known-answer vector duplicated byte-for-byte in
`transmitter.ino`'s `selfTest()`, which runs at boot and halts the board on
mismatch. ESP32↔Python crypto divergence is the most likely bug in a system
like this; both ends fail loudly instead of looking like a flaky radio.

## Direction (in / out): two cards per vehicle

Every vehicle gets **two RFID cards** — one for entry, one for exit. The `tags`
table maps each card to a vehicle and a direction, so direction is *data*, not
a guess:

```sql
select vehicle_id, direction from tags where rfid_uid = $1;
```

**Why not derive it from scan order?** That was the first design, and it failed.
Parity (`row_number() % 2`) is chained state: one missed or spurious scan
inverts that card *forever*, and every later row inherits the error. Layering on
a weekly "assume everyone left" reset made it worse — a vehicle parked on campus
across the boundary had its genuine exit recorded as an entry, manufacturing the
very error the reset was meant to heal.

With a card per direction there is no state at all. Errors stay **independent**:
a wrong tap is exactly one wrong row, and the next scan is still correct. That
property matters more than raw accuracy — independent errors stay rare, chained
ones compound.

**Why not use which gate saw the card?** `device_id` is in the frame and would
work for fixed entry/exit gates. But gate roles here change with university
policy, so it isn't a dependable direction signal.

**Known limit:** people will sometimes tap the wrong card. That produces one
wrong row, fixed with one statement, with nothing downstream affected:

```sql
update vehicle_logs set direction = 'out' where id = <the wrong row>;
```

**Re-read guard:** the same card scanned twice within 60 seconds — a car idling
at the barrier — still gets logged, but flagged `is_reread` so it doesn't read
as a repeated entry. Direction is untouched; the card already settled that. The
threshold is a constant at the top of `vehicle_logs_flag_reread()`.

**Unknown cards** are logged with `vehicle_id` and `direction` both null. An
unregistered vehicle at your gate is exactly the event worth recording, and its
direction is genuinely unknown rather than guessable.

## Known limits

- **The PSK sits in ESP32 flash, readable over USB.** Fine for a campus
  project; it is not real key security. The upgrade is eFuse-backed flash
  encryption, which is one-way and easy to brick a board with.
- **The pending-scan queue is in RAM** (8 entries), so it is lost on a power
  cut. The gate unit is mains-powered; move it to NVS if that changes.
- **`vehicles.active` is not enforced.** It exists so you can deregister a
  vehicle without deleting its history. Acting on it at the gate would be
  access control, which this system does not do.
- **`LORA_FREQ` is 433 MHz**, set from measurement, not assumption. At 868 MHz
  this hardware showed RSSI −112 to −116 dBm at desk range with SNR +6 to +8 —
  a link budget that doesn't close (17 dBm TX minus ~31 dB path loss at 1 m
  should give ≈ −14 dBm). Switching to 433 MHz moved it to **−46 to −51 dBm at
  SNR +10**, about 74 dB of real improvement once the library's RSSI offset
  change (164 vs 157) is accounted for. The modules are 433 MHz parts — the
  RA-01/RA-02 family, not the 868 MHz `-H` variants. Margin above the −123 dBm
  sensitivity floor went from ~8 dB to ~75 dB. Both sketches must always match.
  433.05–434.79 MHz is license-free ISM in India.
