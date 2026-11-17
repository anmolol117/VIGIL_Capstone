#!/usr/bin/env python3
"""Generates VIGIL_Methodology.pdf -- the project methodology document.

Plain black-on-white, no colour. Re-run after changing the system:
    python3 make_report.py
"""

from reportlab.lib import colors
from reportlab.lib.enums import TA_JUSTIFY
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.platypus import (BaseDocTemplate, Frame, KeepTogether, PageBreak,
                                PageTemplate, Paragraph, Spacer, Table, TableStyle)

# ReportLab's built-in fonts use WinAnsi encoding: no arrows, no >=, no unicode
# sub/superscripts. Everything below stays ASCII on purpose.

TITLE = "RFID and LoRa Vehicle Logging System"
SUBTITLE = "End-to-End Design and Methodology"

styles = getSampleStyleSheet()
S = {
    "title": ParagraphStyle("t", parent=styles["Title"], fontName="Helvetica-Bold",
                            fontSize=20, leading=25, textColor=colors.black,
                            spaceAfter=2),
    "sub": ParagraphStyle("s", parent=styles["Normal"], fontName="Helvetica",
                          fontSize=12, leading=16, alignment=1,
                          textColor=colors.black, spaceAfter=18),
    "h1": ParagraphStyle("h1", parent=styles["Heading1"], fontName="Helvetica-Bold",
                         fontSize=14, leading=18, textColor=colors.black,
                         spaceBefore=16, spaceAfter=7),
    "h2": ParagraphStyle("h2", parent=styles["Heading2"], fontName="Helvetica-Bold",
                         fontSize=11.5, leading=15, textColor=colors.black,
                         spaceBefore=11, spaceAfter=5),
    "body": ParagraphStyle("b", parent=styles["Normal"], fontName="Helvetica",
                           fontSize=9.8, leading=14, alignment=TA_JUSTIFY,
                           textColor=colors.black, spaceAfter=7),
    "bullet": ParagraphStyle("bu", parent=styles["Normal"], fontName="Helvetica",
                             fontSize=9.8, leading=14, leftIndent=12,
                             bulletIndent=2, textColor=colors.black, spaceAfter=3),
    "code": ParagraphStyle("c", parent=styles["Normal"], fontName="Courier",
                           fontSize=8.2, leading=11, leftIndent=8,
                           textColor=colors.black, spaceAfter=7),
    "caption": ParagraphStyle("cap", parent=styles["Normal"], fontName="Helvetica-Oblique",
                              fontSize=8.5, leading=11, textColor=colors.black,
                              spaceBefore=3, spaceAfter=10),
}

GRID = TableStyle([
    ("FONT", (0, 0), (-1, 0), "Helvetica-Bold", 8.8),
    ("FONT", (0, 1), (-1, -1), "Helvetica", 8.8),
    ("TEXTCOLOR", (0, 0), (-1, -1), colors.black),
    ("LINEBELOW", (0, 0), (-1, 0), 0.9, colors.black),
    ("LINEBELOW", (0, 1), (-1, -2), 0.25, colors.HexColor("#999999")),
    ("LINEABOVE", (0, 0), (-1, 0), 0.9, colors.black),
    ("LINEBELOW", (0, -1), (-1, -1), 0.9, colors.black),
    ("VALIGN", (0, 0), (-1, -1), "TOP"),
    ("TOPPADDING", (0, 0), (-1, -1), 4),
    ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
    ("LEFTPADDING", (0, 0), (-1, -1), 5),
])


def P(t, s="body"):
    return Paragraph(t, S[s])


def B(t):
    return Paragraph(t, S["bullet"], bulletText="•")


def code(lines):
    txt = "<br/>".join(l.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
                       .replace(" ", "&nbsp;") for l in lines)
    return Paragraph(txt, S["code"])


CELL = ParagraphStyle("cell", fontName="Helvetica", fontSize=8.8, leading=11.5,
                      textColor=colors.black)
CELL_B = ParagraphStyle("cellb", parent=CELL, fontName="Helvetica-Bold")


