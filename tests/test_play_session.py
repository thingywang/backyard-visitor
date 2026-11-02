"""Deterministic Qt interaction tests; no native activation or cursor mutation."""
import os
os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')

from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from PyQt6 import sip
from PyQt6.QtCore import QEvent, QPoint, QPointF, QRect, Qt
from PyQt6.QtGui import QMouseEvent, QSurface, QWindow
from PyQt6.QtWidgets import QApplication, QWidget

from backyard_visitor.play_session import TeaserSession, TICK_MS


class Screen:
    def __init__(self, rect=None):
        self.rect = rect or QRect(-900, -120, 900, 700)
    def geometry(self): return QRect(self.rect)
    def devicePixelRatio(self): return 2.0


class Engine:
    def __init__(self):
        self.active_teaser_id = None
        self.starts, self.stops, self.cursors = [], [], []
        self.accept = True
        self.fail_cursor = self.fail_stop = False
    def start_teaser(self, cid):
        self.starts.append(cid)
        if self.accept: self.active_teaser_id = cid
        return self.accept
    def stop_teaser(self, cid):
        self.stops.append(cid)
        self.active_teaser_id = None
        if self.fail_stop: raise RuntimeError('fake model stop failure')
    def cursor_moved(self, x, y, speed):
        if self.fail_cursor: raise RuntimeError('fake cursor failure')
        self.cursors.append((x, y, speed))
    def clear_cursor(self):
        self.cursor_cleared = True


@pytest.fixture
def session():
    qt = QApplication.instance() or QApplication(['teaser-tests'])
    clock = dict(mono=100.0, wall=1000.0, cursor=QPoint(-300, 240))
    c = SimpleNamespace(engine=Engine(), screen=Screen(), paused=False, _quitting=False,
                        _screen_ready=True, changed=Mock())
    s = TeaserSession(c, 'amber', clock=lambda:clock['mono'], wall_clock=lambda:clock['wall'],
                      cursor_position=lambda:QPoint(clock['cursor']))
    reasons = []
    s.finished.connect(reasons.append)
    yield s, c, clock, reasons, qt
    s.stop('test_cleanup')
    QApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
    qt.processEvents()


def test_start_is_local_cursor_non_focus_canvas_and_one_timer(session):
    s,c,t,reasons,qt = session
    before = QApplication.overrideCursor()
    assert s.start() and s.start()
    canvas=s._canvas
    assert s.active and s.remaining == 15 and s.timer.interval() == TICK_MS
    assert canvas.geometry() == c.screen.geometry()
    assert isinstance(canvas,QWindow) and not isinstance(canvas,QWidget)
    assert canvas.flags() & Qt.WindowType.WindowDoesNotAcceptFocus
    assert canvas.flags() & Qt.WindowType.WindowStaysOnTopHint
    assert canvas.format().alphaBufferSize()==8
    assert canvas.surfaceType()==QSurface.SurfaceType.RasterSurface
    assert s._hint.testAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating)
    assert s._hint.windowFlags() & Qt.WindowType.WindowTransparentForInput
    assert s._hint.testAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
    assert c.screen.geometry().contains(s._hint.geometry())
    assert s._hint.width()<=344 and s._hint.height()<=56
    assert not canvas.flags() & Qt.WindowType.WindowTransparentForInput
    assert canvas.cursor().shape() == Qt.CursorShape.BitmapCursor
    assert not canvas.cursor().pixmap().isNull()
    assert QApplication.overrideCursor() is before
    assert c.engine.starts == ['amber'] and c.changed.call_count == 1


def test_deadline_stops_once_even_if_event_loop_wakes_late(session):
    s,c,t,reasons,qt = session
    assert s.start()
    canvas=s._canvas;hint=s._hint
    t['mono'] += 14.99;s._pulse()
    assert s.active and s.remaining == pytest.approx(.01)
    t['mono'] += .02;s._pulse();s._pulse();s.stop('cancelled')
    assert not s.active and s.remaining == 0 and not s.timer.isActive()
    assert reasons == ['completed'] and c.engine.stops == ['amber']
    assert s._canvas is None and not canvas.isVisible()
    assert s._hint is None and not hint.isVisible()
    QApplication.sendPostedEvents(None,QEvent.Type.DeferredDelete)
    assert sip.isdeleted(canvas) and sip.isdeleted(hint)
    assert not s.start()


def test_real_sleep_expires_even_when_monotonic_excludes_sleep(session):
    s,c,t,reasons,qt=session;s.start()
    t['wall'] += 60;s._pulse()
    assert reasons == ['completed'] and not s.active


