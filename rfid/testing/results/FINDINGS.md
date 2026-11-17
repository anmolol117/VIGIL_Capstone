# VIGIL — Section 7 measured results

Accumulated as each phase completes. Every number here came from an actual run;
nothing is estimated. Regenerate the underlying tables any time with
`python3 analyse.py --tag <phase>`.

**Last updated:** after Phase 1

---

## Test setup (fills the paper's bracketed placeholder)

| Parameter | Value |
|---|---|
| Environment | Indoor |
| TX–RX separation | 0.5 m |
| Antenna orientation | Both vertical, quarter-wave 433 MHz whip |
| Laptop network | Home Wi-Fi |
| Database | Supabase PostgreSQL, region `ap-northeast-2` (Seoul) |
| Radio | SX1276, 433 MHz, SF7, BW 125 kHz, CR 4/5, 8-symbol preamble, CRC on |
| Serial link | 115200 baud, 8N1 |
| Firmware | Instrumented bench build, `DEVICE_ID 11` |
| Cards | 2 registered (entry `aa8d0504`, exit `1e140504`), 2 attendant (entry `82668a5c`, exit `0dce0704`) |

Timing is measured on three unsynchronised clocks — transmitter, receiver, and
laptop — with each segment measured on whichever clock owns both of its
endpoints. Air propagation is not measured directly; it is computed from the
LoRa time-on-air formula and validated against measurement (see below).

---

## Phase 1 — Link health baseline

**Status:** complete. n = 4 scans (plus 4 in a prior verification run).

### Measured vs computed LoRa airtime

The single most defensible result in this section: instrumentation agrees with
first-principles theory on two independent frames.

| Frame | Measured | Computed | Delta | Error |
|---|---|---|---|---|
| DATA, 34 B (63 symbols) | **78.0 ms** | 77.1 ms | +0.9 ms | 1.2 % |
| ACK, 31 B (58 symbols) | **72.8 ms** | 71.9 ms | +0.9 ms | 1.3 % |

Time-on-air computed as `T_preamble + n_payload × T_sym` with
`T_sym = 2^SF / BW`, at the library defaults actually in use. The +0.9 ms
residual is identical on both frames, consistent with fixed SPI setup overhead
rather than measurement error.

### Per-stage latency

| Stage | Mean | Median | p95 | Worst |
|---|---|---|---|---|
| Card detect + UID read | 3.5 ms | 3.5 | 3.6 | 3.6 |
| AES-256-GCM encryption | 0.1 ms | 0.1 | 0.1 | 0.1 |
| LoRa transmit (DATA) | 78.0 ms | 78.0 | 78.0 | 78.0 |
| Receiver relay (LoRa RX → serial) | 2.6 ms | 2.6 | 2.6 | 2.6 |
| Receiver relay (serial → LoRa TX) | 72.8 ms | 72.8 | 72.8 | 72.8 |
| TX done → ACK verified | 1160.7 ms | 1137.1 | 1735.4 | 1735.4 |
| **End-to-end** | **1238.8 ms** | 1215.1 | 1813.6 | 1813.6 |

### Database stage breakdown

| Call | Mean | Median | p95 | Worst | n |
|---|---|---|---|---|---|
| Decrypt + verify (local) | 0.7 ms | 0.1 | 2.5 | 2.5 | 4 |
| `SELECT tags` | 705.3 ms | 557.6 | 1439.3 | 1439.3 | 4 |
| `SELECT unauthorised_tags` | 300.4 ms | 300.4 | 366.6 | 366.6 | 2 |
| `INSERT` (write confirmed) | 208.1 ms | 208.8 | 221.1 | 221.1 | 4 |
| Seal ACK (local) | 0.1 ms | 0.0 | 0.1 | 0.1 | 4 |
| Gateway total | 1064.4 ms | 1040.2 | 1639.1 | 1639.1 | 4 |

Failed or timed-out writes: **0 of 4**. All scans completed on the first
attempt; no retransmissions occurred.

### Signal quality

RSSI ranged **−61 to −84 dBm** at 0.5 m, SNR **+9.5 to +10.0 dB**. All frames
decoded; the link never approached the ≈ −123 dBm SF7 sensitivity floor.

### Findings worth stating in the paper

