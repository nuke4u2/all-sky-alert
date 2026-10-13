"""Small replaceable hardware boundary. All methods run on the camera owner thread."""
from typing import Protocol
from pathlib import Path


class CameraFault(RuntimeError):
    """The driver state is uncertain; release it instead of issuing more work."""


class CameraBackend(Protocol):
    def open(self, settings: dict) -> dict: ...
    def metadata(self) -> dict: ...
    def preview(self) -> tuple[bytes, dict]: ...
    def capture(self, path: Path) -> dict: ...
    def focus(self, mode: str, lens_position: float | None, trigger: bool) -> None: ...
    def close(self) -> None: ...


class RaspberryPiCameraBackend:
    """Apt Picamera2/libcamera only; lazy imports keep ordinary development portable."""
    def __init__(self, *, camera_factory=None, controls=None):
        self.factory, self.controls = camera_factory, controls
        self.camera = None
        self.focus_controls = {}
        self.failed = False

    def open(self, settings):
        if self.factory is None:
            from picamera2 import Picamera2
            from libcamera import controls
            self.factory, self.controls = Picamera2, controls
        self.camera = self.factory(settings["camera_index"])
        camera = self.camera
        self.preview_config = camera.create_preview_configuration(
            main={"size": (settings["preview_width"], settings["preview_height"]), "format": "RGB888"},
            buffer_count=3, controls={"FrameRate": settings["preview_fps"]})
        self.still_config = camera.create_still_configuration(
            main={"size": camera.sensor_resolution}, buffer_count=1)
        camera.configure(self.preview_config)
        supported = camera.camera_controls
        modes = []
        if "AfMode" in supported:
            low, high, _ = supported["AfMode"]
            modes = [name for name in ("Auto", "Continuous", "Manual")
                     if low <= int(getattr(self.controls.AfModeEnum, name)) <= high]
        lens = supported.get("LensPosition")
        self.capabilities = {"focus_modes": modes, "autofocus_trigger": "AfTrigger" in supported and "Auto" in modes,
                             "lens_limits": [float(v) for v in lens] if lens else None}
        if "Continuous" in modes:
            self.focus_controls = {"AfMode": self.controls.AfModeEnum.Continuous}
            camera.set_controls(self.focus_controls)
        camera.start(show_preview=False)
        return {"model": str(camera.camera_properties.get("Model", "Unknown")),
                "sensor_resolution": list(camera.sensor_resolution),
                "focus_mode": "Continuous" if "Continuous" in modes else None,
                "capabilities": self.capabilities}

    def _wait(self, job):
        # A timeout alone leaves the job queued in Picamera2. Explicitly clear it
        # before the owner may issue another operation or restore preview mode.
        try:
            return self.camera.wait(job, timeout=15)
        except TimeoutError:
            self.failed = True
            try:
                self.camera.cancel_all_and_flush()
            finally:
                raise CameraFault("Camera operation timed out; restart camera service") from None

    @staticmethod
    def _metadata(raw):
        return {key: float(raw[key]) if key == "LensPosition" else int(raw[key])
                for key in ("LensPosition", "AfState") if key in raw}

    def metadata(self):
        return self._metadata(self._wait(self.camera.capture_metadata(wait=False)))

    def preview(self):
        from io import BytesIO
        output = BytesIO()
        raw = self._wait(self.camera.capture_file(output, format="jpeg", wait=False))
        return output.getvalue(), self._metadata(raw)

    def capture(self, path):
        camera = self.camera
        # Explicit finally restoration also handles a failed JPEG write. Only one
        # full-resolution buffer; full-resolution frames are never preview-encoded.
        camera.stop()
        try:
            camera.configure(self.still_config)
            if self.focus_controls:
                camera.set_controls(self.focus_controls)
            camera.start(show_preview=False)
            return self._metadata(self._wait(camera.capture_file(str(path), format="jpeg", wait=False)))
        finally:
            try:
                camera.stop()
                if not self.failed:
                    camera.configure(self.preview_config)
                    if self.focus_controls:
                        camera.set_controls(self.focus_controls)
                    camera.start(show_preview=False)
            except Exception:
                self.failed = True
                raise CameraFault("Camera preview restoration failed; restart after inspecting hardware") from None

    def focus(self, mode, lens_position=None, trigger=False):
        if mode not in self.capabilities["focus_modes"]:
            raise ValueError("Focus mode unsupported by this camera")
        values = {"AfMode": getattr(self.controls.AfModeEnum, mode)}
        if lens_position is not None:
            from ..domain import bounded
            limits = self.capabilities["lens_limits"]
            if mode != "Manual" or not limits:
                raise ValueError("Manual lens position unsupported")
            bounded(lens_position, limits[0], limits[1], "LensPosition")
            values["LensPosition"] = lens_position
        if trigger:
            if mode != "Auto" or not self.capabilities["autofocus_trigger"]:
                raise ValueError("Autofocus trigger unsupported")
            values["AfTrigger"] = self.controls.AfTriggerEnum.Start
        self.camera.set_controls(values)
        self.focus_controls = {key: value for key, value in values.items() if key != "AfTrigger"}

    def close(self):
        if self.camera is not None:
            try:
                self.camera.stop()
            finally:
                self.camera.close()
                self.camera = None