def table(rows, widths, caption=None, bold_rows=()):
    # Every string cell becomes a Paragraph. ReportLab does not wrap bare
    # strings in table cells -- they silently overflow into the next column --
    # and a length threshold cannot catch it, since whether text fits depends on
    # the column's width, not its character count.
    bold = set(bold_rows) | {0}          # row 0 is the column header
    rows = [[Paragraph(c, CELL_B if i in bold else CELL) if isinstance(c, str) else c
             for c in row] for i, row in enumerate(rows)]
    t = Table(rows, colWidths=widths, hAlign="LEFT", repeatRows=1)
    t.setStyle(GRID)
    for r in bold_rows:                  # group headers get extra breathing room
        t.setStyle(TableStyle([("TOPPADDING", (0, r), (-1, r), 7)]))
    return [t, P(caption, "caption")] if caption else [t]


def footer(canvas, doc):
    canvas.saveState()
    canvas.setFont("Helvetica", 8)
    canvas.setFillColor(colors.black)
    canvas.drawString(20 * mm, 12 * mm, TITLE)
    canvas.drawRightString(A4[0] - 20 * mm, 12 * mm, "Page %d" % doc.page)
    canvas.setLineWidth(0.4)
    canvas.line(20 * mm, 15 * mm, A4[0] - 20 * mm, 15 * mm)
    canvas.restoreState()


story = []

# ---------------------------------------------------------------- title
story += [Spacer(1, 30 * mm), P(TITLE, "title"), P(SUBTITLE, "sub")]
story += table(
    [["Document", "Design and methodology report"],
     ["Radio link", "LoRa, 433 MHz, SX1276 transceivers"],
     ["Identification", "MFRC522 13.56 MHz RFID reader"],
     ["Cryptography", "AES-256-GCM, authenticated encryption"],
     ["Transport", "Stop-and-wait ARQ with end-to-end acknowledgement"],
     ["Datastore", "PostgreSQL (Supabase)"]],
    [38 * mm, 108 * mm])
story.append(Spacer(1, 6 * mm))
story.append(P(
    "This document describes the complete methodology of the vehicle logging system: the "
    "transmitter and receiver circuits and their pin assignments, the end-to-end "
    "communication pipeline from an RFID card presentation to a committed database row, "
    "the encryption scheme, the acknowledgement mechanism, and the database schema "
    "including the two-card method used to determine direction of travel."))
story.append(PageBreak())

# ---------------------------------------------------------------- 1
story.append(P("1. System Overview", "h1"))
story.append(P(
    "The system records vehicles entering and leaving a campus gate. A vehicle carries "
    "passive RFID cards. When a card is presented at the gate, the transmitter unit reads "
    "its unique identifier (UID), encrypts it, and sends it over a long-range LoRa radio "
    "link to a receiver unit. The receiver passes the still-encrypted message to a laptop "
    "over USB serial. The laptop decrypts it, resolves the card to a registered vehicle, "
    "and writes a row to an online PostgreSQL database. Only once the database confirms "
    "the write does an acknowledgement travel back along the same path to the transmitter."))
story.append(P(
    "The design goal was to replace an earlier fire-and-forget scheme, in which the "
    "transmitter broadcast the UID as plain text and never learned whether it arrived. "
    "That earlier arrangement had two defects: a lost packet meant a vehicle entered with "
    "no record and nothing detected the loss, and the identifier travelled over the air "
    "unprotected, so it could be both read and forged by anyone with comparable radio "
    "hardware."))

story.append(P("1.1 Signal Path", "h2"))
story.append(code([
    "  [Transmitter ESP32]        [Receiver ESP32]      [Laptop]        [Database]",
    "",
    "   RFID card read",
    "   encrypt (AES-GCM)",
    "        |",
    "        |----- LoRa 433 MHz ----->|",
    "                                  |--- USB serial --->|",
    "                                                      |  decrypt",
    "                                                      |  resolve card",
    "                                                      |----- INSERT ---->|",
    "                                                      |<---- confirmed --|",
    "                                                      |  build ACK",
    "                                  |<-- USB serial ----|",
    "        |<---- LoRa 433 MHz ------|",
    "   verify ACK",
    "   confirm to operator",
]))
story.append(P(
    "Figure 1. The receiver holds no cryptographic keys and performs no decryption. It is "
    "a bidirectional relay between the radio link and the serial link, so the payload "
    "remains encrypted from the transmitter all the way to the laptop.", "caption"))

