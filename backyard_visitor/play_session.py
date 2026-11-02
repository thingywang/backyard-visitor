"""An explicit, bounded teaser game using only a window-local cursor.

The transparent canvas intentionally receives clicks on the selected screen
while play is active. It never overrides the global cursor or warps its position.
The controller retains the session and stops it on its own lifecycle boundaries.
"""
from __future__ import annotations

import math
import time
from typing import Callable

from PyQt6.QtCore import QEvent, QObject, QPoint, QRect, QRectF, Qt, QTimer, pyqtSignal
from PyQt6.QtGui import QColor, QCursor, QFont, QPainter, QPen, QSurface, QSurfaceFormat, QWindow
from PyQt6.QtWidgets import QApplication, QWidget

from .desktop import configure_cocoa_input_surface, configure_cocoa_spaces
from .teaser_cursor import normalize_cursor_scale

DURATION_SECONDS = 15.0
TICK_MS = round(1000 / 24)


def wand_cursor(pixel_ratio: float = 1.0, *, facing: int = 1,
                artwork_scale: float = 1.0) -> QCursor:
    """Small toy feather, separate from the collectible gift artwork."""
    from .teaser_cursor import feather_cursor
    return feather_cursor(pixel_ratio, facing=facing, artwork_scale=artwork_scale)


def _hint_geometry(geometry: QRect, scale: float) -> QRect:
    """Size the small hint while keeping its native bounds on the real screen."""
    width = min(round(344*scale), max(1, geometry.width()-round(24*scale)))
    height = min(round(56*scale), max(1, geometry.height()))
    x = geometry.x()+(geometry.width()-width)//2
    y = geometry.y()+min(round(16*scale), max(0, geometry.height()-height))
    return QRect(x, y, width, height)


def _paint_hint(painter, rect: QRectF, seconds: int):
    """Paint in base artwork units; callers own exactly one display scaling."""
    hint = rect.adjusted(.5, .5, -.5, -.5)
    painter.setPen(QPen(QColor(195, 188, 170, 210), .8))
    painter.setBrush(QColor(249, 246, 235, 238))
    painter.drawRoundedRect(hint, 12, 12)
    painter.setPen(QColor("#655d4d"))
    painter.setFont(QFont("PingFang SC", 13))
    painter.drawText(hint.adjusted(8, 6, -8, -22), Qt.AlignmentFlag.AlignCenter,
                     f"逗猫中 · 右键结束 · {seconds} 秒")
    painter.setPen(QColor("#8d8576"))
    painter.setFont(QFont("PingFang SC", 10))
    painter.drawText(hint.adjusted(8, 29, -8, -5), Qt.AlignmentFlag.AlignCenter,
                     "当前屏幕的点击暂用于逗猫")


class _WidgetTeaserCanvas(QWidget):
    def __init__(self, session, geometry: QRect, pixel_ratio: float, *, artwork_scale=1.0):
        flags = (Qt.WindowType.Tool | Qt.WindowType.FramelessWindowHint |
                 Qt.WindowType.NoDropShadowWindowHint | Qt.WindowType.WindowStaysOnTopHint |
                 Qt.WindowType.WindowDoesNotAcceptFocus)
        super().__init__(None, flags)
        self.session = session
        self._artwork_scale = normalize_cursor_scale(artwork_scale)
        self.seconds = 15
        self._inside = True
        self._wands = {direction: wand_cursor(pixel_ratio, facing=direction, artwork_scale=self._artwork_scale)
                       for direction in (-1, 1)}
        self._facing = 1
        self._wand = self._wands[self._facing]
        self.setWindowTitle("后院访客 · 逗猫中")
        self.setAccessibleName("逗猫中；当前屏幕点击暂用于逗猫，右键结束")
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating)
        self.setAttribute(Qt.WidgetAttribute.WA_MacAlwaysShowToolWindow)
        self.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.setMouseTracking(True)
        self.setGeometry(geometry)
        self.setCursor(self._wand)

    def set_remaining(self, remaining):
        seconds = max(0, math.ceil(remaining))
        if self.seconds != seconds:
            self.seconds = seconds
            self.update()

    def set_cursor_direction(self, direction):
        facing = -1 if direction < 0 else 1
        if facing == self._facing:
            return
        self._facing = facing
        self._wand = self._wands[facing]
        if self._inside:
            self.setCursor(self._wand)

    def set_cursor_inside(self, inside):
        if inside == self._inside:
            return
        self._inside = inside
        if inside:
            self.setCursor(self._wand)
        else:
            self.unsetCursor()

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        # Near-transparent fill keeps the whole interactive canvas intentional;
        # the cat and other content underneath remain visible.
        painter.fillRect(self.rect(), QColor(0, 0, 0, 1))
        hint = _hint_geometry(self.rect(), self._artwork_scale)
        painter.translate(hint.x(), hint.y())
        painter.scale(self._artwork_scale, self._artwork_scale)
        _paint_hint(painter, QRectF(0, 0, hint.width()/self._artwork_scale,
                                  hint.height()/self._artwork_scale), self.seconds)

    def mousePressEvent(self, event):
        event.accept()
        if event.button() == Qt.MouseButton.RightButton:
            self.session.stop("cancelled")

    def mouseReleaseEvent(self, event):
        event.accept()

    def mouseMoveEvent(self, event):
        event.accept()

    def wheelEvent(self, event):
        event.accept()

    def enterEvent(self, event):
        self.set_cursor_inside(True)
        super().enterEvent(event)

    def leaveEvent(self, event):
        self.set_cursor_inside(False)
        super().leaveEvent(event)

    def closeEvent(self, event):
        event.accept()
        self.session.stop("cancelled")


