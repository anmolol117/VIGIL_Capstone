#!/usr/bin/env python3
"""Turns the bench CSVs into the tables the paper's Section 7 asks for.

    python3 analyse.py                 # every run found in results/
    python3 analyse.py --tag phase3    # just one

Reads:  results/<tag>_gateway.csv   (from gateway_test.py)
        results/<tag>_serial.csv    (raw transmitter + receiver lines)
"""

import argparse
import csv
import pathlib
import statistics as st

RESULTS = pathlib.Path(__file__).resolve().parent / "results"

# Computed from the LoRa airtime formula at the library defaults actually in
# use (SF7, BW 125 kHz, CR 4/5, 8-symbol preamble, explicit header, CRC on).
# Measurement is compared against these rather than against a guess.
PREDICTED = {"data_tx_ms": 77.1, "ack_tx_ms": 71.9, "radio_rtt_ms": 149.0}


def stats(xs):
    if not xs:
        return None
    xs = sorted(xs)
    return {
        "n": len(xs), "mean": st.mean(xs), "median": st.median(xs),
        "p95": xs[min(int(len(xs) * 0.95), len(xs) - 1)],
        "min": xs[0], "max": xs[-1],
    }


def row(label, s, unit="ms"):
    if not s:
        return f"  {label:<34s} {'no data':>10s}"
    return (f"  {label:<34s} {s['mean']:8.1f}  {s['median']:8.1f}  "
            f"{s['p95']:8.1f}  {s['max']:8.1f}   n={s['n']}")


def header(title):
    print(f"\n{title}")
    print(f"  {'':34s} {'mean':>8s}  {'median':>8s}  {'p95':>8s}  {'worst':>8s}")
    print("  " + "-" * 74)


def load_serial(tag):
    """Parse TIMING / DETECT / RELAY lines out of the raw serial captures.

    Two files, because the two boards are on different ports: the receiver's
    lines are captured by gateway_test.py, the transmitter's by tx_logger.py.
    """
    timing, detect, relay = [], {"ok": 0, "fail": 0, "debounce": 0}, {"in": [], "out": []}
    lines = []
    for suffix in ("_serial.csv", "_tx.csv"):
        p = RESULTS / f"{tag}{suffix}"
        if p.exists():
            lines += [r[1] for r in list(csv.reader(p.open()))[1:] if len(r) > 1]
    for line in lines:
        if line.startswith("TIMING,"):
            f = line.split(",")
            if len(f) >= 8:
                timing.append({
                    "ctr": int(f[1]), "read_ms": int(f[2]) / 1000,
                    "seal_ms": int(f[3]) / 1000, "tx_ms": int(f[4]) / 1000,
                    "wait_ms": int(f[5]) / 1000, "total_ms": int(f[6]) / 1000,
                    "tries": int(f[7]), "status": f[8] if len(f) > 8 else "",
                })
        elif line.startswith("DETECT,"):
            f = line.split(",")
            if len(f) >= 2 and f[1] in detect:
                detect[f[1]] += 1
        elif line.startswith("# RELAY,"):
            f = line.replace("# ", "").split(",")
            if len(f) >= 3 and f[1] in relay:
                relay[f[1]].append(int(f[2]) / 1000)
    return timing, detect, relay


