// Chapters 1–3: introduction, literature survey, problem formulation.
const L = require("./lib.js");
const { D, P, bullets, numbered, chapter, h2, table } = L;
const { AlignmentType } = D;

function ch1() {
  return [
    chapter("INTRODUCTION"),
    h2("1.1 Background"),
    P("The number of vehicles entering university campuses has grown steadily, and with it the work of keeping track of them. At most campus gates a guard checks a sticker or a pass and writes an entry in a register. The record that results is slow to make at peak hours, hard to search, and incomplete whenever a vehicle passes without being written down {{johari2026}}. Inside the campus, roads are shared by cars, two-wheelers, cyclists and pedestrians, yet speeding is noticed only when someone happens to see it, and there is rarely any evidence afterwards. In the parking lots, drivers circle looking for a free bay because nobody, including the security office, knows how full each lot is at a given moment."),
    P("Each of these problems has a well-studied technical answer on its own. RFID tags identify vehicles at an access point without manual checking {{wisanmongkol2008,jeevagan2014}}. Cameras and computer vision can measure vehicle speed {{mouafo2024,sangsuwan2024}} and detect whether parking spaces are occupied {{pklot,cnrpark}}. A campus, however, needs these parts working as one system, each reliable on its own, with results that reach the people who act on them: the guards at the gate and on patrol, the security office and the drivers."),
    h2("1.2 Motivation"),
    P("Three gaps motivated this work. First, most RFID gate systems concentrate on reading the card and give little attention to what happens afterwards. A scan that is read correctly but lost on the radio link, or written to a database that never confirms it, leaves no reliable audit trail; an identifier sent in plain text can also be captured and replayed {{hui2010,prantl2022}}. Second, camera-based speed estimation needs a metric scale. Roadside methods obtain it from surveyed distances, vehicles of known speed or manually set pixel-per-metre values {{dubska2015,kamil2024,sangsuwan2024}}, all of which require a site visit for every camera. A drone can be deployed over any road within minutes, but its altitude and effective focal length are rarely known accurately, and its hovering camera shakes. Third, parking-occupancy research has largely stopped at classifying each bay in an image; the result rarely reaches drivers as live information they can act on."),
    P("VIGIL (Vehicular Intelligence & Ground–UAV Integrated Logistics Systems) addresses these gaps in three subsystems that share one cloud database, one web console and two mobile applications. The overall architecture is shown in {{fig:arch}} and described in Chapter 4."),
    h2("1.3 Objectives and Scope"),
    P("The aim of the project is to design, build and evaluate an integrated campus vehicle monitoring system that:"),
    ...bullets([
      "logs every registered vehicle’s entry and exit at the gate securely and reliably, and confirms each scan only after it is stored;",
      "measures the speed of vehicles on campus roads from an overhead drone without flight metadata, and alerts the nearest guards when a vehicle stays above the limit;",
      "tracks the occupancy of each bay in a parking lot from a fixed camera and shows drivers where space is available; and",
      "presents all three systems to operators through one web console and to guards and drivers through two mobile applications.",
    ]),
    P("The scope is a single campus, Thapar Institute of Engineering & Technology, Patiala. VIGIL records and reports; it does not control access, so it does not operate barriers. It does not read number plates, and the anonymous vehicle tracks seen by the drones are deliberately never linked to the identities recorded at the gate. The measured results reported in Chapter 5 come from bench tests of the gate hardware and from recorded and live video; a full deployment across the campus is future work."),
    h2("1.4 Organisation of the Report"),
    P("Chapter 2 reviews the related literature and identifies the gap this project addresses. Chapter 3 formulates the problem, lists the objectives and states the requirements and constraints. Chapter 4 describes the design of the gate, speed and parking subsystems, the two applications, the web console and the standards followed. Chapter 5 presents the measured outcomes and the lessons learnt. Chapter 6 gives the project timeline and the individual plan, and Chapter 7 concludes and outlines future work."),
  ];
}

