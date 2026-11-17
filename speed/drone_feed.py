"""Bridge a "WiFi UAV" drone camera into the pipeline as an MJPEG stream.

    python3 drone_feed.py                       # drone at 192.168.169.1
    python3 drone_feed.py --size 1280x720       # if the picture is garbled
    -> paste  http://127.0.0.1:8090/feed.mjpg  into the dashboard

The drone (KY UFO app family, Bouffalo BL618 camera module) does not offer a
standard stream that ffmpeg or OpenCV can open. It sends each JPEG frame as UDP
fragments and only sends the next one once the receiver acknowledges it. This
speaks that protocol and re-serves the frames over HTTP on localhost, so the
rest of the system treats the drone like any other live source.

Protocol (port 8801 on BL618 modules, 8800 on older ones; both are tried):
  start          ef 00 04 00
  fragment       93 01 <len:u16> .. <frame seq:u64 @8> .. <index:u32 @32>
                 <count:u32 @36> .. JPEG scan data from byte 56. The JPEG
                 headers are not sent; they are standard tables, rebuilt here.
  acknowledge    ef 02 <len:u16> 02 02 00 01 <slots:u8> 00 00 00 <seq:u32>
                 <command len:u16> <command, padded to 64> <5 quality bytes> 00
                 then one record per recent frame:
                 <seq:u64> <status:u32> <record len:u32> [fragment bitmap]
                 status 0 = receiving (bitmap says which fragments arrived),
                 1 = complete, 2 = dropped.

SAFETY: the acknowledgement also carries a flight command. This sends only
"sticks centred, no flags", which is what the app sends when untouched. Fly
with the drone's physical remote, never with the KY UFO app at the same time,
and test with the propellers off first.

Protocol facts from the open "turbodrone" project (github.com/marshallrichards/
turbodrone, which documents this drone family) and the strings in the KY UFO
app's BFL_SDK; this implementation is our own.
"""

import argparse
import json
import os
import socket
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import cv2
import numpy as np

DRONE_IP = "192.168.169.1"
PORTS = (8801, 8800)
START = b"\xef\x00\x04\x00"
# Sticks centred, no takeoff/land/stop flag: what the app sends when idle.
NEUTRAL_COMMAND = bytes.fromhex("66148080808000020000000000000000" "00000299")
QUALITY_PARAMS = bytes.fromhex("324b142d00")
HEADER_LEN = 56
EOI = b"\xff\xd9"

REQUEST_EVERY_S = 0.08    # re-ask for a frame this long after the last request
MAX_RETRIES = 3           # then give up on that frame and ask for the next
RESTART_AFTER_S = 2.0     # no packets this long -> send start again
WINDOW = 4                # frames kept in flight, as the app does


# =========================================================================
# JPEG header
# =========================================================================

_headers = {}


def jpeg_header(width, height):
    """SOI..SOS for a baseline 4:4:4 JPEG with the standard tables.

    libjpeg at quality 50 writes the standard quantisation tables unscaled and,
    without optimisation, the standard Huffman tables -- exactly the headers the
    drone omits. Encoding a blank frame and keeping everything up to the end of
    the SOS segment gives a header that is correct by construction.
    """
    key = (width, height)
    if key not in _headers:
        ok, buf = cv2.imencode(".jpg", np.zeros((height, width, 3), np.uint8), [
            cv2.IMWRITE_JPEG_QUALITY, 50,
            cv2.IMWRITE_JPEG_OPTIMIZE, 0,
            cv2.IMWRITE_JPEG_SAMPLING_FACTOR, cv2.IMWRITE_JPEG_SAMPLING_FACTOR_444])
        assert ok
        b = buf.tobytes()
        i = b.index(b"\xff\xda")                       # SOS
        _headers[key] = b[:i + 2 + int.from_bytes(b[i + 2:i + 4], "big")]
    return _headers[key]


def scan_data(jpeg):
    """The entropy-coded part of a JPEG: what the drone actually sends."""
    i = jpeg.index(b"\xff\xda")
    start = i + 2 + int.from_bytes(jpeg[i + 2:i + 4], "big")
    return jpeg[start:-2] if jpeg.endswith(EOI) else jpeg[start:]


SIZES = [(640, 360), (640, 480), (1280, 720), (800, 600), (1920, 1080), (320, 240)]


def decodes_cleanly(jpeg):
    """True if libjpeg decodes without complaint.

    The drone never says its resolution. Decoding its scan data under the wrong
    size makes libjpeg warn on stderr -- too big runs out of data, too small
    leaves bytes over -- so a silent decode identifies the right one.
    """
    r, w = os.pipe()
    saved = os.dup(2)
    os.dup2(w, 2)
    try:
        img = cv2.imdecode(np.frombuffer(jpeg, np.uint8), cv2.IMREAD_COLOR)
    finally:
        os.dup2(saved, 2)
        os.close(saved)
        os.close(w)
    os.set_blocking(r, False)
    try:
        warned = bool(os.read(r, 4096))
    except BlockingIOError:
        warned = False
    os.close(r)
    return img is not None and not warned