1. **Authenticated encryption is effectively free.** AES-256-GCM costs 0.1 ms,
   about 0.008 % of end-to-end latency, using the ESP32's hardware AES via
   mbedTLS. The common objection that authenticated encryption is too expensive
   for a microcontroller gate node is not supported by measurement.

2. **The database dominates.** 1160.7 ms of the 1238.8 ms end-to-end mean — 94 %
   — is spent between the LoRa transmission completing and the acknowledgement
   returning, and the gateway breakdown attributes almost all of that to two
   HTTPS round trips to Seoul. Radio and serial together are ~162 ms and
   essentially fixed; the database is variable and an order of magnitude larger.
   This is the direct, quantified cost of the design decision to acknowledge on
   database commit rather than on radio receipt.

3. **Routing cost differs by card class.** A registered card costs two database
   calls (`SELECT tags`, `INSERT`); an attendant or unknown card costs three,
   because `SELECT unauthorised_tags` runs only on a miss. Confirmed in the run:
   `SELECT unauthorised_tags` fired on exactly the 2 attendant presentations.

4. **RSSI variance is operator-induced, not link instability.** The 23 dB spread
   at a fixed 0.5 m separation tracks hand proximity: the MFRC522 shares a board
   with the LoRa antenna, so every card presentation places a hand beside it.
   Body detuning of 10–15 dB is expected and does not indicate a marginal link.

### Measurement caveats (state these; a reviewer will find them otherwise)

- **`RELAY,in` = 2.6 ms is understated.** The ESP32 buffers serial writes, so
  `printf` returns before the bytes reach the wire. The hex-encoded R line is
  ~80 characters ≈ 7 ms at 115200 baud. The 2.6 ms figure is CPU time; actual
  serial transit overlaps it. Do not report 2.6 ms as the serial hop latency.
- **The first HTTPS request pays TLS handshake** (1.1–1.6 s cold). The harness
  issues one warm-up request and discards it, so it does not enter any sample.
- **n = 4 is a baseline check, not a characterisation.** Phase 3 supplies the
  sample size for the latency table.

---

## Phase 2 — RFID read accuracy

**Status:** condition 1 of 3 complete.

### Method, and why the denominator is not a hand tally

Two registered cards were presented alternately. Because the firmware debounce
compares each read against the *immediately preceding* card rather than the last
sighting of that specific card, strict alternation bypasses suppression entirely
— confirmed by 0 debounce events across the run.

That alternation also makes the data self-validating: a completely missed
presentation forces the same UID to appear twice consecutively, which is
detectable in the log. Two such breaks occurred, one across a 137 s pause
between batches (a boundary artefact, not a miss) and one at a 5 s gap.

The operator's hand tally disagreed with the instrumented count by 1–2 over
40 presentations, in the direction of undercounting. **A hand tally is not a
reliable denominator at this sample size**, so the figures below are reported
against denominators the system measures itself.

### Condition 1 — static, normal approach

Card laid flat on the reader face, ~1 cm, held approximately one second.

| Metric | Value |
|---|---|
| Detection attempts | 51 |
| Decoded | 49 |
| Decode failed | 2 |
| Debounce-suppressed | 0 |
| Scans reaching the database | 50 |
| **Per-attempt decode rate** | **49 / 51 = 96.1 %** |
| **Per-presentation decode rate** | **49 / 49 = 100 %** |
| Transient retry rate | 2 / 51 = 3.9 % |
| Card read time | 3.52 ms mean (min 3.50, max 3.54, n = 49) |
| Scans completing on first radio attempt | 48 / 49 |

Both decode rates are correct and measure different things. Per-attempt counts
every call to `PICC_ReadCardSerial()`; per-presentation counts distinct card
presentations, in which the two failures were recovered by the polling loop
milliseconds later and so lost no data.

### Finding: transient decode failures are self-recovering

Both failures were immediately followed by a success in the same second:

```
00:19:40  DETECT,fail
00:19:40  DETECT,ok     <- same presentation, next poll iteration
```

`PICC_ReadCardSerial()` occasionally fails on first attempt while the card is
still settling in the reader field, and the polling loop retries without any
explicit retry logic. **No presentation was lost to a decode failure.** Reporting
3.9 % as a "read failure rate" would misrepresent this: the correct statement is
that 3.9 % of read attempts required one additional poll cycle, costing
microseconds and no data.

