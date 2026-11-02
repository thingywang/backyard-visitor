"""A small wooden garden door, drawn at its native 30px logical size.

Code-native detail stays crisp on Retina displays without extra artwork or timers.
"""
from PyQt6.QtCore import QPointF, QRectF, Qt
from PyQt6.QtGui import QColor, QLinearGradient, QPainter, QPainterPath, QPen


def _arch(x, y, w, h, rise):
    path = QPainterPath(QPointF(x, y+h))
    path.lineTo(x, y+rise)
    path.cubicTo(x, y-rise*.12, x+w, y-rise*.12, x+w, y+rise)
    path.lineTo(x+w, y+h)
    path.closeSubpath()
    return path


def paint_gate(p, rect, opened=False):
    rect = QRectF(rect)
    if rect.isEmpty():
        return
    p.save()
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    p.translate(rect.x(), rect.y())
    p.scale(rect.width()/30, rect.height()/30)
    p.setPen(Qt.PenStyle.NoPen)
    p.setBrush(QColor(23, 25, 21, 48))
    p.drawEllipse(QRectF(3, 26.6, 24, 2.8))
    frame = _arch(3.4, 1.3, 23.2, 26.5, 5.4)
    timber = QLinearGradient(3, 2, 26, 28)
    timber.setColorAt(0, QColor('#e3cfac'))
    timber.setColorAt(.28, QColor('#bda17a'))
    timber.setColorAt(.65, QColor('#a68b65'))
    timber.setColorAt(1, QColor('#705c43'))
    p.setBrush(timber); p.setPen(QPen(QColor('#594d3c'), .8))
    p.drawPath(frame)
    p.setBrush(Qt.BrushStyle.NoBrush)
    p.setPen(QPen(QColor(250, 237, 204, 155), .55))
    p.drawPath(_arch(4.4, 2.2, 21.2, 24.5, 4.8))

    opening = _arch(6.4, 4.5, 17.2, 22.1, 4)
    shade = QLinearGradient(7, 5, 23, 26)
    shade.setColorAt(0, QColor('#1f3028'))
    shade.setColorAt(.58, QColor('#3b5040') if opened else QColor('#4a3827'))
    shade.setColorAt(1, QColor('#718069') if opened else QColor('#6d5035'))
    p.setPen(QPen(QColor('#544735'), .7)); p.setBrush(shade)
    p.drawPath(opening)
    if opened:
        # A quiet recess and threshold make the entrance visibly open.
        p.save(); p.setClipPath(opening)
        p.setPen(QPen(QColor(174, 188, 142, 90), .5))
        p.drawLine(QPointF(13, 26), QPointF(20, 21))
        p.drawLine(QPointF(21, 26), QPointF(20, 21))
        p.restore()

    p.save()
    # Compress only the door leaf, leaving a generous visible passage.
    if opened:
        p.translate(6.6, 0)
        p.shear(0, -.14)
        p.scale(.52, 1)
        p.translate(-6.6, 1)
    leaf = _arch(6.7, 4.9, 16.6, 21.1, 3.8)
    wood = QLinearGradient(7, 5, 23, 25)
    wood.setColorAt(0, QColor('#c0a077'))
    wood.setColorAt(.38, QColor('#a68258'))
    wood.setColorAt(.7, QColor('#b18d62'))
    wood.setColorAt(1, QColor('#806142'))
    p.setBrush(wood); p.setPen(QPen(QColor('#634c34'), .6)); p.drawPath(leaf)
    p.save(); p.setClipPath(leaf)
    for x in (10.8, 15., 19.2):
        p.setPen(QPen(QColor(74, 52, 32, 140), .55))
        p.drawLine(QPointF(x, 4), QPointF(x, 27))
        p.setPen(QPen(QColor(231, 202, 154, 100), .45))
        p.drawLine(QPointF(x+.6, 4), QPointF(x+.6, 27))
    p.setBrush(Qt.BrushStyle.NoBrush)
    p.setPen(QPen(QColor(87, 60, 35, 90), .35))
    for x, y, h in ((8.4, 12, 8), (13.1, 8, 10), (17.3, 15, 9), (21.3, 10, 6)):
        grain = QPainterPath(QPointF(x, y))
        grain.cubicTo(x-1.1, y+h*.3, x+.7, y+h*.7, x, y+h)
        p.drawPath(grain)
    p.drawEllipse(QRectF(12, 18, 1.5, 2.8))
    p.restore()
    # Two forged hinges and a brass ring latch, readable at actual size.
    p.setPen(QPen(QColor('#4c4b40'), .55)); p.setBrush(QColor('#666354'))
    for y in (11., 22.):
        p.drawRoundedRect(QRectF(6.8, y, 5.2, 1.6), .45, .45)
        p.setPen(QPen(QColor('#b2a68c'), .35))
        p.drawPoint(QPointF(7.9, y+.6)); p.drawPoint(QPointF(10.5, y+.6))
        p.setPen(QPen(QColor('#4c4b40'), .55))
    p.setPen(QPen(QColor('#5e4d32'), .6)); p.setBrush(QColor('#ceb27b'))
    p.drawEllipse(QRectF(19.1, 15.2, 1.5, 1.5))
    p.setBrush(Qt.BrushStyle.NoBrush); p.setPen(QPen(QColor('#ead5a4'), .85))
    p.drawEllipse(QRectF(18.9, 16, 2, 2.7))
    p.restore()
    p.setPen(QPen(QColor('#e3ceb0'), .7))
    p.drawLine(QPointF(4.2, 27), QPointF(25.7, 27))
    p.restore()
