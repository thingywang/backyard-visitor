"""Focused demo guards/observation failures; no native GUI or timed play run."""
import os
os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
import json
from pathlib import Path
from types import SimpleNamespace

import pytest
from PyQt6.QtWidgets import QApplication
from backyard_visitor import app
from backyard_visitor.demo_teaser import DemoReport, DemoTeaser, eligible, full_pair, scene_checks
from backyard_visitor.desktop import DesktopAdapter



@pytest.fixture
def qt_exit_requests(monkeypatch):
    # These unit tests share pytest's QApplication rather than owning app.exec().
    # Observe the requested exit code; calling real Qt exit here leaves quitNow
    # set and makes later QSignalSpy nested event loops return immediately.
    qt = QApplication.instance() or QApplication(['teaser-demo-test'])
    requested = []
    monkeypatch.setattr(qt, 'exit', requested.append)
    return qt, requested


def sample():
    return {'phase': 'play', 'two_fully_visible': True, 'canvas_visible': True}


def good_report():
    return {'samples': [sample() for _ in range(55)], 'finished_reason': 'completed', 'active_seconds': 15.02,
            'play_cleared': True, 'rest_arrived': True, 'no_global_override': True,
            'no_journal': True, 'clock_acceleration_seen': False}


def test_fresh_report_atomically_updates_and_refuses_existing_path(tmp_path):
    path = tmp_path/'new.json'
    writer = DemoReport(path)
    writer.write({'state': 'running', 'sample': [1, 2]})
    original = path.read_bytes()
    with pytest.raises(FileExistsError):
        DemoReport(path)
    assert path.read_bytes() == original
    assert json.loads(original)['sample'] == [1, 2]
    assert list(tmp_path.iterdir()) == [path]


def test_existing_temporary_is_not_removed_or_overwritten(tmp_path):
    path = tmp_path/'new.json'
    writer = DemoReport(path)
    temporary = path.with_name(f'.{path.name}.{os.getpid()}.tmp')
    temporary.write_bytes(b'preserve')
    with pytest.raises(FileExistsError):
        writer.write({'state': 'running'})
    assert temporary.read_bytes() == b'preserve'
    assert json.loads(path.read_text())['state'] == 'starting'