### Finding: card read time is essentially constant

3.50–3.54 ms across 49 reads, a 40 µs spread. The MFRC522 exchange is
deterministic, so RFID reading contributes a fixed, negligible 0.3 % of the
1239 ms end-to-end latency.

### Limitation to state in the paper

A presentation the reader never detects at all produces no log line, and the
system therefore cannot count it. Establishing a true end-to-end read accuracy
— including never-detected presentations — needs an external ground truth the
operator's count could not reliably provide. The rates above are conditional on
detection and are stated as such.

### Condition 2 — fast approach

Card swept past the reader face without dwelling, at the fastest speed
achievable by hand.

| Metric | Value |
|---|---|
| Unique scans committed | 49 |
| Detection attempts (instrumented subset) | 23 |
| Decode failures | **0** |
| Debounce suppressions | 0 |
| Scans completing on first radio attempt | 23 / 23 |
| Card read time | 3.53 ms mean (min 3.49, max 3.56) |

**No failure of any kind could be induced by hand speed.** The read transaction
is 3.5 ms; with an effective field of roughly 3 cm, a card must remain in range
for about 5 ms including detection. At walking pace (~1.4 m/s) it remains in
range for ~21 ms, four times the requirement. Reducing dwell to 5 ms requires
about **6 m/s (≈ 20 km/h)** — a vehicle speed, not a hand speed.

The practical limit on this reader is therefore vehicle speed at the gate, and
it is calculable from a measured constant rather than estimated. The ~20 km/h
figure is an order-of-magnitude extrapolation, not a measurement; the field
width is assumed, not characterised.

### Condition 3 — angle and obstruction

Card tilted toward perpendicular, or presented through an obstruction (wallet,
paper, phone), at normal speed.

| Metric | Value |
|---|---|
| Reported presentations | 50 |
| Detection attempts | 55 |
| Decoded | 40 |
| Decode failures | 14 |
| Distinct presentations detected | 42 |
| Decoded eventually | 40 |
| **Detected but never decoded** | **2** |
| Needed at least one retry, then worked | 11 |
| Scans committed to database | 40 |
| Scans completing on first radio attempt | 38 / 40 |
| Card read time | 3.52 ms mean (min 3.50, max 3.54, n = 40) |

| Rate | Value |
|---|---|
| Per-attempt decode | **72.7 %** |
| Per-presentation decode | **95.2 %** |
| End-to-end (against reported count) | **80.0 %** |

### Comparison across conditions

| | Static | Fast | Angle / obstruction |
|---|---|---|---|
| Per-attempt decode rate | 96.1 % | 100 % | **72.7 %** |
| Per-presentation decode rate | 100 % | 100 % | 95.2 % |
| Unrecovered decode failures | 0 | 0 | **2** |
| Presentations apparently never detected | 0–1 | 0–1 | **~8** |
| Card read time when successful | 3.52 ms | 3.53 ms | 3.52 ms |

### Finding: coupling is a threshold, not a gradient

Read time is **3.50–3.56 ms across all three conditions and all 112 successful
reads** — a spread of 60 µs. Degraded coupling never produced a slower read; it
produced either a normal-duration read or nothing at all. The MFRC522 exchange
either completes at full speed or does not complete, so presentation quality
determines *whether* a read happens, never *how long* it takes.

This makes the failure mode easy to reason about operationally: there is no
partial or marginal read state to handle at the gate.

### Finding: most decode failures are self-recovering

Across all conditions, the polling loop retried failed reads without any
explicit retry logic, and the overwhelming majority succeeded within
milliseconds. Only 2 presentations out of 116 detected across the whole phase
were detected and never decoded. Reporting the per-attempt failure rate as a
"read failure rate" would substantially misrepresent the system: a failed
attempt normally costs one extra poll cycle and no data.

### Methodological limitation: the operator count is not a reliable denominator

Across every batch in this phase the operator's hand tally disagreed with the
instrumented count, typically by 1–2 per 10–20 presentations, in both
directions. End-to-end accuracy figures — which need a count of presentations
including those never detected — therefore carry that uncertainty and should be
reported as approximate. Per-attempt and per-presentation rates, whose
denominators the system measures itself, do not.

