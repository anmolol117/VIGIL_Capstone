#!/usr/bin/env python3
"""System architecture flowchart -> Downloads/VIGIL_System_Flowchart.png

Swim lanes, one per device. The outbound path runs down and to the right; the
return path runs back along a single lower band, so the acknowledged round trip
reads as one horseshoe and no two steps ever share a height within a lane.
Palette survives greyscale (lanes differ in value, not only hue).
"""
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.patches import FancyBboxPatch, Polygon, FancyArrowPatch

plt.rcParams["font.family"] = "DejaVu Sans"

W, H = 15.7, 11.5
fig, ax = plt.subplots(figsize=(W, H))
ax.set_xlim(0, W); ax.set_ylim(-0.95, 11.85); ax.axis("off")
fig.patch.set_facecolor("white")

INK, MUTED, WARN, BAD = "#16202B", "#5C6B7A", "#B5730A", "#A62B2B"
LANES = [   # x0,  w,   title,            subtitle,                   tint,      accent
    (0.35, 3.90, "GATE UNIT",      "ESP32 · MFRC522 · SX1276", "#E7EEF5", "#1F4E79"),
    (4.50, 2.40, "RELAY",          "ESP32 · SX1276",           "#EDEFF2", "#5A6B7A"),
    (7.15, 4.50, "CONTROL CENTRE", "Laptop · gateway",         "#E8F0E9", "#2D6A3E"),
    (11.90, 3.40, "DATABASE",      "PostgreSQL",               "#F5EDE4", "#8A5A2B"),
]
LX = {t: (x, w, a) for x, w, t, _, _, a in LANES}
TOP, BOT = 10.12, -0.18

for x, w, title, sub, tint, accent in LANES:
    ax.add_patch(FancyBboxPatch((x, BOT), w, TOP - BOT, boxstyle="round,pad=0,rounding_size=0.06",
                                fc=tint, ec="none", zorder=0))
    ax.add_patch(FancyBboxPatch((x, TOP + 0.06), w, 0.62,
                                boxstyle="round,pad=0,rounding_size=0.06", fc=accent, ec="none", zorder=1))
    ax.text(x + w / 2, TOP + 0.50, title, ha="center", va="center", color="white",
            fontsize=11.5, fontweight="bold", zorder=2)
    ax.text(x + w / 2, TOP + 0.24, sub, ha="center", va="center", color="white",
            fontsize=8.2, alpha=0.88, zorder=2)


def box(lane, y, text, h=0.60, kind="process", w=2.70, fs=9.3):
    x0, lw, accent = LX[lane]
    cx = x0 + lw / 2
    if kind == "terminal":
        ax.add_patch(FancyBboxPatch((cx - w / 2, y - h / 2), w, h,
                                    boxstyle=f"round,pad=0,rounding_size={h/2}",
                                    fc=accent, ec=accent, lw=1.4, zorder=3))
        ax.text(cx, y, text, ha="center", va="center", color="white", fontsize=fs,
                fontweight="bold", zorder=4)
    elif kind == "decision":
        ax.add_patch(Polygon([(cx, y + h), (cx + w / 2, y), (cx, y - h), (cx - w / 2, y)],
                             closed=True, fc="#FDF4E3", ec=WARN, lw=1.3, zorder=3))
        ax.text(cx, y, text, ha="center", va="center", color=INK, fontsize=fs - 0.5, zorder=4)
    elif kind == "store":
        ax.add_patch(FancyBboxPatch((cx - w / 2, y - h / 2), w, h,
                                    boxstyle="round,pad=0,rounding_size=0.04",
                                    fc="white", ec=accent, lw=2.4, zorder=3))
        ax.text(cx, y, text, ha="center", va="center", color=INK, fontsize=fs,
                fontweight="bold", zorder=4)
    else:
        ax.add_patch(FancyBboxPatch((cx - w / 2, y - h / 2), w, h,
                                    boxstyle="round,pad=0,rounding_size=0.09",
                                    fc="white", ec=accent, lw=1.4, zorder=3))
        ax.text(cx, y, text, ha="center", va="center", color=INK, fontsize=fs, zorder=4)
    return cx


