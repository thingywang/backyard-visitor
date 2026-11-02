"""Three actual jump preparation/contact poses and matching mouth landmarks."""
from __future__ import annotations

import json
import math
from pathlib import Path

from PyQt6.QtCore import QRectF, Qt
from PyQt6.QtGui import QImage, QPainter, QPixmap

ASSETS = Path(__file__).with_name("assets")
FRAME_NAMES = ("takeoff", "compressed", "recover")


class JumpArt:
    """Lazy, atomic loading; CatArt owns pose timing, mirroring and coat variants."""

    def __init__(self, assets: Path | None = None):
        self.assets = Path(assets) if assets is not None else ASSETS
        self.native_facing = -1
        self.mouth_anchors: list[tuple[float, float]] = []
        self._loaded = False
        self._frames: list[QPixmap] = []
        self.load_error: str | None = None

    def load_frames(self) -> list[QPixmap]:
        """Return [takeoff, compressed, recover] at 288x224/DPR2, or [].

        mouth_anchors uses 144x112 logical coordinates before CatArt's paint
        offset. Both frames and anchors publish only after the full group passes.
        """
        if self._loaded:
            return self._frames
        self._loaded = True
        try:
            data = json.loads((self.assets / "jump_transitions.json").read_text(encoding="utf-8"))
            if (data["schema_version"] != 1 or data["accepted"] is not True
                    or tuple(info["name"] for info in data["frames"]) != FRAME_NAMES):
                raise ValueError("Three accepted jump poses must have the required order")
            name = data["file"]
            if not isinstance(name, str) or Path(name).name != name:
                raise ValueError("Jump file must be an asset filename")
            if (data["logical_size"] != [144, 112] or data["pixel_ratio"] != 2
                    or data["native_facing"] != -1):
                raise ValueError("Jump artwork must use the shared left-facing Retina canvas")
            width, height = data["width"], data["height"]
            if (type(width) is not int or type(height) is not int
                    or not 1 <= width <= 4096 or not 1 <= height <= 4096):
                raise ValueError("Invalid jump source dimensions")
            scale = data["render_scale"]
            if type(scale) not in (int, float) or not math.isfinite(scale) or not 0 < scale <= 1:
                raise ValueError("Invalid jump source scale")
            destination = data["render_anchor"]
            staged_frames, staged_mouths = [], []
            atlas = QImage(str(self.assets / name))
            if atlas.isNull() or not atlas.hasAlphaChannel() or (atlas.width(), atlas.height()) != (width, height):
                raise ValueError("Jump atlas is missing, changed or lacks alpha")
            for info in data["frames"]:
                rect, anchor, mouth = info["rect"], info["foot_anchor"], info["mouth_anchor"]
                if (len(rect) != 4 or not all(type(v) is int for v in rect)
                        or min(rect[:2]) < 0 or min(rect[2:]) <= 0
                        or rect[0]+rect[2] > width or rect[1]+rect[3] > height):
                    raise ValueError("Jump crop lies outside its atlas")
                for point in (destination, anchor, mouth):
                    if (len(point) != 2 or not all(type(v) in (int, float)
                            and math.isfinite(v) for v in point)):
                        raise ValueError("Invalid jump landmark")
                if not (0 <= mouth[0] <= rect[2] and 0 <= mouth[1] <= rect[3]
                        and 0 <= anchor[0] <= rect[2] and 0 <= anchor[1] <= rect[3]):
                    raise ValueError("Jump source landmarks lie outside crop")
                logical_mouth = (destination[0]+(mouth[0]-anchor[0])*scale/2,
                                 destination[1]+(mouth[1]-anchor[1])*scale/2)
                if not (0 <= logical_mouth[0] <= 144 and 0 <= logical_mouth[1] <= 112):
                    raise ValueError("Jump mouth lies outside output canvas")
                frame = QPixmap(288, 224)
                frame.fill(Qt.GlobalColor.transparent)
                painter = QPainter(frame)
                painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
                painter.drawImage(QRectF(destination[0]*2-anchor[0]*scale,
                                         destination[1]*2-anchor[1]*scale,
                                         rect[2]*scale, rect[3]*scale),
                                  atlas, QRectF(*rect))
                painter.end()
                frame.setDevicePixelRatio(2)
                staged_frames.append(frame)
                staged_mouths.append(logical_mouth)
            self._frames, self.mouth_anchors = staged_frames, staged_mouths
        except (OSError, ValueError, TypeError, KeyError, AttributeError, OverflowError) as error:
            self.load_error = f"Jump artwork unavailable: {error}"
        return self._frames