story.append(P("1.2 Division of Responsibility", "h2"))
story += table(
    [["Unit", "Responsibility"],
     ["Transmitter ESP32", "Reads the RFID card, encrypts the UID, transmits it, waits for "
                           "and verifies the acknowledgement, indicates the outcome."],
     ["Receiver ESP32", "Relays radio frames to serial and serial frames to radio. Holds no "
                        "keys; performs no interpretation of the payload."],
     ["Laptop (gateway)", "Decrypts and authenticates the frame, resolves the card to a "
                          "vehicle and a direction, writes to the database, builds the "
                          "acknowledgement."],
     ["Database", "Stores the vehicle registry, the card assignments, and the movement log. "
                  "Confirmation of the write is what authorises the acknowledgement."]],
    [34 * mm, 112 * mm])

story.append(PageBreak())

# ---------------------------------------------------------------- 2
story.append(P("2. Hardware Design", "h1"))
story.append(P(
    "Both units are built on ESP32 development boards. The transmitter carries two "
    "peripherals on a shared SPI bus; the receiver carries one. All peripherals operate at "
    "3.3 V and are powered from the ESP32 3V3 rail."))

story.append(P("2.1 Shared SPI Bus", "h2"))
story.append(P(
    "The ESP32 hardware SPI (VSPI) peripheral is used for all devices. The three bus "
    "signals are common to every peripheral; each device is selected individually by its "
    "own chip-select line. This allows the RFID reader and the LoRa transceiver on the "
    "transmitter to share one bus with only one additional GPIO per device."))
story += table(
    [["Bus signal", "ESP32 GPIO", "Direction", "Shared by"],
     ["SCK (clock)", "GPIO 18", "Output", "All SPI peripherals"],
     ["MISO (data in)", "GPIO 19", "Input", "All SPI peripherals"],
     ["MOSI (data out)", "GPIO 23", "Output", "All SPI peripherals"]],
    [32 * mm, 26 * mm, 24 * mm, 64 * mm],
    "Table 1. VSPI bus assignment, identical on both units.")

story.append(P("2.2 Transmitter Unit", "h2"))
story.append(P(
    "The transmitter comprises an ESP32, an MFRC522 13.56 MHz RFID reader module, and an "
    "SX1276-based 433 MHz LoRa transceiver module. It is the only unit that touches the "
    "cards and the only unit that holds the encryption key."))
story += table(
    [["Module pin", "Connects to", "Function"],
     ["MFRC522 RFID READER", "", ""],
     ["SDA / SS", "GPIO 5", "Chip select for the RFID reader"],
     ["SCK", "GPIO 18", "Shared SPI clock"],
     ["MOSI", "GPIO 23", "Shared SPI data out"],
     ["MISO", "GPIO 19", "Shared SPI data in"],
     ["RST", "GPIO 22", "Reset"],
     ["IRQ", "not connected", "Interrupt output, unused (card presence is polled)"],
     ["3.3V", "3V3", "Supply. This module is not 5 V tolerant"],
     ["GND", "GND", "Common ground"],
     ["SX1276 LORA TRANSCEIVER", "", ""],
     ["NSS", "GPIO 4", "Chip select for the radio"],
     ["SCK", "GPIO 18", "Shared SPI clock"],
     ["MOSI", "GPIO 23", "Shared SPI data out"],
     ["MISO", "GPIO 19", "Shared SPI data in"],
     ["RESET", "GPIO 14", "Reset"],
     ["DIO0", "GPIO 26", "Transmit-done / receive-done interrupt line"],
     ["VCC", "3V3", "Supply"],
     ["GND", "GND", "Common ground"],
     ["ANT", "433 MHz antenna", "Quarter-wave whip, approx. 165 mm"],
     ["INDICATOR", "", ""],
     ["Status LED", "GPIO 2", "On-board LED; blink count reports the outcome of a scan"]],
    [40 * mm, 32 * mm, 74 * mm],
    "Table 2. Transmitter unit connections. The two chip-select lines (GPIO 5 and GPIO 4) "
    "are what allow both peripherals to coexist on one bus.",
    bold_rows=(1, 10, 20))

story.append(PageBreak())

story.append(P("2.3 Receiver Unit", "h2"))
story.append(P(
    "The receiver comprises an ESP32 and a single SX1276 LoRa transceiver. It has no RFID "
    "reader, since it never reads cards, and no cryptographic material. It is connected to "
    "the laptop by USB, which also supplies its power."))
