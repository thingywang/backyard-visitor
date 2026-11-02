"""One shared fight cloud, two real coat-specific tails, and brief vector marks.

Callers pass the fight's state_age. No widget, timer, model state, or identity
is owned here. The stable mask encloses the whole restrained motion envelope.
"""
from __future__ import annotations

from dataclasses import dataclass
import math

from PyQt6.QtCore import QPointF, QRectF, Qt
from PyQt6.QtGui import QColor, QImage, QPainter, QPainterPath, QPainterPathStroker, QPen, QPixmap, QTransform
from .coat_traits import CoatSpec


@dataclass(frozen=True)
class TailSlot:
    index: int
    root: QPointF
    angle: float
    transform: QTransform
    target: QRectF
    path: QPainterPath
    bounds: QRectF
    exposed_path: QPainterPath


def _age(value):
    return max(0., value) if type(value) in (int, float) and math.isfinite(value) else 0.


def cloud_path(rect):
    """A single connected cloud in the lower part of the full overlay rect."""
    rect = QRectF(rect)
    if rect.isEmpty():
        return QPainterPath()
    x, y, w, h = rect.x(), rect.y(), rect.width(), rect.height()
    margin = min(18., w*.1)
    path = QPainterPath()
    path.setFillRule(Qt.FillRule.WindingFill)
    # Keep the connecting body lower so it does not flatten the upper lobes.
    # Unequal radii/heights give the shared cloud a loose, billowing outline.
    path.addEllipse(QRectF(x+margin, y+h*.49, w-2*margin, h*.46))
    side = min(25., h*.19, (w-2*margin)/3)
    unit = min(32., h*.23, (w-2*margin)*.18)
    span = max(0., w-2*margin-2*side)
    lobes = ((0., .65, side), (.19, .56, unit*.90),
             (.40, .52, unit*1.08), (.63, .60, unit*.85),
             (.82, .54, unit*.98), (1., .66, side))
    for fraction, level, radius in lobes:
        cx = x+margin+side+span*fraction
        cy = y+h*level
        path.addEllipse(QRectF(cx-radius, cy-radius, radius*2, radius*2))
        path.addEllipse(QRectF(cx-radius*.88, y+h*.77-radius*.60,
                              radius*1.76, radius*1.30))
    return path.simplified()


def _slot(rect, index, angle):
    rect = QRectF(rect)
    height = min(67., rect.height()*.48)
    width = height*206/360
    root = QPointF(rect.left()+rect.width()*(.28 if index == 0 else .72),
                   rect.top()+rect.height()*.64)
    # CatArt.tail's original crop is 206x360 with its root at (106,360).
    target = QRectF(-width*106/206, -height, width, height)
    transform = QTransform()
    transform.translate(root.x(), root.y())
    transform.rotate(angle)
    if index == 1:
        transform.scale(-1, 1)
    local = QPainterPath(); local.addRect(target)
    path = transform.map(local)
    return TailSlot(index, root, angle, transform, target, path, path.boundingRect(),
                    path.subtracted(cloud_path(rect)))


def tail_slots(rect, state_age):
    """Two ordered tail slots; exposed_path uses logical, unscaled coordinates.

    Slot zero belongs to coats[0], slot one to coats[1]. A Retina grab must
    sample pixel centres in logical coordinates (physical pixel / image DPR).
    """
    age = _age(state_age)
    return (_slot(rect, 0, -17 + 9*math.sin(age*5.2)),
            _slot(rect, 1, 18 + 10*math.sin(age*4.3+1.1)))


def _star(center, radius):
    path = QPainterPath()
    for i in range(8):
        angle = -math.pi/2+i*math.pi/4
        r = radius if i % 2 == 0 else radius*.28
        point = QPointF(center.x()+math.cos(angle)*r, center.y()+math.sin(angle)*r)
        if i == 0: path.moveTo(point)
        else: path.lineTo(point)
    path.closeSubpath()
    return path


def _scratch(rect, side):
    rect = QRectF(rect)
    x = rect.left()+rect.width()*(.43 if side == 0 else .60)
    y = rect.top()+rect.height()*(.32 if side == 0 else .39)
    path = QPainterPath()
    for i in range(3):
        start = QPointF(x+i*4, y+i*.6)
        path.moveTo(start)
        path.cubicTo(start+QPointF(-2, 5), start+QPointF(-4, 10), start+QPointF(-7, 14))
    return path


def _effect_paths(rect):
    rect = QRectF(rect)
    return (_star(QPointF(rect.left()+rect.width()*.18, rect.top()+rect.height()*.28), 5.5),
            _star(QPointF(rect.left()+rect.width()*.82, rect.top()+rect.height()*.25), 6.5),
            _scratch(rect, 0), _scratch(rect, 1))


def _stroke(path, width):
    stroker = QPainterPathStroker()
    stroker.setWidth(width)
    stroker.setCapStyle(Qt.PenCapStyle.RoundCap)
    stroker.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
    return stroker.createStroke(path)