A future run wanting a defensible end-to-end figure would need external ground
truth: a mechanical presentation jig, or a second sensor (photoelectric beam)
counting presentations independently of the RFID reader.

---

## Phase 3 — Authentication time

**Status:** complete. n = 34 scans (27 first-attempt, 5 retried, 2 queued
through an outage).

### Per-stage latency, first-attempt scans (n = 27)

| Stage | Mean | Median | p95 | Min | Max |
|---|---|---|---|---|---|
| Card detect + UID read | 3.52 ms | 3.51 | 3.54 | 3.50 | 3.55 |
| AES-256-GCM encryption | 0.07 ms | 0.07 | 0.07 | 0.06 | 0.07 |
| LoRa transmit (DATA) | 77.96 ms | 77.97 | 77.97 | 77.95 | 77.97 |
| TX done → ACK verified | 537.27 ms | 520.11 | 658.77 | 447.45 | 1013.39 |
| **End-to-end** | 767.35 ms | **604.43** | 1797.47 | 525.52 | 3456.86 |

**Report the median as typical latency, not the mean.** Stop-and-wait means a
card presented while another scan is in flight waits in the queue, and
`total_us` is measured from the card read — so a few first-attempt scans carry
queueing delay of up to 3.4 s. The median is unaffected by this; the mean is not.

### Gateway stage breakdown (n = 38)

| Stage | Mean | Median | p95 |
|---|---|---|---|
| Decrypt + verify (local) | 0.08 ms | 0.08 | 0.11 |
| `SELECT tags` | 254.85 ms | 199.76 | 683.22 |
| `INSERT` | 221.25 ms | 193.84 | 373.00 |
| Seal ACK (local) | 0.06 ms | 0.07 | 0.08 |
| Gateway total | 476.54 ms | 427.49 | 917.83 |

### Finding: LoRa transmit time has zero measurable variance

77.95–77.97 ms across all 27 first-attempt scans — a 20 µs spread on a 78 ms
operation, against a computed 77.1 ms. The transmit stage is not merely
predictable on average; it is effectively constant.

### Finding: retry latency is deterministic and matches its design parameters

| Attempts | Measured end-to-end | Predicted from constants |
|---|---|---|
| 2 | 4678, 4684 ms | 78 + 3000 + 1000 + 78 + ~500 = **4656 ms** |
| 3 | 9708, 9720 ms | + 3000 + 2000 + 78 = **9734 ms** |

Predicted from `ACK_TIMEOUT_MS = 3000` plus the linear 1 s / 2 s backoff.
Measurements land within 30 ms. Retry cost is therefore a stated design
parameter rather than an empirical observation, and can be quoted directly.

### Finding: most retransmissions are redundant — which is exactly why idempotency matters

Of 5 retried scans, **4 returned status `0x01` ("already recorded")**. The
original transmission had reached the database and only the acknowledgement was
lost on the return path. Only one scan genuinely required its retransmission to
deliver data.

Without the `unique (device_id, counter)` constraint those four retries would
each have created a **duplicate movement record** — one vehicle logged multiple
times for a single gate crossing. The constraint fired on 12.5 % of scans in a
routine 32-scan run under normal conditions. This is the single strongest
empirical argument in the project for acknowledging on database commit and
making retransmission idempotent.

### Finding: zero data loss across a two-minute total outage

The receiver's USB was unplugged mid-run, three cards were presented, and the
cable was reconnected. During the outage the transmitter reported:

```
FAILED after 3 tries, ctr=45093 -- still queued, backing off
```

The outage was longer than planned because the receiver **re-enumerated on a
different device path** on reconnection (`/dev/cu.usbserial-10` →
`/dev/cu.usbserial-110`), and the gateway had to be pointed at the new port.
Total interruption ≈ 2 minutes.

| Scan | Time from card read to committed | Status |
|---|---|---|
| ctr 45093 | **127.79 s** | `0x00` committed |
| ctr 45094 | **121.45 s** | `0x00` committed |

Both returned `0x00`, not `0x01` — these had genuinely not reached the database
before, so the queue was doing real work rather than duplicating. One of the
three presentations was suppressed by the firmware debounce and correctly never
queued.