story += table(
    [["Module pin", "Connects to", "Function"],
     ["SX1276 LORA TRANSCEIVER", "", ""],
     ["NSS", "GPIO 5", "Chip select for the radio"],
     ["SCK", "GPIO 18", "Shared SPI clock"],
     ["MOSI", "GPIO 23", "Shared SPI data out"],
     ["MISO", "GPIO 19", "Shared SPI data in"],
     ["RESET", "GPIO 22", "Reset"],
     ["DIO0", "GPIO 21", "Transmit-done / receive-done interrupt line"],
     ["VCC", "3V3", "Supply"],
     ["GND", "GND", "Common ground"],
     ["ANT", "433 MHz antenna", "Quarter-wave whip, approx. 165 mm"],
     ["HOST LINK", "", ""],
     ["USB", "Laptop", "Serial link at 115200 baud, and power"]],
    [40 * mm, 32 * mm, 74 * mm],
    "Table 3. Receiver unit connections. Note that the chip-select and DIO0 assignments "
    "differ from the transmitter; the two units are not interchangeable without changing "
    "the pin definitions.",
    bold_rows=(1, 11))

story.append(P("2.4 Radio Configuration", "h2"))
story.append(P(
    "Both radios must agree on every parameter below or no communication occurs. The "
    "operating frequency was determined by measurement rather than assumption: the modules "
    "in use are 433 MHz parts, and operating them in the 868 MHz band produced a received "
    "signal strength close to the noise floor even at bench distance."))
story += table(
    [["Parameter", "Value", "Note"],
     ["Frequency", "433 MHz", "Set to match the physical modules fitted"],
     ["Sync word", "0x12", "Rejects unrelated LoRa traffic on the same channel"],
     ["Packet CRC", "Enabled", "Corrupted frames discarded by the radio itself"],
     ["Serial baud", "115200", "Receiver to laptop"],
     ["Typical RSSI", "-46 to -53 dBm", "Measured at bench distance with antennas fitted"]],
    [32 * mm, 32 * mm, 82 * mm],
    "Table 4. Radio and link parameters.")

story.append(PageBreak())

# ---------------------------------------------------------------- 3
story.append(P("3. Communication Pipeline", "h1"))
story.append(P(
    "A single card presentation produces the following sequence. The transmitter handles "
    "one card at a time and does not begin another until the current one is resolved."))

steps = [
    ("1", "Card detection", "The transmitter polls the MFRC522 for a card in the field. On "
     "detection it reads the card's UID, typically four or seven bytes."),
    ("2", "Encryption", "The UID is encrypted on the ESP32 using AES-256-GCM with a "
     "pre-shared key. This produces ciphertext plus a 16-byte authentication tag. A frame "
     "header is attached, carrying the protocol version, message type, device identifier "
     "and a message counter."),
    ("3", "Radio transmission", "The complete frame is transmitted over LoRa. The "
     "transmitter then switches its radio to receive and waits for an acknowledgement."),
    ("4", "Relay to host", "The receiver detects the incoming frame and writes it to the "
     "USB serial link as a single hexadecimal text line, together with the measured signal "
     "strength and signal-to-noise ratio. It does not attempt to interpret the payload."),
    ("5", "Decryption and verification", "The laptop decodes the line and decrypts the "
     "payload. The authentication tag is checked at this point; if it does not verify, the "
     "frame is discarded and no acknowledgement is produced. A frame that fails this check "
     "was either corrupted or not produced by a holder of the key."),
    ("6", "Resolution", "The recovered UID is looked up in the card table, which returns "
     "the vehicle it belongs to and the direction that card represents."),
    ("7", "Database write", "A movement row is inserted, recording the card, the vehicle, "
     "the direction, the signal quality and the timestamp."),
    ("8", "Acknowledgement", "Only after the database confirms the insertion does the "
     "laptop construct an acknowledgement. It carries a status code and echoes the counter "
     "of the frame being acknowledged, and is itself encrypted and authenticated."),
    ("9", "Return path", "The acknowledgement travels back over serial to the receiver, "
     "which relays it over LoRa to the transmitter."),
    ("10", "Confirmation", "The transmitter verifies the acknowledgement's authentication "
     "tag and checks that the echoed counter matches the frame it sent. Only then does it "
     "treat the scan as complete and indicate success to the operator."),
]
story += table([["#", "Stage", "Description"]] + [[a, b, c] for a, b, c in steps],
               [8 * mm, 30 * mm, 108 * mm],
               "Table 5. The ten stages of a single card presentation.")

