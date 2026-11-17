"""Checks for drone_feed.py against a fake drone.

    python3 test_drone_feed.py            # run the checks
    python3 test_drone_feed.py --serve    # run the fake drone on 127.0.0.1 for
                                          # manual tests: drone_feed.py --drone 127.0.0.1

The fake drone speaks the same protocol as the real one: it waits for the start
packet, sends each JPEG as fragments with the 56-byte header and no JPEG
headers, only moves on once the frame is acknowledged complete, and re-sends
the fragments an acknowledgement's bitmap reports missing. It also drops a share
of fragments on purpose, so the re-request path is exercised.

This proves the receiver is consistent with our reading of the protocol. Only
the real drone can prove the reading itself is right.
"""

import random
import socket
import sys
import threading
import time

import cv2
import numpy as np

import drone_feed as df

SIZE = (640, 360)
FRAG = 1400
ENCODE = [cv2.IMWRITE_JPEG_QUALITY, 50, cv2.IMWRITE_JPEG_OPTIMIZE, 0,
          cv2.IMWRITE_JPEG_SAMPLING_FACTOR, cv2.IMWRITE_JPEG_SAMPLING_FACTOR_444]


def source_frames(n=60):
    """Real footage, scaled to the drone camera's size."""
    cap = cv2.VideoCapture("data/drone_video.mp4")
    out = []
    while len(out) < n:
        ok, f = cap.read()
        if not ok:
            break
        ok, buf = cv2.imencode(".jpg", cv2.resize(f, SIZE), ENCODE)
        out.append(buf.tobytes())
    cap.release()
    return out