**Nothing was lost.** Scans survived a crashed gateway, a USB disconnection, an
operating-system device re-enumeration, and a process restart, and were
delivered and committed once the path was restored.

### Confirmed limitation: the re-read flag misfires on queue drain

Row 45094 was flagged `is_reread = true` despite representing a distinct
presentation made two minutes earlier. The trigger compares `logged_at`, which
is insert time, and both queued scans were inserted one second apart when the
queue drained. Frames carry no timestamp — the transmitter has no clock — so the
database cannot distinguish a burst replay from a genuine rapid re-read.

**A cluster of `is_reread` flags following an outage indicates recovery, not
repeated presentations.** Fixing this properly would require the transmitter to
send elapsed-time-since-scan so the gateway could back-date the row.

### Operational note: USB device paths are not stable

Reconnecting the receiver changed its device path. `SERIAL_PORT` in
`gateway/.env` is therefore stale after any replug, and `start.sh` would fail
with a missing-device error. The bench harness auto-detects the transmitter by
its boot banner; the production gateway does not, and this is worth knowing
before a live deployment.

---

## Phase 4 — Database response time

**Status:** complete. Home Wi-Fi only; the second network condition was not run.
Driven by replaying pre-built encrypted frames, so the radio and RFID stages are
excluded entirely and only the laptop-to-database path is measured.

### Registered card — 2 database calls (n = 30)

| Stage | Mean | Median | p95 | Min | Max |
|---|---|---|---|---|---|
| Decrypt + verify (local) | 0.0 ms | 0.0 | 0.1 | 0.0 | 0.1 |
| `SELECT tags` | 412.5 ms | 224.5 | 614.7 | 177.7 | **5004.0** |
| `INSERT` | 240.4 ms | 220.2 | 417.5 | — | 559.2 |
| Seal ACK (local) | 0.1 ms | 0.1 | 0.1 | 0.0 | 0.1 |
| **Gateway total** | 653.1 ms | **460.7** | 794.9 | 358.6 | 5004.3 |

**Failed / timed-out writes: 1 of 30 (3.3 %).** One `SELECT tags` hit the
client's 5 s timeout exactly, returned status `0x02`, and never reached the
insert. Report the median rather than the mean here: a single 5 s timeout drags
the mean from 461 ms to 653 ms.

### Unknown card — 3 database calls (n = 20)

| Stage | Mean | Median | p95 |
|---|---|---|---|
| `SELECT tags` (miss) | 242.0 ms | 217.7 | 396.2 |
| `SELECT unauthorised_tags` (miss) | 270.2 ms | 229.5 | 472.2 |
| `INSERT` | 253.4 ms | 220.2 | 690.9 |
| **Gateway total** | 765.8 ms | **717.5** | 1381.8 |

All 20 returned `0x03`. No failures.

### Duplicate frame — uniqueness constraint (n = 30)

| Stage | Mean | Median | p95 |
|---|---|---|---|
| `SELECT tags` | 198.2 ms | 181.3 | 302.0 |
| `INSERT` (rejected by constraint) | 199.2 ms | 181.4 | 329.7 |
| **Gateway total** | 397.5 ms | **369.0** | 509.7 |

### Finding: routing path determines cost, and the difference is measurable

| Path | DB calls | Median total |
|---|---|---|
| Duplicate (constraint rejection) | 2 | **369 ms** |
| Registered card | 2 | **461 ms** |
| Unknown card | 3 | **718 ms** |

An unknown card costs **56 % more** than a registered one, because
`lookup_guard_tag` runs only on a miss. Rejecting a duplicate is **cheaper than
performing the insert** it replaces — the constraint check completes without a
write. Retransmission is therefore not only harmless but slightly less
expensive than the original operation.

### Finding: an accidental end-to-end demonstration of retry semantics

The duplicate run replayed the same 30 frames a second time. **29 returned
`0x01` and one returned `0x00`** — and that one was precisely the frame whose
original write had timed out at 5 s and returned `0x02`.

This is the complete designed behaviour, observed without being staged:

