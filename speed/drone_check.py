"""Offline check of the real drone's camera link. Needs no internet.

    python3 drone_check.py            # Mac on the drone's Wi-Fi, KY UFO app closed
    python3 drone_check.py 127.0.0.1  # against the fake drone (test_drone_feed.py --serve)

Finds the drone from the Wi-Fi connection itself rather than assuming an
address, checks for standard video (RTSP) as well as the WiFi-UAV UDP protocol,
and saves everything into drone_check/ for analysis: a report, the raw UDP
packets, the first frames, one frame decoded at several resolutions, and the
frame timing (camera timestamps vs Wi-Fi arrival, drone_check/timing.csv).
"""

import os
import socket
import struct
import subprocess
import sys
import threading
import time

import cv2
import numpy as np

import drone_feed as df

OUT = "drone_check"
SECONDS = 20
KEEP_PACKETS = 3000
# Addresses this drone family is known to use; the Wi-Fi gateway is tried first.
KNOWN = ["192.168.169.1", "192.168.1.1", "192.168.0.1", "172.16.10.1"]
TCP_PORTS = [80, 554, 7060, 7070, 8080, 8554, 8888]


class Recorder(df.DroneFeed):
    """The normal receiver, plus a log of every packet in both directions."""

    def __init__(self, *a, **k):
        super().__init__(*a, **k)
        self.log, self.ports_seen = [], {}
        self.first_jpegs, self.raw_frames = [], []

    def _send(self, data):
        if len(self.log) < KEEP_PACKETS:
            self.log.append((time.time(), b">", 0, data))
        super()._send(data)

    def _on_packet(self, pkt, port):
        self.ports_seen[port] = self.ports_seen.get(port, 0) + 1
        if len(self.log) < KEEP_PACKETS:
            self.log.append((time.time(), b"<", port, pkt))
        super()._on_packet(pkt, port)

    def _deliver(self, seq, f):
        if len(self.raw_frames) < 5:
            self.raw_frames.append(b"".join(f.parts[i] for i in range(f.count)))
        super()._deliver(seq, f)
        if len(self.first_jpegs) < 5:
            self.first_jpegs.append(self.jpeg)


def sh(cmd, timeout=15):
    try:
        return subprocess.run(cmd, shell=True, capture_output=True, text=True, timeout=timeout).stdout.strip()
    except Exception as e:
        return f"({type(e).__name__})"


class Heartbeat(threading.Thread):
    """The CooingDV apps' control-channel keepalive: {01 01} to UDP 7099 each second."""

    def __init__(self, ip):
        super().__init__(daemon=True)
        self.ip, self.running, self.replies, self.first = ip, True, 0, b""
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.sock.settimeout(0.2)

    def run(self):
        last = 0.0
        while self.running:
            if time.time() - last >= 1.0:
                self.sock.sendto(b"\x01\x01", (self.ip, 7099))
                last = time.time()
            try:
                data, _ = self.sock.recvfrom(2048)
                self.replies += 1
                self.first = self.first or data[:16]
            except OSError:
                pass

    def stop(self):
        self.running = False
        self.join(timeout=2)
        self.sock.close()


def rtsp_round(url, say, label):
    """Probe over UDP then TCP, keeping ffprobe's own error text.
    Returns the transport that worked, or None."""
    say("", f"--- RTSP {url} ({label})")
    for transport in ("udp", "tcp"):
        r = subprocess.run(["ffprobe", "-v", "error", "-rtsp_transport", transport, "-timeout", "5000000",
                            "-select_streams", "v:0", "-show_entries",
                            "stream=codec_name,width,height,avg_frame_rate", "-of", "csv=p=0", url],
                           capture_output=True, text=True, timeout=15)
        out, err = r.stdout.strip(), " ".join(r.stderr.split())[-240:]
        say(f"{transport}: {out or 'no stream'}" + (f"   error: {err}" if err else ""))
        if out:
            sh(f"ffmpeg -v error -y -rtsp_transport {transport} -i '{url}' -frames:v 1 {OUT}/rtsp_frame.jpg", timeout=15)
            return transport
    return None