function ch2() {
  return [
    chapter("LITERATURE SURVEY"),
    P("This chapter reviews published work in the five areas VIGIL draws on: RFID vehicle identification, LoRa links with secure telemetry, camera-based speed estimation, detection and tracking in aerial imagery, and parking occupancy detection. It closes with a comparison of representative systems and the gap this project fills."),
    h2("2.1 RFID-Based Vehicle Identification"),
    P("RFID has been applied to vehicle identification in transport, parking, tolls and safety {{wisanmongkol2008,jeevagan2014,zhang2020,won2020}}. Wisanmongkol *et al.* integrated a photoelectric sensor with an RFID reader for automatic vehicle identification {{wisanmongkol2008}}. Jeevagan *et al.* proposed an RFID system that identifies the vehicles involved in a collision, with readers triggered by collision sensors {{jeevagan2014}}; it demonstrates identification without manual help, but is not designed for continuous access logging. RFID has also been combined with UAVs, for indoor human–drone interaction and for locating construction resources {{zhang2020,won2020}}."),
    P("Reading at speed is a separate challenge. Zhang *et al.* analysed UHF RFID in high-speed railways and identified insufficient reading time and multi-tag collisions as the main constraints {{zhang2010}}. A campus gate is far slower, but this motivated testing VIGIL’s reader with fast and angled presentations as well as static ones. On security, Hui and Wang proposed a lightweight block cipher for low-cost tags and discussed the risks of sending identifiers in plain text, including tracking, spoofing and replay {{hui2010}}. In VIGIL the card is read locally at the gate, but the same concern applies once its identifier leaves the gate over radio, so the identifier is encrypted before transmission."),
    h2("2.2 LoRa Links and Secure Embedded Telemetry"),
    P("LoRa offers long range at low power in the licence-free ISM bands, but its real coverage depends on the environment. El Chall *et al.* developed indoor, urban and rural LoRaWAN propagation models from field measurements {{elchall2019}}, and Wu *et al.* measured 433 MHz SX1278 links in complex urban settings, reporting packet loss and power consumption {{wu2023}}. Work on RF front ends for long-range monitoring shows how strongly the hardware affects the link budget {{amin2019}}. None of these studies, however, concerns delivery confirmed by a database write."),
    P("Several prototypes add authenticated encryption to LoRa. Kane *et al.* integrated an AEAD scheme into a LoRa 2.4 GHz smart-home architecture with little effect on transmission time {{kane2022}}. Niranjan and Supriya protected real-time vehicle telemetry over LoRa with AES-GCM and reported encryption time, latency and delivery ratio {{niranjan2026}}, and Sasane *et al.* combined LoRa, AES-GCM, a database and a dashboard to track harbour craft {{sasane2025}}. AES-GCM has also been used to protect embedded memory interfaces with low overhead {{liu2013}}, and adaptive encryption frameworks for IoT continue to be proposed {{xu2026}}. Key establishment for LoRa {{junejo2022}} and secure group communication for IoT {{prantl2022}} are active areas as well. On reliability, Rehman *et al.* showed that acknowledgement behaviour in stop-and-wait ARQ directly affects delay and throughput {{rehman2016}}. These works support the use of authenticated encryption and acknowledgements, but none acknowledges a scan only after it has been committed to the database, or makes retransmission idempotent so that a lost acknowledgement cannot create a duplicate record."),
    h2("2.3 Camera-Based Vehicle Speed Estimation"),
    P("A common approach measures how long a vehicle takes to travel between virtual lines on the road. Sangsuwan and Ekpanyapong combined YOLOv3, DeepSORT and optical flow across four intrusion lines and fitted their speed models with vehicles measured by a laser speed gun {{sangsuwan2024}}. Mouafo and Biaou used YOLOv8 and ByteTrack to estimate the average speed over a known distance without calibrating the camera {{mouafo2024}}. Both need distances or reference speeds measured at the site."),
    P("Other methods map image coordinates onto the road plane using projective geometry {{hartley2004}}. Lashkov *et al.* computed real-world distances with a projective transformation at a fixed intersection camera and handled wind-induced vibration with image matching {{lashkov2023}}. Kamil *et al.* rectified monocular video into a bird’s-eye view with a deep homography network, but set the pixel-per-metre value of the rectified view by hand {{kamil2024}}. Dubská *et al.* calibrated roadside cameras automatically from vanishing points in vehicle trajectories, recovering the ground plane and focal length, while the scene scale still had to be supplied separately {{dubska2015}}. All of these target fixed roadside cameras and depend on an external source of metric scale."),
    h2("2.4 Detection and Tracking in Aerial Imagery"),
    P("Single-stage detectors of the YOLO family {{redmon2016}}, now maintained in the Ultralytics framework {{ultralytics}}, are the usual front end for real-time traffic analysis. Detectors trained on ground-level images, however, perform poorly when vehicles are small and seen from directly above. The VisDrone benchmark provides aerial imagery and annotations for exactly this case {{zhu2022}}. ByteTrack associates detections across frames, including low-confidence boxes, which keeps identities through brief detection failures {{bytetrack}}; it has been paired with YOLO detectors improved for small targets {{wang2024}}, and the Kalman motion models inside such trackers remain an active topic {{bayraktar2024}}. A drifting camera can be registered to a fixed reference with feature matching such as ORB {{orb}} and robust model fitting with RANSAC {{ransac}}. For oblique cameras, recent monocular depth networks such as Depth Pro also estimate focal length {{depthpro}}, which VIGIL uses for calibration only, never for metric scale."),
    h2("2.5 Parking Occupancy Detection"),
    P("Vision-based occupancy detection has largely been studied with fixed cameras. The PKLot dataset established a benchmark of parking-space images from fixed cameras under varied weather {{pklot}}, and Amato *et al.* showed that compact convolutional networks can classify each space on embedded hardware {{cnrpark}}. These methods classify image patches at known, fixed positions and assume the camera never moves. Their output is the classification itself; getting live bay status to drivers is outside their scope."),
    h2("2.6 Research Gap"),
    P("{{tab:compare}} compares representative systems with VIGIL. The reviewed work solves each part in isolation: identification without confirmed delivery, encrypted LoRa telemetry without idempotent logging, speed estimation that needs site-specific scale, and occupancy detection that stops at classifying each bay. No reviewed system combines secure, database-confirmed gate logging, drone-based speed estimation with self-calibrated scale, and live bay occupancy for drivers in one campus system with applications for guards and drivers."),
    ...table("compare", "Comparison of representative systems with VIGIL", ["System", "Task", "Link / sensor", "Security", "Delivery or scale"], [
      ["Wisanmongkol *et al.* {{wisanmongkol2008}}", "Vehicle identification", "RFID + photoelectric sensor", "Not reported", "Not reported"],
      ["Niranjan and Supriya {{niranjan2026}}", "Vehicle telemetry", "LoRa, SX1278", "AES-128-GCM", "No delivery acknowledgement"],
      ["Sasane *et al.* {{sasane2025}}", "Craft tracking", "LoRa", "AES-128-GCM", "No delivery acknowledgement"],
      ["Mouafo and Biaou {{mouafo2024}}", "Speed estimation", "Fixed camera", "N/A", "Known distance between lines"],
      ["Kamil *et al.* {{kamil2024}}", "Speed estimation", "Fixed camera", "N/A", "Manual pixel-per-metre"],
      ["Dubská *et al.* {{dubska2015}}", "Camera calibration", "Fixed camera", "N/A", "Scale supplied separately"],
      ["Amato *et al.* {{cnrpark}}", "Parking occupancy", "Fixed camera", "N/A", "Fixed space positions"],
      ["**VIGIL (this work)**", "Gate, speed and parking", "LoRa 433 MHz; drone; camera", "AES-256-GCM", "ACK on database commit; scale from car widths; frame registration"],
    ], [2000, 1500, 1700, 1300, 1806]),
  ];
}

