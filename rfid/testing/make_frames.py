#!/usr/bin/env python3
"""Builds a file of valid encrypted DATA frames for Phase 4 (no hardware needed).

Each frame carries a distinct counter, so every one produces a real INSERT
rather than tripping the uniqueness constraint and returning "already recorded".

    python3 make_frames.py                    # 30 registered-card frames
    python3 make_frames.py -n 50 --uid ffffffff --out unknown.hex
"""

import argparse
import os
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent / "gateway"))

from cryptography.hazmat.primitives.ciphers.aead import AESGCM   # noqa: E402
from gateway import TYPE_DATA, load_env, make_nonce              # noqa: E402

ap = argparse.ArgumentParser()
ap.add_argument("-n", type=int, default=30, help="how many frames")
ap.add_argument("--uid", default="aa8d0504", help="card UID as hex")
ap.add_argument("--device", type=int, default=11, help="device id (11 = bench)")
ap.add_argument("--start", type=int, default=700000, help="first counter value")
ap.add_argument("--out", default="frames.hex")
a = ap.parse_args()

load_env()
key = bytes.fromhex(os.environ["PSK_DATA"])
uid = bytes.fromhex(a.uid)

lines = []
for i in range(a.n):
    ctr = a.start + i
    hdr = (bytes([0x01, TYPE_DATA]) + a.device.to_bytes(4, "little")
           + ctr.to_bytes(8, "little"))
    body = AESGCM(key).encrypt(make_nonce(TYPE_DATA, a.device, ctr), uid, hdr)
    lines.append((hdr + body).hex())

out = pathlib.Path(__file__).resolve().parent / a.out
out.write_text("\n".join(lines) + "\n")
print(f"wrote {a.n} frames -> {out.name}")
print(f"  uid={a.uid}  device_id={a.device}  counters {a.start}..{a.start + a.n - 1}")
print(f"\nclean up afterwards with:")
print(f"  delete from vehicle_logs where device_id = {a.device};")
