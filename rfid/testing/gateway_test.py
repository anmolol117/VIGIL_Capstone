#!/usr/bin/env python3
"""Instrumented gateway for bench testing. Writes per-stage timings to CSV.

Imports crypto and database code from the production gateway rather than
copying it, so the code under test is genuinely the production code -- only the
measurement wrapper differs.

    python3 gateway_test.py --tag phase3          # live, via the receiver
    python3 gateway_test.py --tag phase4 --replay frames.hex   # no hardware

Two CSVs per run, in results/:
    <tag>_gateway.csv    one row per frame: per-stage laptop-side latency
    <tag>_serial.csv     raw transmitter/receiver lines, for cross-referencing
"""

import argparse
import csv
import pathlib
import sys
import time

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent / "gateway"))

import httpx                                                    # noqa: E402
from cryptography.exceptions import InvalidTag                  # noqa: E402

from gateway import (ST_COMMITTED, ST_DB_ERROR, ST_DUPLICATE,   # noqa: E402
                     ST_UNREGISTERED, Supabase, load_env, open_data,
                     parse_rx_line, seal_ack)

RESULTS = pathlib.Path(__file__).resolve().parent / "results"
COLUMNS = ["ts", "counter", "device_id", "uid", "kind", "status", "rssi", "snr",
           "decrypt_ms", "lookup_tag_ms", "lookup_guard_ms", "insert_ms",
           "seal_ack_ms", "gateway_total_ms"]


class Timer:
    """Accumulates named stage durations in milliseconds for one frame."""

    def __init__(self):
        self.d = {}
        self._t0 = time.perf_counter()

    def __call__(self, name):
        self._mark = name
        self._start = time.perf_counter()
        return self

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.d[self._mark] = (time.perf_counter() - self._start) * 1000
        return False

    @property
    def total_ms(self):
        return (time.perf_counter() - self._t0) * 1000


def handle(key, db, frame, rssi, snr, writer):
    t = Timer()
    row = {c: "" for c in COLUMNS}
    row["ts"] = time.strftime("%H:%M:%S")
    row["rssi"], row["snr"] = rssi if rssi is not None else "", snr if snr is not None else ""

    try:
        with t("decrypt"):
            device_id, counter, uid = open_data(key, frame)
    except (InvalidTag, ValueError) as e:
        print(f"  rejected: {e}", file=sys.stderr)
        return None

    uid_hex = uid.hex()
    row.update(counter=counter, device_id=device_id, uid=uid_hex)
    print(f"  uid={uid_hex} ctr={counter} rssi={rssi}")

    try:
        with t("lookup_tag"):
            vehicle_id, direction = db.lookup_tag(uid_hex)

        if vehicle_id:
            row["kind"] = "vehicle"
            with t("insert"):
                status = db.insert_log(device_id, counter, uid_hex, vehicle_id,
                                       direction, rssi, snr)
        else:
            with t("lookup_guard"):
                guard_dir = db.lookup_guard_tag(uid_hex)
            if guard_dir:
                row["kind"] = "guard"
                with t("insert"):
                    status = db.insert_unauthorised(device_id, counter, uid_hex,
                                                    guard_dir, rssi, snr)
            else:
                row["kind"] = "unknown"
                with t("insert"):
                    status = db.insert_log(device_id, counter, uid_hex, None,
                                           None, rssi, snr)
    except httpx.HTTPError as e:
        print(f"  network error: {e}", file=sys.stderr)
        row["kind"] = row["kind"] or "error"
        status = ST_DB_ERROR

    with t("seal_ack"):
        ack = seal_ack(key, device_id, counter, status)

    label = {ST_COMMITTED: "committed", ST_DUPLICATE: "duplicate",
             ST_DB_ERROR: "db error", ST_UNREGISTERED: "unregistered"}.get(status, hex(status))
    print(f"  -> {row['kind']}, {label}")

    row.update(status=f"0x{status:02x}",
               decrypt_ms=f"{t.d.get('decrypt', 0):.2f}",
               lookup_tag_ms=f"{t.d.get('lookup_tag', 0):.2f}",
               lookup_guard_ms=f"{t.d['lookup_guard']:.2f}" if "lookup_guard" in t.d else "",
               insert_ms=f"{t.d.get('insert', 0):.2f}",
               seal_ack_ms=f"{t.d.get('seal_ack', 0):.3f}",
               gateway_total_ms=f"{t.total_ms:.2f}")
    writer.writerow(row)
    return ack


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--tag", required=True, help="run label, used in the CSV filenames")
    ap.add_argument("--port")
    ap.add_argument("--baud", type=int, default=115200)
    ap.add_argument("--replay", metavar="FILE", help="feed hex frames from a file, no radio")
    args = ap.parse_args()

    load_env()
    import os
    key = bytes.fromhex(os.environ["PSK_DATA"])
    db = Supabase(os.environ["SUPABASE_URL"], os.environ["SUPABASE_SERVICE_KEY"])
    port = args.port or os.environ.get("SERIAL_PORT")

    RESULTS.mkdir(exist_ok=True)
    gw_path = RESULTS / f"{args.tag}_gateway.csv"
    sr_path = RESULTS / f"{args.tag}_serial.csv"

    # One warm-up call: the first HTTPS request pays TLS handshake (~2 s cold),
    # which would otherwise land in the sample and skew every mean.
    t0 = time.perf_counter()
    db.lookup_tag("0000warmup0000")
    print(f"warm-up request: {(time.perf_counter()-t0)*1000:.0f} ms (discarded)\n")

    with open(gw_path, "w", newline="") as gf:
        w = csv.DictWriter(gf, fieldnames=COLUMNS)
        w.writeheader()

        if args.replay:
            for n, raw in enumerate(pathlib.Path(args.replay).read_text().split(), 1):
                print(f"[{n}]")
                handle(key, db, bytes.fromhex(raw), None, None, w)
                gf.flush()
            print(f"\nwrote {gw_path.relative_to(RESULTS.parent)}")
            return

        if not port:
            sys.exit("no serial port: pass --port or set SERIAL_PORT in gateway/.env")

        import serial
        # Reconnect rather than die. Unplugging the receiver is a deliberate
        # test condition here, and a harness that crashes on it cannot observe
        # the recovery it exists to measure. The CSV is opened once, outside
        # the reconnect loop, so a reconnect never truncates captured data.
        with open(sr_path, "w", newline="") as sf:
            sw = csv.writer(sf)
            sw.writerow(["ts", "line"])
            while True:
                try:
                    with serial.Serial(port, args.baud, timeout=1) as ser:
                        print(f"listening on {port}   (Ctrl-C to stop)\n")
                        time.sleep(2)
                        ser.reset_input_buffer()
                        while True:
                            raw = ser.readline().decode("ascii", "replace").strip()
                            if not raw:
                                continue
                            sw.writerow([time.strftime("%H:%M:%S"), raw]); sf.flush()
                            if raw.startswith("#"):
                                print(f"receiver: {raw}")
                                continue
                            parsed = parse_rx_line(raw)
                            if not parsed:
                                continue
                            print(f"RX {raw[:52]}...")
                            ack = handle(key, db, *parsed, writer=w)
                            gf.flush()
                            if ack:
                                ser.write(b"A " + ack.hex().encode() + b"\n")
                except KeyboardInterrupt:
                    print(f"\n\nwrote {gw_path.name} and {sr_path.name} in results/")
                    return
                except (serial.SerialException, OSError) as e:
                    print(f"serial lost ({e}); retrying in 3 s", file=sys.stderr, flush=True)
                    time.sleep(3)


if __name__ == "__main__":
    main()