@pytest.mark.parametrize('arguments', [
    ['--demo-scene', 'teaser'], ['--demo-teaser-report', 'unused.json'],
    ['--demo', '--demo-teaser-report', 'unused.json'],
    ['--demo', '--demo-scene', 'teaser', '--hidden'],
    ['--demo', '--demo-scene', 'teaser', '--demo-teaser-report', 'unused.json'],
    ['--demo', '--demo-scene', 'teaser', '--demo-teaser-report', 'unused.json', '--hidden', '--demo-cat-limit', '1'],
    ['--demo', '--demo-scene', 'teaser', '--demo-teaser-report', 'unused.json', '--hidden', '--render-preview', 'unused.png'],
])
def test_invalid_cli_rejected_before_controller_or_report(arguments, monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(app, 'Controller', lambda *a, **k: pytest.fail('Controller must not start'))
    with pytest.raises(SystemExit) as error:
        app.main(arguments)
    assert error.value.code == 2
    assert not list(tmp_path.iterdir())


def test_partial_or_other_pair_is_not_an_eligible_two_cat_observation():
    rows = {'amber': {'fully_inside': True, 'qt_visible': True}, 'mist': {'fully_inside': True, 'qt_visible': True}}
    assert full_pair(rows)
    rows['mist']['fully_inside'] = False
    assert not full_pair(rows)
    assert not full_pair({'amber': rows['amber']})


def test_entry_and_carrying_are_never_forced_into_play():
    values = dict(state='idle', route_motion='walking', posture_remaining=0, carrying_gift=None,
                  demo_gift_pending=None, true_name_pending=False, true_name_remaining=0)
    assert eligible(SimpleNamespace(**values))
    for key, value in [('state', 'entering'), ('route_motion', 'fleeing'), ('posture_remaining', .1),
                       ('carrying_gift', 'feather'), ('demo_gift_pending', 'toy_mouse'), ('true_name_pending', True)]:
        assert not eligible(SimpleNamespace(**{**values, key: value}))


@pytest.mark.parametrize('field,value', [('finished_reason', 'cancelled'), ('active_seconds', 2),
    ('play_cleared', False), ('rest_arrived', False), ('no_global_override', False),
    ('no_journal', False), ('clock_acceleration_seen', True)])
def test_bad_terminal_evidence_prevents_completion(field, value):
    report = good_report()
    assert all(scene_checks(report).values())
    report[field] = value
    assert not all(scene_checks(report).values())


def test_a_later_good_frame_cannot_hide_missing_cat_or_canvas():
    for key in ['two_fully_visible', 'canvas_visible']:
        report = good_report()
        report['samples'][2][key] = False
        assert not all(scene_checks(report).values())


def test_entry_timeout_preserves_natural_positions_and_is_inconclusive(tmp_path, qt_exit_requests):
    qt, requested_exits = qt_exit_requests
    c = app.Controller(qt, demo=True, hidden=True, adapter=DesktopAdapter(), enable_native=False, seed=7, visit_limit=2)
    clock = [0.0]
    probe = DemoTeaser(c, DemoReport(tmp_path/'timeout.json'), clock=lambda: clock[0])
    try:
        probe.start()
        probe.timer.stop()
        c.timer.stop()
        before = {cid: (v.x, v.y, v.state) for cid, v in c.engine.visitors.items()}
        assert set(before) == {'amber', 'mist'}
        assert all(v[2] in {'peeking', 'entering'} for v in before.values())
        clock[0] = 36.0
        probe.pulse()
        assert c._teaser_session is None
        assert probe.report['state'] == 'inconclusive'
        assert probe.report['shutdown_verified']
        assert requested_exits == [0]
        assert {cid: (v.x, v.y, v.state) for cid, v in c.engine.visitors.items()} == before
    finally:
        c.shutdown()
        qt.processEvents()


def test_formal_controller_cannot_enter_diagnostic(tmp_path):
    qt = QApplication.instance() or QApplication(['teaser-demo-test'])
    c = app.Controller(qt, state_dir=tmp_path/'state', hidden=True, adapter=DesktopAdapter(), enable_native=False)
    try:
        with pytest.raises(ValueError):
            DemoTeaser(c, None)
        assert not c.engine.gate_open
        assert not c.engine.visitors
    finally:
        c.shutdown()
        qt.processEvents()


def test_valid_cli_selects_same_entry_seed_and_demo_only_module(tmp_path, monkeypatch):
    import backyard_visitor.demo_teaser as demo
    made, callbacks = {}, []
    qt = SimpleNamespace(setApplicationName=lambda name: None, setOrganizationName=lambda name: None, platformName=lambda: "offscreen", exec=lambda: 0)
    monkeypatch.setattr(app, 'QApplication', SimpleNamespace(instance=lambda: qt))
    def controller_factory(qt_arg, **kwargs):
        made['kwargs'] = kwargs
        made['controller'] = SimpleNamespace()
        return made['controller']
    class Probe:
        def __init__(self, controller, writer):
            made['probe_controller'] = controller
            made['writer'] = writer
        def start(self):
            pytest.fail('main must queue, not synchronously run the scene')
    monkeypatch.setattr(app, 'Controller', controller_factory)
    monkeypatch.setattr(demo, 'DemoTeaser', Probe)
    monkeypatch.setattr(app, 'QTimer', SimpleNamespace(singleShot=lambda delay, call: callbacks.append((delay, call))))
    result = app.main(['--demo', '--demo-scene', 'teaser', '--hidden', '--demo-cat-limit', '2',
                       '--demo-teaser-report', str(tmp_path/'report.json')])
    assert result == 0
    assert made['kwargs'] == dict(state_dir=None, demo=True, hidden=True, visit_limit=2, seed=7)
    assert made['probe_controller'] is made['controller']
    assert len(callbacks) == 1 and callbacks[0][0] == 0
    assert made['writer'].path == tmp_path/'report.json'


def test_cleanup_exception_cannot_leave_diagnostic_marked_running(tmp_path, monkeypatch, qt_exit_requests):
    qt, requested_exits = qt_exit_requests
    c = app.Controller(qt, demo=True, hidden=True, adapter=DesktopAdapter(), enable_native=False, seed=7, visit_limit=2)
    original_shutdown = c.shutdown
    probe = DemoTeaser(c, DemoReport(tmp_path/'failed-cleanup.json'))
    try:
        probe.start()
        def broken_shutdown():
            raise RuntimeError('cleanup fixture failure')
        monkeypatch.setattr(c, 'shutdown', broken_shutdown)
        probe.finish(inconclusive='entry failed')
        assert probe.report['state'] == 'failed'
        assert probe.report['shutdown_verified'] is False
        assert requested_exits == [1]
        assert not probe.timer.isActive()
        assert json.loads((tmp_path/'failed-cleanup.json').read_text())['state'] == 'failed'
    finally:
        original_shutdown()
        qt.processEvents()


@pytest.mark.parametrize('duration', [14.6, 14.99])
def test_completed_reason_cannot_hide_short_monotonic_game(duration):
    report = good_report()
    report['active_seconds'] = duration
    assert report['finished_reason'] == 'completed'
    assert not scene_checks(report)['real_full_15_seconds']
