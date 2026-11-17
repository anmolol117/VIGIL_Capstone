#!/usr/bin/env python3
"""Regenerates the VIGIL paper with the ten content corrections applied.

Prose is reproduced verbatim from "VIGIL Paper draft 1"; only the corrected
claims differ. Grammar and style are deliberately left untouched.
"""

from reportlab.lib import colors
from reportlab.lib.enums import TA_JUSTIFY
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.graphics.shapes import Drawing, Line, Rect, String
from reportlab.platypus import (BaseDocTemplate, Frame, KeepTogether, PageTemplate,
                                Paragraph, Spacer, Table, TableStyle)

TITLE = "VIGIL"
OUT = "/Users/anmol117/Downloads/VIGIL_Paper_draft1_corrected.pdf"

styles = getSampleStyleSheet()
S = {
    "title": ParagraphStyle("t", parent=styles["Title"], fontName="Helvetica-Bold",
                            fontSize=22, leading=27, textColor=colors.black,
                            spaceAfter=20),
    "h1": ParagraphStyle("h1", parent=styles["Heading1"], fontName="Helvetica-Bold",
                         fontSize=13.5, leading=17, textColor=colors.black,
                         spaceBefore=16, spaceAfter=7),
    "h2": ParagraphStyle("h2", parent=styles["Heading2"], fontName="Helvetica-Bold",
                         fontSize=11.5, leading=15, textColor=colors.black,
                         spaceBefore=11, spaceAfter=5),
    "body": ParagraphStyle("b", parent=styles["Normal"], fontName="Helvetica",
                           fontSize=10, leading=14.5, alignment=TA_JUSTIFY,
                           textColor=colors.black, spaceAfter=8),
    "tcap": ParagraphStyle("tc", parent=styles["Normal"], fontName="Helvetica-Bold",
                           fontSize=9.5, leading=13, textColor=colors.black,
                           spaceBefore=4, spaceAfter=5),
    "stub": ParagraphStyle("st", parent=styles["Normal"], fontName="Helvetica-Oblique",
                           fontSize=10, leading=14, textColor=colors.HexColor("#666666"),
                           spaceAfter=8),
}

GRID = TableStyle([
    ("TEXTCOLOR", (0, 0), (-1, -1), colors.black),
    ("LINEABOVE", (0, 0), (-1, 0), 0.9, colors.black),
    ("LINEBELOW", (0, 0), (-1, 0), 0.9, colors.black),
    ("LINEBELOW", (0, 1), (-1, -2), 0.25, colors.HexColor("#999999")),
    ("LINEBELOW", (0, -1), (-1, -1), 0.9, colors.black),
    ("VALIGN", (0, 0), (-1, -1), "TOP"),
    ("TOPPADDING", (0, 0), (-1, -1), 4),
    ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
    ("LEFTPADDING", (0, 0), (-1, -1), 5),
])

CELL = ParagraphStyle("cell", fontName="Helvetica", fontSize=8.8, leading=11.5,
                      textColor=colors.black)
CELL_B = ParagraphStyle("cellb", parent=CELL, fontName="Helvetica-Bold")


def P(t, s="body"):
    return Paragraph(t, S[s])


def table(rows, widths, caption=None):
    # Every string cell becomes a Paragraph: ReportLab does not wrap bare
    # strings in cells, it lets them overflow into the next column.
    body = [[Paragraph(c, CELL_B if i == 0 else CELL) if isinstance(c, str) else c
             for c in row] for i, row in enumerate(rows)]
    t = Table(body, colWidths=widths, hAlign="LEFT", repeatRows=1)
    t.setStyle(GRID)
    block = ([P(caption, "tcap")] if caption else []) + [t]
    # Without this a table breaks wherever the page happens to end, stranding a
    # header row (or a caption) alone at the foot of a page.
    return [KeepTogether(block), Spacer(1, 5 * mm)]


# ---------------------------------------------------------------- diagrams
# Drawn as vector shapes rather than an imported bitmap, so the figures stay
# sharp at any print size and need no external converter. The pin tables in
# Section 6 carry the exhaustive detail; these figures show the structure the
# tables cannot -- in particular that both peripherals share one SPI bus and are
# distinguished only by their chip-select lines.

BLK, WHT = colors.black, colors.white
PIN, TTL, SUB = 6.6, 8.6, 7.0          # font sizes: pin label, box title, subtitle


def _chip(d, x, y, w, h, title, sub=()):
    """A module outline with its name at the top. Pin labels are added by _pin,
    anchored inside the outline at the edge the wire leaves from, so nothing
    floats loose in the gap between modules."""
    d.add(Rect(x, y, w, h, strokeColor=BLK, fillColor=WHT, strokeWidth=1.2))
    d.add(String(x + w / 2, y + h - 13, title, fontName="Helvetica-Bold",
                 fontSize=TTL, textAnchor="middle", fillColor=BLK))
    for i, s in enumerate(sub):
        d.add(String(x + w / 2, y + h - 24 - i * 9.5, s, fontName="Helvetica",
                     fontSize=SUB, textAnchor="middle", fillColor=BLK))


def _pin(d, x, y, text, side):
    """Pin name inside the outline, hugging the edge the wire attaches to."""
    d.add(String(x + (4 if side == "left" else -4), y - 2.3, text,
                 fontName="Helvetica", fontSize=PIN,
                 textAnchor="start" if side == "left" else "end", fillColor=BLK))


