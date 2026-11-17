// RFID -> LoRa transmitter with stop-and-wait ACK and AES-256-GCM.
//
// Reads an RFID UID, encrypts it, sends it over LoRa, and waits for an
// authenticated ACK that only arrives once the row is committed to Supabase.
// Unacknowledged scans stay queued and are retried; nothing is dropped silently.

#include <SPI.h>
#include <MFRC522.h>
#include <LoRa.h>
#include <Preferences.h>
#include <mbedtls/gcm.h>   // ships with the ESP32 core -- nothing to install
#include "secrets.h"       // sits next to this file; copy secrets.h.example if missing

// ---------- pins ----------
#define RFID_SS    5
#define RFID_RST   22
#define LORA_SS    4
#define LORA_RST   14
#define LORA_DIO0  26
#define LED_PIN    2          // built-in on most ESP32 devkits

// Band test: RA-01/RA-02 modules are 433 MHz parts; only the -H variants are
// 868. Must match the receiver.
#define LORA_FREQ  433E6
#define LORA_SYNC  0x12

// ---------- protocol ----------
#define PROTO_VERSION 0x01
#define TYPE_DATA     0x01
#define TYPE_ACK      0x02

#define HDR_LEN       14      // version|type|device_id[4]|counter[8], sent as AAD
#define TAG_LEN       16
#define MAX_UID       10
#define ACK_LEN       (HDR_LEN + 1 + TAG_LEN)   // 31

#define ACK_TIMEOUT_MS   3000    // must cover the full laptop -> Supabase -> laptop trip
#define MAX_TRIES        3
#define LONG_RETRY_MS    30000   // after tries are exhausted, back off and keep scanning
#define UID_DEBOUNCE_MS  5000
#define QUEUE_LEN        8
#define COUNTER_RESERVE  1000    // NVS writes once per this many scans

MFRC522 rfid(RFID_SS, RFID_RST);
Preferences prefs;

// ---------- nonce counter ----------
// Persisted as a high-water mark, not per-scan: on boot we jump forward by
// COUNTER_RESERVE so an unclean power cut can never replay a nonce. Reusing a
// GCM nonce under one key leaks the keystream and permits tag forgery, so this
// is the one piece of state that must survive everything.
static uint64_t counter;
static uint64_t reserveEnd;

static uint64_t nextCounter() {
  uint64_t c = counter++;
  if (counter >= reserveEnd) {
    reserveEnd = counter + COUNTER_RESERVE;
    prefs.putULong64("ctr_hw", reserveEnd);
  }
  return c;
}

// ---------- crypto ----------
// nonce[0] = type, nonce[1..4] = device_id, nonce[5..11] = low 7 bytes of counter.
// The type byte is what keeps a DATA frame and its ACK -- same key, same counter --
// from colliding on a nonce.
static void makeNonce(uint8_t type, uint64_t ctr, uint8_t nonce[12]) {
  uint32_t dev = DEVICE_ID;
  nonce[0] = type;
  memcpy(nonce + 1, &dev, 4);   // ESP32 is little-endian
  memcpy(nonce + 5, &ctr, 7);
}

static void writeHeader(uint8_t *f, uint8_t type, uint64_t ctr) {
  uint32_t dev = DEVICE_ID;
  f[0] = PROTO_VERSION;
  f[1] = type;
  memcpy(f + 2, &dev, 4);
  memcpy(f + 6, &ctr, 8);
}

