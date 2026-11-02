"""Visible consequence, default-safe confirmation, and persisted cancellation."""
import os
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
import pytest
from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import QApplication, QMessageBox
from backyard_visitor.app import Controller
from backyard_visitor.desktop import DesktopAdapter


@pytest.fixture
def controller(tmp_path):
    qt = QApplication.instance() or QApplication(["cancel-residence-ui"])
    c = Controller(qt, state_dir=tmp_path, hidden=True, adapter=DesktopAdapter(), enable_native=False, seed=5)
    c.engine.open_gate(); c.engine.invite("amber")
    c.changed()
    yield c
    c.shutdown(); qt.processEvents()


def card(c):
    c.journal.refresh()
    return next(card for card in c.journal.cards if card.cat_id == "amber")


def accept_residence(c):
    c.engine.cats["amber"].trust = 100
    c.adopt("amber"); c.place_bed("amber")


def test_cancel_control_only_for_actual_resident(controller):
    c = controller
    assert card(c).cancel_residence_button.isHidden()
    c.engine.cats["amber"].trust = 100
    assert card(c).cancel_residence_button.isHidden()
    accept_residence(c)
    visible = card(c)
    assert not visible.cancel_residence_button.isHidden()
    assert "50" in visible.cancel_residence_button.toolTip()


@pytest.mark.parametrize("answer", [QMessageBox.StandardButton.No, QMessageBox.StandardButton.Yes])
def test_confirmation_uses_plain_nickname_and_cancel_is_default_safe(controller, monkeypatch, answer):
    c = controller; accept_residence(c)
    c.engine.cats["amber"].nickname = "<b>小团</b>"
    visible = card(c)
    captured = []
    def answer_dialog(dialog):
        captured.append(dialog.text())
        assert dialog.textFormat() == Qt.TextFormat.PlainText
        assert "<b>小团</b>" in dialog.text()
        assert "50" in dialog.informativeText() and "猫窝" in dialog.informativeText()
        assert dialog.defaultButton() == dialog.button(QMessageBox.StandardButton.No)
        assert dialog.escapeButton() == dialog.button(QMessageBox.StandardButton.No)
        return answer
    monkeypatch.setattr(QMessageBox, "exec", answer_dialog)
    visible.cancel_residence_button.click()
    assert len(captured) == 1
    if answer == QMessageBox.StandardButton.No:
        assert c.engine.resident_id == "amber" and c.engine.cats["amber"].trust == 100
        assert c.engine.bed is not None and c.bed_window.isVisible()
    else:
        assert c.engine.resident_id is None and c.engine.cats["amber"].trust == 50
        assert c.engine.bed is None and not c.bed_window.isVisible()
        visible.refresh()
        assert visible.cancel_residence_button.isHidden() and visible.bed_button.isHidden()
        assert not visible.adopt.isEnabled()
        assert c.save_timer.isActive()


def test_controller_cancellation_survives_relaunch_and_keeps_bowl_position(controller):
    c = controller; accept_residence(c)
    c.engine.set_bowl(450, c.engine.floor_y+80)
    bowl = c.engine.bowl
    assert c.cancel_residence("amber")
    assert c.engine.bowl == bowl
    assert c._save()
    c.shutdown()
    other = Controller(c.qt, state_dir=c.state_dir, hidden=True, adapter=DesktopAdapter(), enable_native=False, seed=9)
    try:
        assert other.engine.resident_id is None and other.engine.cats["amber"].trust == 50
        assert other.engine.bed is None and not other.bed_window.isVisible()
        assert other.engine.bowl == bowl
        assert not other.engine.gate_open and not other.engine.visitors
    finally:
        other.shutdown()


def test_stale_card_and_shutdown_cannot_cancel(controller, monkeypatch):
    c = controller; accept_residence(c)
    visible = card(c)
    assert c.cancel_residence("amber")
    monkeypatch.setattr(QMessageBox, "exec", lambda self: pytest.fail("stale card opened confirmation"))
    visible.confirm_cancel_residence()
    c.engine.cats["amber"].trust = 100; c.adopt("amber")
    c.shutdown()
    assert not c.cancel_residence("amber")
    assert c.engine.resident_id == "amber" and c.engine.cats["amber"].trust == 100


def test_cancellation_while_paused_does_not_restart_animation(controller):
    c = controller; accept_residence(c)
    c.set_paused(True)
    v = c.engine.visitors["amber"]
    before = v.x, v.y, v.age, c.engine._time
    c.bed_window.drag_active = True
    assert c.cancel_residence("amber")
    assert c.paused and not c.timer.isActive()
    assert (v.x, v.y, v.age, c.engine._time) == before
    assert not c.bed_window.drag_active and not c.bed_window.isVisible()
    assert 150 <= v.stay_remaining <= 230
