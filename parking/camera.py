"""Live frames from the analog camera's USB capture dongle, opened by name.

macOS numbers capture devices in whatever order they appear (a nearby iPhone
becomes a camera too), so the dongle is opened by its name through ffmpeg,
which also handles the dongle's raw uyvy422 output.

Two things about this feed shape the code:
  * Only the newest frame matters. When detection runs slower than the camera,
    older frames are dropped instead of piling up as growing delay.
  * The transmitter is switched off for a few minutes at a time to cool down,
    which turns the feed black. is_blank() lets the pipeline tell "camera off"
    from "empty lot". If the dongle itself drops out, ffmpeg is restarted.

    source: "camera:AV TO USB2.0"     # in config.yaml
"""

from __future__ import annotations

import subprocess
import threading
import time

import cv2
import numpy as np

PREFIX = "camera:"


class Camera:
    def __init__(self, name: str, size: str = "720x480", fps: float = 25,
                 filters: str | None = None) -> None:
        self.name = name
        # ffmpeg -vf chain: the picture clean-up in config.yaml (a list or one string)
        self.filters = ",".join(filters) if isinstance(filters, list) else filters
        self.w, self.h = map(int, size.split("x"))
        self.fps = float(fps)
        self._frame: np.ndarray | None = None
        self._seq = 0
        self._cond = threading.Condition()
        self._stopped = False
        self._proc: subprocess.Popen | None = None
        self._last = self._opened = time.time()
        self._warned = False
        threading.Thread(target=self._run, daemon=True).start()

    def _run(self) -> None:
        size = self.w * self.h * 3
        while not self._stopped:
            self._last = time.time()  # a fresh start gets the full 10 s to deliver
            self._proc = subprocess.Popen(
                ["ffmpeg", "-hide_banner", "-loglevel", "error",
                 "-f", "avfoundation", "-framerate", f"{self.fps:g}",
                 "-video_size", f"{self.w}x{self.h}", "-pixel_format", "uyvy422",
                 "-i", f"{self.name}:none",
                 *(["-vf", self.filters] if self.filters else []),
                 "-f", "rawvideo", "-pix_fmt", "bgr24", "-"],
                stdout=subprocess.PIPE,
            )
            while not self._stopped:
                buf = self._proc.stdout.read(size)
                if len(buf) < size:
                    break
                frame = np.frombuffer(buf, np.uint8).reshape(self.h, self.w, 3).copy()
                with self._cond:
                    self._frame, self._seq, self._last = frame, self._seq + 1, time.time()
                    self._cond.notify_all()
            self._proc.kill()
            if not self._stopped:
                time.sleep(5)  # dongle unplugged or busy: try again

    def read(self, timeout: float = 2.0):
        """The next new frame, as (ok, frame); (False, None) if none arrives in time."""
        with self._cond:
            seen = self._seq
            if not self._cond.wait_for(lambda: self._seq != seen, timeout):
                self._stalled()
                return False, None
            return True, self._frame

    def _stalled(self) -> None:
        """No frames at all (a switched-off transmitter still gives black ones)."""
        if self._seq == 0 and not self._warned and time.time() - self._opened > 10:
            self._warned = True
            print(f"[camera] no frames from {self.name!r}. Is it plugged in? On first use, "
                  "allow camera access for this app (Terminal) in System Settings > "
                  "Privacy & Security > Camera.", flush=True)
        if self._seq and time.time() - self._last > 10 and self._proc:
            self._proc.kill()  # stalled dongle: the reader thread reopens it

    def release(self) -> None:
        self._stopped = True
        if self._proc:
            self._proc.kill()


def open_source(source: str, camera_cfg: dict | None = None):
    """A capture for a file, stream URL or "camera:<device name>", and its fps."""
    if source.startswith(PREFIX):
        cfg = camera_cfg or {}
        cam = Camera(source[len(PREFIX):], cfg.get("size", "720x480"), cfg.get("fps", 25),
                     cfg.get("filters"))
        return cam, cam.fps
    cap = cv2.VideoCapture(source)
    if not cap.isOpened():
        raise RuntimeError(f"cannot open source {source!r}")
    fps = cap.get(cv2.CAP_PROP_FPS)
    return cap, (fps if fps and fps > 1 else 30.0)


def is_blank(frame: np.ndarray | None, black_level: float = 16) -> bool:
    """True when there is no picture: no frame at all, or a near-uniform black
    one (transmitter off). A dark but real scene still has texture, so it passes."""
    if frame is None:
        return True
    gray = cv2.cvtColor(frame[::4, ::4], cv2.COLOR_BGR2GRAY)
    return gray.mean() < black_level and gray.std() < black_level / 2


def preview(config: str = "config.yaml", live_dir: str = "live") -> None:
    """Show the raw camera on the dashboard before the lot is calibrated, so it
    can be aimed and calibrated. pipeline.py takes over once it is, publishing
    the same files with the bays added."""
    import json
    import os
    import signal
    from pathlib import Path

    import yaml

    cfg = yaml.safe_load(Path(config).read_text())
    cam_cfg = cfg.get("camera") or {}
    out = Path(live_dir)
    out.mkdir(exist_ok=True)

    def write(name: str, data: bytes) -> None:  # atomic, so the server never reads half a frame
        tmp = out / f".{name}"
        tmp.write_bytes(data)
        os.replace(tmp, out / name)

    def stop(*_):
        raise KeyboardInterrupt

    signal.signal(signal.SIGTERM, stop)
    cap, fps = open_source(cfg["source"], cam_cfg)
    w, h = map(int, cam_cfg.get("size", "720x480").split("x"))
    print(f"[camera] preview of {cfg['source']} - calibrate to start counting", flush=True)
    try:
        while True:
            ok, frame = cap.read()
            if not isinstance(cap, Camera):
                if not ok:
                    break  # end of a file source
                time.sleep(1 / fps)  # play a file at its own speed, like a camera
            blank = is_blank(frame, cam_cfg.get("black_level", 16))
            if frame is None:
                frame = np.zeros((h, w, 3), np.uint8)
            ok, buf = cv2.imencode(".jpg", frame, [cv2.IMWRITE_JPEG_QUALITY, 92])
            if ok:
                write("frame.jpg", buf.tobytes())
                write("raw.jpg", buf.tobytes())  # Calibrate takes the latest one
            write("state.json", json.dumps({
                "name": cfg["lot_code"], "code": cfg["lot_code"], "preview": True,
                "width": frame.shape[1], "height": frame.shape[0], "capacity": 0,
                "codes": [], "rects": [], "polys": [], "occupied": [],
                "at": time.time(), "signal": not blank, "held": False,
            }).encode())
    except KeyboardInterrupt:
        pass
    finally:
        cap.release()


if __name__ == "__main__":
    import argparse

    ap = argparse.ArgumentParser(description="Publish the raw camera to live/ for the dashboard.")
    ap.add_argument("--config", default="config.yaml")
    ap.add_argument("--live-dir", default="live")
    a = ap.parse_args()
    preview(a.config, a.live_dir)