story.append(P(
    "The significance of stage 8 is that the acknowledgement is not generated on receipt of "
    "the radio frame. It is generated on confirmation of the database write. A successful "
    "indication at the gate therefore means the movement has been durably recorded, not "
    "merely that the radio transmission was heard."))

story.append(PageBreak())

# ---------------------------------------------------------------- 4
story.append(P("4. Frame Format and Encryption", "h1"))

story.append(P("4.1 Placement of the Cryptography", "h2"))
story.append(P(
    "Encryption is performed on the transmitter ESP32 and decryption on the laptop. The "
    "receiver, situated between them, never holds the key. The consequence is that the "
    "identifier is protected across both the radio hop and the serial hop, and an attacker "
    "who obtained the receiver hardware would gain nothing that could decrypt traffic."))
story.append(P(
    "The algorithm is AES-256-GCM, an authenticated encryption mode. It provides "
    "confidentiality and integrity in a single operation: the receiver of a message learns "
    "both that the contents are unreadable to third parties and that they have not been "
    "altered in transit. On the ESP32 this is performed by the mbedTLS library included in "
    "the platform, which uses the chip's AES hardware."))

story.append(P("4.2 Data Frame", "h2"))
story.append(P(
    "The header is transmitted in clear text but is included in the authentication "
    "calculation, so it cannot be altered without invalidating the tag. It is readable "
    "because the receiver must inspect the message type to route the frame, and the laptop "
    "requires the counter before it can begin decryption."))
story += table(
    [["Offset", "Length", "Field", "Description"],
     ["0", "1 byte", "Version", "Protocol version"],
     ["1", "1 byte", "Type", "0x01 identifies a data frame"],
     ["2", "4 bytes", "Device ID", "Identifies which gate unit sent the frame"],
     ["6", "8 bytes", "Counter", "Monotonic message counter"],
     ["14", "n bytes", "Ciphertext", "The encrypted RFID UID"],
     ["14 + n", "16 bytes", "Tag", "Authentication tag over header and ciphertext"]],
    [17 * mm, 20 * mm, 26 * mm, 83 * mm],
    "Table 6. Data frame, transmitter to laptop. For a four-byte UID the frame is 34 bytes.")

story.append(P("4.3 Acknowledgement Frame", "h2"))
story += table(
    [["Offset", "Length", "Field", "Description"],
     ["0", "1 byte", "Version", "Protocol version"],
     ["1", "1 byte", "Type", "0x02 identifies an acknowledgement"],
     ["2", "4 bytes", "Device ID", "Echoed from the frame being acknowledged"],
     ["6", "8 bytes", "Counter", "Echoed from the frame being acknowledged"],
     ["14", "1 byte", "Status", "Encrypted outcome code"],
     ["15", "16 bytes", "Tag", "Authentication tag"]],
    [17 * mm, 20 * mm, 26 * mm, 83 * mm],
    "Table 7. Acknowledgement frame, laptop to transmitter. Total length 31 bytes.")

story.append(P(
    "The counter is echoed so that the transmitter can confirm the acknowledgement refers "
    "to the frame currently outstanding, rather than to some earlier exchange."))

story.append(P("4.4 Initialisation Vector Construction", "h2"))
story.append(P(
    "AES-GCM requires a 12-byte initialisation vector, or nonce, that is never repeated "
    "under a given key. It is assembled deterministically from values both ends already "
    "possess, so it does not need to be transmitted:"))
story += table(
    [["Bytes", "Source", "Purpose"],
     ["0", "Message type", "Separates a data frame from its own acknowledgement, which "
      "would otherwise share a counter"],
     ["1 to 4", "Device identifier", "Separates one gate unit from another"],
     ["5 to 11", "Message counter", "Separates successive messages from one unit"]],
    [22 * mm, 34 * mm, 90 * mm],
    "Table 8. Nonce construction.")
story.append(P(
    "The message counter is held in the ESP32's non-volatile storage so that it continues "
    "to advance across power cycles."))

story.append(PageBreak())

# ---------------------------------------------------------------- 5
story.append(P("5. Acknowledgement Scheme", "h1"))
story.append(P(
    "The transport follows a stop-and-wait automatic repeat request discipline. One frame "
    "is in flight at a time. After transmitting, the unit switches to receive and waits for "
    "a bounded interval. If a valid acknowledgement arrives, the scan is complete. If the "
    "interval expires, the same frame is retransmitted, carrying the same counter."))