def _link(d, x1, x2, y, bus=False, arrow=None, count=None):
    """A wire. A bus is a doubled stroke; `count` adds the conventional slash and
    conductor count, which belongs on the SPI bus but not on a cable such as USB."""
    d.add(Line(x1, y, x2, y, strokeColor=BLK, strokeWidth=1.5 if bus else 0.8))
    if bus:
        d.add(Line(x1, y + 2.4, x2, y + 2.4, strokeColor=BLK, strokeWidth=1.5))
    if count:
        mx = (x1 + x2) / 2
        d.add(Line(mx - 4, y - 3.5, mx + 4, y + 6.5, strokeColor=BLK, strokeWidth=0.8))
        d.add(String(mx + 6.5, y + 6, str(count), fontName="Helvetica", fontSize=6,
                     textAnchor="start", fillColor=BLK))
    if arrow:                                   # small solid arrowhead
        ax, s = (x2, -1) if arrow == "right" else (x1, 1)
        d.add(Line(ax, y, ax + 5 * s, y + 2.4, strokeColor=BLK, strokeWidth=0.8))
        d.add(Line(ax, y, ax + 5 * s, y - 2.4, strokeColor=BLK, strokeWidth=0.8))


def _antenna(d, x, ytop):
    d.add(Line(x, ytop - 22, x, ytop, strokeColor=BLK, strokeWidth=1.1))
    for i in range(3):                          # whip glyph
        yy = ytop - i * 6
        d.add(Line(x, yy, x + 7 - i * 1.6, yy + 7, strokeColor=BLK, strokeWidth=0.9))
    d.add(String(x + 12, ytop + 2, "433 MHz", fontName="Helvetica", fontSize=6.2,
                 textAnchor="start", fillColor=BLK))
    d.add(String(x + 12, ytop - 6, "quarter-wave", fontName="Helvetica", fontSize=6.2,
                 textAnchor="start", fillColor=BLK))


def transmitter_diagram():
    d = Drawing(426, 202)
    LX, LW = 8, 84                       # MFRC522
    CX, CW = 178, 96                     # ESP32
    RX, RW = 350, 66                     # SX1276
    _chip(d, LX, 48, LW, 124, "MFRC522", ("RFID reader", "13.56 MHz"))
    _chip(d, CX, 34, CW, 148, "ESP32", ("gate unit", "encrypts UID"))
    _chip(d, RX, 38, RW, 134, "SX1276", ("LoRa", "transceiver"))

    left = [("SDA / SS", "GPIO 5", 128, False, True),
            ("SCK MOSI MISO", "18 23 19", 112, True, False),
            ("RST", "GPIO 22", 96, False, True),
            ("3V3", "3V3", 80, False, False),
            ("GND", "GND", 66, False, False)]
    for a, b, y, bus, arr in left:
        _pin(d, LX + LW, y, a, "right")
        _pin(d, CX, y, b, "left")
        _link(d, LX + LW, CX, y, bus, "left" if arr else None, 3 if bus else None)

    right = [("GPIO 4", "NSS", 124, False, "right"),
             ("18 23 19", "SCK MOSI MISO", 108, True, None),
             ("GPIO 14", "RESET", 92, False, "right"),
             ("GPIO 26", "DIO0", 76, False, "left"),
             ("3V3", "VCC", 60, False, None),
             ("GND", "GND", 46, False, None)]
    for a, b, y, bus, arr in right:
        _pin(d, CX + CW, y, a, "right")
        _pin(d, RX, y, b, "left")
        _link(d, CX + CW, RX, y, bus, arr, 3 if bus else None)

    _antenna(d, RX + RW / 2, 194)
    d.add(Line(RX + RW / 2, 172, RX + RW / 2, 172, strokeColor=BLK))
    # status LED, hung below the microcontroller
    d.add(Line(CX + CW / 2, 34, CX + CW / 2, 20, strokeColor=BLK, strokeWidth=0.8))
    d.add(String(CX + CW / 2 + 5, 17, "status LED, GPIO 2", fontName="Helvetica",
                 fontSize=6.4, textAnchor="start", fillColor=BLK))
    return d


def receiver_diagram():
    d = Drawing(426, 186)
    LX, LW = 12, 92                      # laptop
    CX, CW = 186, 96                     # ESP32
    RX, RW = 352, 62                     # SX1276
    _chip(d, LX, 46, LW, 110, "Laptop", ("control centre", "gateway,", "decrypts"))
    _chip(d, CX, 32, CW, 134, "ESP32", ("relay only", "no key material"))
    _chip(d, RX, 36, RW, 120, "SX1276", ("LoRa", "transceiver"))

    _pin(d, LX + LW, 84, "USB", "right")
    _pin(d, CX, 84, "USB", "left")
    _link(d, LX + LW, CX, 84, True)  # cable, no conductor count
    d.add(String((LX + LW + CX) / 2, 68, "serial 115200,",
                 fontName="Helvetica", fontSize=6.2, textAnchor="middle", fillColor=BLK))
    d.add(String((LX + LW + CX) / 2, 60, "also supplies power",
                 fontName="Helvetica", fontSize=6.2, textAnchor="middle", fillColor=BLK))

    right = [("GPIO 5", "NSS", 110, False, "right"),
             ("18 23 19", "SCK MOSI MISO", 94, True, None),
             ("GPIO 22", "RESET", 78, False, "right"),
             ("GPIO 21", "DIO0", 62, False, "left"),
             ("3V3 GND", "VCC GND", 46, False, None)]
    for a, b, y, bus, arr in right:
        _pin(d, CX + CW, y, a, "right")
        _pin(d, RX, y, b, "left")
        _link(d, CX + CW, RX, y, bus, arr, 3 if bus else None)

    _antenna(d, RX + RW / 2, 178)
    return d


def footer(canvas, doc):
    canvas.saveState()
    canvas.setFont("Helvetica", 8)
    canvas.setFillColor(colors.black)
    canvas.drawString(20 * mm, 12 * mm, TITLE)
    canvas.drawRightString(A4[0] - 20 * mm, 12 * mm, "Page %d" % doc.page)
    canvas.setLineWidth(0.4)
    canvas.line(20 * mm, 15 * mm, A4[0] - 20 * mm, 15 * mm)
    canvas.restoreState()


story = [P(TITLE, "title")]