1. Write times out → gateway returns `0x02` → transmitter is told to retry
2. The row genuinely did not land, so the retransmission is not a duplicate
3. Replay of that frame inserts successfully (`0x00`)
4. Replay of the other 29, which *had* landed, is rejected (`0x01`)

The system distinguishes correctly between a retry that must deliver data and a
retry that must not duplicate it, using nothing but the counter and a database
constraint.

### Finding: local cryptography is negligible against network cost

Decrypt-and-verify and ACK-sealing each measured **0.0–0.1 ms**, against 180–500
ms per database round trip — a factor of several thousand. Combined with the
0.07 ms encryption cost on the ESP32, the entire cryptographic layer of this
system costs well under a millisecond per movement, distributed across two
devices.

### Limitation

Only one network condition was measured. The paper's comparison of baseline
versus degraded networking is not supported by data and should either be dropped
or run separately. For reference, an earlier wired dry run (n = 5) gave
`SELECT tags` 243.8 ms and `INSERT` 213.5 ms — broadly similar medians, so the
Wi-Fi link is not obviously the limiting factor; the geographic distance to
`ap-northeast-2` dominates either way.

---

## Phase 5 — Unauthorised detection

**Status:** complete. 70 presentations across three card classes with known
ground truth.

### Two distinct mechanisms, deliberately separated

The system distinguishes two events that are easy to conflate:

- **Attendant-counted vehicle** — a vehicle with no RFID tag, tallied by the
  gate attendant presenting one of two registered cards held for this purpose.
  These are *known* unauthorised vehicles, counted on purpose.
- **Unidentifiable card** — a card whose UID appears in no table at all. This is
  a security anomaly, not a count.

They are stored in different tables and surfaced separately on the dashboard.
This trial verifies they never mix.

### Confusion matrix

| Card presented | Ground truth | Rows | Classified as vehicle | Given a direction | Correct |
|---|---|---|---|---|---|
| `82668a5c` | attendant entry | 22 | 0 | 22 | **22 / 22** |
| `0dce0704` | attendant exit | 22 | 0 | 22 | **22 / 22** |
| `22d01005` | unregistered | 21 | **0** | **0** | **21 / 21** |

**False positives: 0. False negatives: 0.** No unregistered card was ever
attributed to a vehicle or given a direction; no attendant card was ever treated
as unidentifiable.

### Finding: the attendant tally is exactly symmetric and retry-safe

| Step | `total_in` | `total_out` | `currently_inside` |
|---|---|---|---|
| Baseline | 8 | 4 | 4 |
| After 20 entry presentations | 28 | 4 | **24** |
| After 20 exit presentations | 28 | 24 | **4** |

The running total returned precisely to its starting value. Forty presentations,
zero drift.

Critically, the exit card produced **21 gateway responses for 20 counted
events** — one was a lost-ACK retransmission that returned `0x01` and was
absorbed by the uniqueness constraint. Without it the attendant's count would
have read 21 exits for 20 vehicles. This is the failure mode that would
otherwise silently corrupt an operational tally, and it occurred spontaneously
in a 40-presentation run.

### Finding: unidentifiable cards do not contaminate the tally

Across 21 presentations of a card unknown to the system,
`unauthorised_summary` did not move — `currently_inside` remained 4 throughout.
Every presentation was recorded in `vehicle_logs` with `vehicle_id` and
`direction` null and acknowledged with status `0x03`.

An unknown card is therefore visible to an operator as an anomaly without
inflating the count of unauthorised vehicles, which is the behaviour the
two-table design exists to produce.

### Incidental reliability observations

Within this phase, under ordinary conditions:

- **7 responses returned `0x01`** — retransmissions deduplicated by the
  uniqueness constraint
- **2 responses returned `0x02`** — database timeouts, correctly signalled for
  retry rather than silently dropped
- The unregistered card's first lookup took **752 ms** against ~180–290 ms for
  subsequent ones, showing the cold-path penalty recurring mid-run rather than
  only at process start

### Note on terminology, worth fixing in the paper

The two attendant cards are frequently described as "the unauthorised cards",
but they are **registered** — they exist in `unauthorised_tags`. The paper
should distinguish *attendant tally cards* (registered tokens denoting an
unauthorised vehicle) from *unregistered cards* (unknown UIDs). Conflating them
obscures the fact that the system handles two different events through two
different paths.