def arr(p, q, label=None, colour=INK, lw=1.5, ls="-", dy=0.17, dx=0.0, fs=8.4):
    ax.add_patch(FancyArrowPatch(p, q, arrowstyle="-|>", mutation_scale=15, lw=lw,
                                 color=colour, zorder=2, linestyle=ls, shrinkA=2, shrinkB=2))
    if label:
        ax.text((p[0] + q[0]) / 2 + dx, (p[1] + q[1]) / 2 + dy, label, ha="center",
                va="center", fontsize=fs, color=colour, zorder=5,
                bbox=dict(fc="white", ec="none", pad=1.5, alpha=0.96))


def elbow(pts, colour, lw=1.6, ls="-"):
    """Orthogonal routed line, arrowhead on the final segment."""
    for a, b in zip(pts[:-2], pts[1:-1]):
        ax.plot([a[0], b[0]], [a[1], b[1]], color=colour, lw=lw, ls=ls, zorder=2,
                solid_capstyle="round")
    ax.add_patch(FancyArrowPatch(pts[-2], pts[-1], arrowstyle="-|>", mutation_scale=15,
                                 lw=lw, color=colour, zorder=2, linestyle=ls, shrinkA=0, shrinkB=2))


OUT_Y, RET_Y = 5.50, 1.58          # the outbound hop and the return hop

# ── gate unit, outbound ────────────────────────────────────────────────────
box("GATE UNIT", 9.55, "Vehicle presents card", kind="terminal", h=0.56)
box("GATE UNIT", 8.80, "Read RFID UID   (3.5 ms)")
box("GATE UNIT", 8.00, "Decoded?", kind="decision", h=0.38, w=1.95)
box("GATE UNIT", 7.20, "Assign counter, queue scan")
box("GATE UNIT", 6.40, "Encrypt UID · AES-256-GCM\n(0.07 ms)", h=0.74)
box("GATE UNIT", OUT_Y, "Transmit LoRa frame · 433 MHz\n(78 ms)", h=0.74)
GX = 0.35 + 3.90 / 2

arr((GX, 9.27), (GX, 9.13))
arr((GX, 8.50), (GX, 8.40))
arr((GX, 7.62), (GX, 7.52), "yes", dx=0.30, dy=0.0)
arr((GX, 6.90), (GX, 6.79))
arr((GX, 6.03), (GX, 5.89))
elbow([(GX - 0.98, 8.00), (0.72, 8.00), (0.72, 8.80), (GX - 1.35, 8.80)], MUTED, lw=1.3)
ax.text(0.72, 8.40, "no —\nretry", ha="center", va="center", fontsize=7.8, color=MUTED,
        bbox=dict(fc="#E7EEF5", ec="none", pad=1.2))

# ── outbound hop ───────────────────────────────────────────────────────────
box("RELAY", OUT_Y, "Relay frame\nto USB serial", h=0.74)
box("CONTROL CENTRE", OUT_Y, "Decrypt and verify tag", w=3.10)
arr((GX + 1.35, OUT_Y), (4.50 + 1.20 - 1.35, OUT_Y), "encrypted", dy=0.24)
arr((4.50 + 1.20 + 1.35, OUT_Y), (7.15 + 2.25 - 1.55, OUT_Y), "encrypted", dy=0.24)

# ── control centre ─────────────────────────────────────────────────────────
CX = 7.15 + 4.50 / 2
box("CONTROL CENTRE", 4.68, "Authentic?", kind="decision", h=0.38, w=2.10)
box("CONTROL CENTRE", 3.88, "Look up card in  tags", w=3.10)
box("CONTROL CENTRE", 3.10, "registered  ·  attendant  ·  unknown", w=3.90, fs=8.6)
box("CONTROL CENTRE", 2.34, "INSERT movement record", w=3.10)
box("CONTROL CENTRE", RET_Y, "Seal status as ACK", w=3.10)

