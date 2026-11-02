"""The desktop gate is optional; invitation controls remain reachable."""
import json
import os
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PyQt6.QtWidgets import QApplication, QSystemTrayIcon

from backyard_visitor.app import Controller
from backyard_visitor.desktop import DesktopAdapter
from test_app_lifecycle import Desktop, Screen


@pytest.fixture
def make_controller(tmp_path, monkeypatch):
    qt = QApplication.instance() or QApplication(["gate-visibility-tests"])
    controllers = []
    availability = {"tray": True}
    monkeypatch.setattr(QSystemTrayIcon, "isSystemTrayAvailable", lambda: availability["tray"])

    def create(name="session", saved=None, tray=True):
        availability["tray"] = tray
        folder = tmp_path / name
        if saved is not None:
            folder.mkdir(parents=True, exist_ok=True)
            (folder / "state.json").write_text(json.dumps(saved))
        desktop = Desktop(qt)
        desktop.topLevelWidgets = qt.topLevelWidgets
        c = Controller(desktop, state_dir=folder, hidden=True, adapter=DesktopAdapter(),
                       enable_native=False, seed=11)
        controllers.append(c)
        return c

    yield create
    for c in controllers:
        c.shutdown()
    qt.processEvents()


def test_default_gate_still_visible_and_journal_lazy(make_controller):
    c = make_controller()
    assert c.settings["gate_visible"] is True
    assert c.gate.isVisible() and c._journal is None
    assert not c.engine.gate_open


@pytest.mark.parametrize("value", [None, 0, 1, "false", [], {}])
def test_invalid_saved_gate_flag_keeps_safe_default(make_controller, value):
    c = make_controller(saved={"settings": {"gate_visible": value}})
    assert c.settings["gate_visible"] is True and c.gate.isVisible()


def test_hidden_gate_loads_without_opening_gate_or_journal(make_controller):
    c = make_controller(saved={"settings": {"gate_visible": False}})
    assert not c.gate.isVisible() and c._journal is None
    assert c.tray.isVisible() and not c.engine.gate_open


def test_visibility_switch_preserves_ecology_and_cancels_only_gate_drag(make_controller):
    c = make_controller()
    c.toggle_gate()
    c.engine.invite("amber")
    c.changed()
    before = c.engine.export()
    c.gate.drag_active = True
    c.gate.dragged = False
    assert c.set_gate_visible(False) is True
    assert not c.gate.isVisible() and not c.gate.drag_active
    assert c.engine.export() == before
    assert c.set_gate_visible(True) is True and c.gate.isVisible()
    assert c.engine.export() == before
    assert c.set_gate_visible("false") is False
    assert c.settings["gate_visible"] is True


def test_hidden_choice_persists_per_save(make_controller):
    c = make_controller("first")
    c.set_gate_visible(False)
    assert c._save()
    state = json.loads(c.state_path.read_text())
    assert state["settings"]["gate_visible"] is False
    c.shutdown()
    restored = make_controller("first")
    separate = make_controller("second")
    assert not restored.gate.isVisible()
    assert separate.gate.isVisible()


def test_tray_and_handbook_still_toggle_invitation_when_gate_hidden(make_controller):
    c = make_controller(saved={"settings": {"gate_visible": False}})
    action = next(a for a in c._tray_menu.actions() if a.text() == "开门 / 关门")
    action.trigger()
    assert c.engine.gate_open and not c.gate.isVisible()
    c.show_journal()
    c.journal.gate_button.click()
    assert not c.engine.gate_open and not c.gate.isVisible()
    c.journal.gate_visible_check.setChecked(True)
    assert c.gate.isVisible()
    c.set_gate_visible(False)
    assert not c.journal.gate_visible_check.isChecked()


def test_hidden_choice_survives_pause_levels_and_screen_replacement(make_controller):
    c = make_controller(saved={"settings": {"gate_visible": False}})
    c.set_paused(True)
    c.set_overlay_topmost(False)
    c.set_overlay_topmost(True)
    assert not c.gate.isVisible()
    first = c.screen
    c.qt.remove(first)
    assert not c._screen_ready and not c.gate.isVisible()
    replacement = Screen("replacement", x=-1000, width=1000, height=800)
    c.qt.add(replacement)
    assert c._screen_ready and not c.gate.isVisible()
    c.set_paused(False)
    c.changed()
    assert not c.gate.isVisible()
    c.set_gate_visible(True)
    assert c.gate.isVisible()


def test_show_request_during_no_screen_waits_for_display(make_controller):
    c = make_controller(saved={"settings": {"gate_visible": False}})
    c.qt.remove(c.screen)
    c.set_gate_visible(True)
    assert c.settings["gate_visible"] is True and not c.gate.isVisible()
    c.qt.add(Screen("returned"))
    assert c.gate.isVisible()


def test_hidden_startup_without_tray_keeps_handbook_accessible(make_controller):
    c = make_controller(saved={"settings": {"gate_visible": False}}, tray=False)
    assert c._journal is not None and c.journal.isVisible()
    assert not c.gate.isVisible() and not c.engine.gate_open
    # Closing the only remaining entry must not strand an inaccessible process.
    journal = c.journal
    journal.hide()
    c.release_journal(journal)
    assert c.journal is journal and journal.isVisible()
    assert c.settings["gate_visible"] is False
    c.shutdown()
    c.release_journal(journal)
    assert not journal.isVisible()


def test_hiding_gate_without_tray_exposes_handbook_without_opening_gate(make_controller):
    c = make_controller(tray=False)
    assert c._journal is None and c.gate.isVisible()
    c.set_gate_visible(False)
    assert c.journal.isVisible() and not c.engine.gate_open
    assert not c.gate.isVisible()
