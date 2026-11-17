#!/usr/bin/env python3
"""Generates instrumented copies of the production sketches for bench testing.

Production sketches are READ ONLY here. Every difference between production and
test firmware is one entry in the PATCH lists below, so the instrumentation
delta is auditable at a glance -- and if a production file ever changes such
that an anchor no longer matches, this fails loudly instead of silently
producing a stale test build.

    python3 build_test_sketches.py          # writes testing/VIGIL_*_Test/
"""

import pathlib
import re
import shutil
import sys

HERE = pathlib.Path(__file__).resolve().parent
ROOT = HERE.parent

TEST_DEVICE_ID = 11   # keeps every bench row deletable: where device_id = 11

# ---------------------------------------------------------------- transmitter

TX_PATCHES = [
    # 1. Timing fields on the queued-scan record.
    ("""struct Scan {
  uint8_t  uid[MAX_UID];
  uint8_t  uidLen;
  uint64_t ctr;
  uint8_t  tries;
};""",
     """struct Scan {
  uint8_t  uid[MAX_UID];
  uint8_t  uidLen;
  uint64_t ctr;
  uint8_t  tries;
  // BENCH: micros() wraps every ~71 min. Every value below is a difference
  // between two samples taken well inside that window, and unsigned
  // subtraction wraps correctly, so no special handling is needed.
  uint32_t t_queued;    // micros() when the card finished reading
  uint32_t t_txend;     // micros() after endPacket() of the latest attempt
  uint32_t read_us;     // RFID detect + decode
  uint32_t seal_us;     // AES-256-GCM encrypt
  uint32_t tx_us;       // beginPacket .. endPacket
};"""),

    # 2. Carry the read duration into the queue entry.
    ("""static void push(const uint8_t *uid, uint8_t uidLen) {""",
     """static void push(const uint8_t *uid, uint8_t uidLen, uint32_t read_us) {"""),
    ("""  s->ctr    = nextCounter();   // assigned once; retries reuse it so the DB can dedupe
  s->tries  = 0;
  qCount++;""",
     """  s->ctr    = nextCounter();   // assigned once; retries reuse it so the DB can dedupe
  s->tries  = 0;
  s->read_us  = read_us;       // BENCH
  s->t_queued = micros();      // BENCH
  qCount++;"""),

    # 3. Time the encrypt and the transmit separately.
    ("""  uint8_t frame[HDR_LEN + MAX_UID + TAG_LEN];
  size_t len = sealData(s->uid, s->uidLen, s->ctr, frame);
  if (len == 0) { Serial.println("ERROR: encryption failed"); pop(); return; }

  LoRa.beginPacket();
  LoRa.write(frame, len);
  LoRa.endPacket();""",
     """  uint8_t frame[HDR_LEN + MAX_UID + TAG_LEN];

  uint32_t t0 = micros();                                    // BENCH
  size_t len = sealData(s->uid, s->uidLen, s->ctr, frame);
  s->seal_us = micros() - t0;                                // BENCH
  if (len == 0) { Serial.println("ERROR: encryption failed"); pop(); return; }

  t0 = micros();                                             // BENCH
  LoRa.beginPacket();
  LoRa.write(frame, len);
  LoRa.endPacket();          // blocks until sent, so this measures airtime
  s->t_txend = micros();                                     // BENCH
  s->tx_us   = s->t_txend - t0;                              // BENCH"""),

    # 4. Emit one CSV row per completed scan.
    ("""static void onAck(uint8_t status) {
  switch (status) {""",
     """// BENCH: one CSV row per completed scan, consumed by analyse.py.
//   wait_us  = endPacket .. ACK verified. Covers ACK airtime, both serial
//              hops, decrypt, the database round trip, and the ACK seal.
//   total_us = card read start .. ACK verified, i.e. the whole pipeline.
//
// Reads the queue head directly rather than taking a Scan* argument: the
// Arduino preprocessor injects prototypes above the struct definition, so any
// function whose signature names Scan fails to compile.
static void emitTiming(uint8_t status) {
  uint32_t now = micros();
  Serial.printf("TIMING,%llu,%lu,%lu,%lu,%lu,%lu,%u,0x%02x\\n",
                queue[qHead].ctr, queue[qHead].read_us, queue[qHead].seal_us,
                queue[qHead].tx_us, now - queue[qHead].t_txend,
                now - queue[qHead].t_queued, queue[qHead].tries, status);
}

static void onAck(uint8_t status) {
  emitTiming(status);                                        // BENCH
  switch (status) {"""),

    # 5. Split detect from decode, so read accuracy can be broken down.
    ("""  if (!rfid.PICC_IsNewCardPresent() || !rfid.PICC_ReadCardSerial()) return;

  uint8_t len = rfid.uid.size > MAX_UID ? MAX_UID : rfid.uid.size;""",
     """  // BENCH: a card that never enters the field produces no line at all --
  // that denominator is the human tally, which is exactly why "detected but
  // undecodable" has to be distinguishable from "never seen".
  uint32_t t0 = micros();
  if (!rfid.PICC_IsNewCardPresent()) return;        // nothing there, not a trial
  if (!rfid.PICC_ReadCardSerial()) {
    Serial.printf("DETECT,fail,%lu\\n", micros() - t0);
    return;
  }
  uint32_t read_us = micros() - t0;

  uint8_t len = rfid.uid.size > MAX_UID ? MAX_UID : rfid.uid.size;"""),

    ("""  if (same && (millis() - lastAt) < UID_DEBOUNCE_MS) return;""",
     """  if (same && (millis() - lastAt) < UID_DEBOUNCE_MS) {
    Serial.printf("DETECT,debounce,%lu\\n", read_us);  // BENCH: harness artefact
    return;                                           // not a read failure
  }"""),

    ("""  push(rfid.uid.uidByte, len);
  Serial.println("card read, queued");""",
     """  Serial.printf("DETECT,ok,%lu\\n", read_us);              // BENCH
  push(rfid.uid.uidByte, len, read_us);
  Serial.println("card read, queued");"""),

    # 6. Announce the build so a stray production flash is obvious on the wire.
    ("""  selfTest();""",
     """  Serial.printf("### BENCH BUILD -- instrumented, DEVICE_ID %u ###\\n", DEVICE_ID);
  selfTest();"""),
]