def timing_check(url, say, seconds=20):
    """How late frames arrive over Wi-Fi versus the camera's own timestamps,
    read exactly as the pipeline reads them. Saves drone_check/timing.csv;
    `python3 bench_degraded.py --timing` turns it into km/h of speed error."""
    import speedcam as sc
    say("", f"--- frame timing, {seconds} s (camera timestamps vs Wi-Fi arrival)")
    cap = cv2.VideoCapture(url)
    say(f"stream reports {cap.get(cv2.CAP_PROP_FPS):.1f} fps")
    clock, rows, end = sc.StreamClock(), [], time.time() + seconds
    while time.time() < end:
        ok, _ = cap.read()
        if not ok:
            continue
        a, pts = time.time(), cap.get(cv2.CAP_PROP_POS_MSEC) / 1000
        rows.append((a, pts))
        clock.stamp(a, pts)
    cap.release()
    if len(rows) < 30:
        say(f"only {len(rows)} frames read: timing not measured")
        return
    r = np.array(rows)
    np.savetxt(f"{OUT}/timing.csv", r, delimiter=",", header="arrival_s,pts_s", comments="")
    ok = np.diff(r[:, 1]) > 0
    err = np.abs(np.diff(r[:, 0]) - np.diff(r[:, 1]))[ok][24:]     # skip the opening second
    say(f"camera timestamps: {'usable' if clock.using_camera else 'NOT usable'} "
        f"(timeline breaks {clock.breaks})",
        f"frame spacing by arrival vs camera: median {1000 * np.median(err):.1f} ms, "
        f"p95 {1000 * np.percentile(err, 95):.1f} ms, worst {1000 * err.max():.0f} ms "
        f"(frames are {1000 * np.median(np.diff(r[:, 1])[ok]):.1f} ms apart)")


def tcp_open(ip, port):
    try:
        with socket.create_connection((ip, port), timeout=0.6):
            return True
    except OSError:
        return False


