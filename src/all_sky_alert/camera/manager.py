"""One camera owner, bounded command queue, demand-only preview and isolated schedule."""
from collections import OrderedDict
from copy import deepcopy
from datetime import timedelta
import logging
import os
from queue import Empty, Full, Queue
from threading import Event, RLock, Thread
import time
from uuid import uuid4

from ..domain import iso, utcnow
from .backend import RaspberryPiCameraBackend, CameraFault
from .timelapse import TimelapseSchedule, capture_name

log = logging.getLogger(__name__)


class CameraUnavailable(RuntimeError):
    pass


class CameraBusy(RuntimeError):
    pass


class CameraManager:
    def __init__(self, config, *, backend_factory=RaspberryPiCameraBackend, clock=utcnow, monotonic=time.monotonic):
        self.settings = deepcopy(config.values["camera"])
        self.capture_directory = (config.data_dir / self.settings["capture_directory"]).resolve()
        self.timelapse_directory = (config.data_dir / self.settings["timelapse"]["directory"]).resolve()
        self.backend_factory, self.clock, self.monotonic = backend_factory, clock, monotonic
        self.lock, self.stop_event, self.wake = RLock(), Event(), Event()
        self.queue = Queue(maxsize=4)
        self.jobs = OrderedDict()
        self.thread = None
        self.schedule = TimelapseSchedule(self.settings["timelapse"]["interval_seconds"])
        self.viewers = 0
        self.frame, self.frame_at, self.sequence = None, None, 0
        self.latest_path = None
        self.state = {"enabled": self.settings["enabled"], "state": "stopped" if self.settings["enabled"] else "disabled",
                      "detected": None, "model": None, "focus_mode": None, "lens_position": None,
                      "autofocus_state": None, "error": None, "last_capture_at": None, "last_filename": None,
                      "capabilities": {"focus_modes": [], "autofocus_trigger": False, "lens_limits": None}}

    def start(self):
        with self.lock:
            if not self.settings["enabled"] or self.thread is not None or self.stop_event.is_set():
                return
            self.state["state"] = "starting"
            self.thread = Thread(target=self._run, name="camera-owner", daemon=True)
            self.thread.start()

    def close(self):
        with self.lock:
            if self.stop_event.is_set():
                return
            self.stop_event.set()
            self.schedule.configure(False, self.schedule.interval, self.monotonic())
            self.wake.set()
            thread = self.thread
        if thread is not None:
            thread.join(timeout=35)
            if thread.is_alive():
                self._error("Camera shutdown timed out; inspect camera driver", state="error")

    def snapshot(self):
        with self.lock:
            next_at = None
            if self.schedule.deadline is not None:
                next_at = iso(self.clock() + timedelta(seconds=max(0, self.schedule.deadline - self.monotonic())))
            return {**deepcopy(self.state), "preview_enabled": self.settings["preview_enabled"],
                    "preview_state": "streaming" if self.frame and self.viewers and self.state["state"] == "ready" else "idle",
                    "preview_width": self.settings["preview_width"], "preview_height": self.settings["preview_height"],
                    "preview_fps": self.settings["preview_fps"], "preview_at": self.frame_at,
                    "timelapse_running": self.schedule.running, "next_capture_at": next_at,
                    "interval_seconds": self.schedule.interval,
                    "capture_directory": str(self.capture_directory), "timelapse_directory": str(self.timelapse_directory),
                    "jobs": deepcopy(list(self.jobs.values()))}

    def _available(self):
        if self.stop_event.is_set() or self.state["state"] not in {"ready", "capturing"}:
            raise CameraUnavailable("Camera is unavailable; check status and Pi camera diagnostics")

    def set_timelapse(self, running, interval):
        with self.lock:
            if running:
                self._available()
            self.schedule.configure(running, interval, self.monotonic())
            self.wake.set()

    def subscribe(self):
        with self.lock:
            self._available()
            if not self.settings["preview_enabled"]:
                raise CameraUnavailable("Preview is disabled")
            if self.viewers >= 4:
                raise CameraBusy("At most four preview viewers are supported")
            self.viewers += 1
            self.wake.set()

    def unsubscribe(self):
        with self.lock:
            self.viewers = max(0, self.viewers - 1)
            if not self.viewers:
                self.frame, self.frame_at = None, None

    def preview_frame(self, previous):
        with self.lock:
            if self.state["state"] == "ready" and self.frame is not None and self.sequence != previous:
                return self.sequence, self.frame
            return previous, None

    def submit(self, action, **values):
        if action not in {"capture", "focus"}:
            raise ValueError("Unknown camera action")
        with self.lock:
            self._available()
            if action == "focus":
                cap = self.state["capabilities"]
                mode, lens, trigger = values.get("mode"), values.get("lens_position"), values.get("trigger", False)
                if mode not in cap["focus_modes"] or not isinstance(trigger, bool):
                    raise ValueError("Unsupported focus mode or trigger")
                if trigger and (mode != "Auto" or not cap["autofocus_trigger"]):
                    raise ValueError("Autofocus trigger unsupported")
                if lens is not None:
                    from ..domain import bounded
                    if mode != "Manual" or not cap["lens_limits"]:
                        raise ValueError("Manual lens control unsupported")
                    bounded(lens, *cap["lens_limits"][:2], "LensPosition")
            # One pending manual capture prevents accidental full-resolution bursts.
            if action == "capture" and any(j["action"] == action and j["status"] in {"queued", "running"} for j in self.jobs.values()):
                raise CameraBusy("A manual capture is already pending")
            key = uuid4().hex
            try:
                self.queue.put_nowait((key, action, values))
            except Full:
                raise CameraBusy("Camera command queue is full") from None
            self.jobs[key] = {"id": key, "action": action, "status": "queued", "error": None}
            while len(self.jobs) > 32:
                self.jobs.popitem(last=False)
            self.wake.set()
            return key

    def _error(self, message, *, state=None):
        log.warning("Camera: %s", message)
        with self.lock:
            self.state["error"] = message
            if state:
                self.state["state"] = state
            self.frame, self.frame_at = None, None

    def _metadata(self, raw):
        with self.lock:
            self.state["lens_position"] = raw.get("LensPosition", self.state["lens_position"])
            # libcamera native ordinal state; UI labels the known enum values.
            self.state["autofocus_state"] = raw.get("AfState", self.state["autofocus_state"])

    def _capture(self, backend, kind):
        directory = self.timelapse_directory if kind == "timelapse" else self.capture_directory
        directory.mkdir(parents=True, exist_ok=True)
        path = directory / capture_name(self.clock(), kind)
        temporary = path.with_suffix(".jpg.part")
        with self.lock:
            self.state["state"] = "capturing"
            self.frame, self.frame_at = None, None
        try:
            raw = backend.capture(temporary)
            if not temporary.is_file() or temporary.stat().st_size == 0:
                raise OSError("Capture did not write an image")
            os.replace(temporary, path)
            self._metadata(raw)
            with self.lock:
                self.latest_path = path
                self.state.update(last_capture_at=iso(self.clock()), last_filename=path.name, error=None)
            log.info("Camera %s saved: %s", kind, path.name)
        finally:
            if temporary.exists():
                temporary.unlink()
            with self.lock:
                self.state["state"] = "ready"

    def _run(self):
        backend = None
        try:
            backend = self.backend_factory()
            info = backend.open(self.settings)
            with self.lock:
                self.state.update(info, state="ready", detected=True, error=None)
                self.schedule.configure(self.settings["timelapse"]["enabled"], self.schedule.interval, self.monotonic())
            log.info("Camera ready: %s", info.get("model", "Unknown"))
            next_preview, next_metadata = 0, 0
            while not self.stop_event.is_set():
                self.wake.clear()
                try:
                    key, action, values = self.queue.get_nowait()
                except Empty:
                    key = None
                if key is not None:
                    with self.lock:
                        self.jobs[key]["status"] = "running"
                    try:
                        if action == "capture":
                            self._capture(backend, "still")
                            with self.lock:
                                self.schedule.completed(self.monotonic())
                        else:
                            backend.focus(**values)
                            with self.lock:
                                self.state.update(focus_mode=values["mode"], error=None)
                        with self.lock:
                            self.jobs[key]["status"] = "done"
                    except Exception as exc:
                        message = f"{action} failed ({type(exc).__name__})"
                        self._error(message)
                        with self.lock:
                            self.jobs[key].update(status="failed", error=message)
                        if isinstance(exc, CameraFault):
                            raise
                    continue
                with self.lock:
                    due = self.schedule.due(self.monotonic())
                    viewers = self.viewers
                if due:
                    try:
                        self._capture(backend, "timelapse")
                    except Exception as exc:
                        self._error(f"Timelapse capture failed ({type(exc).__name__})")
                        if isinstance(exc, CameraFault):
                            raise
                    finally:
                        with self.lock:
                            self.schedule.completed(self.monotonic())
                    continue
                now = self.monotonic()
                try:
                    if viewers and self.settings["preview_enabled"] and now >= next_preview:
                        frame, raw = backend.preview()
                        if len(frame) > 2 * 1024 * 1024:
                            raise ValueError("Preview frame exceeds size budget")
                        self._metadata(raw)
                        with self.lock:
                            if self.viewers:
                                self.frame, self.frame_at = frame, iso(self.clock())
                                self.sequence += 1
                        next_preview = self.monotonic() + 1 / self.settings["preview_fps"]
                    elif now >= next_metadata:
                        self._metadata(backend.metadata())
                        next_metadata = self.monotonic() + 2
                except Exception as exc:
                    self._error(f"Camera frame failed ({type(exc).__name__})")
                    if isinstance(exc, CameraFault):
                        raise
                    next_preview = next_metadata = self.monotonic() + 5
                self.wake.wait(0.1)
        except Exception as exc:
            with self.lock:
                if self.state["detected"] is None:
                    self.state["detected"] = False
            self._error(f"Camera startup/worker failed ({type(exc).__name__}); check apt stack, device permissions and camera connection", state="error")
        finally:
            if backend is not None:
                try:
                    backend.close()
                except Exception as exc:
                    self._error(f"Camera release failed ({type(exc).__name__})", state="error")
            with self.lock:
                self.schedule.configure(False, self.schedule.interval, self.monotonic())
                self.frame, self.frame_at = None, None
                if self.state["state"] != "error":
                    self.state["state"] = "stopped"
                for job in self.jobs.values():
                    if job["status"] in {"queued", "running"}:
                        job.update(status="canceled", error="Camera stopped")
