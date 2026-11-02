"""Lazy small rear pose with one shared seat anchor for paint and input."""
from pathlib import Path
import hashlib
import json
import math
from PyQt6.QtCore import QPointF, QRect, QRectF, Qt
from PyQt6.QtGui import QBitmap, QColor, QImage, QPainter, QPixmap, QRegion, QTransform

from .coat_traits import CoatSpec

ASSETS = Path(__file__).with_name('assets')
WIDTH, HEIGHT, SEAT_Y, DPR = 144, 168, 112, 2
# The source haunch fringe still crosses row112; row113 is the narrow tail.
# Keep that fringe attached to the seat, and tuck four pixels of the moving
# root underneath it. Restrict the overlap to the actual tail column so a turn
# never rotates a strip of the haunches together with the tail.
BODY_END = SEAT_Y + 1
TAIL_START = BODY_END - 4
TAIL_ROOT_LEFT, TAIL_ROOT_WIDTH = 64, 20


def tail_slice_region():
    return (QRegion(QRect(0, BODY_END, WIDTH, HEIGHT-BODY_END))
            | QRegion(QRect(TAIL_ROOT_LEFT, TAIL_START, TAIL_ROOT_WIDTH, BODY_END-TAIL_START)))


def split_mask(mask):
    return (mask & QRegion(QRect(0, 0, WIDTH, BODY_END)),
            mask & tail_slice_region())


def source_frame():
    """Render the original unmodified cutout into the runtime canvas."""
    data = json.loads((ASSETS/'rear-tail.json').read_text())
    source = QImage(str(ASSETS/data['file']))
    if source.isNull(): raise RuntimeError('背坐垂尾素材读取失败')
    frame = QPixmap(WIDTH*DPR, HEIGHT*DPR); frame.fill(Qt.GlobalColor.transparent)
    painter = QPainter(frame); painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
    scale = data['render_scale']*DPR
    sx, sy = data['seat_anchor']; ax, ay = data['render_anchor']
    painter.drawImage(QRectF(ax*DPR-sx*scale, ay*DPR-sy*scale,
                            source.width()*scale, source.height()*scale), source)
    painter.end(); frame.setDevicePixelRatio(DPR)
    return frame


def compile_frame():
    data = json.loads((ASSETS/'rear-tail.json').read_text())
    frame = source_frame(); path = ASSETS/data['rendition']
    if not frame.save(str(path), 'PNG'): raise RuntimeError('背坐垂尾小贴图保存失败')
    data['rendition_sha256'] = hashlib.sha256(path.read_bytes()).hexdigest()
    (ASSETS/'rear-tail.json').write_text(json.dumps(data, ensure_ascii=False, indent=2)+'\n')
    return frame


