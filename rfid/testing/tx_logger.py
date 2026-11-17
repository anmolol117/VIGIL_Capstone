#!/usr/bin/env python3
"""Captures the transmitter's serial output during a bench run.

The gateway only reads the receiver's port, so the transmitter's TIMING and
DETECT lines have nowhere to go unless something else is listening. Run this
alongside gateway_test.py, with the same --tag.

    python3 tx_logger.py --tag phase3

Writes results/<tag>_tx.csv. Auto-detects the transmitter by its boot banner if
--port is not given, since its device name changes with the USB socket used.
"""

import argparse
import csv
import pathlib
import sys
import time

import serial
import serial.tools.list_ports

RESULTS = pathlib.Path(__file__).resolve().parent / "results"


def find_transmitter():
    """The transmitter is whichever port answers with the TX boot banner."""
    ports = [p.device for p in serial.tools.list_ports.comports()
             if "usbserial" in p.device or "wchusbserial" in p.device]
    for port in ports:
        try:
            with serial.Serial(port, 115200, timeout=1) as s:
                s.setDTR(False); time.sleep(0.12); s.setDTR(True)
                time.sleep(2.6)
                out = s.read(s.in_waiting or 1).decode("ascii", "replace")
            if "self-test" in out or "RFID initialized" in out:
                return port, ("BENCH" in out)
        except serial.SerialException:
            continue          # busy: almost certainly the receiver, held by the gateway
    return None, False


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--tag", required=True)
    ap.add_argument("--port")
    ap.add_argument("--baud", type=int, default=115200)
    a = ap.parse_args()

    port, bench = (a.port, None) if a.port else find_transmitter()
    if not port:
        sys.exit("transmitter not found. Is it plugged in, and is the gateway "
                 "holding the receiver rather than this board?")
    if bench is False:
        print("WARNING: that board is running PRODUCTION firmware -- it emits no "
              "TIMING or DETECT lines.\n", file=sys.stderr)

    RESULTS.mkdir(exist_ok=True)
    path = RESULTS / f"{a.tag}_tx.csv"

    # Deliberately NOT using readline(): when the CH340 driver wedges, the
    # underlying read() blocks indefinitely and pyserial's timeout never fires,
    # leaving the process alive, the fd held, and the capture silently dead.
    # Polling in_waiting and reassembling lines ourselves keeps the loop
    # responsive, so a stalled stream is detectable and the port can be reopened.
    STALE = 20.0          # seconds of silence before assuming the stream died

    n_scan = n_detect = 0
    buf = b""

    def emit(line):
        nonlocal n_scan, n_detect
        w.writerow([time.strftime("%H:%M:%S"), line]); f.flush()
        if line.startswith("TIMING,"):
            n_scan += 1
            p = line.split(",")
            print(f"  [{n_scan:3d}] ctr={p[1]}  read={int(p[2])/1000:.1f}ms  "
                  f"seal={int(p[3])/1000:.2f}ms  tx={int(p[4])/1000:.1f}ms  "
                  f"wait={int(p[5])/1000:.0f}ms  total={int(p[6])/1000:.0f}ms  "
                  f"try={p[7]}  {p[8] if len(p) > 8 else ''}", flush=True)
        elif line.startswith("DETECT,"):
            n_detect += 1
            kind = line.split(",")[1]
            if kind != "ok":
                print(f"        DETECT,{kind}   (detections: {n_detect})", flush=True)
        elif not line.startswith(("TX uid", "card read")):
            print(f"  {line}", flush=True)

    f = open(path, "w", newline="")
    w = csv.writer(f)
    w.writerow(["ts", "line"])
    print(f"capturing transmitter on {port} -> {path.name}   (Ctrl-C to stop)\n")

    ser = serial.Serial(port, a.baud, timeout=0)
    last_data = time.time()
    try:
        while True:
            try:
                n = ser.in_waiting
                if n:
                    buf += ser.read(n)
                    last_data = time.time()
                    while b"\n" in buf:
                        raw, buf = buf.split(b"\n", 1)
                        line = raw.decode("ascii", "replace").strip()
                        if line:
                            emit(line)
                else:
                    time.sleep(0.02)
            except (serial.SerialException, OSError) as e:
                print(f"  serial error: {e} -- reopening", file=sys.stderr, flush=True)
                last_data = 0

            if time.time() - last_data > STALE:
                print(f"  stream silent {STALE:.0f}s -- reopening port", file=sys.stderr, flush=True)
                try:
                    ser.close()
                except Exception:
                    pass
                time.sleep(0.5)
                try:
                    ser = serial.Serial(port, a.baud, timeout=0)
                    buf = b""
                    last_data = time.time()
                except serial.SerialException as e:
                    print(f"  reopen failed: {e}", file=sys.stderr, flush=True)
                    time.sleep(2)
    except KeyboardInterrupt:
        print(f"\n\n{n_detect} detections, {n_scan} completed scans -> {path.name}")
    finally:
        try:
            ser.close()
        except Exception:
            pass
        f.close()


if __name__ == "__main__":
    main()