def detect_size(scan):
    """The resolution under which this frame decodes cleanly, or None."""
    return next((s for s in SIZES if decodes_cleanly(jpeg_header(*s) + scan + EOI)), None)


# =========================================================================
# Packets
# =========================================================================

def parse_fragment(pkt):
    """Fragment packet -> (frame seq, index, count, scan data) or None."""
    if len(pkt) < HEADER_LEN or pkt[0] != 0x93 or pkt[1] != 0x01:
        return None
    seq = int.from_bytes(pkt[8:16], "little")
    index = int.from_bytes(pkt[32:36], "little")
    count = int.from_bytes(pkt[36:40], "little")
    if count == 0 or index >= count:
        return None
    return seq, index, count, pkt[HEADER_LEN:]


def ack_record(seq, status, bitmap=b""):
    return (seq.to_bytes(8, "little") + status.to_bytes(4, "little")
            + (16 + len(bitmap)).to_bytes(4, "little") + bitmap)


def ack_packet(seq, records):
    pkt = bytearray(b"\xef\x02\x00\x00\x02\x02\x00\x01")
    pkt += bytes([len(records)]) + b"\x00\x00\x00"
    pkt += (seq & 0xFFFFFFFF).to_bytes(4, "little")
    pkt += len(NEUTRAL_COMMAND).to_bytes(2, "little")
    pkt += NEUTRAL_COMMAND.ljust(64, b"\x00")
    pkt += QUALITY_PARAMS + b"\x00"
    for r in records:
        pkt += r
    pkt[2:4] = len(pkt).to_bytes(2, "little")
    return bytes(pkt)