arr((CX, OUT_Y - 0.30), (CX, 5.08))
arr((CX, 4.30), (CX, 4.20), "yes", dx=0.30, dy=0.0)
arr((CX, 3.58), (CX, 3.42))
arr((CX, 2.78), (CX, 2.66))
arr((CX + 1.05, 4.68), (11.52, 4.68), colour=BAD, lw=1.3)
ax.text(10.95, 4.94, "no — discarded,\nno acknowledgement", ha="center", va="center",
        fontsize=7.9, color=BAD, style="italic")

# ── database ───────────────────────────────────────────────────────────────
DX = 11.90 + 3.40 / 2
box("DATABASE", 2.34, "(device, counter)\nalready present?", kind="decision", h=0.46, w=3.05, fs=8.5)
box("DATABASE", RET_Y, "COMMIT   /   reject duplicate", kind="store", w=3.05, fs=8.6)
arr((CX + 1.55, 2.34), (DX - 1.53, 2.34))
arr((DX, 2.34 - 0.48), (DX, RET_Y + 0.32))

# ── return hop ─────────────────────────────────────────────────────────────
box("RELAY", RET_Y, "Relay ACK\nto LoRa   (73 ms)", h=0.74)
box("GATE UNIT", RET_Y, "Verify ACK tag\nand echoed counter", h=0.74)
arr((DX - 1.53, RET_Y), (CX + 1.55, RET_Y), "status", dy=0.22)
arr((CX - 1.55, RET_Y), (4.50 + 1.20 + 1.35, RET_Y), "encrypted", dy=0.22)
arr((4.50 + 1.20 - 1.35, RET_Y), (GX + 1.35, RET_Y), "encrypted", dy=0.22)

box("GATE UNIT", 0.80, "Valid?", kind="decision", h=0.36, w=1.85)
box("GATE UNIT", 0.14, "Movement recorded", kind="terminal", h=0.52)
arr((GX, RET_Y - 0.37), (GX, 1.18))
arr((GX, 0.44), (GX, 0.42), "yes", dx=0.30, dy=0.0)

# retransmit: same counter, so the database can recognise the repeat
elbow([(GX - 0.93, 0.80), (0.72, 0.80), (0.72, OUT_Y), (GX - 1.35, OUT_Y)], WARN, lw=1.6, ls=(0, (5, 3)))
ax.text(0.90, 3.25, "no, or 3 s timeout —\nretransmit with the\nsame counter\n(3 tries, then queue)",
        ha="left", va="center", fontsize=8.0, color=WARN, style="italic",
        bbox=dict(fc="#E7EEF5", ec="none", pad=1.6))

# ── the encrypted span ─────────────────────────────────────────────────────
for x in (4.42, 7.02):
    ax.plot([x, x], [BOT, TOP], color=BAD, lw=1.4, ls=(0, (6, 4)), zorder=1, alpha=0.75)
ax.text(5.72, -0.62, "payload stays encrypted across both links — the relay holds no key",
        ha="center", va="center", fontsize=8.6, color=BAD, style="italic")

ax.text(0.35, 11.52, "VIGIL — System Architecture", fontsize=17.5, fontweight="bold", color=INK)
ax.text(0.35, 11.18, "RFID identification over LoRa with authenticated encryption and "
        "database-confirmed acknowledgement", fontsize=10, color=MUTED)
ax.text(W - 0.35, 11.36, "the acknowledgement is issued only\nafter the database confirms the write",
        fontsize=9.2, color=INK, ha="right", va="center", style="italic")

fig.savefig("/Users/anmol117/Downloads/VIGIL_System_Flowchart.png", dpi=200,
            bbox_inches="tight", pad_inches=0.30, facecolor="white")
print("wrote /Users/anmol117/Downloads/VIGIL_System_Flowchart.png")