def _prepare_cocoa_space_policy(window):
    """Configure our native window before exposing it in the active Space."""
    if QApplication.platformName() == "cocoa":
        window._native_spaces = configure_cocoa_spaces(int(window.winId()), True)


def _prepare_cocoa_input_surface(window):
    """Apply the native nonzero-alpha surface only on the Cocoa plugin."""
    if QApplication.platformName() == "cocoa":
        window._native_input = configure_cocoa_input_surface(int(window.winId()))



class _TeaserCanvas(QWindow):
    """Transparent native input surface; deliberately has no QBackingStore.

    Cocoa adds a near-clear native layer background for input. No full-screen
    QPainter/QImage/QPixmap is allocated; the small text hint is a separate
    QWidget. This object has QWindow APIs, not QWidget.grab/rect.
    """
    def __init__(self, session, geometry: QRect, pixel_ratio: float, *, artwork_scale=1.0):
        super().__init__()
        self.session = session
        self._artwork_scale = normalize_cursor_scale(artwork_scale)
        self._inside = True
        self._wands = {direction: wand_cursor(pixel_ratio, facing=direction, artwork_scale=self._artwork_scale)
                       for direction in (-1, 1)}
        self._facing = 1
        self._wand = self._wands[self._facing]
        # Qt 6.9 Cocoa reads the same private properties QWidget sets for
        # WA_ShowWithoutActivating / WA_MacAlwaysShowToolWindow. Set them before
        # native creation; WindowDoesNotAcceptFocus alone is not a show policy.
        self.setProperty("_q_showWithoutActivating", True)
        self.setProperty("_q_macAlwaysShowToolWindow", True)
        self.setFlags(Qt.WindowType.Tool | Qt.WindowType.FramelessWindowHint |
                      Qt.WindowType.NoDropShadowWindowHint | Qt.WindowType.WindowStaysOnTopHint |
                      Qt.WindowType.WindowDoesNotAcceptFocus)
        format = QSurfaceFormat()
        format.setAlphaBufferSize(8)
        self.setFormat(format)
        self.setSurfaceType(QSurface.SurfaceType.RasterSurface)
        self.setTitle("后院访客 · 逗猫中 · 右键结束")
        self.setGeometry(geometry)
        self.setCursor(self._wand)

    def set_cursor_direction(self, direction):
        facing = -1 if direction < 0 else 1
        if facing == self._facing:
            return
        self._facing = facing
        self._wand = self._wands[facing]
        if self._inside:
            self.setCursor(self._wand)

    def set_cursor_inside(self, inside):
        if inside == self._inside:
            return
        self._inside = inside
        if inside:
            self.setCursor(self._wand)
        else:
            self.unsetCursor()

    def mousePressEvent(self, event):
        event.accept()
        if event.button() == Qt.MouseButton.RightButton:
            self.session.stop("cancelled")

    def mouseReleaseEvent(self, event):
        event.accept()

    def mouseMoveEvent(self, event):
        event.accept()

    def wheelEvent(self, event):
        event.accept()

    def event(self, event):
        if event.type() == QEvent.Type.Enter and hasattr(self, "_inside"):
            self.set_cursor_inside(True)
        elif event.type() == QEvent.Type.Leave and hasattr(self, "_inside"):
            self.set_cursor_inside(False)
        return super().event(event)

    def closeEvent(self, event):
        event.accept()
        self.session.stop("cancelled")