def test_negative_screen_coordinates_and_leaving_screen(session):
    s,c,t,reasons,qt=session;s.start()
    assert c.engine.cursors[-1] == (-300,240,0)
    count=len(c.engine.cursors)
    t['cursor']=QPoint(10,240);s._pulse()
    assert len(c.engine.cursors)==count and s.active
    assert c.engine.cursor_cleared
    assert s._canvas.cursor().shape()==Qt.CursorShape.ArrowCursor
    t['cursor']=QPoint(-899,-119);s._pulse()
    assert c.engine.cursors[-1]==(-899,-119,0)
    assert s._canvas.cursor().shape()==Qt.CursorShape.BitmapCursor
    assert c.changed.call_count==1  # Sampling must not reset the animation clock.


def test_right_press_cancels_and_left_click_is_consumed(session):
    s,c,t,reasons,qt=session;s.start();canvas=s._canvas
    def press(button):
        return QMouseEvent(QEvent.Type.MouseButtonPress,QPointF(20,20),QPointF(-880,-100),
                           button,button,Qt.KeyboardModifier.NoModifier)
    left=press(Qt.MouseButton.LeftButton);canvas.mousePressEvent(left)
    assert s.active and left.isAccepted()
    right=press(Qt.MouseButton.RightButton);canvas.mousePressEvent(right)
    assert right.isAccepted() and not s.active and reasons==['cancelled']
    assert c.engine.stops==['amber']


@pytest.mark.parametrize('boundary,reason',[('pause','paused'),('quit','quit'),('screen','screen_changed'),
                                          ('geometry','screen_changed'),('no_screen','screen_changed'),
                                          ('model_cancel','cancelled')])
def test_boundaries_remove_interception(session,boundary,reason):
    s,c,t,reasons,qt=session;s.start();canvas=s._canvas
    if boundary=='pause':c.paused=True
    elif boundary=='quit':c._quitting=True
    elif boundary=='screen':c.screen=Screen()
    elif boundary=='geometry':c.screen.rect.translate(20,0)
    elif boundary=='no_screen':c._screen_ready=False
    elif boundary=='model_cancel':c.engine.active_teaser_id=None
    s._pulse()
    assert not s.active and not canvas.isVisible() and reasons==[reason]


def test_rejected_model_does_not_create_canvas_or_stop_another_game(session):
    s,c,t,reasons,qt=session;c.engine.accept=False
    assert not s.start() and s._canvas is None and not s.timer.isActive()
    assert not c.engine.stops and not reasons and c.changed.call_count==0


@pytest.mark.parametrize('fail',['cursor','stop'])
def test_model_error_cannot_leave_click_interceptor(session,fail):
    s,c,t,reasons,qt=session;s.start();canvas=s._canvas
    if fail=='cursor':c.engine.fail_cursor=True;s._pulse()
    else:c.engine.fail_stop=True;s.stop('cancelled')
    assert not s.active and not canvas.isVisible() and not s.timer.isActive()
    assert reasons==['error'] and 'RuntimeError' in s.error
    assert c.engine.stops==['amber']


def test_external_close_and_quit_cleanup_are_idempotent(session):
    s,c,t,reasons,qt=session;s.stop();assert s.start()
    s._canvas.close();s._on_quit();s.stop('paused')
    assert reasons==['cancelled'] and c.engine.stops==['amber']
    assert s._application is None and s._canvas is None


@pytest.mark.parametrize('flag',['paused','_quitting','_screen_ready'])
def test_unavailable_controller_never_starts(session,flag):
    s,c,t,reasons,qt=session
    setattr(c,flag,flag!='_screen_ready')
    assert not s.start() and not c.engine.starts


def test_already_destroyed_canvas_still_stops_model_and_timer(session):
    s,c,t,reasons,qt=session;s.start()
    s._canvas.deleteLater()
    QApplication.sendPostedEvents(None,QEvent.Type.DeferredDelete)
    s.stop('screen_changed')
    assert not s.active and not s.timer.isActive() and s._canvas is None
    assert c.engine.stops==['amber'] and reasons==['screen_changed']


def test_partial_model_start_failure_is_rolled_back(session):
    s,c,t,reasons,qt=session
    def broken_start(cid):
        c.engine.active_teaser_id=cid
        raise RuntimeError('partial model start')
    c.engine.start_teaser=broken_start
    assert not s.start() and s._canvas is None
    assert c.engine.stops==['amber'] and c.engine.active_teaser_id is None
    assert reasons==['error'] and not s.timer.isActive()


def test_hint_renders_only_small_countdown_and_does_not_allocate_screen_pixmap(session):
    s,c,t,reasons,qt=session;assert s.start()
    hint=s._hint;before=hint.grab()
    assert before.width()<=344*hint.devicePixelRatioF()
    assert before.height()<=56*hint.devicePixelRatioF()
    t['mono']+=2.2;s._pulse()
    assert hint.seconds==13
    assert s._canvas.geometry()==c.screen.geometry()
    assert not hasattr(s._canvas,'grab')
    assert not before.toImage().isNull()