# ---------------------------------------------------------------- INTRODUCTION
story.append(P("INTRODUCTION", "h1"))
story.append(P("Background", "h2"))
story.append(P(
    "The increase in the number of vehicles within university campuses, institutional "
    "facilities, and controlled access environments has created a need for automated and "
    "reliable vehicle monitoring systems. Conventional vehicle access management generally "
    "depends on manual verification, physical passes, or surveillance personnel, which can "
    "result in delays during peak entry and exit periods and may lead to incomplete or "
    "inaccurate records. RFID-based identification provides an effective alternative by "
    "allowing vehicles to be associated with unique electronic identifiers and enabling "
    "automated recognition at access points. In the proposed VIGIL system, each registered "
    "vehicle is associated with an RFID card whose unique identifier is used for vehicle "
    "identification and movement logging. The RFID reader is interfaced with an ESP32 "
    "microcontroller, which forms the ground-level sensing and processing unit of the system."))
story.append(P(
    "However, vehicle identification alone does not provide a complete access-management "
    "solution. A practical deployment must also ensure that the identification data is "
    "securely transmitted, reliably received, and correctly stored. Wireless communication "
    "introduces the possibility of packet loss, while transmission of RFID identifiers "
    "without protection can expose the system to interception or spoofing. Furthermore, "
    "simply receiving an RFID transmission does not guarantee that the corresponding vehicle "
    "movement has been successfully recorded in the central database. These limitations "
    "motivate the development of an integrated architecture in which vehicle identification, "
    "secure communication, reliable transmission, database logging, and operator monitoring "
    "are treated as interconnected components rather than independent functions."))
story.append(P(
    # CORRECTION 1: MFRC52 -> MFRC522
    "Our proposed system addresses all requirements with the combination of MFRC522 RFID "
    "reader, ESP32 microcontroller, LoRa wireless communication, authenticated encryption, a "
    "gateway computer, and a PostgreSQL-based database. The overall VIGIL framework is "
    "intended for campus and institutional environments where automated vehicle "
    "authentication and monitoring is needed."))

story.append(P("Motivation", "h2"))
story.append(P(
    "RFID has been proposed repeatedly in the literature as a way to speed up gate "
    "verification, and LoRa as a way to carry that data over campus distances without "
    "depending on continuous internet connectivity. Still, many implementations concentrate "
    "primarily on the identification stage and provide limited consideration to the security "
    "and reliability of the entire communication pipeline. A system might successfully read "
    "an RFID card at an entry point but still fail to provide a reliable audit trail if the "
    "wireless packet is lost before reaching the database. Also, transmitting the RFID "
    "identifier in plaintext can easily expose the identifier to interception and allow an "
    "unauthorized device to reproduce it."))
story.append(P(
    "The motivation of the proposed work is to develop a vehicle identification and logging "
    "architecture in which reliability and security extend across our complete data path. "
    "Rather than treating RFID scanning, wireless transmission, and database storage as "
    "distinct and independent stages, our approach links them with authenticated encryption "
    "and a database-confirmed acknowledgement mechanism, ensuring the system not only "
    "identifies a vehicle but also provides confidence that the corresponding movement event "
    "has securely been processed and recorded."))

story.append(P("Problem Statement", "h2"))
story.append(P(
    "The proposed system addresses the problem of secure and reliable transmission and "
    "logging of RFID-based vehicle identification data in a wireless campus environment. In "
    "a conventional RFID logging architecture, an RFID identifier may be transmitted without "
    "cryptographic protection, making the identification data vulnerable to manipulation. "
    "Furthermore, successful reception of an RFID packet at the communication gateway does "
    "not always guarantee that the corresponding vehicle movement has been stored in the "
    "backend database."))
story.append(P(
    "The technical problem discussed in this work is to design an end-to-end vehicle logging "
    "architecture that can protect our RFID identification data during wireless "
    "transmission, reliably deliver the data to the adequate processing gateway, "
    "authenticate and resolve the received identifier, and confirm successful database "
    "storage before considering the vehicle movement successfully logged."))

# ---------------------------------------------------------------- LIT REVIEW
story.append(P("LITERATURE REVIEW", "h1"))
story.append(P("RFID Based Vehicle Identification", "h2"))
story.append(P(
    "RFID technology has been investigated for vehicle identification in several application "
    "domains like transportation, parking, tolls, and vehicle safety. Jeevagan proposed an "
    "RFID-based vehicle identification system specifically for collisions. In the proposed "
    "system, each vehicle contains its own RFID tag containing its details and an RFID "
    "reader that is activated when a collision is detected. The collision sensors positioned "
    "at the front and rear of the vehicle trigger the RFID readers, helping in the "
    "identification of the involved vehicles to be exchanged and stored securely."))
story.append(P(
    "The study basically demonstrates that RFID can become a direct mechanism for obtaining "
    "any vehicle's identity without manual help. The authors discussed existing RFID "
    "applications as well, like in toll collection, traffic and parking management indicting "
    "the broader applicability of RFID for automated vehicle-related services. As the "
    "solution proposed is specifically for collisions, its architecture is not intended for "
    "continuous vehicle access monitoring."))

# ---------------------------------------------------------------- 6] HARDWARE
story.append(P("6] Hardware Prototype", "h1"))
story.append(P(
    "The vehicle logging system is realised as two ESP32 development boards that include a "
    "transmitter unit which is mounted at the campus gate and a receiver unit connected to "
    "the control-centre laptop used for communicating over a dedicated 433MHz LoRa radio "
    "link. Both the units have a common design pattern i.e. an ESP32 hosting one or more SPI "
    "peripherals on the shared VSPI bus. Each peripheral is addressed through its own "
    "chip-select line. This allows the transmitter to host both the RFID reader and the LoRa "
    "transceiver on a single bus using only one additional GPIO per device. The transmitter "
    "is the only unit that touches RFID cards and holds cryptographic key material. The "
    "receiver holds no keys and performs no interpretation of the payload. It acts as a "
    "bidirectional relay between the radio link and USB serial link to the laptop. "
    # CORRECTION 3: three items no longer mapped onto two places by "respectively"
    "The separate functionality of both the units keeps identification and encryption "
    "concentrated at the gate, and database logic at the control centre. It also simplifies "
    "the trust boundary of the system."))

