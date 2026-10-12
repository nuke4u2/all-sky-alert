"""Camera defaults and full configuration validation; no hardware imports."""
from pathlib import Path

from ..domain import bounded

DEFAULTS = {
    "enabled": False, "backend": "picamera2", "camera_index": 0,
    "capture_directory": "camera/stills", "preview_enabled": True,
    "preview_width": 640, "preview_height": 480, "preview_fps": 2,
    "timelapse": {"enabled": False, "interval_seconds": 60,
                  "directory": "camera/timelapse"},
}


def validate_interval(value):
    bounded(value, 10, 604800, "camera.timelapse.interval_seconds")


def validate(settings, base, data_dir):
    for section, key in ((settings, "enabled"), (settings, "preview_enabled"),
                         (settings["timelapse"], "enabled")):
        if not isinstance(section[key], bool):
            raise ValueError("Camera toggles must be boolean")
    if settings["backend"] != "picamera2":
        raise ValueError("Only the picamera2 camera backend is implemented")
    for key, low, high in (("camera_index", 0, 3), ("preview_width", 160, 1280),
                           ("preview_height", 120, 720)):
        bounded(settings[key], low, high, "camera." + key)
        if not isinstance(settings[key], int):
            raise ValueError("Camera index and dimensions must be integers")
    bounded(settings["preview_fps"], 1, 5, "camera.preview_fps")
    validate_interval(settings["timelapse"]["interval_seconds"])
    # Capture paths are local-only operational configuration, never web inputs.
    root = (Path(base) / data_dir).resolve()
    for value in (settings["capture_directory"], settings["timelapse"]["directory"]):
        if not isinstance(value, str) or not value or any(ord(c) < 32 for c in value):
            raise ValueError("Set a camera runtime directory")
        path = (root / value).resolve()
        if path == root or not path.is_relative_to(root):
            raise ValueError("Camera capture directories must be below service.data_dir")
