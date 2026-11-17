"""Diagrams and composites for the VIGIL capstone report (written to figs/)."""
import sys
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.patches import FancyBboxPatch
from PIL import Image, ImageChops

OUT = Path(sys.argv[1])
SHOTS = Path(sys.argv[2])     # earlier simulator captures
MEDIA = Path(sys.argv[3])     # web/media
plt.rcParams.update({"font.family": "Times New Roman", "font.size": 11})

INK, LINE = "#1D1D1F", "#6E6E73"
FILL = {"gate": "#E8F1FC", "road": "#FDECEC", "lot": "#E7F6EC", "db": "#F2F2F7", "ui": "#FFFFFF"}


def box(ax, x, y, w, h, title, body="", kind="ui", bold=True):
    ax.add_patch(FancyBboxPatch((x, y), w, h, boxstyle="round,pad=0.008,rounding_size=0.012",
                                fc=FILL[kind], ec=INK, lw=1.1))
    if body:
        ax.text(x + w / 2, y + h * 0.66, title, ha="center", va="center", weight="bold" if bold else "normal", fontsize=11)
        ax.text(x + w / 2, y + h * 0.30, body, ha="center", va="center", fontsize=9.2, color="#333336", linespacing=1.25)
    else:
        ax.text(x + w / 2, y + h / 2, title, ha="center", va="center", weight="bold" if bold else "normal", fontsize=10.5)


def arrow(ax, a, b, label="", both=False, lx=0, ly=0):
    ax.annotate("", xy=b, xytext=a, arrowprops=dict(arrowstyle="<|-|>" if both else "-|>", color=INK, lw=1.1,
                                                   shrinkA=0, shrinkB=0, mutation_scale=11))
    if label:
        ax.text((a[0] + b[0]) / 2 + lx, (a[1] + b[1]) / 2 + ly, label, ha="center", va="center", fontsize=8.6,
                color="#333336", bbox=dict(fc="white", ec="none", pad=1.2))


def canvas(w, h):
    fig = plt.figure(figsize=(w, h), dpi=220)
    ax = fig.add_axes([0, 0, 1, 1])
    ax.set_xlim(0, 1); ax.set_ylim(0, 1); ax.axis("off")
    return fig, ax


def architecture():
    fig, ax = canvas(10, 6.6)
    cols = [0.03, 0.355, 0.68]; cw = 0.29
    # sensing
    box(ax, cols[0], 0.80, cw, 0.15, "Gate unit", "ESP32 + MFRC522 reader\nSX1276 LoRa radio, 433 MHz", "gate")
    box(ax, cols[1], 0.80, cw, 0.15, "Drone camera", "Hovering over the road\nRTSP video over Wi-Fi", "road")
    box(ax, cols[2], 0.80, cw, 0.15, "Lot camera", "Fixed analog camera\nabove the parking lot", "lot")
    # processing
    box(ax, cols[0], 0.53, cw, 0.17, "Receiver + gateway", "Receiver ESP32 relays frames\nLaptop gateway decrypts,\nverifies and logs each scan", "gate")
    box(ax, cols[1], 0.53, cw, 0.17, "Speed pipeline", "Detect, track, stabilise,\nmeasure speed, log offenders\nwith an evidence photo", "road")
    box(ax, cols[2], 0.53, cw, 0.17, "Parking pipeline", "Register frames, detect,\ntrack, decide each bay\nfree or taken", "lot")
    arrow(ax, (cols[0] + cw / 2, 0.80), (cols[0] + cw / 2, 0.70), "AES-256-GCM over LoRa, ACK", both=True)
    arrow(ax, (cols[1] + cw / 2, 0.80), (cols[1] + cw / 2, 0.70), "RTSP / RTP")
    arrow(ax, (cols[2] + cw / 2, 0.80), (cols[2] + cw / 2, 0.70), "video")
    # database
    box(ax, 0.03, 0.33, 0.94, 0.11, "Cloud PostgreSQL Database",
        "PostgreSQL with row-level security  ·  Realtime  ·  Storage for evidence photos  ·  Edge function for push", "db")
    for c in cols:
        arrow(ax, (c + cw / 2, 0.53), (c + cw / 2, 0.44), "HTTPS")
    # people
    box(ax, 0.03, 0.06, 0.29, 0.17, "Gate log", "Who is on campus,\nevery entry and exit", "ui")
    box(ax, 0.355, 0.145, 0.135, 0.085, "Speed console", "", "ui", bold=False)
    box(ax, 0.505, 0.145, 0.14, 0.085, "Guard Alert app", "", "ui", bold=False)
    box(ax, 0.355, 0.045, 0.29, 0.085, "Three nearest guards on duty", "", "ui", bold=False)
    box(ax, 0.68, 0.145, 0.135, 0.085, "Parking console", "", "ui", bold=False)
    box(ax, 0.83, 0.145, 0.14, 0.085, "Campus Parking app", "", "ui", bold=False)
    box(ax, 0.68, 0.045, 0.29, 0.085, "Drivers looking for a space", "", "ui", bold=False)
    for x in (0.175, 0.42, 0.575, 0.745, 0.90):
        arrow(ax, (x, 0.33), (x, 0.235 if x != 0.175 else 0.23))
    fig.savefig(OUT / "architecture.png", facecolor="white"); plt.close(fig)