class RearArt:
    def __init__(self):
        data = json.loads((ASSETS/'rear-tail.json').read_text())
        path = ASSETS/data['rendition']
        valid = path.is_file() and hashlib.sha256(path.read_bytes()).hexdigest() == data.get('rendition_sha256')
        frame = QPixmap(str(path)) if valid else source_frame()
        if frame.isNull() or (frame.width(), frame.height()) != (WIDTH*DPR, HEIGHT*DPR):
            raise RuntimeError('背坐垂尾小贴图无效')
        frame.setDevicePixelRatio(DPR)
        self.frames = {'ginger': frame}
        logical = frame.toImage().scaled(WIDTH, HEIGHT, Qt.AspectRatioMode.IgnoreAspectRatio,
                                        Qt.TransformationMode.SmoothTransformation)
        mask = QRegion(QBitmap.fromImage(logical.createAlphaMask()))
        self.body_mask, self.tail_mask = split_mask(mask)
        self._coat_masks = {'ginger': (self.body_mask, self.tail_mask)}
        self._transition_canvas = None

    def frame(self, coat):
        if coat in self.frames: return self.frames[coat]
        original = self.frames['ginger']
        if isinstance(coat, CoatSpec):
            from .procedural_coat import procedural_frame
            self.frames[coat] = procedural_frame(original, coat)
            return self.frames[coat]
        if coat == 'black':
            from .black_coat import black_frame
            self.frames[coat] = black_frame(original)
            return self.frames[coat]
        source = original.toImage()
        if coat == 'gray':
            source.setDevicePixelRatio(1)
            alpha = source.convertToFormat(QImage.Format.Format_Alpha8)
            source = source.convertToFormat(QImage.Format.Format_Grayscale8).convertToFormat(QImage.Format.Format_ARGB32_Premultiplied)
            source.setAlphaChannel(alpha)
        elif coat == 'tuxedo':
            p = QPainter(source); p.setCompositionMode(QPainter.CompositionMode.CompositionMode_SourceAtop)
            p.fillRect(source.rect(), QColor(33, 31, 29, 94)); p.end()
        else: return original
        source.setDevicePixelRatio(DPR)
        self.frames[coat] = QPixmap.fromImage(source)
        return self.frames[coat]

    @staticmethod
    def tail_transform(age, amount):
        # Tuck beside the seat during the turn, then a restrained resting sway.
        amount = max(0., min(1., amount))
        eased = amount*amount*(3-2*amount)
        angle = -80*(1-eased)+math.sin(age*.85)*2.5*eased
        transform = QTransform(); transform.translate(WIDTH/2, SEAT_Y)
        transform.rotate(angle); transform.translate(-WIDTH/2, -SEAT_Y)
        return transform

    def input_region(self, age, amount, coat='ginger'):
        # The window mask also clips painting, so authored black fur must use
        # the same silhouette as its displayed frame, including the moving tail.
        key = 'black' if coat == 'black' or isinstance(coat, CoatSpec) else 'ginger'
        if key not in self._coat_masks:
            logical = self.frame(key).toImage().scaled(
                WIDTH, HEIGHT, Qt.AspectRatioMode.IgnoreAspectRatio,
                Qt.TransformationMode.SmoothTransformation)
            mask = QRegion(QBitmap.fromImage(logical.createAlphaMask()))
            self._coat_masks[key] = split_mask(mask)
        body, tail = self._coat_masks[key]
        return body | self.tail_transform(age, amount).map(tail)

    def paint(self, painter, age, coat, amount):
        if amount <= 0: return
        amount = min(1., amount)
        frame = self.frame(coat)
        if amount < 1:
            # Fade the assembled pose once. Fading each overlapping piece would
            # darken the root during the turn even though the seam is covered.
            if self._transition_canvas is None:
                self._transition_canvas = QPixmap(WIDTH*DPR, HEIGHT*DPR)
                self._transition_canvas.setDevicePixelRatio(DPR)
            self._transition_canvas.fill(Qt.GlobalColor.transparent)
            assembled = QPainter(self._transition_canvas)
            assembled.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
            self._paint_pose(assembled, frame, age, amount)
            assembled.end()
            painter.save(); painter.setOpacity(painter.opacity()*amount)
            painter.drawPixmap(QPointF(0, 0), self._transition_canvas)
            painter.restore()
        else:
            self._paint_pose(painter, frame, age, amount)

    def _paint_pose(self, painter, frame, age, amount):
        painter.save()
        painter.save()
        painter.setTransform(self.tail_transform(age, amount), True)
        painter.setClipRegion(tail_slice_region(), Qt.ClipOperation.IntersectClip)
        painter.drawPixmap(QRectF(0, TAIL_START, WIDTH, HEIGHT-TAIL_START), frame,
                           QRectF(0, TAIL_START*DPR, WIDTH*DPR, (HEIGHT-TAIL_START)*DPR))
        painter.restore()
        # Cover the overlapped root with the fixed body, preserving its fur and
        # hiding the rotating crop edge at either resting-sway endpoint.
        painter.drawPixmap(QRectF(0, 0, WIDTH, BODY_END), frame,
                           QRectF(0, 0, WIDTH*DPR, BODY_END*DPR))
        painter.restore()
