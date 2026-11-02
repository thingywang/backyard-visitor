"""One lazy, transparent fallen-fur sprite within the existing gift bounds."""
from __future__ import annotations

from pathlib import Path

from PyQt6.QtCore import QRectF, Qt
from PyQt6.QtGui import QImage, QPainter, QPixmap

ASSETS = Path(__file__).with_name("assets")
LOGICAL_SIZE = 38
PIXEL_RATIO = 2


class FurArt:
    """Retain one small frame; source pixels and game geometry stay unchanged."""

    def __init__(self, assets: Path | None = None):
        self.assets = Path(assets) if assets is not None else ASSETS
        self._loaded = False
        self._frame: QPixmap | None = None
        self.load_error: str | None = None

    def frame(self) -> QPixmap | None:
        if self._loaded:
            return self._frame
        self._loaded = True
        source = QImage(str(self.assets / "fur-tuft.png"))
        if source.isNull() or not source.hasAlphaChannel():
            self.load_error = "Transparent fallen-fur artwork could not be loaded"
            return None
        side = LOGICAL_SIZE * PIXEL_RATIO
        frame = QPixmap(side, side)
        frame.fill(Qt.GlobalColor.transparent)
        # Fit the unchanged original. Four physical pixels protect wisps from
        # clipping without changing the established 38px desktop object.
        scale = (side-8) / max(source.width(), source.height())
        width, height = source.width()*scale, source.height()*scale
        painter = QPainter(frame)
        painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
        painter.drawImage(QRectF((side-width)/2, (side-height)/2, width, height), source)
        painter.end()
        frame.setDevicePixelRatio(PIXEL_RATIO)
        self._frame = frame
        return frame

    def paint(self, painter: QPainter, rect) -> bool:
        rect = QRectF(rect)
        if rect.isEmpty():
            return False
        frame = self.frame()
        if frame is None:
            return False
        side = min(rect.width(), rect.height())
        target = QRectF(rect.center().x()-side/2, rect.center().y()-side/2, side, side)
        painter.save()
        painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
        painter.drawPixmap(target, frame, QRectF(frame.rect()))
        painter.restore()
        return True