story.append(P("RFID TRANSMITTER CIRCUIT", "h2"))
story.append(P(
    "The transmitter circuit consists of MFRC522 13.56 MHz RFID reader combined with an "
    "SX1276-based 433 MHz LoRa transceiver on the shared SPI bus. As the IRQ line of the "
    "MFRC522 is left unconnected, therefore, the presence of the card is polled. "
    # CORRECTION 4: LED audience made consistent with the User Interface section
    "The on-board status LED (GPIO 2) reports the outcome of a scan to the gate attendant "
    "via a blink code. A quarter-wave 433 MHz whip antenna is fitted to the radio. Radio "
    "parameters are fixed on both ends of the link. A 433 MHz carrier, sync word 0x12 (to "
    "reject unrelated LoRa traffic) and packet CRC is enabled so that all frames with errors "
    "are discarded by the radio itself before reaching the application code."))
story.append(KeepTogether([
    Spacer(1, 3 * mm), transmitter_diagram(), Spacer(1, 2 * mm),
    P("Figure 1  Transmitter unit. The RFID reader and the LoRa transceiver share a single "
      "VSPI bus; each is selected by its own chip-select line, so both peripherals cost only "
      "one additional GPIO each.", "tcap")]))
story += table(
    [["Module", "Pin", "ESP32 GPIO", "Function"],
     ["MFRC522 RFID Reader", "SDA / SS", "GPIO 5", "Chip select for the RFID reader"],
     ["MFRC522 RFID Reader", "SCK / MOSI / MISO", "18 / 23 / 19", "Shared SPI bus"],
     ["MFRC522 RFID Reader", "RST", "GPIO 22", "Reset"],
     ["MFRC522 RFID Reader", "IRQ", "not connected", "Unused; card presence is polled"],
     ["SX1276 LoRa Transceiver", "NSS", "GPIO 4", "Chip select for the radio"],
     ["SX1276 LoRa Transceiver", "RESET", "GPIO 14", "Reset"],
     ["SX1276 LoRa Transceiver", "DIO0", "GPIO 26", "TX-done / RX-done interrupt"],
     ["Indicator", "Status LED", "GPIO 2", "Blink count reports scan outcome"]],
    [40 * mm, 30 * mm, 25 * mm, 55 * mm])

# CORRECTION 2: heading no longer claims RFID hardware on the receiver
story.append(P("LoRa RECEIVER CIRCUIT", "h2"))
story.append(P(
    "The receiver circuit consists of an SX1276 LoRa transceiver. Its role is to relay radio "
    "signals to the serial. It is connected to the control-centre laptop over USB at 115200 "
    "baud."))
story.append(KeepTogether([
    Spacer(1, 3 * mm), receiver_diagram(), Spacer(1, 2 * mm),
    P("Figure 2  Receiver unit. It carries no RFID hardware and no cryptographic key "
      "material, relaying frames between the radio link and the USB serial link so the "
      "payload remains encrypted end to end.", "tcap")]))
story += table(
    [["Module", "Pin", "ESP32 GPIO", "Function"],
     ["SX1276 LoRa Transceiver", "NSS", "GPIO 5", "Chip select for the radio"],
     ["SX1276 LoRa Transceiver", "SCK / MOSI / MISO", "18 / 23 / 19", "Shared SPI bus"],
     ["SX1276 LoRa Transceiver", "RESET", "GPIO 22", "Reset"],
     ["SX1276 LoRa Transceiver", "DIO0", "GPIO 21", "TX-done / RX-done interrupt"],
     ["Host link", "USB", "-", "Serial link to laptop at 115200 baud; also supplies power"]],
    [40 * mm, 30 * mm, 25 * mm, 55 * mm])

story.append(P("ENCRYPTION MODULE", "h2"))
story.append(P(
    "The card identifiers are protected by using AES-256-GCM. It is an "
    "authenticated-encryption mode that provides both confidentiality and integrity in a "
    "single pass. For the transmitter ESP32, encryption is performed using the mbedTLS "
    "library and is decrypted only on the laptop. The receiver does not hold the key. This "
    "is more helpful than the already existing method called fire-and-forget scheme that "
    "broadcasts the UID as text with no confirmation of the delivery. "
    # CORRECTION 5: package -> packet
    "A lost packet produced a silent gap in the log and the identifier could be forged by "
    "anyone with comparable radio hardware. "
    # CORRECTION 6: the nonce is derived from transmitted header fields, not secret
    "The 12-byte GCM nonce is assembled from header fields already present in the frame, so "
    "it requires no dedicated field of its own. Byte 0 is the message type which separates a "
    "data frame from its acknowledgement, bytes 1-4 are the device identifiers used for "
    "separating the gate units and bytes 5-11 are the monotonic message counter used for "
    "separating successive messages."))
story += table(
    [["Offset", "Length", "Field", "Description"],
     ["0", "1 byte", "Version", "Protocol version"],
     ["1", "1 byte", "Type", "0x01, data frame"],
     ["2", "4 bytes", "Device ID", "Identifies the gate unit"],
     ["6", "8 bytes", "Counter", "Monotonic message counter"],
     ["14", "n bytes", "Ciphertext", "Encrypted RFID UID"],
     ["14+n", "16 bytes", "Tag", "Authentication tag over header + ciphertext"]],
    [18 * mm, 22 * mm, 26 * mm, 84 * mm],
    "Data frame (transmitter to laptop), 34 bytes for a 4-byte UID:")