def report(tag):
    gw_path = RESULTS / f"{tag}_gateway.csv"
    gw = list(csv.DictReader(gw_path.open())) if gw_path.exists() else []
    timing, detect, relay = load_serial(tag)

    print("\n" + "=" * 78)
    print(f"  RUN: {tag}")
    print("=" * 78)

    # ---- RFID read accuracy (Phase 2) -------------------------------------
    total_detect = detect["ok"] + detect["fail"] + detect["debounce"]
    if total_detect:
        print("\nRFID READ ACCURACY")
        print("  Denominator is your hand tally of presentations -- a card never seen")
        print("  produces no line at all, so the system cannot count it.\n")
        print(f"  detected and decoded          {detect['ok']:5d}")
        print(f"  detected, decode failed       {detect['fail']:5d}")
        print(f"  suppressed by debounce        {detect['debounce']:5d}   (harness artefact)")
        print(f"  total detections              {total_detect:5d}")
        seen = detect["ok"] + detect["fail"]
        if seen:
            print(f"\n  decode rate (decoded/detected)   {detect['ok'] / seen * 100:6.1f} %")
        print("  end-to-end accuracy              [ decoded / your tally ]")

    # ---- Authentication time (Phase 3) ------------------------------------
    if timing:
        clean = [t for t in timing if t["tries"] == 1]
        retried = [t for t in timing if t["tries"] > 1]

        header("AUTHENTICATION TIME  (transmitter clock, first-attempt scans)")
        for key, label in (("read_ms", "Card detect + UID read"),
                           ("seal_ms", "AES-256-GCM encryption"),
                           ("tx_ms", "LoRa transmit (DATA)"),
                           ("wait_ms", "TX done -> ACK verified"),
                           ("total_ms", "End-to-end")):
            print(row(label, stats([t[key] for t in clean])))

        if relay["in"] or relay["out"]:
            header("RECEIVER RELAY  (receiver clock)")
            print(row("LoRa RX -> serial out", stats(relay["in"])))
            print(row("serial in -> LoRa TX", stats(relay["out"])))

        s = stats([t["tx_ms"] for t in clean])
        if s:
            print("\nMEASURED vs COMPUTED AIRTIME")
            d = s["mean"] - PREDICTED["data_tx_ms"]
            print(f"  DATA frame transmit   measured {s['mean']:7.1f} ms   "
                  f"computed {PREDICTED['data_tx_ms']:6.1f} ms   delta {d:+6.1f} ms")
            print("  (endPacket() blocks until the frame is sent, so this is airtime")
            print("   plus SPI setup -- expect measured to sit slightly above computed.)")

        if retried:
            header("RETRY PATH  (scans needing more than one attempt)")
            print(row("End-to-end, retried", stats([t["total_ms"] for t in retried])))
            print(f"\n  {len(retried)} of {len(timing)} scans required a retransmission "
                  f"({len(retried) / len(timing) * 100:.1f} %)")

    # ---- Database response time (Phase 4) ---------------------------------
    if gw:
        header("DATABASE RESPONSE TIME  (laptop clock)")
        for col, label in (("decrypt_ms", "Decrypt + verify (local)"),
                           ("lookup_tag_ms", "SELECT tags"),
                           ("lookup_guard_ms", "SELECT unauthorised_tags"),
                           ("insert_ms", "INSERT (write confirmed)"),
                           ("seal_ack_ms", "Seal ACK (local)"),
                           ("gateway_total_ms", "Gateway total")):
            print(row(label, stats([float(r[col]) for r in gw if r.get(col)])))

        errs = [r for r in gw if r["status"] == "0x02"]
        print(f"\n  failed / timed-out writes        {len(errs)} of {len(gw)}")

        kinds = {}
        for r in gw:
            kinds[r["kind"]] = kinds.get(r["kind"], 0) + 1
        if len(kinds) > 1:
            print("\n  by routing path: " + ", ".join(f"{k}={v}" for k, v in sorted(kinds.items())))
            print("  (an unknown card costs 3 database calls, a registered one 2)")

    # ---- Unauthorised detection (Phase 5) ---------------------------------
    if gw and any(r["kind"] in ("guard", "unknown") for r in gw):
        print("\nUNAUTHORISED DETECTION")
        conf = {"vehicle": 0, "guard": 0, "unknown": 0}
        for r in gw:
            if r["kind"] in conf:
                conf[r["kind"]] += 1
        print(f"  classified as registered vehicle {conf['vehicle']:5d}")
        print(f"  classified as attendant card     {conf['guard']:5d}")
        print(f"  classified as unknown card       {conf['unknown']:5d}")
        print("\n  Compare each against how many you actually presented; any")
        print("  disagreement is a false positive or false negative.")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--tag", help="analyse one run; default is all of them")
    a = ap.parse_args()

    if not RESULTS.exists():
        print("no results/ directory yet -- run gateway_test.py first")
        return
    tags = ([a.tag] if a.tag else
            sorted({p.name.rsplit("_", 1)[0] for p in RESULTS.glob("*_*.csv")}))
    if not tags:
        print("no CSVs found in results/")
        return
    for t in tags:
        report(t)
    print()


if __name__ == "__main__":
    main()