def bitmap(count, received):
    words = [0] * ((count + 31) // 32)
    for i in received:
        words[i // 32] |= 1 << (i % 32)
    return b"".join(w.to_bytes(4, "little") for w in words)


# =========================================================================
# Receiver
# =========================================================================

class Frame:
    def __init__(self, count):
        self.count = count
        self.parts = {}
        self.status = 0          # 0 receiving, 1 complete, 2 dropped
        self.retries = 0

    def complete(self):
        return len(self.parts) == self.count

    def record(self, seq):
        return ack_record(seq, self.status,
                          bitmap(self.count, self.parts) if self.status == 0 else b"")


class DroneFeed(threading.Thread):
    """Talks to the drone and keeps the latest complete JPEG."""

    def __init__(self, ip=DRONE_IP, ports=PORTS, size=None):
        super().__init__(daemon=True)
        self.ip, self.ports, self.size = ip, list(ports), size
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.sock.bind(("", 0))
        self.sock.settimeout(0.02)
        self.frames = {}          # seq -> Frame, a small window
        self.last_done = 0        # newest delivered seq
        self.jpeg = None
        self.cond = threading.Condition()
        self.running = True
        self.stats = {"frames": 0, "dropped": 0, "retries": 0, "port": None, "fps": 0.0}
        self._last_rx = self._last_req = 0.0
        self._fps_t, self._fps_n = time.time(), 0

    # -- sending ----------------------------------------------------------

    def _send(self, data):
        for port in self.ports:
            try:
                self.sock.sendto(data, (self.ip, port))
            except OSError:
                pass

    def _request(self, seq):
        """Ask for the frame after `seq`: once bare, once with the frame window
        (the app sends both, and the drone stalls without them)."""
        recent = [f.record(s) for s, f in sorted(self.frames.items())
                  if self.last_done - s < 5]
        recent = recent or [ack_record(seq, 1, b"\xff\xff\xff\xff"), ack_record(seq, 3)]
        self._send(ack_packet(seq, []))
        self._send(ack_packet(seq, recent))
        self._last_req = time.time()

    # -- receiving --------------------------------------------------------

    def _on_packet(self, pkt, port):
        frag = parse_fragment(pkt)
        if frag is None:
            return
        if self.stats["port"] != port:           # lock onto whichever port answered
            self.stats["port"], self.ports = port, [port]
        self._last_rx = time.time()
        seq, index, count, data = frag
        if seq <= self.last_done and seq in self.frames and self.frames[seq].status:
            return                                # late duplicate of a finished frame
        f = self.frames.setdefault(seq, Frame(count))
        f.parts[index] = data
        if f.status == 0 and f.complete():
            f.status = 1
            self._deliver(seq, f)

    def _deliver(self, seq, f):
        scan = b"".join(f.parts[i] for i in range(f.count))
        if self.size is None:                     # learn the resolution from the first frame
            self.size = detect_size(scan) or (640, 360)
            self.stats["size"] = f"{self.size[0]}x{self.size[1]}"
        w, h = self.size
        jpeg = jpeg_header(w, h) + scan + EOI
        with self.cond:
            self.jpeg = jpeg
            self.cond.notify_all()
        self.stats["frames"] += 1
        self._fps_n += 1
        now = time.time()
        if now - self._fps_t >= 1.0:
            self.stats["fps"] = round(self._fps_n / (now - self._fps_t), 1)
            self._fps_t, self._fps_n = now, 0
        for s in [s for s in self.frames if s < seq]:     # older ones are moot now
            if self.frames[s].status == 0:
                self.frames[s].status = 2
                self.stats["dropped"] += 1
        self.last_done = max(self.last_done, seq)
        for s in sorted(self.frames)[:-WINDOW]:
            del self.frames[s]
        self._request(seq)

    def _watchdog(self):
        now = time.time()
        if now - self._last_rx > RESTART_AFTER_S:
            self._send(START)                     # (re)start the stream
            self._request(self.last_done)
            self._last_rx = now - RESTART_AFTER_S + 0.25   # retry every 0.25 s
            return
        # Re-ask only once data has stalled. Asking on a fixed timer while
        # fragments are still streaming in triggers needless re-sends.
        if now - max(self._last_req, self._last_rx) < REQUEST_EVERY_S:
            return
        pending = [(s, f) for s, f in self.frames.items() if f.status == 0]
        if pending:                               # ask again; the bitmap names the gaps
            s, f = min(pending)
            f.retries += 1
            self.stats["retries"] += 1
            if f.retries > MAX_RETRIES:
                f.status = 2
                self.stats["dropped"] += 1
                self.last_done = max(self.last_done, s)
        self._request(self.last_done)

    def run(self):
        self._send(START)
        while self.running:
            try:
                pkt, addr = self.sock.recvfrom(65535)
                self._on_packet(pkt, addr[1])
            except socket.timeout:
                pass
            except OSError:
                time.sleep(0.1)
            self._watchdog()

    def stop(self):
        self.running = False
        self.join(timeout=2)
        self.sock.close()


# =========================================================================
# HTTP: the stream the pipeline opens
# =========================================================================

def serve(feed, port):
    class Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.0"     # MJPEG multipart is unreliable over 1.1 keep-alive

        def log_message(self, *a):
            pass

        def do_GET(self):
            if self.path.startswith("/feed.mjpg"):
                self.send_response(200)
                self.send_header("Content-Type", "multipart/x-mixed-replace; boundary=frame")
                self.end_headers()
                last = None
                try:
                    while True:
                        with feed.cond:
                            feed.cond.wait_for(lambda: feed.jpeg is not last, timeout=5)
                            jpeg = last = feed.jpeg
                        if jpeg is None:
                            continue
                        self.wfile.write(b"--frame\r\nContent-Type: image/jpeg\r\n"
                                         b"Content-Length: " + str(len(jpeg)).encode()
                                         + b"\r\n\r\n" + jpeg + b"\r\n")
                except (BrokenPipeError, ConnectionResetError):
                    return
            elif self.path.startswith("/snapshot.jpg") and feed.jpeg:
                self.send_response(200)
                self.send_header("Content-Type", "image/jpeg")
                self.end_headers()
                self.wfile.write(feed.jpeg)
            elif self.path.startswith("/stats"):
                body = json.dumps(feed.stats | {"since_packet_s": round(time.time() - feed._last_rx, 1)})
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(body.encode())
            else:
                self.send_error(404)

    ThreadingHTTPServer.daemon_threads = True
    srv = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--drone", default=DRONE_IP, help="drone address")
    ap.add_argument("--size", default="auto",
                    help="camera resolution WxH; detected from the first frame by default")
    ap.add_argument("--port", type=int, default=8090, help="local HTTP port")
    args = ap.parse_args()
    size = None if args.size == "auto" else tuple(int(v) for v in args.size.lower().split("x"))

    feed = DroneFeed(args.drone, size=size)
    feed.start()
    serve(feed, args.port)
    print(f"  drone {args.drone}  ->  http://127.0.0.1:{args.port}/feed.mjpg   (ctrl-c to stop)")
    try:
        while True:
            time.sleep(5)
            s = feed.stats
            print(f"  {s['fps']:5.1f} fps   frames {s['frames']}   dropped {s['dropped']}   "
                  f"size {s.get('size', '?')}   port {s['port'] or 'waiting for drone…'}")
    except KeyboardInterrupt:
        feed.stop()
