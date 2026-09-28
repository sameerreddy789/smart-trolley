"""Camera-based barcode / QR scanner (OpenCV + pyzbar).

Two scanners, two paths
-----------------------
PRIMARY   the USB barcode gun. It behaves as a keyboard, so it is captured in
          the browser (see static/js/app.js) — nothing to do here. This keeps
          working when the app is in the cloud, because the keystrokes land in
          the kiosk browser, not on the Pi's GPIO.

BACKUP    this module. A background thread grabs camera frames, decodes any
          barcode or QR code with pyzbar, de-bounces repeats and hands each new
          code to a callback.

Where it runs
-------------
* HARDWARE_MODE=local  the app itself runs this loop.
* HARDWARE_MODE=cloud  `pi_agent.py` on the trolley runs it and forwards codes
                       to the cloud; the cloud app does not start a camera.
* simulation            not started at all.
"""
from __future__ import annotations

import threading
import time
from typing import Callable

from app.config import settings


class CameraScanner:
    def __init__(self, on_scan: Callable[[str], None],
                 camera_index: int | None = None,
                 debounce_s: float = 2.0) -> None:
        self._on_scan = on_scan
        self._camera_index = (
            settings.camera_index if camera_index is None else camera_index
        )
        self._thread: threading.Thread | None = None
        self._running = False
        self._last_code = ""
        self._last_ts = 0.0
        self._debounce_s = debounce_s
        self.status = "stopped"
        self._latest_jpeg_lock = threading.Lock()
        self._latest_jpeg: bytes | None = None

    # -- lifecycle ---------------------------------------------------------
    def start(self, force: bool = False) -> bool:
        """Start the capture loop. `force=True` bypasses the mode check
        (used by pi_agent.py, which is always on real hardware)."""
        if self._running:
            return True
        if not force:
            if not settings.camera_enabled:
                self.status = "disabled (CAMERA_ENABLED=false)"
                print("[scanner] camera disabled by config")
                return False
            if settings.is_simulated:
                self.status = "disabled (simulation)"
                print("[scanner] camera disabled (simulation mode)")
                return False
            if settings.is_cloud:
                self.status = "disabled (cloud: pi_agent owns the camera)"
                print("[scanner] camera not started: the Pi agent owns it")
                return False

        self._running = True
        self.status = "starting"
        self._thread = threading.Thread(target=self._loop, daemon=True)
        self._thread.start()
        print(f"[scanner] camera scanner started (index {self._camera_index})")
        return True

    def stop(self) -> None:
        self._running = False
        self.status = "stopped"

    @property
    def running(self) -> bool:
        return self._running

    def get_latest_jpeg(self) -> bytes | None:
        """Return the most recently captured camera frame as JPEG bytes."""
        with self._latest_jpeg_lock:
            return self._latest_jpeg

    # -- internals ---------------------------------------------------------
    def _emit(self, code: str) -> None:
        now = time.time()
        if code == self._last_code and (now - self._last_ts) < self._debounce_s:
            return
        self._last_code = code
        self._last_ts = now
        try:
            self._on_scan(code)
        except Exception as exc:
            print(f"[scanner] on_scan error: {exc}")

    def _loop(self) -> None:
        try:
            import cv2
            from pyzbar import pyzbar
        except Exception as exc:
            self.status = f"libs unavailable: {exc}"
            self._running = False
            print(f"[scanner] camera libs unavailable, stopping: {exc}")
            print("[scanner] on the Pi: sudo apt install -y libzbar0 && "
                  "pip install -r requirements-hardware.txt")
            return

        cap = cv2.VideoCapture(self._camera_index)
        if not cap.isOpened():
            self.status = f"camera {self._camera_index} could not be opened"
            self._running = False
            print(f"[scanner] {self.status}; backup scanning off")
            return

        self.status = "running"
        try:
            while self._running:
                ok, frame = cap.read()
                if not ok:
                    time.sleep(0.05)
                    continue

                # Cache latest frame for vision verification
                try:
                    _, buf = cv2.imencode(".jpg", frame, [int(cv2.IMWRITE_JPEG_QUALITY), 85])
                    with self._latest_jpeg_lock:
                        self._latest_jpeg = buf.tobytes()
                except Exception:
                    pass

                for bc in pyzbar.decode(frame):
                    code = bc.data.decode("utf-8", errors="ignore").strip()
                    if code:
                        self._emit(code)
                time.sleep(0.03)
        finally:
            cap.release()
            self.status = "stopped"


_scanner: CameraScanner | None = None


def get_scanner(on_scan: Callable[[str], None] | None = None) -> CameraScanner | None:
    global _scanner
    if _scanner is None and on_scan is not None:
        _scanner = CameraScanner(on_scan)
    return _scanner


def capture_camera_frame() -> bytes | None:
    """Return the latest camera frame or capture a fresh snapshot.

    Tries:
    1. The running CameraScanner's latest frame.
    2. Directly capturing 1 frame using OpenCV (webcam on laptop or Pi).
    3. If simulated and a sample frame exists at data/sample_frame.jpg, returns that.
    """
    global _scanner
    if _scanner is not None and _scanner.running:
        buf = _scanner.get_latest_jpeg()
        if buf:
            return buf

    # Standalone snapshot attempt
    try:
        import cv2
        cam_idx = settings.camera_index
        cap = cv2.VideoCapture(cam_idx)
        if cap.isOpened():
            try:
                # Discard 2 frames so sensor/auto-exposure warms up
                for _ in range(2):
                    cap.read()
                ok, frame = cap.read()
                if ok and frame is not None:
                    _, buf = cv2.imencode(".jpg", frame, [int(cv2.IMWRITE_JPEG_QUALITY), 85])
                    return buf.tobytes()
            finally:
                cap.release()
    except Exception as exc:
        print(f"[scanner] direct capture fallback: {exc}")

    # Fallback: check if local test frame exists
    import os
    test_path = os.path.join("data", "sample_frame.jpg")
    if os.path.exists(test_path):
        try:
            with open(test_path, "rb") as fh:
                return fh.read()
        except Exception:
            pass

    return None