def flow(name, steps, side=None, size=(10, 2.6)):
    fig, ax = canvas(*size)
    n = len(steps); gap = 0.018; w = (0.96 - gap * (n - 1)) / n; y, h = (0.42, 0.42) if side else (0.2, 0.6)
    xs = []
    for i, (t, b, k) in enumerate(steps):
        x = 0.02 + i * (w + gap); xs.append(x)
        box(ax, x, y, w, h, t, b, k)
        if i:
            arrow(ax, (x - gap, y + h / 2), (x, y + h / 2))
    if side:
        i_from, i_to, t, b = side
        sx = xs[i_from]; box(ax, sx, 0.04, (xs[i_to] + w) - sx, 0.22, t, b, "db")
        arrow(ax, (xs[i_from] + w / 2, y), (xs[i_from] + w / 2, 0.26))
        arrow(ax, (xs[i_to] + w / 2, 0.26), (xs[i_to] + w / 2, y))
    fig.savefig(OUT / name, facecolor="white"); plt.close(fig)


def gantt():
    import datetime as dt
    rows = [  # (label, start, end, kind): the four members' plans merged; nothing after 20 Nov
        ("Project initiation and planning", "2026-01-11", "2026-02-14", "db"),
        ("Literature review and system design", "2026-02-11", "2026-04-15", "db"),
        ("Camera feed testing and drone build", "2026-04-10", "2026-09-08", "road"),
        ("Vehicle tracking and speed estimation", "2026-02-12", "2026-10-31", "road"),
        ("RFID gate: circuit, firmware and protocol", "2026-05-08", "2026-08-16", "gate"),
        ("RFID database, dashboard and bench tests", "2026-06-16", "2026-09-30", "gate"),
        ("Parking research and slot detection", "2026-02-15", "2026-10-31", "lot"),
        ("Mobile apps, web console and integration", "2026-06-16", "2026-10-31", "lot"),
        ("Testing, verification and final demonstration", "2026-09-08", "2026-11-20", "db"),
    ]
    fig, ax = plt.subplots(figsize=(10, 4.4), dpi=220)
    d = lambda s: dt.date.fromisoformat(s).toordinal()
    for i, (label, a, b, k) in enumerate(rows):
        ax.barh(i, d(b) - d(a), left=d(a), height=0.56, color=FILL[k], edgecolor=INK, lw=0.9)
    ax.set_yticks(range(len(rows)), [r[0] for r in rows], fontsize=10)
    ax.invert_yaxis()
    months = [dt.date(2026, m, 1) for m in range(1, 13)]
    ax.set_xticks([m.toordinal() for m in months], [m.strftime("%b") for m in months], fontsize=10)
    ax.set_xlim(months[0].toordinal(), dt.date(2026, 11, 30).toordinal())
    ax.grid(axis="x", color="#D2D2D7", lw=0.8); ax.set_axisbelow(True)
    for s in ("top", "right"): ax.spines[s].set_visible(False)
    fig.tight_layout(); fig.savefig(OUT / "gantt.png", facecolor="white"); plt.close(fig)


def side_by_side(name, paths, height=1500, gap=90):
    ims = [Image.open(p).convert("RGB") for p in paths]
    ims = [im.resize((round(im.width * height / im.height), height), Image.LANCZOS) for im in ims]
    out = Image.new("RGB", (sum(i.width for i in ims) + gap * (len(ims) - 1), height), "white")
    x = 0
    for im in ims:
        out.paste(im, (x, 0)); x += im.width + gap
    out.save(OUT / name)


def trim(name, src, pad=40):
    im = Image.open(src).convert("RGB")
    box_ = ImageChops.difference(im, Image.new("RGB", im.size, "white")).getbbox()
    im = im.crop((max(box_[0] - pad, 0), max(box_[1] - pad, 0), min(box_[2] + pad, im.width), min(box_[3] + pad, im.height)))
    im.save(OUT / name)


architecture()
flow("speed_pipeline.png", [
    ("Drone video", "Overhead view,\nRTSP over Wi-Fi", "road"),
    ("Detect + track", "VisDrone YOLO\n+ ByteTrack", "road"),
    ("Stabilise", "Register to a\nreference view", "road"),
    ("Project", "Nadir model,\nscale s = h / f", "road"),
    ("Measure", "Least-squares fit\nover 0.6 s", "road"),
    ("Reliability gate", "Moving, stopped\nor unreliable", "road"),
    ("Log + dispatch", "Peak speed, photo,\nnearest guards", "road"),
], side=(1, 3, "Scale self-calibration (once per flight)", "25th percentile of perpendicular car widths against W = 1.83 m"), size=(10, 3.3))
flow("parking_pipeline.png", [
    ("Register", "ORB + RANSAC\nhomography to the\nlot's reference image", "lot"),
    ("Detect", "VisDrone YOLO,\nconf 0.10", "lot"),
    ("Warp", "Boxes mapped into\nthe reference frame", "lot"),
    ("Track", "ByteTrack in the\nstabilised frame", "lot"),
    ("Count", "Parked test in metres,\n3-sample debounce\nper bay", "lot"),
    ("Write", "Bay changes at once,\nsnapshot every 20 s,\noffline queue", "lot"),
], size=(10, 2.5))
gantt()
side_by_side("guard_app.png", [SHOTS / "ga_home.png", SHOTS / "ga_alert.png"])
side_by_side("campus_app.png", [SHOTS / "cp_lots.png", SHOTS / "cp_map.png"])
trim("gate_arch_trim.png", OUT / "gate_arch.png")
Image.open(MEDIA / "parking-sign.webp").convert("RGB").save(OUT / "parking_sign.png")
Image.open(MEDIA / "road.jpg").convert("RGB").save(OUT / "speed_frame.jpg", quality=90)
print("figures written:", sorted(p.name for p in OUT.iterdir()))
