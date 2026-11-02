"""Lazy, small raster gifts shared by desktop objects and carried-gift overlays."""
from __future__ import annotations

import json
from pathlib import Path

from PyQt6.QtCore import QRectF, Qt
from PyQt6.QtGui import QImage, QPainter, QPixmap, QTransform

ASSETS = Path(__file__).with_name("assets")
LOGICAL_SIZE = 38
PIXEL_RATIO = 2
KINDS = frozenset(("feather", "bottle_cap", "mouse"))


class GiftArt:
    """Reuse one instance per controller; no atlas pixels survive the first load.

    ``toy_mouse`` preserves the existing save format while displaying the real
    mouse raster. If realistic_mouse is false, mouse requests return None/False
    so the caller can draw its existing simplified mouse. Fur/unknown objects
    also return False instead of silently appearing as a different gift.
    """

    def __init__(self, assets: Path | None = None):
        self.assets = Path(assets) if assets is not None else ASSETS
        self._loaded = False
        self._frames: dict[str, QPixmap] = {}
        self._mirrors: dict[str, QPixmap] = {}
        self.load_error: str | None = None

    def _load(self):
        if self._loaded:
            return
        self._loaded = True
        try:
            data = json.loads((self.assets / "gifts.json").read_text(encoding="utf-8"))
            atlas = QImage(str(self.assets / data["file"]))
            if atlas.isNull():
                self.load_error = "Gift atlas could not be loaded"
                return
            pixel_size = LOGICAL_SIZE * PIXEL_RATIO
            inset = 2 * PIXEL_RATIO
            for kind in KINDS:
                rect = data["frames"][kind]["rect"]
                if (len(rect) != 4 or any(type(n) is not int for n in rect)
                        or rect[0] < 0 or rect[1] < 0 or rect[2] <= 0 or rect[3] <= 0
                        or rect[0]+rect[2] > atlas.width() or rect[1]+rect[3] > atlas.height()):
                    raise ValueError(f"Invalid gift crop: {kind}")
                scale = min((pixel_size-2*inset)/rect[2], (pixel_size-2*inset)/rect[3])
                width, height = rect[2]*scale, rect[3]*scale
                frame = QPixmap(pixel_size, pixel_size)
                frame.fill(Qt.GlobalColor.transparent)
                painter = QPainter(frame)
                painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
                painter.drawImage(QRectF((pixel_size-width)/2, (pixel_size-height)/2, width, height),
                                  atlas, QRectF(*rect))
                painter.end()
                frame.setDevicePixelRatio(PIXEL_RATIO)
                self._frames[kind] = frame
            # atlas is a local QImage; retain only the three ~23 KB small frames.
        except (OSError, ValueError, TypeError, KeyError, AttributeError) as error:
            self._frames.clear()
            self.load_error = f"Gift atlas unavailable: {error}"

    def frame(self, kind: str, *, realistic_mouse: bool = True, facing: int = -1) -> QPixmap | None:
        kind = "mouse" if kind == "toy_mouse" else kind
        if kind not in KINDS or (kind == "mouse" and not realistic_mouse):
            return None
        self._load()
        frame = self._frames.get(kind)
        if frame is None or facing <= 0:
            return frame
        if kind not in self._mirrors:
            self._mirrors[kind] = frame.transformed(QTransform().scale(-1, 1))
        return self._mirrors[kind]

    def paint(self, painter: QPainter, rect, kind: str, *, realistic_mouse: bool = True,
              facing: int = -1, opacity: float = 1) -> bool:
        """Fit the sprite within rect, preserving shape and the caller's painter state."""
        rect = QRectF(rect)
        if rect.isEmpty():
            return False
        frame = self.frame(kind, realistic_mouse=realistic_mouse, facing=facing)
        if frame is None:
            return False
        side = min(rect.width(), rect.height())
        target = QRectF(rect.center().x()-side/2, rect.center().y()-side/2, side, side)
        painter.save()
        painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
        painter.setOpacity(painter.opacity()*max(0., min(1., opacity)))
        painter.drawPixmap(target, frame, QRectF(frame.rect()))
        painter.restore()
        return True

    draw = paint
