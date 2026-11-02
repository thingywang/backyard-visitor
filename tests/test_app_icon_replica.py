"""UI integration of the separately opted-in, non-mutating icon gesture."""
import os
os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
import time
import pytest
from PyQt6.QtCore import QByteArray, QBuffer, QIODevice, Qt
from PyQt6.QtGui import QColor, QImage
from PyQt6.QtWidgets import QApplication
from backyard_visitor.app import Controller
from backyard_visitor.desktop import DesktopAdapter, DesktopTarget, Rect


class FakeIcons(DesktopAdapter):
    def __init__(self):
        super().__init__()
        self.capabilities.update(desktop_icons=True, icon_replica=True)
        self.target = DesktopTarget('allowed', 'Local icon', Rect(200, 300, 64, 64))
        self.reads = 0
        image = QImage(64, 64, QImage.Format.Format_ARGB32)
        image.fill(QColor('green'))
        data = QByteArray()
        buffer = QBuffer(data)
        buffer.open(QIODevice.OpenModeFlag.WriteOnly)
        image.save(buffer, 'PNG')
        self.png = bytes(data)

    def desktop_icons(self):
        self.reads += 1
        return [self.target]

    def icon_replica_png(self, identity):
        assert identity == self.target.id
        return self.png


@pytest.fixture
def c(tmp_path):
    qt = QApplication.instance() or QApplication([])
    adapter = FakeIcons()
    c = Controller(qt, state_dir=tmp_path, hidden=True, adapter=adapter, enable_native=False)
    yield c
    c.shutdown()
    qt.processEvents()


def enable(c):
    c._enable_native = True
    c.settings.update(icons_enabled=True, icon_ids=['allowed'])
    c.adapter.icons_enabled = True
    c._icons = [c.adapter.target]
    c._icons_checked = time.monotonic()
    c.set_icon_replica(True)


def test_no_replica_work_without_separate_opt_in(c, monkeypatch):
    work = []
    monkeypatch.setattr(c, '_run_worker', lambda *args: work.append(args))
    c._request_icon_replica(c.adapter.target)
    assert not work and c.adapter.reads == 0
    c.settings.update(icons_enabled=True, icon_ids=['allowed'])
    c._request_icon_replica(c.adapter.target)
    assert not work


def test_revoke_or_pause_discards_late_worker_result(c, monkeypatch):
    enable(c)
    work = []
    monkeypatch.setattr(c, '_run_worker', lambda *args: work.append(args))
    c._request_icon_replica(c.adapter.target)
    assert len(work) == 1
    _, query, received = work[0]
    result = query()
    c.set_paused(True)
    received(result)
    assert c._icon_replica is None
    c.set_paused(False)
    c._replica_attempted = -float('inf')
    c._request_icon_replica(c.adapter.target)
    c.set_icons_enabled(False)
    work[-1][2](result)
    assert c._icon_replica is None
    assert not c.adapter.icon_replica_enabled


def test_overlay_is_input_transparent_and_disappears_on_changed_icon(c, monkeypatch):
    enable(c)
    monkeypatch.setattr(c, '_run_worker', lambda key, query, received: received(query()))
    c._request_icon_replica(c.adapter.target)
    overlay = c._icon_replica
    assert overlay is not None and overlay.isVisible()
    assert overlay.windowFlags() & Qt.WindowType.WindowTransparentForInput
    c._icons = [DesktopTarget('allowed', 'Local icon', Rect(280, 300, 64, 64))]
    overlay.step()
    assert not overlay.isVisible() and not overlay.timer.isActive()
    overlay.finish()
    assert c._icon_replica is None


def test_realistic_gifts_toggle_keeps_saved_progress(c):
    c.set_realistic_mouse(False)
    assert c._save()
    import json
    saved = json.loads(c.state_path.read_text())
    assert saved['settings']['realistic_mouse'] is False
    assert c.engine.cats['amber'].trust == 0


def test_native_window_failure_does_not_show_replica(c, monkeypatch):
    enable(c)
    def failed_windows():
        c.adapter.last_error = 'Native window geometry unavailable'
        return []
    monkeypatch.setattr(c.adapter, 'windows', failed_windows)
    monkeypatch.setattr(c, '_run_worker', lambda key, query, received: received(query()))
    c._request_icon_replica(c.adapter.target)
    assert c._icon_replica is None


def test_new_journal_occlusion_discards_pending_replica(c, monkeypatch):
    enable(c)
    work = []
    monkeypatch.setattr(c, '_run_worker', lambda *args: work.append(args))
    c._request_icon_replica(c.adapter.target)
    result = work[0][1]()
    c.show_journal()
    c.journal.move(100, 200)
    work[0][2](result)
    assert c._icon_replica is None


def test_revoked_permission_updates_settings_and_clear_paths(c, monkeypatch):
    enable(c)
    def revoked_icons():
        c.adapter.enable_desktop_icons(False)
        return []
    monkeypatch.setattr(c.adapter, 'desktop_icons', revoked_icons)
    monkeypatch.setattr(c, '_run_worker', lambda key, query, received: received(query()))
    c._request_icon_replica(c.adapter.target)
    assert c._icons == [] and c.engine.paths == []
    assert c.settings['icons_enabled'] is False
    assert c.settings['icon_replica'] is False


def test_initial_expired_frame_never_shows_at_origin(c):
    from PyQt6.QtGui import QPixmap
    from backyard_visitor.overlays import IconReplica
    enable(c)
    now = time.monotonic()
    assert c._replica_policy.begin(c.adapter.target, now)
    c._icons_checked = now - 3
    overlay = IconReplica(c, c.adapter.target, QPixmap(64,64))
    c._icon_replica = overlay
    assert not overlay.start()
    assert not overlay.isVisible() and not overlay.timer.isActive()
    c._cancel_icon_replica()