story += table(
    [["Offset", "Length", "Field", "Description"],
     ["0", "1 byte", "Version", "Protocol version"],
     ["1", "1 byte", "Type", "0x02, acknowledgement"],
     ["2", "4 bytes", "Device ID", "Echoed from the acknowledged frame"],
     ["6", "8 bytes", "Counter", "Echoed from the acknowledged frame"],
     ["14", "1 byte", "Status", "Encrypted outcome code"],
     ["15", "16 bytes", "Tag", "Authentication tag"]],
    [18 * mm, 22 * mm, 26 * mm, 84 * mm],
    "Acknowledgement frame (laptop to transmitter), 31 bytes:")

story.append(P("BACKEND DEVELOPMENT", "h2"))
story.append(P(
    "The gateway is a Python program running on the control-centre laptop. It holds the "
    "serial connection to the receiver and decrypts the incoming frames. It resolves the "
    "recovered UID against the card-assignment table and writes movement records to the "
    "database. "
    # CORRECTION 7: HTTPS is already HTTP over TLS
    "Communication with the database takes place over HTTPS. The link between the "
    "transmitter and the gateway follows a stop-and-wait ARQ discipline i.e. only one frame "
    "is in flight at a time. After transmitting, the transmitter switches to receive and "
    "waits for a bounded interval. If there is no valid acknowledgement then it retransmits "
    "the same frame with the same counter. "
    # CORRECTION 8: the condition was stated backwards
    "If a frame is recorded successfully but its acknowledgement is lost on the return path, "
    "the retransmission carries the same counter and is rejected by the database's "
    "uniqueness constraint rather than creating a duplicate row. The gateway returns a "
    "status \"already recorded\" instead."))
story += table(
    [["Status code", "Meaning", "Gate indication"],
     ["0x00", "Movement recorded", "One blink"],
     ["0x01", "Already recorded (a repeat)", "Two blinks"],
     ["0x02", "Database unavailable", "Retransmission"],
     ["0x03", "Recorded, card not registered", "Three blinks"]],
    [26 * mm, 62 * mm, 62 * mm])

story.append(P("DATABASE DESIGN", "h1"))
story.append(P(
    "The schema is implemented in PostgreSQL and separates 3 concerns : identity of the "
    "vehicle, card-to-vehicle direction mapping and the movement of the log. As the records "
    "are not duplicated into the movement rows, therefore, a change to a vehicle's details "
    "does not rewrite the records."))
story += table(
    [["Table", "Purpose"],
     ["vehicles",
      "One row per registered vehicle: plate number, type, owner, role, active flag."],
     # CORRECTION 9a: "single lookup" overstated the gateway's lookup path
     ["tags",
      "Maps each card UID to a vehicle and a direction ('in' / 'out'); the lookup the "
      "gateway performs on every presentation."],
     # CORRECTION 9b: unregistered presentations are logged too
     ["vehicle_logs",
      "One row per card presentation, including device ID, counter, RSSI, SNR and "
      "timestamp; unique on (device_id, counter). Presentations of unregistered cards are "
      "also recorded, with the vehicle reference and direction left null."],
     ["unauthorised_tags / unauthorised_events",
      "Separate tables for a gate attendant's own entry/exit cards, used to count vehicles "
      "with no registered card."]],
    [45 * mm, 105 * mm])

story.append(P("DASHBOARD DEVELOPMENT", "h1"))
story.append(P(
    # CORRECTION 10a: the gateway does not serve the page; it is a separate process
    "A locally hosted operator web page runs alongside the gateway as a separate process, "
    "reading directly from the database. It provides information like number of vehicles "
    "present currently in the campus against those which are absent, the day's traffic, the "
    "count of unauthorised vehicles present and a searchable record of movements of vehicles "
    "by their direction and role."))

story.append(P("USER INTERFACE", "h1"))
story.append(P(
    "The primary interface is the operator dashboard for the ground-level layer which is "
    "designed for security personnel and administrators at the control centre. The drivers "
    "interact with the system only at the entry and exit gate. The blink code on the "
    "transmitter's status LED is the only feedback given at the point of presentation, "
    # CORRECTION 10b: matches the transmitter-circuit section
    "confirming to the gate attendant whether the scan was recorded or rejected."))

story.append(P("7] EXPERIMENTAL RESULTS", "h1"))

story.append(P("Test Setup", "h2"))
story.append(P(
    "Measurements were taken on the prototype described in Section 6: an ESP32 with MFRC522 "
    "and SX1276 acting as the gate transmitter, a second ESP32 with SX1276 as the receiver "
    "connected over USB to the control-centre laptop, and a PostgreSQL database hosted on "
    "Supabase in region ap-northeast-2. The two units were placed indoors 0.5 m apart with "
    "both quarter-wave antennas vertical. The laptop was connected over domestic Wi-Fi."))
story += table(
    [["Parameter", "Value"],
     ["Environment", "Indoor, 0.5 m transmitter to receiver separation"],
     ["Antennas", "Quarter-wave 433 MHz whip, both vertical"],
     ["Radio", "SX1276, 433 MHz, SF7, BW 125 kHz, CR 4/5, CRC enabled"],
     ["Serial link", "115200 baud, 8N1"],
     ["Database", "PostgreSQL (Supabase), region ap-northeast-2"],
     ["Laptop network", "Domestic Wi-Fi"],
     ["Cards used", "2 registered (entry, exit), 2 attendant, 1 unregistered"],
     ["Firmware", "Instrumented build, separate device identifier"]],
    [38 * mm, 112 * mm])
story.append(P(
    "Timing was recorded on three unsynchronised clocks, one per device, with each segment "
    "measured on whichever clock owns both of its endpoints. Propagation over the air is not "
    "measured directly; it is computed from the LoRa time-on-air expression and compared "
    "against measurement in Table 7.3. Signal strength ranged from -46 to -84 dBm across the "
    "campaign, with the variation attributable to hand proximity to the antenna during card "
    "presentation rather than to link instability, since the reader and the radio share a "
    "board."))