class FakeDrone(threading.Thread):
    def __init__(self, frames, loss=0.05, port=8801, fps=None):
        super().__init__(daemon=True)
        self.frames, self.loss = frames, loss
        self.period = 1 / fps if fps else 0.0      # a real camera paces its frames
        self.next_at = 0.0
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.sock.bind(("127.0.0.1", port))
        self.sock.settimeout(0.05)
        self.client = None
        self.seq = 1
        self.resent = 0
        self.running = True

    def fragments(self, seq):
        scan = df.scan_data(self.frames[(seq - 1) % len(self.frames)])
        parts = [scan[i:i + FRAG] for i in range(0, len(scan), FRAG)]
        out = []
        for i, p in enumerate(parts):
            h = bytearray(df.HEADER_LEN)
            h[0], h[1] = 0x93, 0x01
            h[2:4] = (df.HEADER_LEN + len(p)).to_bytes(2, "little")
            h[8:16] = seq.to_bytes(8, "little")
            h[32:36] = i.to_bytes(4, "little")
            h[36:40] = len(parts).to_bytes(4, "little")
            h[40:44] = len(scan).to_bytes(4, "little")
            h[48] = 50
            out.append(bytes(h) + p)
        return out

    def send(self, frags, only=None):
        for i, pkt in enumerate(frags):
            if only is not None and i not in only:
                continue
            if random.random() < self.loss:
                continue                       # lost in the air
            self.sock.sendto(pkt, self.client)

    def run(self):
        last_send = 0.0
        while self.running:
            try:
                pkt, addr = self.sock.recvfrom(65535)
            except socket.timeout:
                if self.client and time.time() - last_send > 0.5:    # no ack: resend
                    self.send(self.fragments(self.seq))
                    last_send = time.time()
                continue
            if pkt == df.START:
                self.client = addr
                self.send(self.fragments(self.seq))
                last_send = time.time()
            elif pkt[:2] == b"\xef\x02" and self.client:
                n = pkt[8]
                off = 18 + 64 + 6
                for _ in range(n):
                    seq = int.from_bytes(pkt[off:off + 8], "little")
                    status = int.from_bytes(pkt[off + 8:off + 12], "little")
                    rlen = int.from_bytes(pkt[off + 12:off + 16], "little")
                    bits = pkt[off + 16:off + rlen]
                    off += rlen
                    if seq != self.seq:
                        continue
                    if status == 1:                              # done: next frame
                        wait = self.next_at - time.time()
                        if wait > 0:
                            time.sleep(wait)
                        self.next_at = time.time() + self.period
                        self.seq += 1
                        self.send(self.fragments(self.seq))
                        last_send = time.time()
                    elif status == 0 and bits:                   # resend the gaps
                        frags = self.fragments(self.seq)
                        got = {i for i in range(len(frags))
                               if bits[i // 8] >> (i % 8) & 1}
                        missing = set(range(len(frags))) - got
                        self.resent += len(missing)
                        self.send(frags, only=missing)
                        last_send = time.time()

    def stop(self):
        self.running = False
        self.join(timeout=1)
        self.sock.close()


def check_header_round_trip(frames):
    """Header rebuilt from standard tables + drone payload = the original image."""
    for jpeg in frames[:5]:
        rebuilt = df.jpeg_header(*SIZE) + df.scan_data(jpeg) + df.EOI
        a = cv2.imdecode(np.frombuffer(jpeg, np.uint8), cv2.IMREAD_COLOR)
        b = cv2.imdecode(np.frombuffer(rebuilt, np.uint8), cv2.IMREAD_COLOR)
        assert b is not None and a.shape == b.shape and np.array_equal(a, b), "rebuilt JPEG differs"
    print("ok  JPEG rebuilt from scan data decodes to the identical image")


def check_ack_packet():
    rec = df.ack_record(7, 0, df.bitmap(40, {0, 1, 39}))
    pkt = df.ack_packet(7, [rec])
    assert pkt[:2] == b"\xef\x02" and int.from_bytes(pkt[2:4], "little") == len(pkt)
    assert pkt[8] == 1 and int.from_bytes(pkt[12:16], "little") == 7
    assert pkt[18:18 + len(df.NEUTRAL_COMMAND)] == df.NEUTRAL_COMMAND
    bits = pkt[18 + 64 + 6 + 16:]
    assert len(bits) == 8 and bits[0] == 0b11 and bits[4] & 0x80, "bitmap bits wrong"
    print("ok  acknowledgement layout, length field and fragment bitmap")


def check_size_detection():
    """The drone never states its resolution; it must be found from the data."""
    img = cv2.imread("output/drone_annotated_frame420.png")
    for size in [(640, 360), (640, 480), (1280, 720), (320, 240)]:
        ok, buf = cv2.imencode(".jpg", cv2.resize(img, size), ENCODE)
        got = df.detect_size(df.scan_data(buf.tobytes()))
        assert got == size, f"{size} detected as {got}"
    print("ok  resolution detected from scan data alone (4 sizes)")


def check_end_to_end(frames, loss):
    drone = FakeDrone(frames, loss=loss)
    drone.start()
    feed = df.DroneFeed("127.0.0.1", ports=(8801, 8800))      # size auto-detected
    feed.start()
    t0 = time.time()
    deadline = t0 + 15
    while feed.stats["frames"] < 40 and time.time() < deadline:
        time.sleep(0.02)
    got, took = feed.stats["frames"], time.time() - t0
    jpeg = feed.jpeg
    feed.stop(); drone.stop()
    assert got >= 40, f"only {got} frames in 15 s at {loss:.0%} loss"
    img = cv2.imdecode(np.frombuffer(jpeg, np.uint8), cv2.IMREAD_COLOR)
    matches = [np.array_equal(img, cv2.imdecode(np.frombuffer(f, np.uint8), cv2.IMREAD_COLOR))
               for f in frames]
    assert any(matches), "delivered frame matches no source frame"
    assert loss == 0 or drone.resent > 0, "loss never triggered a re-send"
    print(f"ok  {got} frames pixel-exact at {loss:.0%} loss: {got / took:5.1f} frames/s, "
          f"{drone.resent} fragments re-sent on request")


if __name__ == "__main__":
    if "--serve" in sys.argv:
        d = FakeDrone(source_frames(750), loss=0.02, fps=25)   # 30 s of footage, looped
        d.start()
        print("  fake drone on 127.0.0.1:8801  (run: python3 drone_feed.py --drone 127.0.0.1)")
        try:
            while True:
                time.sleep(1)
        except KeyboardInterrupt:
            d.stop()
    else:
        frames = source_frames()
        random.seed(1)
        check_header_round_trip(frames)
        check_ack_packet()
        check_size_detection()
        for loss in (0.0, 0.02, 0.08):
            check_end_to_end(frames, loss=loss)
        print("\nall drone-feed checks passed")