story.append(P(
    "Retransmission preserving the counter is what makes a repeat safe. The database "
    "treats the combination of device identifier and counter as unique, so a frame that was "
    "in fact recorded the first time, but whose acknowledgement was lost on the return "
    "path, does not produce a second record. The laptop recognises the condition and "
    "returns a status indicating the movement was already recorded, which the transmitter "
    "accepts as success."))
story.append(P(
    "Scans that cannot be delivered are retained by the transmitter and retried, rather "
    "than discarded, so a temporary interruption of the laptop or the network delays a "
    "record instead of losing it."))

story += table(
    [["Status", "Meaning", "Indication at the gate"],
     ["0x00", "Movement recorded", "One blink"],
     ["0x01", "Already recorded (a repeat)", "Two blinks"],
     ["0x02", "Database unavailable", "Retransmission"],
     ["0x03", "Recorded, card not registered", "Three blinks"]],
    [18 * mm, 62 * mm, 66 * mm],
    "Table 9. Acknowledgement status codes and the corresponding gate indication.")

# ---------------------------------------------------------------- 6
story.append(P("6. Database Schema", "h1"))
story.append(P(
    "The database separates three concerns: who is registered, which cards they hold, and "
    "what movements have occurred. Registry details are not copied into movement records, "
    "so a change of vehicle particulars does not rewrite historical entries."))

story.append(P("6.1 Determining Direction: The Two-Card Method", "h2"))
story.append(P(
    "A single reader at a single point cannot by itself determine whether a vehicle is "
    "arriving or departing; a card presentation carries no directional information. The "
    "system resolves this by issuing each vehicle two cards. One card denotes entry and the "
    "other denotes exit, and the driver presents the appropriate card. Direction is "
    "therefore recorded data rather than an inference."))
story.append(P(
    "The alternative considered was to infer direction by alternating successive "
    "presentations of a single card: the first presentation an entry, the second an exit, "
    "and so on. This was rejected because it makes direction a function of the entire "
    "preceding history of that card. A single presentation that failed to be recorded, or "
    "one recorded in error, inverts the interpretation of every subsequent presentation, "
    "and the error persists indefinitely with no means of resynchronisation."))
story.append(P(
    "With two cards there is no accumulated state. Each record carries its own direction, "
    "so a mistaken presentation affects one record only and the following presentation is "
    "unaffected. Errors remain independent of one another rather than compounding. A "
    "mistaken record is corrected by amending that single row."))
story.append(P(
    "A further alternative was to derive direction from which gate observed the card, using "
    "the device identifier already present in the frame. This was rejected because the "
    "designation of gates as entry or exit is subject to administrative change, and the "
    "identifier would then no longer be a dependable indicator."))

story.append(PageBreak())

story.append(P("6.2 Vehicle Registry", "h2"))
story.append(P("Holds one row per registered vehicle. Cards are recorded separately, so a "
               "vehicle may be registered before any card is issued to it."))
story.append(code([
    "create table vehicles (",
    "  id            uuid primary key default gen_random_uuid(),",
    "  plate_number  text not null unique,",
    "  vehicle_type  text not null,   -- car, motorcycle, scooter, bus, truck, ...",
    "  vehicle_name  text,            -- model, e.g. 'Maruti Swift'",
    "  owner_name    text not null,",
    "  owner_role    text not null,   -- professor, staff, student, guard, visitor",
    "  active        boolean not null default true,",
    "  registered_at timestamptz not null default now()",
    ");",
]))

story.append(P("6.3 Card Assignments", "h2"))
story.append(P("Maps each physical card to a vehicle and to the direction that card "
               "denotes. A vehicle normally has two rows here: one entry card and one exit "
               "card."))
story.append(code([
    "create table tags (",
    "  rfid_uid   text primary key,        -- card UID as hexadecimal",
    "  vehicle_id uuid not null references vehicles(id) on delete cascade,",
    "  direction  text not null check (direction in ('in','out')),",
    "  issued_at  timestamptz not null default now()",
    ");",
]))
story.append(P(
    "This single table is what the gateway consults on each presentation. One lookup by "
    "card UID returns both the vehicle and the direction, which are then written to the "
    "movement record."))

story.append(P("6.4 Movement Log", "h2"))
story.append(P(
    "One row per card presentation. The card UID is always stored, so that a presentation "
    "of an unrecognised card still produces a record; in that case the vehicle reference "
    "and the direction are left empty, since neither is known."))
