# Bench test harness

Everything here exists only to collect Section 7 measurements. **No file in this
folder is part of the deployed system**, and nothing here writes to the
production sketches or `gateway/gateway.py` — the test sketches are *generated*
from them, read-only.

Delete this whole folder after the paper is written and the system is unchanged.

## Why the test firmware is generated, not copied

`build_test_sketches.py` reads the production sketches and applies an explicit
list of patches. That means:

- the instrumentation delta is auditable — every difference is one entry in the
  `TX_PATCHES` / `RX_PATCHES` lists
- there is no 370-line duplicate drifting out of sync
- if a production sketch ever changes so an anchor no longer matches, the build
  **fails loudly** instead of quietly flashing stale firmware

```bash
python3 build_test_sketches.py
```

Writes `VIGIL_TX_Test/` and `VIGIL_RX_Test/`, each a complete Arduino sketch.

## DEVICE_ID 11

The bench transmitter identifies as **device 11**, not 1. Every row it produces
is removable in one statement, with no risk to your sample data or mock fleet:

```sql
delete from vehicle_logs where device_id = 11;
delete from unauthorised_events where device_id = 11;
```

A different device id also means a disjoint GCM nonce space, so reusing counter
values across the two builds is cryptographically safe.

## What the instrumentation emits

**Transmitter** — one CSV line per completed scan:

```
TIMING,<counter>,<read_us>,<seal_us>,<tx_us>,<wait_us>,<total_us>,<tries>,<status>
```

`wait_us` runs from `endPacket()` to ACK verified, so it contains the ACK
airtime, both serial hops, decrypt, the database round trip, and the ACK seal.
`total_us` is the whole pipeline from the start of the card read.

Plus one line per card detection, which is what makes read accuracy
decomposable:

```
DETECT,ok,<us>          detected and decoded
DETECT,fail,<us>        detected, decode failed
DETECT,debounce,<us>    suppressed by the 5 s guard (harness artefact, not a failure)
```

A presentation the reader never sees produces **no line at all** — that is why
your hand tally is the denominator.

**Receiver** — relay latency on its own clock, `#`-prefixed so the gateway
ignores it:

```
# RELAY,in,<us>     LoRa RX complete -> serial line written
# RELAY,out,<us>    serial line read -> LoRa TX complete
```

**Gateway** — `results/<tag>_gateway.csv`, per-stage laptop-side latency with
`lookup_tag`, `lookup_guard_tag` and the insert timed separately.

## Running each phase

```bash
# Phase 1 + 2 + 3 + 5  (live, hardware attached)
python3 gateway_test.py --tag phase3

# Phase 4  (no hardware at all)
python3 make_frames.py -n 30
python3 gateway_test.py --tag phase4 --replay frames.hex

# Any time
python3 analyse.py --tag phase3
python3 analyse.py                 # every run in results/
```

`gateway_test.py` fires one warm-up request and discards it — the first HTTPS
call pays TLS handshake (~1.2 s cold) and would otherwise skew every mean.

## Flashing

Arduino compiles a sketch folder, so copy these into the sketchbook as their own
sketches rather than overwriting the production ones:

```bash
cp -R VIGIL_TX_Test VIGIL_RX_Test ~/Documents/Arduino/
```

Then flash from the IDE, or:

```bash
CLI="/Applications/Arduino IDE.app/Contents/Resources/app/lib/backend/resources/arduino-cli"
"$CLI" upload -p <port> --fqbn "esp32:esp32:esp32:UploadSpeed=115200" ~/Documents/Arduino/VIGIL_TX_Test
```

Upload at **115200**, not the default — the CH340 adapters here drop the
connection at 921600.

Both bench builds announce themselves on boot (`### BENCH BUILD ###`,
`# relay ready -- BENCH BUILD`), so a production board flashed by mistake is
obvious immediately.

## Restoring production firmware (Phase 6)

```bash
"$CLI" upload -p <tx-port> --fqbn "esp32:esp32:esp32:UploadSpeed=115200" ~/Documents/Arduino/VIGIL_LoRa_Transmitter_Final
"$CLI" upload -p <rx-port> --fqbn "esp32:esp32:esp32:UploadSpeed=115200" ~/Documents/Arduino/VIGIL_LoRa_Receiver_Final
```

Those sketchbook folders were never modified, so they are already correct —
`DEVICE_ID 1`, no instrumentation.
