// Cover, declaration, certificate and the Roman-numbered front matter.
const fs = require("fs");
const path = require("path");
const L = require("./lib.js");
const { D, FONT, P, front, glossary, runs, GDOCS, captions } = L;
const { Paragraph, TextRun, AlignmentType, ImageRun, Table, TableRow, TableCell, WidthType, BorderStyle, TableOfContents } = D;

const TITLE = "VIGIL: Vehicular Intelligence & Ground–UAV Integrated Logistics Systems";
const SUBTITLE = "Integrated Campus Vehicle Monitoring using Secure RFID–LoRa Gate Logging, Drone-Based Speed Estimation and Smart Parking";
const STUDENTS = [["Anmol Mangat", "102303369"], ["Khushi Garg", "102306068"], ["Ishita Vaid", "102356011"], ["Ishaan Singh", "102303382"]];

const c = (text, o = {}) => new Paragraph({ alignment: AlignmentType.CENTER, spacing: { line: 300, before: o.before ?? 0, after: o.after ?? 0 },
  children: [new TextRun({ text, font: FONT, bold: o.bold, italics: o.italic, size: o.size ?? 24 })] });

function authors() {
  const none = { style: BorderStyle.NONE, size: 0, color: "FFFFFF" };
  const nb = { top: none, bottom: none, left: none, right: none };
  const row = (a, b) => new TableRow({ children: [a, b].map((t, i) => new TableCell({ width: { size: 2600, type: WidthType.DXA }, borders: nb,
    children: [new Paragraph({ alignment: i ? AlignmentType.LEFT : AlignmentType.LEFT, spacing: { line: 300, after: 40 },
      children: [new TextRun({ text: t, font: FONT, bold: true, size: 26 })] })] })) });
  return new Table({ width: { size: 5200, type: WidthType.DXA }, columnWidths: [2600, 2600], alignment: AlignmentType.CENTER,
    rows: STUDENTS.map(([n, r]) => row(n.toUpperCase(), r)) });
}

function cover(figs) {
  const logo = fs.readFileSync(path.join(figs, "ti_logo.png"));
  return [
    c(TITLE, { bold: true, size: 32, before: 600, after: 200 }),
    c(SUBTITLE, { italic: true, size: 26, after: 600 }),
    c("submitted in partial fulfillment of the requirements", { italic: true }),
    c("for the degree of", { italic: true, after: 360 }),
    c("BACHELOR OF TECHNOLOGY", { bold: true }),
    c("in", { italic: true }),
    c("ELECTRONICS AND COMMUNICATION ENGINEERING", { bold: true }),
    c("by", { italic: true, before: 120, after: 240 }),
    authors(),
    c("under the supervision of", { before: 480, after: 120 }),
    c("Dr. Sudhanshu Tyagi", { bold: true, size: 26, after: 480 }),
    new Paragraph({ alignment: AlignmentType.CENTER, spacing: { after: 240 },
      children: [new ImageRun({ type: "png", data: logo, transformation: { width: 84, height: 84 } })] }),
    c("DEPARTMENT OF ELECTRONICS AND COMMUNICATION ENGINEERING", { bold: true }),
    c("THAPAR INSTITUTE OF ENGINEERING & TECHNOLOGY", { bold: true, size: 26 }),
    c("PATIALA", { bold: true, size: 26 }),
    c("November 2026", { bold: true, before: 120 }),
  ];
}

function signatures(lines) {
  const none = { style: BorderStyle.NONE, size: 0, color: "FFFFFF" };
  const nb = { top: none, bottom: none, left: none, right: none };
  const cells = lines.map(block => new TableCell({ width: { size: 4153, type: WidthType.DXA }, borders: nb,
    children: block.map((t, i) => new Paragraph({ spacing: { line: 276, after: 0, before: i === 0 ? 600 : 0 },
      children: [new TextRun({ text: t, font: FONT, bold: i === 1 })] })) }));
  const rows = [];
  for (let i = 0; i < cells.length; i += 2) rows.push(new TableRow({ children: [cells[i], cells[i + 1] || new TableCell({ borders: nb, width: { size: 4153, type: WidthType.DXA }, children: [new Paragraph("")] })] }));
  return new Table({ width: { size: 8306, type: WidthType.DXA }, columnWidths: [4153, 4153], rows });
}

function declaration() {
  return [
    front("DECLARATION", { noToc: true }),
    P("We declare that this written submission represents our ideas in our own words and where others’ ideas or words have been included, we have adequately cited and referenced the original sources. We also declare that we have adhered to all principles of academic honesty and integrity and have not misrepresented or fabricated or falsified any idea/data/fact/source in our submission to the best of our knowledge. We understand that any violation of the above will be cause for disciplinary action by the Institute and can also evoke penal action from the sources which have thus not been properly cited or from whom proper permission has not been taken when needed."),
    P("Place: Patiala", { before: 360, after: 0 }),
    P("Date: ____________", { after: 0 }),
    signatures(STUDENTS.map(([n, r]) => ["Signature", n, r])),
  ];
}

