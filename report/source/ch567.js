// Chapters 5–7, references and appendix.
const L = require("./lib.js");
const { D, P, bullets, numbered, chapter, front, h2, h3, figure, table, runs, REFS, citeOrder, FONT } = L;
const { AlignmentType, Paragraph, TextRun } = D;
const C = AlignmentType.CENTER, LT = AlignmentType.LEFT;

function ch5() {
  return [
    chapter("OUTCOMES AND PROSPECTIVE LEARNING"),
    P("This chapter reports what the system achieved, using only measured results, and states the conditions and limits of each measurement. It closes with the lessons learnt."),

    h2("5.1 Gate Subsystem"),
    P("The gate was tested on the bench in five phases with an instrumented build of the firmware. The units were 0.5 m apart indoors with vertical quarter-wave antennas, the laptop used home Wi-Fi, and the database was hosted in the ap-northeast-2 (Seoul) region. Timing was measured on three unsynchronised clocks, transmitter, receiver and laptop, with each stage measured on the clock that owns both its endpoints."),
    h3("5.1.1 Authentication Time"),
    P("{{tab:rfid_latency}} gives the per-stage latency of 27 scans that completed on the first attempt. The typical time from presenting a card to the gate receiving a verified acknowledgement of the committed row was 0.6 s. The median is reported rather than the mean because stop-and-wait makes a card presented while another scan is in flight wait in the queue, which raises a few samples to several seconds. Reading the card and encrypting its identifier together cost under 4 ms, and the radio transmission was effectively constant at 77.96 ms with a 20 µs spread."),
    ...table("rfid_latency", "Per-stage latency at the gate, first-attempt scans (n = 27)", ["Stage", "Mean", "Median", "95th percentile"], [
      ["Card detect and UID read", "3.52 ms", "3.51 ms", "3.54 ms"], ["AES-256-GCM encryption", "0.07 ms", "0.07 ms", "0.07 ms"],
      ["LoRa transmission (DATA)", "77.96 ms", "77.97 ms", "77.97 ms"], ["Transmission done to ACK verified", "537.27 ms", "520.11 ms", "658.77 ms"],
      ["**End to end**", "**767.35 ms**", "**604.43 ms**", "**1797.47 ms**"],
    ], [3606, 1566, 1566, 1568], { align: [LT, C, C, C] }),
    P("Most of the time is spent on the two HTTPS round trips to the database. In the first baseline run, 94 % of the mean end-to-end time lay between the end of the radio transmission and the return of the acknowledgement. This is the measured cost of waiting for the database commit before acknowledging a scan. Encryption, at 0.07 ms, is about 0.01 % of the total, so authenticated encryption is effectively free on the ESP32’s hardware AES. Retries behaved exactly as designed: scans that needed two or three attempts took 4678–4684 ms and 9708–9720 ms, within 30 ms of the values predicted from the 3 s timeout and the back-off."),
    h3("5.1.2 RFID Read Accuracy"),
    P("Two registered cards were presented alternately under three conditions ({{tab:rfid_read}}). The per-attempt rate counts every read call; the per-presentation rate counts distinct presentations. Almost every failed attempt was recovered by the polling loop a few milliseconds later and lost no data. Coupling behaved as a threshold: across all 112 successful reads the read time stayed between 3.50 and 3.56 ms, so a poor presentation affected whether a read succeeded but not how long it took. Speed made no difference, but tilting the card or reading through a wallet or phone caused real failures."),
    ...table("rfid_read", "RFID read accuracy under three presentation conditions", ["Measure", "Static", "Fast", "Angle or obstruction"], [
      ["Per-attempt decode rate", "96.1 % (49/51)", "100 % (23/23)", "72.7 % (40/55)"],
      ["Per-presentation decode rate", "100 %", "100 %", "95.2 %"],
      ["Detected but never decoded", "0", "0", "2"],
      ["Mean read time when successful", "3.52 ms", "3.53 ms", "3.52 ms"],
    ], [2906, 1800, 1800, 1800], { align: [LT, C, C, C] }),
    P("These rates are conditional on detection: a presentation the reader never detects leaves no log line. The operator’s hand tally disagreed with the instrumented count by one or two per batch, so it could not serve as an independent ground truth, and end-to-end accuracy including never-detected presentations needs a presentation jig or a second sensor in future tests."),
    h3("5.1.3 Database Response and Idempotency"),
    P("Replaying pre-built encrypted frames through the gateway isolated the laptop-to-database path ({{tab:rfid_db}}). An unknown card costs 56 % more than a registered one because the tally-card table is consulted only on a miss, and rejecting a duplicate is cheaper than the insert it replaces. One of 30 registered-card writes hit the client’s 5 s timeout and correctly returned status 0x02 for a retry. When the same 30 frames were replayed again, 29 were rejected as already recorded and exactly one was inserted: the frame whose first write had timed out. The counter and the database constraint were enough to tell a retry that still had to deliver data from one that would only have duplicated it."),
    ...table("rfid_db", "Gateway time by routing path", ["Path", "Database calls", "Median total", "n"], [
      ["Duplicate (rejected by constraint)", "2", "369 ms", "30"], ["Registered card", "2", "461 ms", "30"], ["Unknown card", "3", "718 ms", "20"],
    ], [3706, 1600, 1600, 1400], { align: [LT, C, C, C] }),
    P("In routine runs the constraint fired on 12.5 % of scans: of five retransmitted scans in one run, four had already reached the database and only their acknowledgement had been lost. Without the constraint each would have become a duplicate movement record."),
    h3("5.1.4 Unregistered Vehicles and Outage Recovery"),
    P("{{tab:rfid_unauth}} gives the classification of 65 presentations of the two tally cards and one card unknown to the system. There were no false positives and no false negatives. The tally returned exactly to its starting value after 20 entry and 20 exit presentations, although one of the exit presentations produced a lost-acknowledgement retransmission that the constraint absorbed; the unknown card never moved the tally."),
    ...table("rfid_unauth", "Classification of tally and unknown cards", ["Card presented", "Ground truth", "Rows", "Treated as a vehicle", "Correct"], [
      ["Tally card (entry)", "Unregistered vehicle entering", "22", "0", "22 / 22"],
      ["Tally card (exit)", "Unregistered vehicle leaving", "22", "0", "22 / 22"],
      ["Unknown card", "Security anomaly", "21", "0", "21 / 21"],
    ], [1900, 2500, 1000, 1700, 1206], { align: [LT, LT, C, C, C] }),
    P("To test recovery, the receiver’s USB cable was unplugged in mid-run, three cards were presented and the cable was reconnected. The interruption lasted about two minutes, partly because the receiver reappeared under a different device path. Both queued scans were committed with status 0x00, 127.8 s and 121.5 s after their cards were read, and the third presentation had correctly been suppressed by the 5 s debounce. Nothing was lost through a gateway crash, a USB disconnection, a device re-enumeration and a process restart."),

    h2("5.2 Speed Subsystem"),
    P("To measure what the drone camera’s quality and shake cost, clean overhead footage was degraded to match the real camera, with its MJPEG blocking, its noise and its recorded shake replayed frame by frame. Both copies were run through the live chain, and the degraded copy was scored against the clean one over 600 frames ({{tab:spd_stab}})."),
    ...table("spd_stab", "Effect of shake correction on degraded drone footage", ["Speed chain", "Median error, moving", "90th pct. error", "Parked cars given ≥ 5 km/h", "Usable readings"], [
      ["No correction", "3.3 km/h", "11.7 km/h", "54 %", "89 %"], ["Drift corrector (offline tool)", "2.5 km/h", "9.2 km/h", "32 %", "67 %"],
      ["**Motion compensation (used)**", "**0.6 km/h**", "**3.6 km/h**", "**5 %**", "**91 %**"],
    ], [2506, 1450, 1450, 1500, 1400], { align: [LT, C, C, C, C] }),
    P("Frame clean-up before detection improved detection further ({{tab:spd_clean}}): recall rose from 76 % to 84 % at a cost of two points of precision, and the error in the calibrated height fell from −4.1 % to −1.0 %. Dedicated deblocking and colour smoothing were also tested and added nothing measurable, so they were left out."),
    ...table("spd_clean", "Effect of frame clean-up, with motion compensation on", ["Frames", "Detection recall", "Precision", "90th pct. speed error", "Calibrated height error"], [
      ["Raw", "76 %", "94 %", "3.6 km/h", "−4.1 %"], ["**Clean-up (used)**", "**84 %**", "92 %", "**3.1 km/h**", "**−1.0 %**"],
    ], [1906, 1600, 1400, 1700, 1700], { align: [LT, C, C, C, C] }),
    P("Frame timing was tested by passing an RTP stream through a relay that added Wi-Fi-like delay, jitter and 150 ms stalls ({{tab:spd_timing}}). The relay’s timing is simulated; a diagnostic tool now records the real drone’s timing for future tests."),
    ...table("spd_timing", "Effect of frame timing on measured speed", ["Frame times from", "Spacing error, 95th pct.", "Speed error, median", "90th pct.", "99th pct."], [
      ["Arrival at the laptop", "40.9 ms", "0.50 km/h", "3.94 km/h", "11.92 km/h"],
      ["**Camera clock (used)**", "**0.02 ms**", "**0.02 km/h**", "**0.02 km/h**", "**1.16 km/h**"],
    ], [2306, 1600, 1500, 1400, 1500], { align: [LT, C, C, C, C] }),
    P("The geometry was verified on real footage in three ways. Overhead, parked cars tracked for 29 s moved less than 0.3 m and read 0.05–0.38 km/h, while moving traffic had a median of 41.2 km/h. Obliquely, the recovered road tilt agreed with the lane-marking vanishing point to within 0.6°, and lane widths read 3.85 m and 3.67 m against a 3.7 m reference. On a live traffic-circle stream the recovered focal length gave a horizon at row 239, against a visible tree line at about 239, and changing the detector changed the calibrated height by only 0.5 %. The scale statistic was chosen by measurement ({{tab:spd_scale}})."),
    ...table("spd_scale", "Choice of the width statistic for scale calibration", ["Statistic", "Estimated height", "Lane-width error"], [
      ["Minimum", "16.63 m", "+140 %"], ["5th percentile", "7.77 m", "+12 %"], ["**25th percentile (used)**", "**7.04 m**", "**+2 %**"], ["Median", "6.52 m", "−6 %"],
    ], [3306, 2500, 2500], { align: [LT, C, C] }),
    P("The quadratic noise model over-predicted noise by 5 to 20 times and withheld most readings: it reported 35 % of readings on a motorway clip, against 92 % with the linear model. The logging rule behaved as designed. On one clip at a 40 km/h limit, 44 vehicles crossed the limit at some moment, 20 were logged, and the 24 suppressed vehicles had a longest continuous exceedance of 0.37 s (median) and 0.67 s (maximum), so none stayed over the limit long enough to be logged. In an end-to-end run, 54 writes collapsed to 18 rows with no duplicates and a valid evidence link for each, and an unreachable database cost one warning while the video continued at full frame rate. The event row is written over a held connection in about 0.2 s, against 1.8 s for a fresh connection, and Realtime alerts reached a guard’s phone in a median of 0.23 s. {{tab:spd_perf}} gives the detector throughput on the laptop’s GPU."),
    ...table("spd_perf", "Detector throughput on an Apple M4 Pro (MPS)", ["Model", "Image size", "Detection rate", "Vehicles found"], [
      ["YOLO11m", "1280", "15.9 fps", "10"], ["**YOLO11s (default, oblique)**", "1280", "34.1 fps", "15"], ["YOLO11s", "960", "53.1 fps", "14"], ["YOLO11n", "1280", "64.0 fps", "12"],
    ], [3306, 1600, 1700, 1700], { align: [LT, C, C, C] }),
    P("With YOLO11s, the live pipeline sustains about 25 fps end to end, including tracking, geometry, annotation and image encoding, at under 100 ms latency. Calibration takes about 30 s for an oblique camera, mostly in the depth network, and about 10 s overhead, where no depth network is used. A limitation remains: the speed estimates have not yet been compared against an independent ground truth such as a radar gun or GPS-logged test vehicle, which is the most important experiment still to be done."),

    h2("5.3 Parking Subsystem and Applications"),
    P("The parking pipeline runs end to end on a 165-bay demonstration lot, from recorded overhead footage to the console, the database and the Campus Parking application, and on the live analog camera. Its automated tests cover the parts whose failures would not be obvious from the console: the homography round trip, registration against a known translation, bay assignment at the painted line, the debounce state machine and the ground plane in metres. Its occupancy accuracy against a hand-labelled ground truth has not yet been measured and is listed as a limitation."),
    P("Both applications pass static analysis and their unit tests, which cover the status mapping and parsing in Campus Parking and the distance and direction text in Guard Alert. In an end-to-end demonstration at a 40 km/h limit, the speed pipeline logged real violations from the demonstration footage, the database dispatched them to an on-duty guard, and the alerts appeared on the Guard Alert application with their evidence photos, as shown in {{fig:guard_app}}."),

    h2("5.4 Integration"),
    P("The three systems, built independently, now run together from one launcher and one web console, against one database of 17 tables in three ownership groups (Appendix A). Row-level security is enabled on every table, the applications use only the publishable key, and the gate’s identity log and the cameras’ anonymous tracks remain separate. Each console still runs on its own, which keeps the systems independent for testing and failure."),

    h2("5.5 Prospective Learning"),
    P("Several lessons from the project apply beyond this particular hardware:"),
    ...numbered([
      "The radio frequency, the scale statistic, the noise model and the logging threshold were all chosen by measurement, and two of the initial choices (868 MHz and the median width) turned out to be wrong.",
      "Acknowledging a scan only once it is stored adds about half a second, but the gate light then always reflects a stored record.",
      "A per-device counter and a database constraint made every retransmission idempotent, and in routine use the constraint fired on one scan in eight.",
      "Separate states for an unreliable speed, an unknown card and missing camera data stop a failure from being read as a quiet road, an empty gate or an empty lot.",
      "For an overhead camera, a closed-form camera model with a scale taken from the traffic itself outperformed a learned depth estimate, so a network is used only where the geometry needs it.",
      "Queueing and cold connections create long tails, so results are reported as medians with their sample sizes, test conditions and limitations, which also makes them easier to reproduce.",
      "Keys were kept out of the code and out of version control, every system kept its own launcher and tests, and the systems were integrated without one depending on another.",
    ]),
  ];
}