def main():
    os.makedirs(OUT, exist_ok=True)
    for f in os.listdir(OUT):                 # never mix in results from an earlier run
        os.remove(os.path.join(OUT, f))
    rep = [f"drone check  {time.strftime('%Y-%m-%d %H:%M:%S')}", ""]

    def say(*lines):
        for line in lines:
            print(line)
            rep.append(line)

    # --- 1. the Wi-Fi link ------------------------------------------------
    if len(sys.argv) > 1:
        candidates = [sys.argv[1]]
        say(f"testing {sys.argv[1]} (given)")
    else:
        ip = sh("ipconfig getifaddr en0")
        gw = sh("ipconfig getoption en0 router")
        say("--- Wi-Fi (en0)", f"Mac address on Wi-Fi: {ip or 'NONE'}",
            f"Wi-Fi gateway (the drone): {gw or 'NONE'}")
        if not ip:
            say("", "The Mac's Wi-Fi has no address: it is not connected to the drone.",
                "Join the drone's Wi-Fi from the menu-bar Wi-Fi icon, wait until it is",
                "connected, then run this again.")
            open(f"{OUT}/report.txt", "w").write("\n".join(rep) + "\n")
            return
        candidates = ([gw] + [k for k in KNOWN if k != gw]) if gw else KNOWN

    # --- internet: the speeding log and guard alerts need it ----------------
    say("", "--- internet (speeding log + guard alerts)")
    say(f"default route via: {sh('route -n get default | awk \'/interface/{print $2}\'') or 'NONE'}"
        "   (iPhone USB is usually en5-en8; en0 is the Wi-Fi)")
    t0 = time.time()
    resp = sh("curl -s --max-time 10 -X POST -H 'Content-Type: application/json' -d '{\"check\":true}' "
              "https://grknwhtsoginetxkmeto.supabase.co/functions/v1/dispatch", timeout=15)
    say(f"Supabase (alert sender): {'reachable' if 'secret' in resp else 'NOT reachable'} "
        f"in {1000 * (time.time() - t0):.0f} ms")
    try:
        import psycopg
        from dotenv import load_dotenv
        load_dotenv(".env")
        t0 = time.time()
        with psycopg.connect(os.environ["DATABASE_URL"], connect_timeout=10) as conn:
            t1 = time.time()
            conn.execute("select 1").fetchone()
            t2 = time.time()
        say(f"database: connect {1000 * (t1 - t0):.0f} ms, query {1000 * (t2 - t1):.0f} ms")
    except Exception as e:
        say(f"database: NOT reachable ({type(e).__name__})")

    # --- 2. find the drone -----------------------------------------------
    say("", "--- reachability")
    drone = None
    for c in candidates:
        ping = sh(f"ping -c 2 -t 3 {c} | grep -oE '[0-9.]+% packet loss'")
        route = sh(f"route -n get {c} | awk '/interface/{{print $2}}'")
        say(f"{c:15s} ping: {ping or 'no reply':18s} route via {route}")
        if drone is None and ping and not ping.startswith("100"):
            drone = c
    drone = drone or candidates[0]
    say(f"using {drone}")

    # --- 3. standard video (CooingDV family: RTSP on 7070) -----------------
    say("", "--- open TCP ports on the drone")
    open_ports = [p for p in TCP_PORTS if tcp_open(drone, p)]
    say(f"open: {open_ports or 'none of ' + str(TCP_PORTS)}")
    if 7070 in open_ports:
        url = f"rtsp://{drone}:7070/webcam"
        works = rtsp_round(url, say, "without heartbeat")
        if not works:
            # The apps send {01 01} to UDP 7099 every second. It is a keepalive,
            # not a flight command; some drones may only stream while it runs.
            hb = Heartbeat(drone)
            hb.start()
            time.sleep(3)
            works = rtsp_round(url, say, "with heartbeat on UDP 7099")
            hb.stop()
            say(f"control-port replies during heartbeat: {hb.replies} "
                f"(first bytes {hb.first.hex() if hb.first else '-'})")
        if works:
            transport = works
            say("", f"--- 10 s recording over {transport}")
            sh(f"ffmpeg -v error -y -rtsp_transport {transport} -i '{url}' -t 10 -c copy {OUT}/clip.mp4", timeout=30)
            info = sh(f"ffprobe -v error -select_streams v:0 -count_frames -show_entries "
                      f"stream=codec_name,width,height,avg_frame_rate,nb_read_frames -of csv=p=0 {OUT}/clip.mp4")
            say(f"clip: {info or 'recording failed'}   (codec,width,height,fps,frames)")
            timing_check(url, say)
        open(f"{OUT}/report.txt", "w").write("\n".join(rep) + "\n")
        print(f"\nDone. Results are in {OUT}/. Reconnect to your normal Wi-Fi and tell Claude.")
        return

    # --- 4. WiFi-UAV UDP protocol -----------------------------------------
    say("", f"--- WiFi-UAV UDP video on {drone}, {SECONDS} s")
    rec = Recorder(drone)                      # resolution auto-detected
    rec.start()
    for i in range(SECONDS):
        time.sleep(1)
        s = rec.stats
        print(f"  {i + 1:2d}s  frames {s['frames']:5d}  fps {s['fps']:5.1f}  "
              f"port {s['port']}  packets in {sum(rec.ports_seen.values())}")
    rec.stop()
    s = rec.stats
    say(f"packets from drone by port: {rec.ports_seen or 'NONE'}",
        f"frames {s['frames']}  dropped {s['dropped']}  retries {s['retries']}  "
        f"fps {s['fps']}  locked port {s['port']}")
    kinds = {}
    for _, d, _, p in rec.log:
        if d == b"<" and len(p) >= 2:
            kinds[p[:2].hex()] = kinds.get(p[:2].hex(), 0) + 1
    say(f"incoming packet types (first 2 bytes): {kinds or 'none'}")

    with open(f"{OUT}/packets.bin", "wb") as fh:       # [t:f64][dir:1][port:u16][len:u32][bytes]
        for t, d, port, p in rec.log:
            fh.write(struct.pack("<dcHI", t, d, port, len(p)) + p)
    for i, j in enumerate(rec.first_jpegs):
        open(f"{OUT}/frame_{i}.jpg", "wb").write(j)
    if rec.raw_frames:
        scan = rec.raw_frames[-1]
        clean = []
        for w, h in df.SIZES:
            jpeg = df.jpeg_header(w, h) + scan + df.EOI
            if df.decodes_cleanly(jpeg):
                clean.append(f"{w}x{h}")
            img = cv2.imdecode(np.frombuffer(jpeg, np.uint8), cv2.IMREAD_COLOR)
            if img is not None:
                cv2.imwrite(f"{OUT}/size_{w}x{h}.png", img)
        say(f"resolution: decodes cleanly at {clean or 'NO candidate size'} "
            f"(receiver chose {rec.stats.get('size')})",
            f"saved {len(rec.first_jpegs)} frames and trial decodes at {len(df.SIZES)} sizes")

    open(f"{OUT}/report.txt", "w").write("\n".join(rep) + "\n")
    print(f"\nDone. Results are in {OUT}/. Reconnect to your normal Wi-Fi and tell Claude.")


if __name__ == "__main__":
    main()