function ch3() {
  return [
    chapter("PROBLEM FORMULATION AND OBJECTIVES"),
    h2("3.1 Problem Statement"),
    P("The problem addressed is to monitor vehicle movement on a campus automatically, securely and verifiably, using low-cost hardware, and to deliver the results to the people who act on them. It divides into three technical problems and one of integration."),
    ...numbered([
      "**Secure, confirmed gate logging.** An RFID scan must be protected from interception and replay while it travels from the gate over radio, and the gate must be told a vehicle’s movement was recorded only when it is actually stored. Lost acknowledgements and retransmissions must never create duplicate records, and the direction of travel must not drift as errors accumulate.",
      "**Speed from an overhead drone without known scale.** Pixel motion must be converted into metric speed when the drone’s altitude and focal length are unknown, its camera shakes while hovering, and its video arrives over Wi-Fi with uneven timing. A reading that cannot be distinguished from noise must not be reported as a speed, and only sustained, reliable violations may be logged.",
      "**Bay occupancy from a fixed camera.** Each marked bay must be classified as free or taken through a wide-angle fisheye view, while vehicles move in and out and pass over bays without parking.",
      "**Integration.** The three systems, built independently, must share one database without interfering with each other, keep identity and anonymous tracking separate, and present their results through one console and two mobile applications.",
    ]),
    h2("3.2 Objectives"),
    ...numbered([
      "Design and build an RFID–LoRa gate unit and gateway with end-to-end AES-256-GCM, a database-confirmed acknowledgement, idempotent retransmission and explicit direction of travel.",
      "Measure the gate’s read accuracy, latency, database response and classification of unregistered and tally cards on real hardware.",
      "Develop a drone speed pipeline that calibrates its scale from the tracked cars, compensates for camera shake, uses the camera’s own timing, and labels unreliable readings.",
      "Log speeding vehicles once with their peak speed and evidence, and dispatch the nearest on-duty guards through a mobile application.",
      "Develop a parking pipeline that registers frames to a reference image, tracks vehicles and debounces the state of each bay, with a dashboard and a driver application.",
      "Integrate the three systems around one cloud database with row-level security, a single web console and a one-click launcher.",
      "Report only measured results, and state the limitations of each measurement.",
    ]),
    h2("3.3 Requirements and Constraints"),
    P("**Functional requirements.** The gate must log entry and exit for registered vehicles, count vehicles without cards through the guard, and flag unknown cards. The speed system must show live speeds, keep a violations log with evidence, and alert guards. The parking system must show each bay’s state and each lot’s occupancy to operators and drivers."),
    P("**Non-functional requirements.** Identifiers must be encrypted and authenticated end to end. No movement record may be lost or duplicated by a network fault. The speed console must keep working offline on the drone’s own Wi-Fi. Applications may read only what they need, so backend writers use a privileged key while the apps use a publishable key restricted by row-level security. Guard accounts are limited to the institute’s e-mail domain."),
    P("**Constraints.** The project used commodity parts: two ESP32 boards with SX1276 radios and an MFRC522 reader, a consumer drone whose camera streams MJPEG at 640 × 352 pixels and about 22 fps over Wi-Fi, an analog camera with a 170° fisheye lens, and one laptop with 24 GB of shared memory that has to run the vision models one at a time. The laptop has a single Wi-Fi radio, so it cannot join the drone’s network and the internet at once. The radio must use a licence-free band."),
  ];
}

module.exports = { ch1, ch2, ch3 };
