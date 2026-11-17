// LoRa <-> USB serial relay.
//
// Deliberately holds no keys and does no crypto: payloads stay encrypted
// end-to-end from the transmitter to the laptop, opaque even to this hop.
//
//   LoRa in   ->  "R <hex> <rssi> <snr>\n"  out on serial
//   "A <hex>\n" in on serial  ->  LoRa out
//
// Hex lines rather than binary framing so you can watch the whole protocol in
// a plain serial monitor.

#include <SPI.h>
#include <LoRa.h>

#define LORA_SS    5
#define LORA_RST   22
#define LORA_DIO0  21

// Must match the transmitter.
#define LORA_FREQ  433E6
#define LORA_SYNC  0x12

#define MAX_FRAME  64
#define MAX_LINE   (MAX_FRAME * 2 + 8)

static char line[MAX_LINE];
static size_t lineLen = 0;

static int hexNibble(char c) {
  if (c >= '0' && c <= '9') return c - '0';
  if (c >= 'a' && c <= 'f') return c - 'a' + 10;
  if (c >= 'A' && c <= 'F') return c - 'A' + 10;
  return -1;
}

// Returns decoded byte count, or -1 if the hex is malformed.
static int hexDecode(const char *s, size_t n, uint8_t *out, size_t outMax) {
  if (n % 2 || n / 2 > outMax) return -1;
  for (size_t i = 0; i < n; i += 2) {
    int hi = hexNibble(s[i]), lo = hexNibble(s[i + 1]);
    if (hi < 0 || lo < 0) return -1;
    out[i / 2] = (uint8_t)((hi << 4) | lo);
  }
  return n / 2;
}

static void handleLine() {
  if (lineLen < 3 || line[0] != 'A' || line[1] != ' ') return;

  uint32_t t0 = micros();                                    // BENCH
  uint8_t frame[MAX_FRAME];
  int n = hexDecode(line + 2, lineLen - 2, frame, sizeof(frame));
  if (n <= 0) return;

  LoRa.beginPacket();
  LoRa.write(frame, n);
  LoRa.endPacket();
  // parsePacket() in loop() puts the radio back into receive.

  // BENCH: '#' prefix so the gateway treats this as a comment and ignores it.
  Serial.printf("# RELAY,out,%lu\n", micros() - t0);
}

static void pollSerial() {
  while (Serial.available()) {
    char c = Serial.read();
    if (c == '\n' || c == '\r') {
      if (lineLen) { line[lineLen] = '\0'; handleLine(); lineLen = 0; }
    } else if (lineLen < MAX_LINE - 1) {
      line[lineLen++] = c;
    } else {
      lineLen = 0;   // overlong line, resync on the next newline
    }
  }
}

static void pollLoRa() {
  int n = LoRa.parsePacket();
  if (n <= 0) return;

  uint32_t t0 = micros();                                    // BENCH
  int rssi = LoRa.packetRssi();
  float snr = LoRa.packetSnr();

  Serial.print("R ");
  while (LoRa.available()) {
    uint8_t b = LoRa.read();
    if (b < 0x10) Serial.print('0');
    Serial.print(b, HEX);
  }
  Serial.printf(" %d %.1f\n", rssi, snr);

  // BENCH: measured after the line is on the wire, so it includes the ~7 ms
  // the hex-encoded frame costs at 115200 baud.
  Serial.printf("# RELAY,in,%lu\n", micros() - t0);
}

void setup() {
  Serial.begin(115200);
  SPI.begin(18, 19, 23);

  LoRa.setPins(LORA_SS, LORA_RST, LORA_DIO0);
  if (!LoRa.begin(LORA_FREQ)) {
    Serial.println("# FATAL: LoRa init failed");
    while (1);
  }
  LoRa.setSyncWord(LORA_SYNC);
  LoRa.enableCrc();

  Serial.println("# relay ready -- BENCH BUILD, instrumented");
}

void loop() {
  pollLoRa();
  pollSerial();
}