// Returns total frame length, or 0 on failure.
static size_t sealData(const uint8_t *uid, uint8_t uidLen, uint64_t ctr, uint8_t *out) {
  uint8_t nonce[12];
  writeHeader(out, TYPE_DATA, ctr);
  makeNonce(TYPE_DATA, ctr, nonce);

  mbedtls_gcm_context gcm;
  mbedtls_gcm_init(&gcm);
  int rc = mbedtls_gcm_setkey(&gcm, MBEDTLS_CIPHER_ID_AES, PSK_DATA, 256);
  if (rc == 0) {
    rc = mbedtls_gcm_crypt_and_tag(&gcm, MBEDTLS_GCM_ENCRYPT, uidLen,
                                   nonce, sizeof(nonce),
                                   out, HDR_LEN,              // AAD = the header
                                   uid, out + HDR_LEN,
                                   TAG_LEN, out + HDR_LEN + uidLen);
  }
  mbedtls_gcm_free(&gcm);
  return rc == 0 ? HDR_LEN + uidLen + TAG_LEN : 0;
}

// Verifies an ACK really came from the laptop AND refers to the frame we sent.
// Without the counter check a replayed old ACK would convince us a scan was
// logged when it never reached the database.
static bool openAck(const uint8_t *f, size_t len, uint64_t expectCtr, uint8_t *status) {
  if (len != ACK_LEN || f[0] != PROTO_VERSION || f[1] != TYPE_ACK) return false;

  uint32_t dev; uint64_t ctr;
  memcpy(&dev, f + 2, 4);
  memcpy(&ctr, f + 6, 8);
  if (dev != DEVICE_ID || ctr != expectCtr) return false;

  uint8_t nonce[12];
  makeNonce(TYPE_ACK, ctr, nonce);

  mbedtls_gcm_context gcm;
  mbedtls_gcm_init(&gcm);
  int rc = mbedtls_gcm_setkey(&gcm, MBEDTLS_CIPHER_ID_AES, PSK_DATA, 256);
  if (rc == 0) {
    rc = mbedtls_gcm_auth_decrypt(&gcm, 1,
                                  nonce, sizeof(nonce),
                                  f, HDR_LEN,
                                  f + HDR_LEN + 1, TAG_LEN,
                                  f + HDR_LEN, status);
  }
  mbedtls_gcm_free(&gcm);
  return rc == 0;
}

// Known-answer test, shared byte-for-byte with gateway/test_crypto.py. A silent
// disagreement between mbedtls and Python here would look like "the radio is
// flaky" at 2am, so fail loudly at boot instead.
static void selfTest() {
  const uint8_t key[32] = {
    0x00,0x01,0x02,0x03,0x04,0x05,0x06,0x07, 0x08,0x09,0x0a,0x0b,0x0c,0x0d,0x0e,0x0f,
    0x10,0x11,0x12,0x13,0x14,0x15,0x16,0x17, 0x18,0x19,0x1a,0x1b,0x1c,0x1d,0x1e,0x1f };
  const uint8_t pt[4]  = { 0xde, 0xad, 0xbe, 0xef };
  const uint8_t want[20] = {
    0xff,0x6e,0x46,0x6b,                                              // ciphertext
    0xe9,0x6f,0x39,0xd4,0x0c,0xe7,0x99,0x1d,0xf9,0xd6,0x77,0xd6,0x4c,0x75,0x31,0xdf };

  uint8_t nonce[12] = { 0x01, 0x07,0x00,0x00,0x00, 0xd2,0x04,0x00,0x00,0x00,0x00,0x00 };
  uint8_t aad[14]   = { 0x01,0x01, 0x07,0x00,0x00,0x00, 0xd2,0x04,0x00,0x00,0x00,0x00,0x00,0x00 };
  uint8_t got[20];

  mbedtls_gcm_context gcm;
  mbedtls_gcm_init(&gcm);
  mbedtls_gcm_setkey(&gcm, MBEDTLS_CIPHER_ID_AES, key, 256);
  int rc = mbedtls_gcm_crypt_and_tag(&gcm, MBEDTLS_GCM_ENCRYPT, sizeof(pt),
                                     nonce, sizeof(nonce), aad, sizeof(aad),
                                     pt, got, TAG_LEN, got + sizeof(pt));
  mbedtls_gcm_free(&gcm);

  if (rc != 0 || memcmp(got, want, sizeof(want)) != 0) {
    Serial.println("FATAL: AES-GCM self-test failed -- crypto does not match the gateway.");
    while (1) { digitalWrite(LED_PIN, !digitalRead(LED_PIN)); delay(100); }
  }
  Serial.println("AES-GCM self-test OK");
}