RX_PATCHES = [
    ("""  uint8_t frame[MAX_FRAME];
  int n = hexDecode(line + 2, lineLen - 2, frame, sizeof(frame));
  if (n <= 0) return;

  LoRa.beginPacket();
  LoRa.write(frame, n);
  LoRa.endPacket();
  // parsePacket() in loop() puts the radio back into receive.
}""",
     """  uint32_t t0 = micros();                                    // BENCH
  uint8_t frame[MAX_FRAME];
  int n = hexDecode(line + 2, lineLen - 2, frame, sizeof(frame));
  if (n <= 0) return;

  LoRa.beginPacket();
  LoRa.write(frame, n);
  LoRa.endPacket();
  // parsePacket() in loop() puts the radio back into receive.

  // BENCH: '#' prefix so the gateway treats this as a comment and ignores it.
  Serial.printf("# RELAY,out,%lu\\n", micros() - t0);
}"""),

    ("""  int rssi = LoRa.packetRssi();
  float snr = LoRa.packetSnr();""",
     """  uint32_t t0 = micros();                                    // BENCH
  int rssi = LoRa.packetRssi();
  float snr = LoRa.packetSnr();"""),

    ("""  Serial.printf(" %d %.1f\\n", rssi, snr);
}""",
     """  Serial.printf(" %d %.1f\\n", rssi, snr);

  // BENCH: measured after the line is on the wire, so it includes the ~7 ms
  // the hex-encoded frame costs at 115200 baud.
  Serial.printf("# RELAY,in,%lu\\n", micros() - t0);
}"""),

    ("""  Serial.println("# relay ready");""",
     """  Serial.println("# relay ready -- BENCH BUILD, instrumented");"""),
]


def apply(src_text, patches, label):
    out = src_text
    for i, (find, repl) in enumerate(patches, 1):
        if out.count(find) != 1:
            sys.exit(f"FAIL {label} patch {i}: anchor found {out.count(find)} times, "
                     f"expected exactly 1.\n  Production source changed -- "
                     f"update the anchor in build_test_sketches.py.\n"
                     f"  anchor starts: {find.splitlines()[0][:70]}")
        out = out.replace(find, repl)
    return out


def build(prod_path, sketch_name, patches):
    src = pathlib.Path(prod_path).read_text()
    out = apply(src, patches, sketch_name)
    d = HERE / sketch_name
    d.mkdir(parents=True, exist_ok=True)
    (d / f"{sketch_name}.ino").write_text(out)
    return d, len(out.splitlines()) - len(src.splitlines())


tx_dir, tx_delta = build(ROOT / "transmitter/transmitter.ino", "VIGIL_TX_Test", TX_PATCHES)
rx_dir, rx_delta = build(ROOT / "receiver/receiver.ino", "VIGIL_RX_Test", RX_PATCHES)

# The bench build needs the same key but a distinct device id, so its rows can
# be deleted wholesale without touching production data. A different device id
# also means a disjoint nonce space, so reusing counter values is safe.
prod_secrets = (ROOT / "transmitter/secrets.h").read_text()
test_secrets = re.sub(r"#define DEVICE_ID\s+\d+u?",
                      f"#define DEVICE_ID {TEST_DEVICE_ID}u", prod_secrets)
if f"DEVICE_ID {TEST_DEVICE_ID}u" not in test_secrets:
    sys.exit("FAIL: could not rewrite DEVICE_ID in secrets.h")
test_secrets = test_secrets.replace(
    "// Generated key.",
    f"// BENCH BUILD -- DEVICE_ID is {TEST_DEVICE_ID}, not 1. Test rows are removable\n"
    f"// with: delete from vehicle_logs where device_id = {TEST_DEVICE_ID};\n"
    "// Generated key.")
(tx_dir / "secrets.h").write_text(test_secrets)

print(f"built  {tx_dir.relative_to(ROOT)}/  (+{tx_delta} lines, DEVICE_ID {TEST_DEVICE_ID})")
print(f"built  {rx_dir.relative_to(ROOT)}/  (+{rx_delta} lines)")
print("\nproduction sketches were read only, never written.")
