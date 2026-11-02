"""Small model-clock affection feedback, painted inside the existing cat window.

This is deliberately not a particle system: three reusable paths need no new
window, timer, random state, wall clock, or persistent data.
"""
from __future__ import annotations

import math

from PyQt6.QtGui import QColor, QPainterPath, QPen


def _heart_path():
    path = QPainterPath()
    path.moveTo(0, .95)
    path.cubicTo(-.55, .48, -1, .10, -1, -.38)
    path.cubicTo(-1, -1.03, -.30, -1.23, 0, -.63)
    path.cubicTo(.30, -1.23, 1, -1.03, 1, -.38)
    path.cubicTo(1, .10, .55, .48, 0, .95)
    path.closeSubpath()
    return path


_HEART = _heart_path()


def paint_purr_hearts(painter, elapsed, facing=1, opacity=1.0, *, text_on_left=False):
    """Draw three quiet, staggered hearts above the body; respect caller clips.

    ``elapsed`` is Visitor.state_age, so pausing/repainting cannot advance the
    effect. All pixels remain in the top 48 logical pixels of the cat canvas,
    clear of the sleeping body and opposite the text/recognition bubble.
    """
    if not math.isfinite(elapsed) or not math.isfinite(opacity) or opacity <= 0:
        return
    elapsed = max(0., elapsed)
    # Use the free corner opposite the text. Gift responses may occupy the
    # left instead; ordinary/recognition text stays right.
    direction = -1 if facing < 0 else 1
    painter.save()
    original_opacity = painter.opacity()
    painter.setPen(QPen(QColor('#ae6b75'), .36))
    painter.setBrush(QColor('#dfa0a8'))
    for index, (center, radius) in enumerate(((25., 3.2), (40., 4.0), (55., 2.8))):
        center = 144.-center if text_on_left else center
        age = (elapsed + index * .8) % 2.4
        fade = min(1., age / .24, (2.4-age) / .55)
        painter.setOpacity(original_opacity * min(1., opacity) * .82 * max(0., fade))
        painter.save()
        painter.translate(center + direction * 1.8 * math.sin(age*2.1 + index),
                          40. - age*11.)
        painter.rotate(direction * (index-1) * 9.)
        painter.scale(radius, radius)
        painter.drawPath(_HEART)
        painter.restore()
    painter.restore()