// ---------- pending scans ----------
struct Scan {
  uint8_t  uid[MAX_UID];
  uint8_t  uidLen;
  uint64_t ctr;
  uint8_t  tries;
};

static Scan    queue[QUEUE_LEN];
static uint8_t qHead = 0, qCount = 0;
static uint32_t nextAttempt = 0;
static bool     waitingAck  = false;
static uint32_t ackDeadline = 0;

// No helper returning Scan* here on purpose: the Arduino preprocessor injects
// auto-generated prototypes above the struct definition, so any function whose
// *signature* names Scan fails to compile. Locals are fine; signatures are not.

static void pop() {
  qHead = (qHead + 1) % QUEUE_LEN;
  qCount--;
}

static void push(const uint8_t *uid, uint8_t uidLen) {
  if (qCount == QUEUE_LEN) {
    Serial.println("WARN: queue full, dropping oldest unsent scan");
    pop();
  }
  Scan *s = &queue[(qHead + qCount) % QUEUE_LEN];
  memcpy(s->uid, uid, uidLen);
  s->uidLen = uidLen;
  s->ctr    = nextCounter();   // assigned once; retries reuse it so the DB can dedupe
  s->tries  = 0;
  qCount++;
}

// ponytail: RAM ring buffer, so queued scans are lost on a power cut. The gate
// unit is mains-powered; move this to NVS if that stops being true.

static void blink(int n) {
  for (int i = 0; i < n; i++) {
    digitalWrite(LED_PIN, HIGH); delay(80);
    digitalWrite(LED_PIN, LOW);  delay(120);
  }
}

static void uidToHex(const uint8_t *uid, uint8_t len, char *buf) {
  for (uint8_t i = 0; i < len; i++) sprintf(buf + i * 2, "%02x", uid[i]);
  buf[len * 2] = '\0';
}

static void sendHead() {
  Scan *s = &queue[qHead];
  uint8_t frame[HDR_LEN + MAX_UID + TAG_LEN];
  size_t len = sealData(s->uid, s->uidLen, s->ctr, frame);
  if (len == 0) { Serial.println("ERROR: encryption failed"); pop(); return; }

  LoRa.beginPacket();
  LoRa.write(frame, len);
  LoRa.endPacket();

  char hex[MAX_UID * 2 + 1];
  uidToHex(s->uid, s->uidLen, hex);
  Serial.printf("TX uid=%s ctr=%llu try=%u\n", hex, s->ctr, s->tries + 1);

  s->tries++;
  waitingAck  = true;
  ackDeadline = millis() + ACK_TIMEOUT_MS;
}

// Shared by both failure paths -- a timeout and a 0x02 "database error" ACK are
// the same situation, so both must respect MAX_TRIES. Checking it only on the
// timeout path lets a persistent DB error retry forever at full speed.
static void scheduleRetry() {
  Scan *s = &queue[qHead];
  waitingAck = false;

  if (s->tries >= MAX_TRIES) {
    Serial.printf("FAILED after %u tries, ctr=%llu -- still queued, backing off\n",
                  s->tries, s->ctr);
    s->tries = 0;
    nextAttempt = millis() + LONG_RETRY_MS;
    blink(5);
  } else {
    nextAttempt = millis() + 1000UL * s->tries;   // 1s, then 2s
  }
}

static void onAck(uint8_t status) {
  switch (status) {
    case 0x00: Serial.println("ACK: committed");                     blink(1); break;
    case 0x01: Serial.println("ACK: already committed (duplicate)");  blink(2); break;
    case 0x03: Serial.println("ACK: committed, TAG NOT REGISTERED");  blink(3); break;
    case 0x04: Serial.println("ACK: enroll mode -- NOT logged");      blink(4); break;
    case 0x02:
      Serial.println("ACK: database error, will retry");
      scheduleRetry();
      return;
    default:
      Serial.printf("ACK: unknown status 0x%02x, treating as retry\n", status);
      scheduleRetry();
      return;
  }
  pop();
  waitingAck = false;
  nextAttempt = 0;
}

