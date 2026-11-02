"""Small directional toy feather, drawn independently of the gift sprites."""
from __future__ import annotations

import math

from PyQt6.QtCore import QPointF, Qt
from PyQt6.QtGui import (QColor, QCursor, QLinearGradient, QPainter,
                         QPainterPath, QPen, QPixmap, QTransform)

LOGICAL_SIZE = 28
HOTSPOT = (14, 14)


def normalize_cursor_scale(value) -> float:
    """Keep only finite, bounded artwork scales at the native cursor boundary."""
    if type(value) not in (int, float):
        return 1.0
    try:
        value = float(value)
    except (OverflowError, ValueError):
        return 1.0
    return min(2.5, max(.6, value)) if math.isfinite(value) else 1.0


def feather_cursor(pixel_ratio: float = 1.0, *, facing: int = 1,
                   artwork_scale: float = 1.0) -> QCursor:
    """Keep a central hotspot fixed while the feather turns with horizontal motion.

    Each canvas builds just two cursors. No gift atlas, full-screen raster, global
    cursor override or per-frame pixmap allocation is needed.
    """
    ratio = max(1.0, min(3.0, float(pixel_ratio)))
    scale = normalize_cursor_scale(artwork_scale)
    pixmap = QPixmap(math.ceil(LOGICAL_SIZE * scale * ratio),
                     math.ceil(LOGICAL_SIZE * scale * ratio))
    pixmap.setDevicePixelRatio(ratio)
    pixmap.fill(Qt.GlobalColor.transparent)
    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    painter.scale(scale, scale)
    feather = QPainterPath(QPointF(7, 22))
    feather.cubicTo(5.8, 18, 7.6, 14, 9.7, 11.5)
    feather.lineTo(10.8, 12.1)
    feather.lineTo(10.6, 10.5)
    feather.cubicTo(14.5, 6.8, 19.5, 4.8, 25, 4)
    feather.cubicTo(24, 9.5, 21.9, 13.9, 19, 17.2)
    feather.lineTo(17.8, 17.3)
    feather.lineTo(18.1, 18.2)
    feather.cubicTo(14.3, 21.5, 10.5, 23, 7, 22)
    # Muted sage upper vane and warm cream lower vane distinguish the toy from
    # the brown/grey realistic gift. A pale rim reads against dark wallpapers.
    shading = QLinearGradient(9, 7, 22, 24)
    shading.setColorAt(0, QColor("#a8c3b4"))
    shading.setColorAt(.52, QColor("#73988c"))
    shading.setColorAt(1, QColor("#e4d9ba"))
    painter.setPen(QPen(QColor("#eee6d2"), 1.6))
    painter.setBrush(shading)
    painter.drawPath(feather)
    painter.setPen(QPen(QColor("#536d62"), .7))
    painter.drawPath(feather)
    shaft = QPainterPath(QPointF(3, 25))
    shaft.cubicTo(9, 20, 15, 11, 24, 5)
    painter.setPen(QPen(QColor("#f3e9cc"), 1.05, Qt.PenStyle.SolidLine,
                        Qt.PenCapStyle.RoundCap))
    painter.drawPath(shaft)
    # Fine, closely spaced barbs and two small splits read as a feather rather
    # than a leaf, even at its small native cursor size.
    painter.save()
    painter.setClipPath(feather)
    for i in range(11):
        t = .1 + i * .073
        x, y = 7 + 18*t, 22 - 18*t
        length = 2.0 + 4.0 * math.sin(math.pi*t)
        painter.setPen(QPen(QColor(229, 233, 211, 190), .42,
                            Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
        painter.drawLine(QPointF(x, y), QPointF(x-.55*length, y-length))
        painter.setPen(QPen(QColor(73, 112, 96, 145), .4,
                            Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
        painter.drawLine(QPointF(x, y), QPointF(x+length, y+.5*length))
    painter.restore()
    painter.end()
    if facing < 0:
        pixmap = pixmap.transformed(QTransform().scale(-1, 1))
    return QCursor(pixmap, *(round(value*scale) for value in HOTSPOT))
