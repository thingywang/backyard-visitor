"""Four genuine running poses, loaded atomically on a shared Retina canvas."""
from __future__ import annotations

import json
import math
from pathlib import Path

from PyQt6.QtCore import QRectF, Qt
from PyQt6.QtGui import QImage, QPainter, QPixmap, QTransform

ASSETS = Path(__file__).with_name("assets")
FRAME_NAMES = ("hind_push", "extended_flight", "fore_contact", "gathered_flight")


class RunArt:
    """Keep only small frames; CatArt may reuse its existing coat-aware cache."""

    def __init__(self, assets: Path | None = None):
        self.assets = Path(assets) if assets is not None else ASSETS
        self.cycle_seconds = .48
        self.native_facing = -1
        self.mouth_anchors: list[tuple[float, float]] = []
        self._loaded = False
        self._frames: list[QPixmap] = []
        self._mirrors: dict[int, QPixmap] = {}
        self.load_error: str | None = None

    def load_frames(self) -> list[QPixmap]:
        """Return four 288x224/DPR2 poses, or []; publish landmarks atomically.

        Mouth landmarks use 144x112 logical coordinates, before CatArt's -2px
        drawing offset. Airborne poses use a virtual foot_anchor BELOW the crop:
        anchoring to their visible feet would incorrectly remove suspension.
        """
        if self._loaded:
            return self._frames
        self._loaded = True
        try:
            data = json.loads((self.assets / "run.json").read_text(encoding="utf-8"))
            if (data["schema_version"] != 1 or data["accepted"] is not True
                    or tuple(row["name"] for row in data["frames"]) != FRAME_NAMES
                    or data["logical_size"] != [144, 112] or data["pixel_ratio"] != 2
                    or data["native_facing"] != -1):
                raise ValueError("Run manifest does not match the four-pose canvas contract")
            name = data["file"]
            if not isinstance(name, str) or Path(name).name != name:
                raise ValueError("Run file must be an asset filename")
            width, height = data["width"], data["height"]
            if any(type(v) is not int or not 1 <= v <= 4096 for v in (width, height)):
                raise ValueError("Invalid run atlas dimensions")
            scale, cycle = data["render_scale"], data["cycle_seconds"]
            if any(type(v) not in (int, float) or not math.isfinite(v) for v in (scale, cycle)):
                raise ValueError("Invalid run scale or duration")
            if not 0 < scale <= 1 or not .1 <= cycle <= 10:
                raise ValueError("Invalid run scale or duration")
            destination = data["render_anchor"]
            atlas = QImage(str(self.assets / name))
            if atlas.isNull() or not atlas.hasAlphaChannel() or (atlas.width(), atlas.height()) != (width, height):
                raise ValueError("Run atlas is missing, changed or lacks alpha")
            staged, mouths = [], []
            for row in data["frames"]:
                crop, anchor, mouth = row["rect"], row["foot_anchor"], row["mouth_anchor"]
                if (len(crop) != 4 or any(type(v) is not int for v in crop)
                        or min(crop[:2]) < 0 or min(crop[2:]) <= 0
                        or crop[0]+crop[2] > width or crop[1]+crop[3] > height):
                    raise ValueError("Run crop lies outside atlas")
                for point in (destination, anchor, mouth):
                    if (len(point) != 2 or any(type(v) not in (int, float)
                            or not math.isfinite(v) for v in point)):
                        raise ValueError("Invalid run landmark")
                if not (0 <= anchor[0] <= crop[2] and 0 <= anchor[1] <= crop[3]+256
                        and 0 <= mouth[0] <= crop[2] and 0 <= mouth[1] <= crop[3]):
                    raise ValueError("Run landmark lies outside permitted source geometry")
                logical_mouth = (destination[0]+(mouth[0]-anchor[0])*scale/2,
                                 destination[1]+(mouth[1]-anchor[1])*scale/2)
                if not (0 <= logical_mouth[0] <= 144 and 0 <= logical_mouth[1] <= 112):
                    raise ValueError("Run mouth lies outside the output canvas")
                frame = QPixmap(288, 224)
                frame.fill(Qt.GlobalColor.transparent)
                painter = QPainter(frame)
                painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
                painter.drawImage(QRectF(destination[0]*2-anchor[0]*scale,
                                         destination[1]*2-anchor[1]*scale,
                                         crop[2]*scale, crop[3]*scale),
                                  atlas, QRectF(*crop))
                painter.end()
                frame.setDevicePixelRatio(2)
                staged.append(frame)
                mouths.append(logical_mouth)
            self._frames, self.mouth_anchors = staged, mouths
            self.cycle_seconds = float(cycle)
        except (OSError, ValueError, TypeError, KeyError, AttributeError, OverflowError) as error:
            self.load_error = f"Run artwork unavailable: {error}"
        return self._frames

    def frame_index(self, age: float = 0, *, gait_phase: float | None = None) -> int | None:
        if not self.load_frames():
            return None
        value = age if gait_phase is None else gait_phase
        if not math.isfinite(value) or value < 0:
            value = 0
        phase = (value/self.cycle_seconds if gait_phase is None else value) % 1
        return min(3, int(phase*4+1e-9))

    def frame(self, age: float = 0, facing: int = -1, *, gait_phase: float | None = None) -> QPixmap | None:
        index = self.frame_index(age, gait_phase=gait_phase)
        if index is None:
            return None
        if facing == self.native_facing:
            return self._frames[index]
        if index not in self._mirrors:
            self._mirrors[index] = self._frames[index].transformed(QTransform().scale(-1, 1))
        return self._mirrors[index]

    def paint(self, painter: QPainter, rect, age: float = 0, facing: int = -1,
              *, gait_phase: float | None = None, opacity: float = 1) -> bool:
        rect = QRectF(rect)
        if rect.isEmpty():
            return False
        frame = self.frame(age, facing, gait_phase=gait_phase)
        if frame is None:
            return False
        scale = min(rect.width()/144, rect.height()/112)
        target = QRectF(rect.center().x()-72*scale, rect.bottom()-112*scale, 144*scale, 112*scale)
        painter.save()
        painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
        painter.setOpacity(painter.opacity()*max(0., min(1., opacity)))
        painter.drawPixmap(target, frame, QRectF(frame.rect()))
        painter.restore()
        return True

    draw = paint
