"""Three real sitting-down key poses, aligned by the planted near front paw."""
from __future__ import annotations

import json
import math
from pathlib import Path

from PyQt6.QtCore import QRectF, Qt
from PyQt6.QtGui import QImage, QPainter, QPixmap

ASSETS = Path(__file__).with_name("assets")


class TransitionArt:
    """Existing StandingArt supplies the start; these frames end in a side sit.

    CatArt owns direction/coat variants and timing. load_frames() supplies only
    the three new key poses. No image blending, body scaling or intermediate
    anatomy is invented by this loader.
    """

    def __init__(self, assets: Path | None = None):
        self.assets = Path(assets) if assets is not None else ASSETS
        self.duration_seconds = 0.72
        self.native_facing = -1
        self._loaded = False
        self._frames: list[QPixmap] = []
        self.load_error: str | None = None

    def load_frames(self) -> list[QPixmap]:
        if self._loaded:
            return self._frames
        self._loaded = True
        try:
            data = json.loads((self.assets / "settling.json").read_text(encoding="utf-8"))
            if data["schema_version"] != 1 or data["accepted"] is not True or len(data["frames"]) != 3:
                raise ValueError("Three accepted settling poses are required")
            name = data["file"]
            if not isinstance(name, str) or Path(name).name != name:
                raise ValueError("Settling file must be an asset filename")
            if data["logical_size"] != [144, 112] or data["pixel_ratio"] != 2:
                raise ValueError("Settling poses require the shared Retina canvas")
            scale = float(data["render_scale"])
            duration = float(data["duration_seconds"])
            if not math.isfinite(scale) or not 0 < scale <= 1:
                raise ValueError("Invalid settling scale")
            if not math.isfinite(duration) or not .1 <= duration <= 3:
                raise ValueError("Invalid settling duration")
            width, height = data["width"], data["height"]
            if (type(width) is not int or type(height) is not int
                    or not 1 <= width <= 4096 or not 1 <= height <= 4096):
                raise ValueError("Invalid settling source dimensions")
            atlas = QImage(str(self.assets / name))
            if atlas.isNull() or not atlas.hasAlphaChannel() or (atlas.width(), atlas.height()) != (width, height):
                raise ValueError("Settling atlas is missing, changed or lacks alpha")
            destination = data["render_anchor"]
            staged = []
            for info in data["frames"]:
                rect, anchor = info["rect"], info["foot_anchor"]
                if (len(rect) != 4 or not all(type(v) is int for v in rect)
                        or min(rect[:2]) < 0 or min(rect[2:]) <= 0
                        or rect[0]+rect[2] > width or rect[1]+rect[3] > height):
                    raise ValueError("Settling crop lies outside its atlas")
                if (len(anchor) != 2 or len(destination) != 2
                        or not all(type(v) in (int, float) and math.isfinite(v)
                                   for v in [*anchor, *destination])):
                    raise ValueError("Invalid settling anchor")
                canvas = QPixmap(288, 224)
                canvas.fill(Qt.GlobalColor.transparent)
                painter = QPainter(canvas)
                painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
                painter.drawImage(QRectF(destination[0]*2-anchor[0]*scale,
                                         destination[1]*2-anchor[1]*scale,
                                         rect[2]*scale, rect[3]*scale),
                                  atlas, QRectF(*rect))
                painter.end()
                canvas.setDevicePixelRatio(2)
                staged.append(canvas)
            self._frames = staged
            self.duration_seconds = duration
            self.native_facing = data.get("native_facing", -1)
        except (OSError, ValueError, TypeError, KeyError, AttributeError, OverflowError) as error:
            self.load_error = f"Settling artwork unavailable: {error}"
        return self._frames

    def frame(self, progress: float) -> QPixmap | None:
        """Select one new key pose; progress 1 holds the side-seated endpoint."""
        frames = self.load_frames()
        if not frames:
            return None
        if type(progress) not in (int, float) or not math.isfinite(progress):
            progress = 0.0
        index = min(len(frames)-1, int(max(0.0, min(1.0, progress))*len(frames)))
        return frames[index]
