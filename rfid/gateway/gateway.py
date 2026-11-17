#!/usr/bin/env python3
"""Laptop gateway: decrypt LoRa frames off the receiver, commit to Supabase, ACK back.

The ACK only leaves here after the database has confirmed the write, so the
transmitter's green light means "durably logged", not "radio reached something".

Usage:
    python gateway.py                      # normal operation
    python gateway.py --enroll             # print UIDs instead of logging them
    python gateway.py --replay frames.hex  # drive the pipeline with no radio
"""

import argparse
import os
import sys
import time

import httpx
from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

# --- protocol constants, must match transmitter.ino ---
PROTO_VERSION = 0x01
TYPE_DATA = 0x01
TYPE_ACK = 0x02
HDR_LEN = 14
TAG_LEN = 16

ST_COMMITTED = 0x00
ST_DUPLICATE = 0x01
ST_DB_ERROR = 0x02
ST_UNREGISTERED = 0x03
ST_NOT_LOGGED = 0x04  # enroll mode: acknowledged but deliberately not written


def load_env(path=None):
    """Minimal .env reader -- not worth a dependency for KEY=value lines.

    Resolved next to this file, not the cwd, so the script works from anywhere.
    """
    if path is None:
        path = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".env")
    if not os.path.exists(path):
        return
    with open(path) as fh:
        for raw in fh:
            line = raw.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                os.environ.setdefault(k.strip(), v.strip().strip("'\""))


# ---------------------------------------------------------------- crypto


def make_nonce(msg_type: int, device_id: int, counter: int) -> bytes:
    """nonce = type | device_id[4 LE] | counter[low 7 bytes LE].

    The type byte is what stops a DATA frame and its ACK -- same key, same
    counter -- from sharing a nonce. GCM nonce reuse under one key leaks the
    keystream and allows tag forgery, so this must match the ESP32 exactly.
    """
    return (
        bytes([msg_type])
        + device_id.to_bytes(4, "little")
        + (counter & ((1 << 56) - 1)).to_bytes(7, "little")
    )


def open_data(key: bytes, frame: bytes):
    """Verify and decrypt a DATA frame -> (device_id, counter, uid_bytes).

    Raises ValueError / InvalidTag on anything malformed or tampered.
    """
    if len(frame) < HDR_LEN + 1 + TAG_LEN:
        raise ValueError(f"frame too short ({len(frame)} bytes)")
    if frame[0] != PROTO_VERSION:
        raise ValueError(f"bad version 0x{frame[0]:02x}")
    if frame[1] != TYPE_DATA:
        raise ValueError(f"not a DATA frame (type 0x{frame[1]:02x})")

    device_id = int.from_bytes(frame[2:6], "little")
    counter = int.from_bytes(frame[6:14], "little")
    header, body = frame[:HDR_LEN], frame[HDR_LEN:]

    uid = AESGCM(key).decrypt(make_nonce(TYPE_DATA, device_id, counter), body, header)
    return device_id, counter, uid


def seal_ack(key: bytes, device_id: int, counter: int, status: int) -> bytes:
    """Build an authenticated ACK echoing the frame being acknowledged."""
    header = (
        bytes([PROTO_VERSION, TYPE_ACK])
        + device_id.to_bytes(4, "little")
        + counter.to_bytes(8, "little")
    )
    body = AESGCM(key).encrypt(
        make_nonce(TYPE_ACK, device_id, counter), bytes([status]), header
    )
    return header + body


# ---------------------------------------------------------------- supabase