function ch6(FIG) {
  return [
    chapter("PROJECT TIMELINE"),
    h2("6.1 Project Timeline"),
    P("The project ran from January to November 2026. {{fig:gantt}} shows its phases, merged from each member’s plan; the bars are approximate to within about half a month. Planning, the literature review and the system design came first, from January to April. Work on speed estimation and parking began in parallel from February, the RFID gate from May, and the applications and integration from June. Testing, verification and the final demonstration ran from September and were completed by 20 November."),
    ...figure(FIG, "gantt", "gantt.png", 6.0, "Project timeline, January to November 2026"),
    h2("6.2 Individual Plan"),
    P("{{tab:plan}} lists the work packages and the member responsible for each."),
    ...table("plan", "Work packages and responsibilities", ["Work package", "Main tasks", "Responsible member"], [
      ["Gate hardware and firmware", "Wiring, radio tests, MFRC522 reading, counter storage, retries", "[Member name]"],
      ["Gate protocol and gateway", "Frame format, AES-256-GCM, acknowledgement, database schema, dashboard", "[Member name]"],
      ["Gate bench testing", "Five test phases, analysis, RFID paper", "[Member name]"],
      ["Speed estimation", "Camera model, calibration, motion compensation, timing, reliability gate", "[Member name]"],
      ["Speeding log and guard dispatch", "Event logging, evidence, dispatch trigger, push function", "[Member name]"],
      ["Parking pipeline", "Annotation, registration, tracking, occupancy, live camera, console", "[Member name]"],
      ["Mobile applications", "Campus Parking and Guard Alert", "[Member name]"],
      ["Integration and report", "Unified console, launcher, design, documentation, this report", "[Member name]"],
    ], [2400, 4100, 1806]),
  ];
}