story.append(P("RFID Read Accuracy", "h2"))
story.append(P(
    "Two registered cards were presented alternately under three conditions. Because the "
    "firmware debounce compares each read against the immediately preceding card rather than "
    "the last sighting of that card, alternation avoids suppression entirely. Two rates are "
    "reported. The per-attempt rate counts every call to the reader's decode routine; the "
    "per-presentation rate counts distinct card presentations, in which a failed attempt "
    "recovered by the next poll cycle is not a lost read."))
story += table(
    [["Condition", "Attempts", "Decoded", "By attempt", "By presentation", "Failed"],
     ["Static, normal approach", "51", "49", "96.1 %", "100 %", "0"],
     ["Fast approach", "23", "23", "100 %", "100 %", "0"],
     ["Angle / obstruction", "55", "40", "72.7 %", "95.2 %", "2"]],
    [40 * mm, 19 * mm, 18 * mm, 23 * mm, 30 * mm, 20 * mm],
    "Table 7.1  Read accuracy by presentation condition. "
    "'By attempt' is the per-attempt decode rate, 'by presentation' the "
    "per-presentation rate, and 'failed' the count of presentations detected but "
    "never decoded.")
story.append(P(
    "Card read time was 3.50 to 3.56 ms across all 112 successful reads in all three "
    "conditions, a spread of 60 microseconds. Degraded coupling never produced a slower read, "
    "only the absence of one, so presentation quality determines whether a read occurs and "
    "never how long it takes. No failure of any kind could be induced by presentation speed "
    "achievable by hand: with a 3.5 ms read and an effective field of approximately 3 cm, a "
    "card must remain in range about 5 ms, which at walking pace it exceeds fourfold. The "
    "corresponding limit is near 6 m/s, a vehicle speed rather than a hand speed."))
story.append(P(
    "The end-to-end rate, which would include presentations the reader never detects, cannot "
    "be established from these data. A presentation that is never detected produces no log "
    "entry, and the operator count used as an external denominator disagreed with the "
    "instrumented count by one to two per twenty presentations. The rates in Table 7.1 are "
    "conditional on detection and are stated as such."))

story.append(P("Authentication Time", "h2"))
story.append(P(
    "The full pipeline from card detection to a verified acknowledgement was measured over 32 "
    "scans, of which 27 completed on the first radio attempt."))
story += table(
    [["Stage", "Mean", "Median", "p95", "Min", "Max"],
     ["Card detect and UID read", "3.52", "3.51", "3.54", "3.50", "3.55"],
     ["AES-256-GCM encryption", "0.07", "0.07", "0.07", "0.06", "0.07"],
     ["LoRa transmit (data frame)", "77.96", "77.97", "77.97", "77.95", "77.97"],
     ["Receiver relay, radio to serial", "2.60", "2.60", "2.62", "2.59", "2.64"],
     ["Receiver relay, serial to radio", "72.80", "72.80", "72.81", "72.78", "72.82"],
     ["Transmit done to ACK verified", "537.27", "520.11", "658.77", "447.45", "1013.39"],
     ["End-to-end", "767.35", "604.43", "1797.47", "525.52", "3456.86"]],
    [50 * mm, 20 * mm, 20 * mm, 22 * mm, 18 * mm, 20 * mm],
    "Table 7.2  Per-stage latency in milliseconds, first-attempt scans (n = 27).")
story.append(P(
    "The median rather than the mean should be read as the typical figure. Because only one "
    "frame is in flight at a time, a card presented while another scan is outstanding waits "
    "in the queue, and the end-to-end measurement begins at the card read; a small number of "
    "first-attempt scans therefore carry queueing delay of up to 3.4 s."))
story.append(P(
    "Transmission time was constant to within 20 microseconds. Comparing it against the "
    "value computed from the LoRa time-on-air expression provides an independent check on the "
    "measurement method rather than only a reported number."))
story += table(
    [["Frame", "Measured", "Computed", "Difference", "Error"],
     ["Data, 34 bytes (63 symbols)", "78.0 ms", "77.1 ms", "+0.9 ms", "1.2 %"],
     ["Acknowledgement, 31 bytes (58 symbols)", "72.8 ms", "71.9 ms", "+0.9 ms", "1.3 %"]],
    [58 * mm, 22 * mm, 22 * mm, 24 * mm, 18 * mm],
    "Table 7.3  Measured transmission time against the computed time on air.")
story.append(P(
    "The residual is identical on both frames, consistent with fixed SPI setup overhead rather "
    "than measurement error. Encryption accounted for 0.07 ms, below 0.01 % of end-to-end "
    "latency, and decryption and acknowledgement sealing on the laptop each measured under "
    "0.1 ms. The objection that authenticated encryption is too costly for a microcontroller "
    "gate node is not supported by these measurements."))
story.append(P(
    "Retransmission latency follows directly from the acknowledgement timeout and the linear "
    "backoff, and was reproduced to within 30 ms: two attempts completed in 4678 and 4684 ms "
    "against a predicted 4656 ms, and three attempts in 9708 and 9720 ms against a predicted "
    "9734 ms. Retransmission cost is therefore a stated design parameter rather than an "
    "empirical quantity."))
story.append(P(
    "Of the five scans that required retransmission, four returned the status indicating the "
    "movement was already recorded. In those cases the original frame had reached the database "
    "and only the acknowledgement was lost on the return path. Without the uniqueness "
    "constraint each would have produced a duplicate movement record for a single gate "
    "crossing. The constraint was exercised in 12.5 % of scans during ordinary operation."))