def scene_mask(rect):
    """Stable cloud/tail/effect envelope, with transparent desktop corners."""
    rect = QRectF(rect)
    if rect.isEmpty():
        return QPainterPath()
    cloud = cloud_path(rect)
    scene = cloud.united(_stroke(cloud, 3))
    # Union every degree plus a small raster allowance. Actual angles remain
    # within these ranges; no per-frame mask update or opaque rectangle needed.
    for index, angles in ((0, range(-26, -7)), (1, range(8, 29))):
        envelope = QPainterPath()
        for angle in angles:
            envelope = envelope.united(_slot(rect, index, angle).path)
        scene = scene.united(envelope).united(_stroke(envelope, 3))
    for path in _effect_paths(rect):
        scene = scene.united(path).united(_stroke(path, 4))
    boundary = QPainterPath(); boundary.addRect(rect)
    return scene.intersected(boundary).simplified()


def _pulse(age, period, offset, duration):
    phase = (age+offset) % period
    return math.sin(math.pi*phase/duration) if phase < duration else 0.


class FightArt:
    """Small tail variant cache; each input coat keeps its own real pixels."""
    cloud_path = staticmethod(cloud_path)
    scene_mask = staticmethod(scene_mask)
    tail_slots = staticmethod(tail_slots)

    def __init__(self, tail: QPixmap):
        self._source = QPixmap(tail) if tail is not None else QPixmap()
        self._variants = {}
        self.load_error = None if not self._source.isNull() else "打架尾巴素材不可用"

    def tail_frame(self, coat):
        coat = coat if isinstance(coat, CoatSpec) or coat in {"ginger", "gray", "tuxedo", "black"} else "ginger"
        if coat in self._variants:
            return self._variants[coat]
        if self._source.isNull():
            return None
        frame = self._source
        if isinstance(coat, CoatSpec):
            from .procedural_coat import procedural_frame
            frame = procedural_frame(frame, coat)
        elif coat == "black":
            from . import black_coat
            frame = black_coat.black_frame(frame)
            if black_coat.load_error:
                self.load_error = black_coat.load_error
        elif coat == "gray":
            image = frame.toImage(); image.setDevicePixelRatio(1)
            alpha = image.convertToFormat(QImage.Format.Format_Alpha8)
            image = image.convertToFormat(QImage.Format.Format_Grayscale8).convertToFormat(QImage.Format.Format_ARGB32_Premultiplied)
            image.setAlphaChannel(alpha); image.setDevicePixelRatio(frame.devicePixelRatio())
            frame = QPixmap.fromImage(image)
        elif coat == "tuxedo":
            image = frame.toImage()
            painter = QPainter(image)
            painter.setCompositionMode(QPainter.CompositionMode.CompositionMode_SourceAtop)
            painter.fillRect(image.rect(), QColor(33, 31, 29, 94))
            painter.end()
            frame = QPixmap.fromImage(image)
        self._variants[coat] = frame
        return frame

    def paint(self, painter, rect, coats, state_age):
        """Paint exactly two tails, one cloud, and up to two brief vector marks.

        Returned metrics contain public coats/geometry only. They support
        diagnostics but do not replace observing actual product-rendered pixels.
        """
        if len(coats) != 2:
            raise ValueError("共享打架烟团必须接收两只猫的毛色")
        rect, age = QRectF(rect), _age(state_age)
        slots = tail_slots(rect, age)
        metrics = {"cloud_count": 1, "tail_count": 0, "tails": [], "active_marks": []}
        painter.save()
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
        for slot, coat in zip(slots, coats):
            frame = self.tail_frame(coat)
            painted = frame is not None and not frame.isNull()
            if painted:
                painter.save(); painter.setTransform(slot.transform, True)
                painter.drawPixmap(slot.target, frame, QRectF(frame.rect()))
                painter.restore(); metrics["tail_count"] += 1
            metrics["tails"].append({"slot": slot.index, "coat": coat, "painted": painted,
                                      "angle": slot.angle, "root": [slot.root.x(), slot.root.y()],
                                      "bounds": [slot.bounds.x(), slot.bounds.y(), slot.bounds.width(), slot.bounds.height()]})
        # This is the only cloud silhouette. Painting it after both tails hides
        # both roots naturally, rather than drawing a small cloud per cat.
        cloud = cloud_path(rect)
        painter.setPen(QPen(QColor("#969385"), 1))
        painter.setBrush(QColor("#e5e3d7")); painter.drawPath(cloud)
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.setPen(QPen(QColor("#a5a18f"), 1.15))
        for fraction in (.32, .55, .73):
            cx = rect.left()+rect.width()*fraction
            cy = rect.top()+rect.height()*.63+math.sin(age*4+fraction)*2
            painter.drawArc(QRectF(cx-13, cy-10, 26, 22), 20*16, 130*16)
        paths = _effect_paths(rect)
        timings = ((1.35, .08, .28), (1.50, .73, .26), (1.10, .32, .22), (1.20, .83, .24))
        # Limit simultaneous marks by giving each side its own staggered event.
        for index, (path, timing) in enumerate(zip(paths, timings)):
            opacity = _pulse(age, *timing)
            if opacity <= .02 or len(metrics["active_marks"]) >= 2:
                continue
            painter.save(); painter.setOpacity(painter.opacity()*opacity)
            if index < 2:
                painter.setPen(QPen(QColor("#8c8065"), .9)); painter.setBrush(QColor("#d7c58d"))
            else:
                painter.setPen(QPen(QColor("#8a806c"), 1.55, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
                painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.drawPath(path); painter.restore()
            metrics["active_marks"].append("star" if index < 2 else "scratch")
        painter.restore()
        metrics["load_error"] = self.load_error
        return metrics