class Supabase:
    def __init__(self, url, service_key):
        self.base = url.rstrip("/") + "/rest/v1"
        self.client = httpx.Client(
            timeout=5.0,
            headers={
                "apikey": service_key,
                "Authorization": f"Bearer {service_key}",
                "Content-Type": "application/json",
            },
        )

    def lookup_tag(self, uid_hex):
        """(vehicle_id, direction) for a card, or (None, None) if unknown.

        One lookup gives both: the card itself carries the direction, so
        nothing has to be inferred from scan history.
        """
        r = self.client.get(
            f"{self.base}/tags",
            params={
                "rfid_uid": f"eq.{uid_hex}",
                "select": "vehicle_id,direction",
                "limit": "1",
            },
        )
        r.raise_for_status()
        rows = r.json()
        return (rows[0]["vehicle_id"], rows[0]["direction"]) if rows else (None, None)

    def lookup_guard_tag(self, uid_hex):
        """'in'/'out' if this is one of the guard's unauthorised-vehicle cards."""
        r = self.client.get(
            f"{self.base}/unauthorised_tags",
            params={"rfid_uid": f"eq.{uid_hex}", "select": "direction", "limit": "1"},
        )
        r.raise_for_status()
        rows = r.json()
        return rows[0]["direction"] if rows else None

    def insert_unauthorised(self, device_id, counter, uid_hex, direction, rssi, snr):
        r = self.client.post(
            f"{self.base}/unauthorised_events",
            headers={"Prefer": "return=minimal"},
            json={
                "device_id": device_id,
                "counter": counter,
                "rfid_uid": uid_hex,
                "direction": direction,
                "rssi": rssi,
                "snr": snr,
            },
        )
        if r.status_code == 409:
            return ST_DUPLICATE
        if r.status_code >= 400:
            print(f"  supabase {r.status_code}: {r.text}", file=sys.stderr)
            return ST_DB_ERROR
        return ST_COMMITTED

    def insert_log(self, device_id, counter, uid_hex, vehicle_id, direction, rssi, snr):
        """Returns an ACK status byte."""
        r = self.client.post(
            f"{self.base}/vehicle_logs",
            headers={"Prefer": "return=minimal"},
            json={
                "device_id": device_id,
                "counter": counter,
                "rfid_uid": uid_hex,
                "vehicle_id": vehicle_id,
                "direction": direction,
                "rssi": rssi,
                "snr": snr,
            },
        )
        # A retried frame trips unique(device_id, counter). That is the single
        # source of truth for idempotency -- no separate local ledger to drift.
        if r.status_code == 409:
            return ST_DUPLICATE
        if r.status_code >= 400:
            print(f"  supabase {r.status_code}: {r.text}", file=sys.stderr)
            return ST_DB_ERROR
        return ST_COMMITTED if vehicle_id else ST_UNREGISTERED


# ---------------------------------------------------------------- pipeline


def handle_frame(key, db, frame, rssi, snr, enroll=False):
    """Decrypt, commit, and return the ACK bytes -- or None if the frame is bogus."""
    try:
        device_id, counter, uid = open_data(key, frame)
    except (InvalidTag, ValueError) as e:
        # Stay silent: an unauthenticated frame gets no ACK and no row.
        print(f"  rejected: {e}", file=sys.stderr)
        return None

    uid_hex = uid.hex()
    print(f"  uid={uid_hex} device={device_id} counter={counter} rssi={rssi}")

    if enroll:
        print(f"  ENROLL -> add a tags row: rfid_uid={uid_hex}, "
              f"vehicle_id=<the vehicle>, direction='in' or 'out'")
        # Distinct from ST_UNREGISTERED: nothing was written, so the gate must
        # not be told "committed". An ACK that overstates durability is worse
        # than no ACK at all.
        return seal_ack(key, device_id, counter, ST_NOT_LOGGED)

    try:
        vehicle_id, direction = db.lookup_tag(uid_hex)
        if vehicle_id:
            # Registered vehicle -- the common case, kept to two round trips.
            print(f"  direction={direction} (from card)")
            status = db.insert_log(
                device_id, counter, uid_hex, vehicle_id, direction, rssi, snr
            )
        else:
            # Only on a miss do we check whether it's one of the guard's cards.
            guard_dir = db.lookup_guard_tag(uid_hex)
            if guard_dir:
                print(f"  GUARD CARD -> unauthorised vehicle {guard_dir}")
                status = db.insert_unauthorised(
                    device_id, counter, uid_hex, guard_dir, rssi, snr
                )
            else:
                status = db.insert_log(
                    device_id, counter, uid_hex, None, None, rssi, snr
                )
    except httpx.HTTPError as e:
        print(f"  network error: {e}", file=sys.stderr)
        status = ST_DB_ERROR

    label = {
        ST_COMMITTED: "committed",
        ST_DUPLICATE: "duplicate, already committed",
        ST_DB_ERROR: "db error, transmitter will retry",
        ST_UNREGISTERED: "committed, TAG NOT REGISTERED",
        ST_NOT_LOGGED: "acknowledged, not logged (enroll mode)",
    }[status]
    print(f"  -> {label}")
    return seal_ack(key, device_id, counter, status)