function certificate() {
  return [
    front("CERTIFICATE", { noToc: true }),
    P(`This is to certify that the report titled **${TITLE}**, submitted by Anmol Mangat, Khushi Garg, Ishita Vaid and Ishaan Singh, to the Thapar Institute of Engineering & Technology, Patiala, for the award of the degree of Bachelor of Technology, is a record of the project work done by them under my supervision. The contents of this report, in full or in parts, have not been submitted to any other Institute or University for the award of any degree or diploma.`),
    P("Place: Patiala", { before: 360, after: 0 }),
    P("Date: ____________", { after: 0 }),
    signatures([
      ["Signature", "Dr. Sudhanshu Tyagi", "[Designation]", "Supervisor", "Department of Electronics and", "Communication Engineering", "TIET Patiala, 147004"],
    ]),
  ];
}

function acknowledgments() {
  return [
    front("ACKNOWLEDGMENTS", { pageBreakBefore: false }),
    P("We are deeply grateful to our supervisor, Dr. Sudhanshu Tyagi, Department of Electronics and Communication Engineering, for his guidance, encouragement and patience throughout this project."),
    P("We thank the Department of Electronics and Communication Engineering and the Thapar Institute of Engineering & Technology, Patiala, for the laboratory facilities and the opportunity to build a system for our own campus. We also thank the campus security staff, whose daily work at the gates and on the roads motivated this project, and everyone who helped us test the system."),
    P("Finally, we thank our families for their constant support."),
    P("Anmol Mangat, Khushi Garg, Ishita Vaid and Ishaan Singh", { align: AlignmentType.RIGHT, before: 360 }),
  ];
}

function abstract() {
  return [
    front("ABSTRACT"),
    P("Vehicle movement on a university campus is still managed largely by hand: guards write entries in registers at the gates, speeding on internal roads is noticed only when someone happens to see it, and drivers circle the parking lots looking for a free space. This report presents VIGIL (Vehicular Intelligence & Ground–UAV Integrated Logistics Systems), an integrated campus vehicle monitoring system that automates these three tasks with low-cost hardware and shares their results through one cloud database, a single web console and two mobile applications."),
    P("VIGIL consists of three subsystems. At the gate, each vehicle carries two RFID cards, one for entry and one for exit. An ESP32 reads the card, encrypts the identifier with AES-256-GCM and sends it over a 433 MHz LoRa link to a receiver and a laptop gateway, which logs the movement in a PostgreSQL database. The gate is told a scan succeeded only after the database has committed it, and retransmissions are made idempotent by a per-device counter. On the road, a hovering drone streams overhead video to a speed pipeline that detects and tracks vehicles, removes the drone’s own shake, and converts pixel motion into metric speed using a scale it calibrates from the widths of the cars themselves, with no flight metadata. Readings that cannot be told apart from noise are labelled unreliable and are not reported. Vehicles that stay above the limit are logged once with their peak speed and an evidence photo, and the three nearest on-duty guards are alerted on the Guard Alert app. At the parking lots, a fixed camera feeds a parking pipeline that registers every frame to a reference image, tracks vehicles and decides whether each marked bay is free or taken; drivers see the result in the Campus Parking app."),
    P("All numbers reported here are measured. At the gate, the typical time from tap to a committed database row was 0.6 s (median, n = 27), encryption cost 0.07 ms, and no scan was lost through a two-minute total outage of the gateway link. Across 65 test presentations, no tally card or unknown card was misclassified. In the speed pipeline, on footage degraded to match the drone camera and its recorded shake, motion compensation reduced the median speed error from 3.3 km/h to 0.6 km/h and the share of parked cars given a false speed of 5 km/h or more from 54 % to 5 %. In a test with Wi-Fi-like network delays, using the camera’s own timestamps reduced the 90th-percentile speed error caused by frame timing from 3.94 km/h to 0.02 km/h. Alerts reached a guard’s phone in a median of 0.23 s."),
    P("**Layout of the report.** Chapter 1 introduces the problem and the scope of the work. Chapter 2 reviews the literature on RFID vehicle identification, LoRa telemetry, camera-based speed estimation, aerial detection and parking occupancy. Chapter 3 formulates the problem and lists the objectives. Chapter 4 describes the design of each subsystem, the applications, the web console and the standards followed. Chapter 5 presents the measured outcomes and the lessons learnt. Chapter 6 gives the project timeline and the individual plan, and Chapter 7 concludes with directions for future work."),
    P("**Keywords:** RFID, LoRa, AES-GCM, unmanned aerial vehicle, vehicle speed estimation, parking occupancy detection, computer vision, campus security, mobile applications."),
  ];
}

const tocBlock = (title, field) => [
  front(title, { noToc: title === "TABLE OF CONTENTS" }),
  field,
];