story.append(code([
    "create table vehicle_logs (",
    "  id         bigint generated always as identity primary key,",
    "  device_id  int    not null,          -- which gate unit",
    "  counter    bigint not null,          -- message counter from the frame",
    "  rfid_uid   text   not null,          -- always recorded, even if unknown",
    "  vehicle_id uuid   references vehicles(id),",
    "  direction  text   check (direction in ('in','out')),",
    "  rssi       int,                      -- received signal strength, dBm",
    "  snr        real,                     -- signal to noise ratio, dB",
    "  logged_at  timestamptz not null default now(),",
    "  unique (device_id, counter)",
    ");",
]))

story.append(PageBreak())

story.append(P("6.5 Unauthorised Vehicles", "h2"))
story.append(P(
    "Vehicles with no registered card are counted by the gate attendant, who holds a "
    "further pair of cards: one presented for each unauthorised vehicle entering, the other "
    "for each one leaving. These are kept in their own table because the rows count "
    "anonymous vehicles rather than identified ones, and every row shares the same pair of "
    "card identifiers."))
story.append(code([
    "create table unauthorised_tags (",
    "  rfid_uid  text primary key,",
    "  direction text not null check (direction in ('in','out')),",
    "  label     text",
    ");",
    "",
    "create table unauthorised_events (",
    "  id        bigint generated always as identity primary key,",
    "  device_id int    not null,",
    "  counter   bigint not null,",
    "  rfid_uid  text   not null,",
    "  direction text   not null check (direction in ('in','out')),",
    "  rssi      int,",
    "  snr       real,",
    "  logged_at timestamptz not null default now(),",
    "  unique (device_id, counter)",
    ");",
]))
story.append(P(
    "The number of unauthorised vehicles present is a running total: incremented by an "
    "entry presentation and decremented by an exit presentation."))

story.append(P("6.6 Derived Views", "h2"))
story += table(
    [["View", "Purpose"],
     ["vehicle_movements", "Movement records joined to registry details, giving owner, "
      "role, plate number and vehicle for each presentation."],
     ["vehicle_presence", "Current location of each registered vehicle, taken from its most "
      "recent directional record."],
     ["unauthorised_summary", "Running totals of unauthorised entries and exits, and the "
      "number presently on site."]],
    [42 * mm, 104 * mm],
    "Table 10. Views presented to the operator interface.")

# ---------------------------------------------------------------- 7
story.append(P("7. Gateway and Operator Interface", "h1"))
story.append(P(
    "The gateway is a Python program on the laptop. It holds the serial connection to the "
    "receiver, performs decryption and verification, resolves cards against the database, "
    "writes movement records, and constructs acknowledgements. Communication with the "
    "database uses its HTTP interface over TLS."))
story.append(P(
    "The operator interface is a web page served locally. It presents the number of "
    "vehicles currently on site against those absent, the day's traffic, the count of "
    "unauthorised vehicles present, a list of vehicles currently on site, and a searchable "
    "record of all movements filterable by direction and by role. It also displays the time "
    "elapsed since the last presentation, which serves as an indication that the gate "
    "equipment remains in service. The page refreshes automatically."))

story.append(P("8. Summary", "h1"))
story.append(P(
    "The system establishes an encrypted and acknowledged path from a card presented at a "
    "campus gate to a durably recorded row in an online database. Encryption is performed "
    "at the point of reading and decryption only at the host, so the identifier is never "
    "exposed on either the radio or the serial link, and the intermediate relay holds no "
    "key material. Acknowledgement is issued only on confirmation of the database write, so "
    "the indication given at the gate reflects the state of the record rather than the "
    "state of the radio. Direction of travel is carried by the card presented rather than "
    "inferred from history, which keeps any individual error isolated and correctable."))

doc = BaseDocTemplate("VIGIL_Methodology.pdf", pagesize=A4,
                      leftMargin=20 * mm, rightMargin=20 * mm,
                      topMargin=18 * mm, bottomMargin=20 * mm,
                      title=TITLE, author="", subject=SUBTITLE)
doc.addPageTemplates([PageTemplate(
    id="main",
    frames=[Frame(doc.leftMargin, doc.bottomMargin, doc.width, doc.height, id="f")],
    onPage=footer)])
doc.build(story)
print("wrote VIGIL_Methodology.pdf")