story.append(P(
    "A deliberate interruption was also performed by disconnecting the receiver during a run. "
    "Two presentations made during the interruption were retained by the transmitter and "
    "delivered on restoration, 127.8 s and 121.4 s after the cards were read, both committing "
    "as new records rather than duplicates. No movement was lost across a gateway process "
    "failure, a physical disconnection, and an operating-system device re-enumeration."))

story.append(P("Database Response Time", "h2"))
story.append(P(
    "The database path was measured in isolation by replaying pre-encrypted frames into the "
    "gateway, excluding the reader and the radio. Three routing paths were compared. A "
    "registered card requires two queries; an unrecognised card requires three, because the "
    "attendant card table is consulted only after the first lookup fails."))
story += table(
    [["Path", "Queries", "Mean", "Median", "p95", "n"],
     ["Duplicate frame (constraint rejection)", "2", "397.5", "369.0", "509.7", "30"],
     ["Registered card", "2", "653.1", "460.7", "794.9", "30"],
     ["Unrecognised card", "3", "765.8", "717.5", "1381.8", "20"]],
    [58 * mm, 18 * mm, 18 * mm, 20 * mm, 20 * mm, 12 * mm],
    "Table 7.4  Gateway-side latency in milliseconds by routing path.")
story += table(
    [["Stage", "Mean", "Median", "p95", "Min", "Max"],
     ["Decrypt and verify (local)", "0.03", "0.03", "0.10", "0.02", "0.11"],
     ["SELECT on card assignments", "412.5", "224.5", "614.7", "177.7", "5004.0"],
     ["INSERT, write confirmed", "240.4", "220.2", "417.5", "175.1", "559.2"],
     ["Seal acknowledgement (local)", "0.06", "0.07", "0.08", "0.02", "0.20"]],
    [50 * mm, 20 * mm, 20 * mm, 22 * mm, 18 * mm, 20 * mm],
    "Table 7.5  Stage detail for a registered card, milliseconds (n = 30).")
story.append(P(
    "One write of thirty exceeded the five-second client timeout and returned the status "
    "indicating the database was unavailable, a failure rate of 3.3 %. That single outlier "
    "raises the mean from 461 ms to 653 ms, which is why the median is the appropriate "
    "summary. Replaying the same frames subsequently produced twenty-nine rejections and one "
    "successful insertion, the latter being precisely the frame whose original write had timed "
    "out: the system distinguished correctly between a retransmission that had to deliver data "
    "and one that had to avoid duplicating it, using only the message counter and a database "
    "constraint."))
story.append(P(
    "Rejecting a duplicate proved less costly than performing the insertion it replaces, so "
    "retransmission is not merely harmless but marginally cheaper than the original operation. "
    "Against database round trips of 180 to 500 ms, the entire cryptographic layer of the "
    "system costs well under one millisecond per movement across both devices. Only a single "
    "network condition was measured; the comparison against a degraded network is not "
    "supported by these data."))

story.append(P("Unauthorized Detection Results", "h2"))
story.append(P(
    "Two mechanisms were evaluated against known ground truth. The first is the attendant "
    "tally, in which two registered cards are presented to count vehicles carrying no tag. The "
    "second is presentation of a card whose identifier appears in no table, which is an "
    "identification anomaly rather than a count. The two are stored separately, and the trial "
    "confirms they do not mix."))
story += table(
    [["Card presented", "Ground truth", "Records", "Given a vehicle", "Given a direction", "Correct"],
     ["Attendant entry card", "attendant", "22", "0", "22", "22 / 22"],
     ["Attendant exit card", "attendant", "22", "0", "22", "22 / 22"],
     ["Unregistered card", "unknown", "21", "0", "0", "21 / 21"]],
    [34 * mm, 23 * mm, 18 * mm, 28 * mm, 30 * mm, 17 * mm],
    "Table 7.6  Classification against known ground truth. No false positives or negatives.")
story.append(P(
    "No unregistered card was attributed to a vehicle or assigned a direction, and no "
    "attendant card was treated as unidentifiable. Across twenty entry presentations followed "
    "by twenty exit presentations the running total of unauthorised vehicles present moved "
    "from 4 to 24 and returned to exactly 4, with no drift over forty presentations."))
story.append(P(
    "The exit card produced twenty-one acknowledgements for twenty counted events, one of "
    "which was a retransmission following a lost acknowledgement and was absorbed by the "
    "uniqueness constraint. Without it the attendant's count would have recorded twenty-one "
    "departures for twenty vehicles. Throughout the twenty-one presentations of the "
    "unregistered card the attendant totals did not move, so an unidentifiable card is visible "
    "to an operator as an anomaly without inflating the count of unauthorised vehicles."))

story.append(P("Limitations", "h2"))
story.append(P(
    "These figures characterise one prototype pair on one bench over a short campaign. They do "
    "not establish long-term reliability, behaviour across temperature, or performance at "
    "realistic gate separation, and the read-accuracy conditions are defined manual gestures "
    "rather than vehicles in motion. The end-to-end read rate is not established, for the "
    "reason given above. A single network condition was measured. The acknowledgement frames "
    "carry no timestamp, since the transmitter has no clock, so records delivered together "
    "after an interruption are inserted within the same interval and are flagged by the "
    "duplicate-read guard even though they represent distinct presentations; a cluster of such "
    "flags following an outage indicates recovery rather than repeated presentation."))

story.append(P("8] COMPARATIVE ANALYSIS", "h1"))

story.append(P("Comparison with Existing RFID Systems", "h2"))
story.append(P(
    "Most RFID-based vehicle identification work reviewed above focuses on read reliability "
    "rather than on security or delivery guarantees. Wisanmongkol et al. [N] paired "
    "photoelectric sensors with RFID reads at a checkpoint to automate vehicle "
    "identification, and Jeevagan et al. [N] proposed collision-triggered RFID reads that "
    "exchange vehicle identity automatically after an accident. Both address identification, "
    "but neither incorporates encryption, delivery acknowledgement, or a privacy layer, which "
    "is the gap identified in Section 1."))