function ch7() {
  return [
    chapter("CONCLUSION AND FUTURE WORK"),
    h2("7.1 Conclusion"),
    P("This project designed, built and evaluated VIGIL, an integrated campus vehicle monitoring system with three subsystems, two mobile applications and one web console around a shared cloud database. The main conclusions are:"),
    ...numbered([
      "A low-cost RFID–LoRa gate can log vehicle movements securely and verifiably. With AES-256-GCM end to end and an acknowledgement sent only after the database commits, the typical tap-to-confirmation time was 0.6 s, encryption cost 0.07 ms, and no scan was lost through a two-minute outage.",
      "Idempotent retransmission was needed in practice: the uniqueness constraint absorbed lost-acknowledgement retries on 12.5 % of scans in routine use, each of which would otherwise have been a duplicate record.",
      "Two cards per vehicle make the direction of travel reliable, and keeping tally cards separate from unknown cards classified all 65 test presentations correctly.",
      "For an overhead drone, metric speed depends only on the ratio of height to focal length, which can be calibrated from the cars themselves at the 25th percentile of their widths, with no flight metadata.",
      "Motion compensation, frame clean-up and the camera’s own timestamps together make a shaking consumer drone usable for speed measurement, cutting the median error on degraded footage from 3.3 km/h to 0.6 km/h.",
      "Registering every frame to a reference image turns parking occupancy into a simple, debounced displacement test that keeps bay outlines aligned even if the camera shifts.",
      "The three systems can share one database with row-level security while remaining independent, and the gate’s identity log and the cameras’ anonymous tracks can be kept apart to protect privacy.",
    ]),
    h2("7.2 Future Work"),
    ...bullets([
      "Compare drone speed estimates against an independent ground truth, such as a radar gun or a GPS-logged test vehicle, and publish the speed paper’s results section.",
      "Measure parking occupancy accuracy against hand-labelled frames, and on footage from the campus’s own lots.",
      "Measure the gate’s end-to-end read accuracy with an external ground truth, such as a presentation jig or a beam sensor, and repeat the latency tests on campus networking.",
      "Harden the gate: enable the ESP32’s flash encryption to protect the key, keep the pending-scan queue in non-volatile storage, and send the time elapsed since each scan so that rows delayed by an outage are timestamped correctly.",
      "Deploy the gate at a campus entrance and the cameras over a real road and lot, and add a second network link to the drone station.",
      "Add aggregate analytics that compare gate entries with lot fill rates without linking identities, and prune old parking snapshots.",
    ]),
  ];
}

