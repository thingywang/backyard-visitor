"""Four alternating walk key poses, decoded on demand into small Retina frames."""
from __future__ import annotations

import json
import math
from pathlib import Path

from PyQt6.QtCore import QRectF, QSize, Qt
from PyQt6.QtGui import QImageReader, QPainter, QPixmap, QTransform

ASSETS = Path(__file__).with_name("assets")


class MotionArt:
    """Share one instance; CatArt can append load_frames() to its coat-aware cache.

    The four poses change actual near/far limb support, rather than cycling two
    versions of one planted stance. This remains a four-keyframe walk, not a
    claim of high-frame-rate motion capture or generated in-between anatomy.
    """

    def __init__(self, assets: Path | None = None):
        self.assets = Path(assets) if assets is not None else ASSETS
        self.cycle_seconds = 0.8
        self.native_facing = -1
        self._loaded = False
        self._frames: list[QPixmap] = []
        self._mirrors: dict[int, QPixmap] = {}
        self.load_error: str | None = None

    def load_frames(self) -> list[QPixmap]:
        """Return four shared 288x224/DPR2 frames; source images are not retained."""
        if self._loaded:
            return self._frames
        self._loaded = True
        try:
            data = json.loads((self.assets / "locomotion.json").read_text(encoding="utf-8"))
            if data.get("accepted") is not True or len(data["frames"]) != 4:
                raise ValueError("Walk manifest requires four visually accepted poses")
            self.cycle_seconds = float(data["cycle_seconds"])
            if not math.isfinite(self.cycle_seconds) or not 0.1 <= self.cycle_seconds <= 10:
                raise ValueError("Invalid walk cycle duration")
            self.native_facing = data.get("native_facing", -1)
            scale = float(data["render_scale"])
            if not math.isfinite(scale) or not 0 < scale <= 1:
                raise ValueError("Invalid walk scale")
            destination = data["render_anchor"]
            for info in data["frames"]:
                reader = QImageReader(str(self.assets / info["file"]))
                # Ask Qt for the final small decode size. PNG backends may use
                # a transient full decode, but only this small image survives read().
                reader.setScaledSize(QSize(round(info["width"]*scale), round(info["height"]*scale)))
                image = reader.read()
                if image.isNull():
                    raise ValueError(f"Walk frame unavailable: {info['file']}")
                frame = QPixmap(288, 224)
                frame.fill(Qt.GlobalColor.transparent)
                painter = QPainter(frame)
                painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
                ax, ay = info["foot_anchor"]
                target = QRectF(destination[0]*2-ax*scale, destination[1]*2-ay*scale,
                                info["width"]*scale, info["height"]*scale)
                painter.drawImage(target, image)
                painter.end()
                frame.setDevicePixelRatio(2)
                self._frames.append(frame)
                # Never retain four full-size source images at once.
                del image, reader
        except (OSError, ValueError, TypeError, KeyError, AttributeError) as error:
            self._frames.clear()
            self.load_error = f"Walk artwork unavailable: {error}"
        return self._frames

    def frame_index(self, age: float) -> int | None:
        frames = self.load_frames()
        if not frames:
            return None
        if not math.isfinite(age) or age < 0:
            age = 0
        phase = (age % self.cycle_seconds) / self.cycle_seconds
        return min(len(frames)-1, int(phase*len(frames)+1e-9))

    def frame(self, age: float, facing: int = -1) -> QPixmap | None:
        index = self.frame_index(age)
        if index is None:
            return None
        frame = self._frames[index]
        if facing == self.native_facing:
            return frame
        if index not in self._mirrors:
            self._mirrors[index] = frame.transformed(QTransform().scale(-1, 1))
        return self._mirrors[index]

    def paint(self, painter: QPainter, rect, age: float = 0, facing: int = -1,
              opacity: float = 1) -> bool:
        """Render the shared 144x112 canvas; no extra bobbing or ground offset."""
        rect = QRectF(rect)
        if rect.isEmpty():
            return False
        frame = self.frame(age, facing)
        if frame is None:
            return False
        scale = min(rect.width()/144, rect.height()/112)
        width, height = 144*scale, 112*scale
        target = QRectF(rect.center().x()-width/2, rect.bottom()-height, width, height)
        painter.save()
        painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
        painter.setOpacity(painter.opacity()*max(0., min(1., opacity)))
        painter.drawPixmap(target, frame, QRectF(frame.rect()))
        painter.restore()
        return True

    draw = paint