def parse_rx_line(line):
    """'R <hex> <rssi> <snr>' -> (frame, rssi, snr), or None."""
    parts = line.split()
    if len(parts) < 2 or parts[0] != "R":
        return None
    try:
        frame = bytes.fromhex(parts[1])
    except ValueError:
        return None
    rssi = int(parts[2]) if len(parts) > 2 else None
    snr = float(parts[3]) if len(parts) > 3 else None
    return frame, rssi, snr


def main():
    load_env()  # must run before the parser is built, or --port's default misses .env

    ap = argparse.ArgumentParser()
    ap.add_argument("--port", default=os.environ.get("SERIAL_PORT"))
    ap.add_argument("--baud", type=int, default=115200)
    ap.add_argument("--enroll", action="store_true", help="print UIDs, do not log them")
    ap.add_argument("--replay", metavar="FILE", help="feed hex frames from a file")
    args = ap.parse_args()

    key = bytes.fromhex(os.environ["PSK_DATA"])
    if len(key) != 32:
        sys.exit("PSK_DATA must be 32 bytes (64 hex chars)")

    db = Supabase(os.environ["SUPABASE_URL"], os.environ["SUPABASE_SERVICE_KEY"])

    if args.replay:
        with open(args.replay) as fh:
            for n, raw in enumerate(fh, 1):
                raw = raw.strip()
                if not raw or raw.startswith("#"):
                    continue
                print(f"[replay line {n}]")
                ack = handle_frame(key, db, bytes.fromhex(raw), None, None, args.enroll)
                if ack:
                    print(f"  ack {ack.hex()}")
        return

    if not args.port:
        sys.exit("no serial port: pass --port or set SERIAL_PORT in .env")

    import serial  # imported late so the tests and --replay run without pyserial

    # Reconnect rather than die. A USB glitch used to kill logging permanently;
    # the transmitter's queue is only 8 deep, so an unnoticed dead gateway
    # eventually drops scans on the floor.
    while True:
        try:
            with serial.Serial(args.port, args.baud, timeout=1) as ser:
                print(f"listening on {args.port}")
                time.sleep(2)  # ESP32 resets when the port opens
                ser.reset_input_buffer()

                while True:
                    raw = ser.readline().decode("ascii", "replace").strip()
                    if not raw:
                        continue
                    if raw.startswith("#"):
                        print(f"receiver: {raw}")
                        continue

                    parsed = parse_rx_line(raw)
                    if not parsed:
                        continue

                    print(f"RX {raw}")
                    ack = handle_frame(key, db, *parsed, enroll=args.enroll)
                    if ack:
                        ser.write(b"A " + ack.hex().encode() + b"\n")

        except KeyboardInterrupt:
            print("\nstopped")
            return
        except (serial.SerialException, OSError) as e:
            print(f"serial lost ({e}); retrying in 3s", file=sys.stderr)
            time.sleep(3)


if __name__ == "__main__":
    main()