class _TeaserHint(QWidget):
    """Small raster text window above the input surface; never intercepts input."""
    def __init__(self, geometry: QRect, *, artwork_scale=1.0):
        flags = (Qt.WindowType.Tool | Qt.WindowType.FramelessWindowHint |
                 Qt.WindowType.NoDropShadowWindowHint | Qt.WindowType.WindowStaysOnTopHint |
                 Qt.WindowType.WindowDoesNotAcceptFocus | Qt.WindowType.WindowTransparentForInput)
        super().__init__(None, flags)
        self._artwork_scale = normalize_cursor_scale(artwork_scale)
        self.seconds = 15
        self.setWindowTitle("后院访客 · 逗猫提示")
        self.setAccessibleName("逗猫中；当前屏幕点击暂用于逗猫，右键结束")
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating)
        self.setAttribute(Qt.WidgetAttribute.WA_MacAlwaysShowToolWindow)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        self.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        rect = _hint_geometry(geometry, self._artwork_scale)
        self.setFixedSize(rect.size())
        self.move(rect.topLeft())

    def set_remaining(self, remaining):
        seconds = max(0, math.ceil(remaining))
        if seconds != self.seconds:
            self.seconds = seconds
            self.update()

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.scale(self._artwork_scale, self._artwork_scale)
        _paint_hint(painter, QRectF(0, 0, self.width()/self._artwork_scale,
                                  self.height()/self._artwork_scale), self.seconds)


