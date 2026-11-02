"""Independent real activity poses: belly, swat, tired rest and play crouch."""
from __future__ import annotations

import json
import math
from pathlib import Path

from PyQt6.QtCore import QRectF, Qt
from PyQt6.QtGui import QImage, QPainter, QPixmap, QTransform

ASSETS = Path(__file__).with_name("assets")
STATES = ("belly", "swatting", "resting", "play_crouch")


class ActivityArt:
    """Decode once into four small poses, without retaining the source atlas."""

    def __init__(self, assets: Path | None = None):
        self.assets = Path(assets) if assets is not None else ASSETS
        self.native_facing = -1
        self._loaded = False
        self._frames: dict[str, QPixmap] = {}
        self._mirrors: dict[str, QPixmap] = {}
        self.load_error: str | None = None

    def load_frames(self) -> dict[str, QPixmap]:
        """Return exactly STATES at 288x224/DPR2; incomplete groups return {}."""
        if self._loaded:
            return self._frames
        self._loaded = True
        try:
            data = json.loads((self.assets / "activity.json").read_text(encoding="utf-8"))
            if (data["schema_version"] != 1 or data["accepted"] is not True
                    or tuple(row["name"] for row in data["frames"]) != STATES
                    or data["states"] != {name: [i] for i, name in enumerate(STATES)}
                    or data["logical_size"] != [144,112] or data["pixel_ratio"] != 2
                    or data["native_facing"] != -1):
                raise ValueError("Activity manifest must define the four accepted poses")
            name = data["file"]
            if not isinstance(name, str) or Path(name).name != name:
                raise ValueError("Activity file must be an asset filename")
            width, height = data["width"], data["height"]
            if any(type(v) is not int or not 1 <= v <= 4096 for v in (width,height)):
                raise ValueError("Invalid activity atlas dimensions")
            scale, destination = data["render_scale"], data["render_anchor"]
            if type(scale) not in (int,float) or not math.isfinite(scale) or not 0 < scale <= 1:
                raise ValueError("Invalid activity source scale")
            atlas = QImage(str(self.assets / name))
            if (atlas.isNull() or not atlas.hasAlphaChannel()
                    or (atlas.width(),atlas.height()) != (width,height)):
                raise ValueError("Activity atlas is missing, changed or lacks alpha")
            staged = {}
            for row in data["frames"]:
                crop, anchor = row["rect"], row["foot_anchor"]
                if (len(crop) != 4 or any(type(v) is not int for v in crop)
                        or min(crop[:2]) < 0 or min(crop[2:]) <= 0
                        or crop[0]+crop[2] > width or crop[1]+crop[3] > height):
                    raise ValueError("Activity crop lies outside atlas")
                for point in (anchor,destination):
                    if (len(point) != 2 or any(type(v) not in (int,float)
                            or not math.isfinite(v) for v in point)):
                        raise ValueError("Invalid activity landmark")
                if not (0 <= anchor[0] <= crop[2] and 0 <= anchor[1] <= crop[3]
                        and 0 <= destination[0] <= 144 and 0 <= destination[1] <= 112):
                    raise ValueError("Activity anchor lies outside canvas")
                frame = QPixmap(288,224)
                frame.fill(Qt.GlobalColor.transparent)
                painter = QPainter(frame)
                painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
                painter.drawImage(QRectF(destination[0]*2-anchor[0]*scale,
                                         destination[1]*2-anchor[1]*scale,
                                         crop[2]*scale,crop[3]*scale),
                                  atlas,QRectF(*crop))
                painter.end()
                frame.setDevicePixelRatio(2)
                staged[row["name"]] = frame
            self._frames = staged
        except (OSError,ValueError,TypeError,KeyError,AttributeError,OverflowError) as error:
            self.load_error = f"Activity artwork unavailable: {error}"
        return self._frames

    def frame(self, state: str, facing: int = -1) -> QPixmap | None:
        frames = self.load_frames()
        if state not in frames:
            return None
        if facing == self.native_facing:
            return frames[state]
        if state not in self._mirrors:
            self._mirrors[state] = frames[state].transformed(QTransform().scale(-1,1))
        return self._mirrors[state]

    def paint(self, painter: QPainter, rect, state: str, facing: int = -1) -> bool:
        rect = QRectF(rect)
        if rect.isEmpty():
            return False
        frame = self.frame(state,facing)
        if frame is None:
            return False
        scale = min(rect.width()/144,rect.height()/112)
        target = QRectF(rect.center().x()-72*scale,rect.bottom()-112*scale,144*scale,112*scale)
        painter.save()
        painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
        painter.drawPixmap(target,frame,QRectF(frame.rect()))
        painter.restore()
        return True

    draw = paint
