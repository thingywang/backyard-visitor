"""Overlay level changes retain state and never promote the normal journal."""
import json
import os
os.environ.setdefault('QT_QPA_PLATFORM','offscreen')

import pytest
from PyQt6.QtCore import QPoint, QRect, Qt
from PyQt6.QtGui import QRegion
from PyQt6.QtWidgets import QApplication

from backyard_visitor.app import Controller
from backyard_visitor.desktop import DesktopAdapter
from backyard_visitor.model import Gift
from backyard_visitor.overlays import Overlay


@pytest.fixture(scope='module')
def qt():
    return QApplication.instance() or QApplication(['overlay-topmost-test'])


@pytest.fixture
def make_controller(qt,tmp_path):
    controllers=[]
    def create(name='session',saved=None):
        folder=tmp_path/name
        if saved is not None:
            folder.mkdir(parents=True,exist_ok=True)
            (folder/'state.json').write_text(json.dumps(saved))
        c=Controller(qt,state_dir=folder,hidden=True,adapter=DesktopAdapter(),enable_native=False,seed=5)
        controllers.append(c)
        return c
    yield create
    for c in controllers:c.shutdown()
    qt.processEvents()


def assert_level(window, enabled):
    assert window.windowType()==(Qt.WindowType.Tool if enabled else Qt.WindowType.Window)
    assert bool(window.windowFlags() & Qt.WindowType.WindowStaysOnTopHint)==enabled
    assert window.testAttribute(Qt.WidgetAttribute.WA_MacAlwaysShowToolWindow)
    assert window.testAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating)
    assert window.windowFlags() & Qt.WindowType.WindowDoesNotAcceptFocus


def test_new_install_defaults_on_without_loading_journal(make_controller):
    c=make_controller()
    assert c.settings['overlay_topmost'] is True and c._journal is None
    for window in (c.gate,c.bowl,c.bin):assert_level(window,True)
    assert not c.bowl.isVisible() and not c.bin.isVisible()


def test_switch_updates_existing_and_future_overlays_without_changing_visibility(make_controller):
    c=make_controller();c.toggle_gate();c.engine.invite('amber')
    c.engine.gifts.append(Gift('gift-test','feather',200,300,'amber'));c.changed()
    c.bowl.hide();c.bin.hide()
    windows=[c.gate,c.bowl,c.bin,*c.cat_windows.values(),*c.gift_windows.values()]
    c.gate.move(QPoint(253,117));c.gate.setMask(QRegion(QRect(2,3,15,16)))
    states=[(w.pos(),w.isVisible(),w.mask()) for w in windows]
    for enabled in (False,True,False):
        c.set_overlay_topmost(enabled)
        for w,(pos,visible,mask) in zip(windows,states):
            assert_level(w,enabled)
            assert (w.pos(),w.isVisible(),w.mask())==(pos,visible,mask)
    later=Overlay(c,20,20,'future-overlay')
    try:
        assert_level(later,False)
        assert not later.isVisible()
        c.set_overlay_topmost(True)
        assert_level(later,True)
        assert not later.isVisible()
    finally:later.deleteLater()


def test_custom_input_flags_survive_switch(make_controller):
    c=make_controller();w=Overlay(c,20,20,'non-interactive')
    try:
        w.setWindowFlag(Qt.WindowType.WindowTransparentForInput,True)
        c.set_overlay_topmost(False)
        assert w.windowFlags() & Qt.WindowType.WindowTransparentForInput
        c.set_overlay_topmost(True)
        assert w.windowFlags() & Qt.WindowType.WindowTransparentForInput
    finally:w.deleteLater()


def test_journal_remains_normal_and_checkbox_controls_policy(make_controller,qt):
    c=make_controller();c.show_journal();j=c.journal
    before=j.windowFlags()
    j.topmost_check.setChecked(False)
    assert c.settings['overlay_topmost'] is False and j.windowFlags()==before
    assert j.windowType()==Qt.WindowType.Window
    assert not j.windowFlags() & Qt.WindowType.WindowStaysOnTopHint
    c.set_overlay_topmost(True)
    assert j.topmost_check.isChecked() and j.windowFlags()==before
    assert c.gate.testAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating)
    j.hide();c.set_overlay_topmost(False)
    assert not j.isVisible()


def test_false_setting_persists_and_new_controller_uses_it(make_controller):
    c=make_controller();c.set_overlay_topmost(False);assert c._save()
    state=json.loads(c.state_path.read_text())
    assert state['settings']['overlay_topmost'] is False
    restored=make_controller('restored',state)
    assert restored.settings['overlay_topmost'] is False
    assert_level(restored.gate,False)


@pytest.mark.parametrize('settings',[{}, {'overlay_topmost':None}, {'overlay_topmost':0},
                                     {'overlay_topmost':'false'}, {'overlay_topmost':[]}])
def test_missing_or_non_boolean_saved_setting_uses_safe_default(make_controller,settings):
    c=make_controller(saved={'version':1,'settings':settings})
    assert c.settings['overlay_topmost'] is True
    assert_level(c.gate,True)


def test_cocoa_spaces_follow_show_and_native_type_changes_without_revealing_hidden(make_controller,monkeypatch):
    import backyard_visitor.overlays as overlays
    calls=[]
    monkeypatch.setattr(overlays.QApplication,'platformName',staticmethod(lambda:'cocoa'))
    monkeypatch.setattr(overlays,'configure_cocoa_spaces',lambda view,enabled:calls.append((view,enabled)) or {'collection_behavior':257 if enabled else 128},raising=False)
    c=make_controller()
    assert calls and calls[-1]==(int(c.gate.winId()),True)
    hidden=Overlay(c,23,27,'hidden-effect');hidden.setWindowFlag(Qt.WindowType.WindowTransparentForInput,True)
    calls.clear()
    try:
        for enabled in (False,True):
            c.set_overlay_topmost(enabled)
            assert (int(c.gate.winId()),enabled) in calls
            assert not hidden.isVisible() and not c.bin.isVisible()
        hidden.show()
        assert calls[-1]==(int(hidden.winId()),True)
        assert hidden.windowFlags() & Qt.WindowType.WindowTransparentForInput
        hidden.hide();calls.clear();hidden.show()
        assert calls==[(int(hidden.winId()),True)]
        calls.clear();hidden.setVisible(True);hidden.setVisible(True)
        assert calls==[]  # Repeated bowl sync must not perform native work.
    finally:hidden.close();hidden.deleteLater()


def test_cocoa_space_failure_is_reported_without_exception_escaping_qt(make_controller,monkeypatch):
    import backyard_visitor.overlays as overlays
    monkeypatch.setattr(overlays.QApplication,'platformName',staticmethod(lambda:'cocoa'))
    def fail(view,enabled):raise RuntimeError('native policy rejected')
    monkeypatch.setattr(overlays,'configure_cocoa_spaces',fail,raising=False)
    c=make_controller()
    assert c.gate.isVisible()
    assert any('全屏' in note for note in c._notes)