class TeaserSession(QObject):
    """One 15-second session. A completed session is not reused.

    Required controller members: engine, screen. The model exposes
    start_teaser(cat_id)->bool, stop_teaser(cat_id), cursor_moved(x,y,speed),
    and clear_cursor() for a toy outside the selected full display.
    Optional controller changed() refreshes the UI only at start/stop, never on
    each cursor sample (which would reset the engine animation clock).
    finished(reason) is emitted once; natural completion uses 'completed'.
    Clock/cursor injection exists for deterministic, permission-free tests.
    """
    finished = pyqtSignal(str)

    def __init__(self, c, cat_id: str, parent=None, *,
                 clock: Callable[[], float] | None = None,
                 wall_clock: Callable[[], float] | None = None,
                 cursor_position: Callable[[], QPoint] | None = None):
        super().__init__(parent)
        self.c, self.cat_id = c, cat_id
        self._clock = clock or time.monotonic
        self._wall_clock = wall_clock or time.time
        self._cursor_position = cursor_position or QCursor.pos
        self._active = self._ended = self._model_started = False
        self._started = self._wall_started = 0.0
        self._screen = None
        self._geometry = QRect()
        self._artwork_scale = 1.0
        self._previous_cursor_x = None
        self._canvas = None
        self._hint = None
        self._application = None
        self.error: str | None = None
        self.timer = QTimer(self)
        self.timer.setTimerType(Qt.TimerType.PreciseTimer)
        self.timer.setInterval(TICK_MS)
        self.timer.timeout.connect(self._pulse)

    @property
    def active(self):
        return self._active

    @property
    def remaining(self):
        if not self._active:
            return 0.0
        # Wall time detects actual sleep on systems where monotonic excludes it.
        # Clock jumps can end this short interaction; they never extend it.
        elapsed = max(0.0, self._clock() - self._started,
                      self._wall_clock() - self._wall_started)
        return max(0.0, DURATION_SECONDS - elapsed)

    def start(self) -> bool:
        if self._active:
            return True
        if self._ended or getattr(self.c, "paused", False) or getattr(self.c, "_quitting", False):
            return False
        if not getattr(self.c, "_screen_ready", True):
            return False
        try:
            self._screen = self.c.screen
            self._geometry = QRect(self._screen.geometry())
            if not self._geometry.isValid():
                return False
            # Mark the attempt so even a partially failed model transition gets
            # a stop attempt and cannot leave a click-intercepting canvas.
            self._model_started = True
            if not self.c.engine.start_teaser(self.cat_id):
                self._model_started = False
                return False
            self._active = True
            self._started, self._wall_started = self._clock(), self._wall_clock()
            ratio = getattr(self._screen, "devicePixelRatio", lambda: 1.0)()
            self._artwork_scale = normalize_cursor_scale(getattr(self.c, "artwork_scale", 1.0))
            # Cocoa uses a native color layer, without a full-screen raster buffer.
            # Keep the QWidget path on other desktop plugins.
            native_surface = QApplication.platformName() in {"cocoa", "offscreen"}
            canvas_type = _TeaserCanvas if native_surface else _WidgetTeaserCanvas
            self._canvas = canvas_type(self, self._geometry, ratio, artwork_scale=self._artwork_scale)
            if native_surface:
                _prepare_cocoa_space_policy(self._canvas)
                _prepare_cocoa_input_surface(self._canvas)
            self._canvas.show()
            if not native_surface:
                self._canvas.raise_()
            # Cocoa QWindow.raise() explicitly activates the process. show()
            # already orders each tool window front, with the hint shown last.
            # See qtbase v6.9.2 qcocoawindow.mm, QCocoaWindow::raise/setVisible.
            if native_surface:
                self._hint = _TeaserHint(self._geometry, artwork_scale=self._artwork_scale)
                _prepare_cocoa_space_policy(self._hint)
                self._hint.show()
            self._application = QApplication.instance()
            if self._application is not None:
                self._application.aboutToQuit.connect(self._on_quit)
            self._notify()
            self.timer.start()
            self._pulse()
            return self._active
        except Exception as exc:
            self.error = f"{type(exc).__name__}: {exc}"
            self.stop("error")
            return False

    def _notify(self):
        if not getattr(self.c, "_quitting", False):
            changed = getattr(self.c, "changed", None)
            if callable(changed):
                changed()

    def _on_quit(self):
        self.stop("quit")

    def _pulse(self):
        if not self._active:
            return
        try:
            remaining = self.remaining
            if remaining <= 0:
                self.stop("completed")
                return
            if getattr(self.c, "_quitting", False):
                self.stop("quit")
                return
            if getattr(self.c, "paused", False):
                self.stop("paused")
                return
            if (not getattr(self.c, "_screen_ready", True) or self.c.screen is not self._screen
                    or self._screen.geometry() != self._geometry):
                self.stop("screen_changed")
                return
            if (hasattr(self.c.engine, "active_teaser_id")
                    and self.c.engine.active_teaser_id != self.cat_id):
                self.stop("cancelled")
                return
            position = self._cursor_position()
            inside = self._geometry.contains(position)
            self._canvas.set_cursor_inside(inside)
            (self._hint or self._canvas).set_remaining(remaining)
            if inside:
                x = position.x()
                if self._previous_cursor_x is not None:
                    delta = x - self._previous_cursor_x
                    if delta:
                        self._canvas.set_cursor_direction(delta)
                self._previous_cursor_x = x
                # Native boundaries and feather direction use actual desktop
                # points; only the engine target enters artwork scene units.
                convert = getattr(self.c, "to_scene", None)
                point = convert(x, position.y()) if callable(convert) else (x, position.y())
                self.c.engine.cursor_moved(*point, 0)
            else:
                # Both UI sampling paths use full display geometry, not the
                # available work area, and forget the invisible toy target.
                self.c.engine.clear_cursor()
                # Re-entering a screen is not a motion sample: keep the last
                # direction until an actual movement inside it is observed.
                self._previous_cursor_x = None
        except Exception as exc:
            self.error = f"{type(exc).__name__}: {exc}"
            self.stop("error")

    def stop(self, reason="cancelled"):
        if self._ended or not (self._active or self._model_started or self._canvas is not None):
            return
        self._active, self._ended = False, True
        self.timer.stop()
        self._previous_cursor_x = None
        canvas, self._canvas = self._canvas, None
        if canvas is not None:
            try:
                canvas.unsetCursor()
                canvas.hide()
                canvas.close()
                canvas.deleteLater()
            except RuntimeError:
                pass  # Qt may already have destroyed an externally closed view.
        hint, self._hint = self._hint, None
        if hint is not None:
            try:
                hint.hide()
                hint.close()
                hint.deleteLater()
            except RuntimeError:
                pass
        if self._application is not None:
            try:
                self._application.aboutToQuit.disconnect(self._on_quit)
            except (TypeError, RuntimeError):
                pass
            self._application = None
        try:
            if self._model_started:
                self._model_started = False
                self.c.engine.stop_teaser(self.cat_id)
            self._notify()
        except Exception as exc:
            self.error = f"{type(exc).__name__}: {exc}"
        self.finished.emit("error" if self.error is not None else str(reason))