static void pollAck() {
  int n = LoRa.parsePacket();
  if (n <= 0) {
    if ((int32_t)(millis() - ackDeadline) >= 0) scheduleRetry();
    return;
  }

  uint8_t buf[ACK_LEN];
  size_t got = 0;
  while (LoRa.available() && got < sizeof(buf)) buf[got++] = LoRa.read();
  while (LoRa.available()) LoRa.read();

  uint8_t status;
  if (openAck(buf, got, queue[qHead].ctr, &status)) onAck(status);
  // else: not our ACK, or forged. Ignore and keep waiting for the real one.
}

static void pollCard() {
  static uint8_t  lastUid[MAX_UID];
  static uint8_t  lastLen = 0;
  static uint32_t lastAt  = 0;

  if (!rfid.PICC_IsNewCardPresent() || !rfid.PICC_ReadCardSerial()) return;

  uint8_t len = rfid.uid.size > MAX_UID ? MAX_UID : rfid.uid.size;
  bool same = (len == lastLen) && memcmp(rfid.uid.uidByte, lastUid, len) == 0;
  rfid.PICC_HaltA();

  if (same && (millis() - lastAt) < UID_DEBOUNCE_MS) return;

  memcpy(lastUid, rfid.uid.uidByte, len);
  lastLen = len;
  lastAt  = millis();

  push(rfid.uid.uidByte, len);
  Serial.println("card read, queued");
}

void setup() {
  Serial.begin(115200);
  pinMode(LED_PIN, OUTPUT);
  delay(200);

  selfTest();

  prefs.begin("vlog", false);
  counter    = prefs.getULong64("ctr_hw", 0) + COUNTER_RESERVE;
  reserveEnd = counter + COUNTER_RESERVE;
  prefs.putULong64("ctr_hw", reserveEnd);
  Serial.printf("counter starts at %llu\n", counter);

  // Shared VSPI bus. Both libraries drive their own CS per transaction, but
  // that only holds once both are initialised -- until LoRa.begin() runs, its
  // NSS floats and the SX1276 will answer SPI traffic meant for the MFRC522,
  // fighting it on MISO. Park both chip selects HIGH before touching either.
  pinMode(LORA_SS, OUTPUT); digitalWrite(LORA_SS, HIGH);
  pinMode(RFID_SS, OUTPUT); digitalWrite(RFID_SS, HIGH);

  SPI.begin(18, 19, 23);

  rfid.PCD_Init();
  delay(50);
  uint8_t ver = rfid.PCD_ReadRegister(MFRC522::VersionReg);
  if (ver == 0x00 || ver == 0xFF) {
    Serial.printf("ERROR: MFRC522 not responding (VersionReg=0x%02x) -- check wiring/3V3\n", ver);
  } else {
    Serial.printf("RFID initialized (MFRC522 version 0x%02x)\n", ver);
  }

  LoRa.setPins(LORA_SS, LORA_RST, LORA_DIO0);
  if (!LoRa.begin(LORA_FREQ)) {
    Serial.println("FATAL: LoRa init failed");
    while (1) { digitalWrite(LED_PIN, !digitalRead(LED_PIN)); delay(500); }
  }
  LoRa.setSyncWord(LORA_SYNC);
  LoRa.enableCrc();               // off by default; drops corrupt frames before GCM sees them
  Serial.println("LoRa initialized -- present a card");
}

void loop() {
  if (waitingAck) {
    pollAck();
    return;                       // stop-and-wait: one frame in flight at a time
  }

  if (qCount && (int32_t)(millis() - nextAttempt) >= 0) {
    sendHead();
    return;
  }

  pollCard();
}