function references() {
  return [
    front("REFERENCES"),
    ...citeOrder.map((k, i) => new Paragraph({ alignment: AlignmentType.JUSTIFIED, spacing: { line: 300, after: 100 },
      indent: { left: 567, hanging: 567 }, children: [new TextRun({ text: `[${i + 1}]\t`, font: FONT }), ...runs(REFS[k])] })),
  ];
}

function appendix() {
  return [
    front("APPENDIX A"),
    new Paragraph({ alignment: AlignmentType.CENTER, spacing: { after: 360 }, children: [new TextRun({ text: "DATABASE TABLES AND SERVICES", font: FONT, bold: true })] }),
    P("{{tab:db_tables}} lists the database objects by the subsystem that owns them. All are in one cloud PostgreSQL 17 database hosted in the ap-northeast-2 (Seoul) region."),
    ...table("db_tables", "Database objects by owner", ["Owner", "Tables and views", "Functions and services"], [
      ["Gate (RFID)", "vehicles, tags, vehicle_logs, unauthorised_tags, unauthorised_events; views vehicle_movements, vehicle_presence, unauthorised_summary", "Re-read flag trigger"],
      ["Speed and Guard Alert", "speeding_events, guards, dispatches", "Dispatch trigger, first-responder function, distance and bearing functions, push edge function, evidence storage, Realtime channel per guard"],
      ["Parking and Campus Parking", "parking_lots, parking_slots, drone_sessions, parking_tracks, slot_events, parking_snapshots, slot_status, cv_vehicle_log; view lot_occupancy", "Status levels computed in lot_occupancy"],
      ["Shared", "allowed_email_domains", "is_allowed_email(): only institute accounts can be guards"],
    ], [1900, 3706, 2700]),
    P("{{tab:ports}} lists the local services started by the launcher."),
    ...table("ports", "Local services", ["Service", "Address"], [
      ["Unified web console", "http://localhost:8070"], ["Parking console", "http://127.0.0.1:8080"], ["Parking camera annotator (stand-alone)", "http://localhost:8081"],
      ["Speed console (Kestrel)", "http://localhost:8000"], ["Gate log dashboard", "http://localhost:8777"],
    ], [4153, 4153]),
  ];
}

module.exports = { ch5, ch6, ch7, references, appendix };