def test_external_surface_destruction_still_removes_small_hint(session):
    s,c,t,reasons,qt=session;assert s.start();hint=s._hint
    s._canvas.deleteLater();QApplication.sendPostedEvents(None,QEvent.Type.DeferredDelete)
    s.stop('screen_changed')
    assert s._hint is None and not hint.isVisible()
    QApplication.sendPostedEvents(None,QEvent.Type.DeferredDelete)
    assert sip.isdeleted(hint) and c.engine.stops==['amber']


def test_event_delivery_to_qwindow_consumes_right_click_without_global_cursor(session):
    s,c,t,reasons,qt=session;assert s.start();surface=s._canvas
    event=QMouseEvent(QEvent.Type.MouseButtonPress,QPointF(20,20),QPointF(-880,-100),
        Qt.MouseButton.RightButton,Qt.MouseButton.RightButton,Qt.KeyboardModifier.NoModifier)
    QApplication.sendEvent(surface,event)
    assert event.isAccepted() and reasons==['cancelled']
    assert s._canvas is None and s._hint is None and QApplication.overrideCursor() is None


def test_native_setup_failure_cannot_leave_click_layer_or_running_model(session,monkeypatch):
    import backyard_visitor.play_session as play
    s,c,t,reasons,qt=session
    seen=[]
    def fail(canvas):
        seen.append(canvas)
        raise RuntimeError('native input configuration failed')
    monkeypatch.setattr(play,'_prepare_cocoa_input_surface',fail)
    assert not s.start()
    assert not s.active and not s.timer.isActive() and s._canvas is None and s._hint is None
    assert not seen[0].isVisible()
    assert c.engine.active_teaser_id is None and c.engine.stops==['amber']
    assert reasons==['error']
    QApplication.sendPostedEvents(None,QEvent.Type.DeferredDelete)
    assert sip.isdeleted(seen[0])


def test_qwindow_has_nonactivating_contract_before_first_show(session,monkeypatch):
    import backyard_visitor.play_session as play
    s,c,t,reasons,qt=session
    observed=[]
    original=play._TeaserCanvas.show
    def inspect_show(canvas):
        observed.append((canvas.property('_q_showWithoutActivating'),
                         canvas.property('_q_macAlwaysShowToolWindow')))
        original(canvas)
    monkeypatch.setattr(play._TeaserCanvas,'show',inspect_show)
    assert s.start()
    assert observed==[(True,True)]


def test_native_game_show_never_calls_raise_that_activates_cocoa_app(session,monkeypatch):
    import backyard_visitor.play_session as play
    s,c,t,reasons,qt=session
    calls=[]
    monkeypatch.setattr(play._TeaserCanvas,'raise_',lambda obj:calls.append('canvas'))
    monkeypatch.setattr(play._TeaserHint,'raise_',lambda obj:calls.append('hint'))
    assert s.start()
    assert calls==[]
    assert s._canvas.isVisible() and s._hint.isVisible()
    assert s._canvas.flags() & Qt.WindowType.WindowDoesNotAcceptFocus
    assert s._hint.testAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating)


def test_other_platforms_keep_widget_fallback_and_existing_raise(session,monkeypatch):
    import backyard_visitor.play_session as play
    s,c,t,reasons,qt=session
    calls=[]
    monkeypatch.setattr(play.QApplication,'platformName',staticmethod(lambda:'xcb'))
    monkeypatch.setattr(play._WidgetTeaserCanvas,'raise_',lambda obj:calls.append('widget'))
    assert s.start()
    assert isinstance(s._canvas,play._WidgetTeaserCanvas) and s._hint is None
    assert calls==['widget']
    assert s._canvas.testAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating)


def test_cocoa_spaces_configured_before_play_windows_are_exposed(session,monkeypatch):
    import backyard_visitor.play_session as play
    s,c,t,reasons,qt=session;seen=[]
    monkeypatch.setattr(play.QApplication,'platformName',staticmethod(lambda:'cocoa'))
    monkeypatch.setattr(play,'_prepare_cocoa_input_surface',lambda window:None)
    def prepare(window):
        assert not window.isVisible()
        seen.append(type(window).__name__)
    monkeypatch.setattr(play,'_prepare_cocoa_space_policy',prepare,raising=False)
    assert s.start()
    assert seen==['_TeaserCanvas','_TeaserHint']


def test_space_setup_failure_removes_game_resources(session,monkeypatch):
    import backyard_visitor.play_session as play
    s,c,t,reasons,qt=session
    def fail(window):raise RuntimeError('native Space policy rejected')
    monkeypatch.setattr(play,'_prepare_cocoa_space_policy',fail,raising=False)
    assert not s.start()
    assert s._canvas is None and s._hint is None and not s.timer.isActive()
    assert c.engine.active_teaser_id is None and reasons==['error']
