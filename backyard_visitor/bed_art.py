"""Floor-aligned photographic bed, with input derived from its visible alpha."""
from __future__ import annotations

from pathlib import Path
import math

from PyQt6.QtCore import QPointF, QRect, QRectF, Qt
from PyQt6.QtGui import (QColor, QImage, QLinearGradient, QPainter, QPen,
                         QPixmap, QRadialGradient, QRegion)

from .resident_bed import BED_HEIGHT, BED_WIDTH

ASSET = Path(__file__).with_name("assets") / "resident-bed.png"
PIXEL_RATIO = 2
# Ignore almost transparent distant specks only when choosing the source rect.
# Source pixels (including the soft shadow within the rect) remain unchanged.
CORE_ALPHA_MIN = 16
SOURCE_PADDING_FRACTION = .006


def _alpha_rows(image):
    alpha = image.convertToFormat(QImage.Format.Format_Alpha8)
    raw = alpha.constBits().asstring(alpha.sizeInBytes())
    stride, width = alpha.bytesPerLine(), alpha.width()
    return [raw[y*stride:y*stride+width] for y in range(alpha.height())]


def _alpha_bounds(rows, minimum=1):
    lookup = bytes(a if a >= minimum else 0 for a in range(256))
    left, top, right, bottom = len(rows[0]), len(rows), 0, 0
    for y, row in enumerate(rows):
        row = row.translate(lookup)
        stripped = row.lstrip(b"\0")
        if not stripped:
            continue
        left = min(left, len(row)-len(stripped))
        right = max(right, len(row.rstrip(b"\0")))
        top, bottom = min(top, y), y+1
    return QRect(left, top, right-left, bottom-top) if right > left else QRect()


def _input_region(image):
    """Keep a logical pixel if any of its rendered physical pixels is visible."""
    rows = _alpha_rows(image)
    region = QRegion()
    for y in range(BED_HEIGHT):
        start = None
        for x in range(BED_WIDTH+1):
            visible = x < BED_WIDTH and any(
                any(rows[y*PIXEL_RATIO+dy][x*PIXEL_RATIO:(x+1)*PIXEL_RATIO])
                for dy in range(PIXEL_RATIO))
            if visible and start is None:
                start = x
            elif not visible and start is not None:
                region |= QRegion(QRect(start, y, x-start, 1))
                start = None
    return region


class BedArt:
    """The PNG is read unchanged; Qt crops its whitespace and scales uniformly."""
    def __init__(self, asset: Path | None = None):
        self.asset = Path(asset) if asset is not None else ASSET
        self.load_error: str | None = None
        self.source_rect = QRect()
        self.core_rect = QRect()
        self.source_padding = 0
        self.target_rect = QRectF()
        self._loaded = False
        self._frame: QPixmap | None = None
        self._region = QRegion()

    def frame(self) -> QPixmap | None:
        if self._loaded:
            return self._frame
        self._loaded = True
        try:
            source = QImage(str(self.asset))
            if source.isNull() or not source.hasAlphaChannel():
                raise ValueError("missing, unreadable, or non-transparent PNG")
            rows = _alpha_rows(source)
            core = _alpha_bounds(rows, CORE_ALPHA_MIN)
            if core.isEmpty() or not any(0 in row for row in rows):
                raise ValueError("bed must have visible pixels and transparent background")
            # Less than one logical pixel of surrounding source shadow is enough
            # for a soft contact edge; remote near-zero alpha must not lift the bed.
            padding = math.ceil(core.width()*SOURCE_PADDING_FRACTION)
            bounds = core.adjusted(-padding, -padding, padding, padding).intersected(source.rect())
            # A low rim remains on the same desktop floor, below the cat window.
            # Width/height are fitted together: no stretching or sprite movement.
            scale = min((BED_WIDTH-6)/bounds.width(), (BED_HEIGHT-2)/bounds.height())
            width, height = bounds.width()*scale, bounds.height()*scale
            target = QRectF((BED_WIDTH-width)/2, BED_HEIGHT-1-height, width, height)
            canvas = QImage(BED_WIDTH*PIXEL_RATIO, BED_HEIGHT*PIXEL_RATIO,
                            QImage.Format.Format_ARGB32_Premultiplied)
            canvas.fill(Qt.GlobalColor.transparent)
            canvas.setDevicePixelRatio(PIXEL_RATIO)
            painter = QPainter(canvas)
            painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
            painter.drawImage(target, source, QRectF(bounds))
            painter.end()
            region = _input_region(canvas)
            if region.isEmpty():
                raise ValueError("bed artwork has no visible pixels at desktop size")
            self.source_rect, self.target_rect = bounds, target
            self.core_rect, self.source_padding = core, padding
            self._region, self._frame = region, QPixmap.fromImage(canvas)
        except (OSError, ValueError, TypeError, OverflowError) as error:
            self.load_error = f"Resident bed artwork unavailable: {error}"
        return self._frame

    def input_region(self) -> QRegion:
        if self.frame() is not None:
            return QRegion(self._region)
        return QRegion(QRect(3, 2, BED_WIDTH-6, BED_HEIGHT-3), QRegion.RegionType.Ellipse)

    def paint(self, painter, rect) -> bool:
        rect = QRectF(rect)
        if rect.isEmpty():
            return False
        frame = self.frame()
        if frame is None:
            self._paint_fallback(painter, rect)
            return False
        painter.save()
        painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
        scale = min(rect.width()/BED_WIDTH, rect.height()/BED_HEIGHT)
        target = QRectF(rect.center().x()-BED_WIDTH*scale/2,
                        rect.bottom()-BED_HEIGHT*scale, BED_WIDTH*scale, BED_HEIGHT*scale)
        painter.drawPixmap(target, frame, QRectF(frame.rect()))
        painter.restore()
        return True

    @staticmethod
    def _paint_fallback(p, rect):
        """Retain the previous bed if its optional artwork cannot be decoded."""
        p.save()
        p.translate(rect.topLeft())
        p.scale(rect.width()/BED_WIDTH, rect.height()/BED_HEIGHT)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor(29, 37, 28, 38))
        p.drawEllipse(QRectF(4, 10, BED_WIDTH-8, 33))
        rim = QLinearGradient(0, 3, 0, 41)
        rim.setColorAt(0, QColor("#d9d0be")); rim.setColorAt(.48, QColor("#b1a38e")); rim.setColorAt(1, QColor("#8e806d"))
        p.setBrush(rim); p.drawEllipse(QRectF(3, 2, BED_WIDTH-6, 39))
        cushion = QRadialGradient(QPointF(BED_WIDTH/2, 20), BED_WIDTH*.47)
        cushion.setColorAt(0, QColor("#b0b49c")); cushion.setColorAt(.75, QColor("#90987e")); cushion.setColorAt(1, QColor("#747d65"))
        p.setBrush(cushion); p.drawEllipse(QRectF(15, 7, BED_WIDTH-30, 25))
        p.setPen(QPen(QColor(245, 239, 224, 110), .8))
        p.drawArc(QRectF(7, 5, BED_WIDTH-14, 32), 185*16, 170*16)
        p.setPen(QPen(QColor(115, 103, 85, 100), .7))
        for x in range(18, BED_WIDTH-16, 9):
            p.drawLine(QPointF(x, 34), QPointF(x+2, 37))
        p.restore()