// Google Docs has no list-of-figures field, so the lists are written out (no page numbers)
const captionList = (title, kind) => [front(title), ...captions.filter(c => c[0] === kind).map(([k, n, t]) =>
  new Paragraph({ spacing: { line: 300, after: 80 }, indent: { left: 1134, hanging: 1134 },
    children: [new TextRun({ text: `${k} ${n}.\t`, font: FONT }), ...runs(t)] }))];

function contents() {
  return [
    ...tocBlock("TABLE OF CONTENTS", new TableOfContents("Table of Contents", { hyperlink: true, headingStyleRange: "1-2" })),
    ...(GDOCS ? captionList("LIST OF FIGURES", "Figure") : tocBlock("LIST OF FIGURES", new TableOfContents("List of Figures", { hyperlink: true, captionLabelIncludingNumbers: "Figure" }))),
    ...(GDOCS ? captionList("LIST OF TABLES", "Table") : tocBlock("LIST OF TABLES", new TableOfContents("List of Tables", { hyperlink: true, captionLabelIncludingNumbers: "Table" }))),
  ];
}

function abbreviations() {
  const rows = [
    ["ACK", "Acknowledgement"], ["AEAD", "Authenticated Encryption with Associated Data"], ["AES", "Advanced Encryption Standard"],
    ["API", "Application Programming Interface"], ["BW", "Bandwidth (LoRa channel)"], ["CR", "Coding Rate"], ["CRC", "Cyclic Redundancy Check"],
    ["FCM", "Firebase Cloud Messaging"], ["fps", "Frames per second"], ["GCM", "Galois/Counter Mode"], ["GPIO", "General-Purpose Input/Output"],
    ["HTTPS", "Hypertext Transfer Protocol Secure"], ["IoT", "Internet of Things"], ["ISM", "Industrial, Scientific and Medical (radio band)"],
    ["LoRa", "Long Range (chirp spread-spectrum modulation)"], ["MJPEG", "Motion JPEG"], ["MPS", "Metal Performance Shaders (Apple GPU back end)"],
    ["NVS", "Non-Volatile Storage (ESP32)"], ["ORB", "Oriented FAST and Rotated BRIEF"], ["PSK", "Pre-Shared Key"], ["PSNR", "Peak Signal-to-Noise Ratio"],
    ["RANSAC", "Random Sample Consensus"], ["RFID", "Radio-Frequency Identification"], ["RLS", "Row-Level Security"],
    ["RSSI", "Received Signal Strength Indicator"], ["RTP", "Real-time Transport Protocol"], ["RTSP", "Real Time Streaming Protocol"],
    ["SF", "Spreading Factor"], ["SNR", "Signal-to-Noise Ratio"], ["SPI", "Serial Peripheral Interface"], ["SQL", "Structured Query Language"],
    ["SSIM", "Structural Similarity Index"], ["TIET", "Thapar Institute of Engineering & Technology, Patiala"], ["UAV", "Unmanned Aerial Vehicle"],
    ["VIGIL", "Vehicular Intelligence & Ground–UAV Integrated Logistics Systems"],
    ["UID", "Unique Identifier (of an RFID card)"], ["YOLO", "You Only Look Once (object detector)"],
  ];
  return [front("ABBREVIATIONS"), glossary(rows)];
}

function notation() {
  const rows = [
    ["BW", "LoRa channel bandwidth, Hz"], ["d", "viewing ray of a pixel"], ["D", "distance from the camera to a vehicle, m"],
    ["e", "measured extent of a vehicle’s bounding box, pixels"], ["f", "camera focal length, pixels"], ["h", "camera height above the road, m"],
    ["H_t", "homography from frame t to the reference frame"], ["k", "bounding-box jitter as a fraction of box size"],
    ["L", "vehicle length, m"], ["n", "unit normal of the road plane"], ["P", "point on the road plane, m"],
    ["P_p", "p-th percentile of a set of samples"], ["s", "metres-per-pixel scale of a nadir camera, m/pixel"],
    ["SF", "LoRa spreading factor"], ["T", "length of the speed-fit window, s"], ["T_min", "minimum continuous time above the limit before a vehicle is logged, s"],
    ["T_sym", "LoRa symbol time, s"], ["(u, v)", "pixel coordinates"], ["(c_x, c_y)", "principal point, pixels"],
    ["v", "vehicle speed, km/h"], ["v_x, v_y", "velocity components on the road plane, m/s"], ["V_L", "speed limit, km/h"],
    ["W", "reference car width, 1.83 m"], ["(X, Y)", "ground coordinates on the road plane, m"], ["z_b", "height of a vehicle’s centroid above the road, m"],
    ["φ", "angle between a vehicle’s heading and the image axis, degrees"], ["σ_pos", "position uncertainty, m"], ["σ_v", "speed uncertainty, km/h"],
    ["τ_k", "time stamp of the k-th observation of a track, s"],
  ];
  return [front("NOTATION"), glossary(rows, 1500)];
}

module.exports = { cover, declaration, certificate, acknowledgments, abstract, contents, abbreviations, notation, TITLE };