story.append(P(
    "Junejo et al. [N] address a related but distinct problem: generating shared secret keys "
    "over LoRa links from channel randomness, rather than authenticating and encrypting an "
    "identifier that has already been issued, as the present system does. Prantl et al. [N] "
    "survey secure group-communication schemes for the Internet of Things in general, "
    "providing a useful taxonomy but no vehicle-identification implementation against which "
    "to compare directly."))
story.append(P(
    "The proposed system differs from these baselines in treating confidentiality and "
    "delivery assurance as primary requirements rather than as later additions. "
    "Authenticated encryption is applied at the point of the card read, and the "
    "acknowledgement returned to the gate is issued only once the database has confirmed the "
    "write. Neither property is reported by the RFID identification baselines reviewed, which "
    "report identification success but not payload confidentiality or end-to-end delivery."))

story.append(P("Feature Comparison", "h2"))
story += table(
    [["Feature", "Wisanmongkol\net al. [N]", "Jeevagan\net al. [N]", "Junejo\net al. [N]", "Proposed system"],
     ["Identification method", "RFID with photoelectric sensor",
      "RFID, triggered by collision", "Not applicable, key generation only",
      "RFID, dual-card directional"],
     ["Communication link", "Not specified", "Not specified", "LoRaWAN",
      "LoRa 433 MHz, point to point"],
     ["Payload encryption", "Not reported", "Not reported", "Not applicable",
      "AES-256-GCM"],
     ["Delivery acknowledgement", "Not reported", "Not reported", "Not reported",
      "Stop-and-wait, database confirmed"],
     ["Direction of travel", "Not addressed", "Not addressed", "Not addressed",
      "Explicit, two-card method"],
     ["Reported read accuracy", "Not quantified", "Not quantified", "Not applicable",
      "96.1 % static, 100 % fast, 72.7 % at angle"]],
    [32 * mm, 26 * mm, 26 * mm, 28 * mm, 38 * mm],
    "Table 8.1  Feature comparison against the reviewed baselines. Accuracy figures for the "
    "proposed system are per-attempt decode rates from Table 7.1.")

story.append(P("Advantages of the Proposed System", "h2"))
story.append(P(
    "The vehicle identifier is protected by authenticated encryption from the point of the "
    "RFID read to the control-centre host, a property absent from the RFID baselines "
    "reviewed. Encryption is performed on the gate microcontroller and decryption only on the "
    "host, so the relaying receiver holds no key material and the identifier is never exposed "
    "on either the radio or the serial link. Section 7 shows this costs 0.07 ms per movement "
    "on the transmitter and under 0.1 ms on the host, below 0.01 % of end-to-end latency."))
story.append(P(
    "Delivery is confirmed against the database rather than against radio receipt, so the "
    "indication given at the gate reflects a durably recorded row. This addresses the silent "
    "loss failure mode of a fire-and-forget scheme, in which a lost packet leaves no record "
    "and no indication that anything was lost."))
story.append(P(
    "Retransmission is safe by construction. The uniqueness constraint on device identifier "
    "and message counter means a lost acknowledgement cannot produce a duplicate movement "
    "record. This is not only a design property: during the measurement campaign the "
    "constraint was exercised on 12.5 % of scans under ordinary operating conditions, and in "
    "four of five retransmissions the original frame had already been recorded, so each would "
    "otherwise have created a duplicate record for a single gate crossing."))
story.append(P(
    "A temporary database or network interruption delays a record rather than losing it. "
    "Presentations made during a deliberate two-minute interruption were retained by the "
    "transmitter and committed 127.8 s and 121.4 s after the cards were read, across a "
    "gateway process failure, a physical disconnection, and an operating-system device "
    "re-enumeration."))
story.append(P(
    "Direction of travel is recorded from the card presented rather than inferred from scan "
    "history or from gate identity, so a single erroneous or missed presentation cannot "
    "invert the interpretation of every subsequent record. The single-card alternation "
    "approach was considered and rejected during design for precisely this reason: it makes "
    "direction a function of the entire preceding history of that card, with no means of "
    "resynchronisation after an error."))
story.append(P(
    "Presentations of unregistered cards are recorded rather than discarded, with the vehicle "
    "reference and direction left empty, giving a complete audit trail rather than gaps. "
    "Section 7 confirms all twenty-one such presentations were classified correctly, and that "
    "they did not affect the separate count of attendant-logged unauthorised vehicles."))

story.append(P("Cost of the Design Choices", "h2"))
story.append(P(
    "These properties are not free, and the measurements quantify what they cost. "
    "Acknowledging on database commit rather than on radio receipt raises the median "
    "end-to-end time from roughly 150 ms, the irreducible radio round trip, to 604 ms. The "
    "additional latency is spent almost entirely on two HTTPS round trips to a geographically "
    "distant database, and it caps gate throughput at approximately one vehicle per second. "
    "For a campus gate this is acceptable; for a high-throughput barrier it would not be, and "
    "a hop-by-hop acknowledgement with asynchronous persistence would be the appropriate "
    "alternative."))
story.append(P(
    "The two-card method likewise trades one failure mode for another. It removes the "
    "cascading error inherent in inferred direction, but depends on the driver presenting the "
    "correct card, and a wrong presentation produces one incorrect record. That error is "
    "bounded and individually correctable, whereas an inference error is neither."))

doc = BaseDocTemplate(OUT, pagesize=A4,
                      leftMargin=20 * mm, rightMargin=20 * mm,
                      topMargin=18 * mm, bottomMargin=20 * mm,
                      title="VIGIL", author="", subject="")
doc.addPageTemplates([PageTemplate(
    id="main",
    frames=[Frame(doc.leftMargin, doc.bottomMargin, doc.width, doc.height, id="f")],
    onPage=footer)])
doc.build(story)
print("wrote", OUT)
