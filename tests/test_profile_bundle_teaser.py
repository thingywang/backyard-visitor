"""Frozen observer tests; never start an application or native window query."""
from pathlib import Path
from scripts.profile_bundle_teaser import command, fingerprints, frozen_assessment, native_coverage, native_metadata
from scripts.profile_multi_teaser import diagnostic_state


def test_launches_actual_bundle_entry_with_isolated_guarded_demo():
    exe = Path('/sample/BackyardVisitor.app/Contents/MacOS/BackyardVisitor')
    result = command(exe, Path('/sample/new.json'))
    assert result[0] == str(exe)
    assert result[1:] == ['--demo', '--demo-scene', 'teaser', '--demo-cat-limit', '2', '--hidden',
                          '--demo-teaser-report', '/sample/new.json', '--smoke-seconds', '56']


def record(pid, wid, rect, alpha=1):
    return {'kCGWindowOwnerPID': pid, 'kCGWindowNumber': wid, 'kCGWindowIsOnscreen': True,
            'kCGWindowAlpha': alpha, 'kCGWindowBounds': dict(zip(('X', 'Y', 'Width', 'Height'), rect)),
            'kCGWindowName': 'Never retained'}


def test_native_observation_only_counts_own_visible_same_display_windows():
    screen = [0, 0, 1920, 1080]
    rows = [record(10, 1, [200, 800, 144, 112]), record(10, 2, [500, 800, 144, 112]),
            record(10, 3, screen), record(20, 4, screen), record(10, 5, screen, 0)]
    value = native_metadata(rows, 10, [screen], screen)
    assert value['cats']['max_count_on_one_display'] == 2
    assert value['screen_sized_window_ids'] == [3]
    assert 'Never retained' not in str(value)
    rows[1]['kCGWindowBounds']['X'] = 1900
    assert native_metadata(rows, 10, [screen], screen)['cats']['max_count_on_one_display'] == 1


def good_native_rows():
    return [{'native_monotonic': 10+i*.25, 'native': {'cats': {'max_count_on_one_display': 2},
             'screen_sized_window_ids': [3]}} for i in range(61)]


def test_sparse_native_coverage_or_one_missing_input_layer_is_inconclusive():
    phases = [{'phase': 'play', 'monotonic': 10}, {'phase': 'cleanup', 'monotonic': 25}]
    rows = good_native_rows()
    assert native_coverage(rows, phases)['valid']
    assert not native_coverage(rows[::4], phases)['valid']
    rows[4]['native']['screen_sized_window_ids'] = []
    assert not native_coverage(rows, phases)['valid']


def test_failed_coverage_clean_exit_or_fingerprints_cannot_pass():
    child = {'state': 'checks_complete', 'shutdown_verified': True}
    assert diagnostic_state(child, 0, True, [], None, {'valid': True}) == 'complete'
    assert diagnostic_state(child, 0, True, [], None, {'valid': False}) == 'inconclusive'
    for code, stable, errors, termination in [(1, True, [], None), (0, False, [], None),
        (0, True, ['sample failed'], None), (0, True, [], 'deadline')]:
        assert diagnostic_state(child, code, stable, errors, termination, {'valid': True}) == 'failed'


def test_rss_all_samples_and_strict_decimal_limit_with_frozen_label():
    assert frozen_assessment([{'rss_bytes': 150000000}], 'complete')['assessment'] == 'within_150MB_in_observed_frozen_demo_scenario'
    assert frozen_assessment([{'rss_bytes': 150000001}], 'complete')['assessment'] == 'exceeded_150MB_in_observed_frozen_demo_scenario'
    value = frozen_assessment([{'rss_bytes': 150000001}], 'inconclusive')
    assert value['assessment'] == 'inconclusive'
    assert value['observed_peak_exceeds_limit'] is True


def test_resource_or_executable_change_alters_bundle_fingerprint(tmp_path):
    (tmp_path/'entry').write_bytes(b'entry')
    (tmp_path/'resource').write_bytes(b'first')
    (tmp_path/'alias').symlink_to(tmp_path/'resource')
    first = fingerprints(tmp_path)
    assert set(first) == {'entry', 'resource', 'alias'}
    (tmp_path/'resource').write_bytes(b'second')
    assert fingerprints(tmp_path) != first


def test_symlink_destination_change_also_changes_bundle_identity(tmp_path):
    (tmp_path/'first').write_bytes(b'same bytes')
    (tmp_path/'second').write_bytes(b'same bytes')
    alias = tmp_path/'alias'
    alias.symlink_to('first')
    original = fingerprints(tmp_path)
    alias.unlink()
    alias.symlink_to('second')
    assert fingerprints(tmp_path) != original


def test_submission_failure_leaves_durable_failed_job(tmp_path, monkeypatch):
    import json
    import scripts.profile_bundle_teaser as observer
    monkeypatch.setattr(observer.sys, 'platform', 'darwin')
    def failed_popen(*args, **kwargs):
        raise OSError('Cannot start test supervisor')
    monkeypatch.setattr(observer.subprocess, 'Popen', failed_popen)
    path = tmp_path/'report.json'
    assert observer.main(['--output', str(path), '--bundle', str(tmp_path/'bundle.app')]) == 1
    job = json.loads(path.with_suffix('.job.json').read_text())
    assert job['state'] == 'failed' and 'supervisor_pid' not in job
    assert not path.exists()


class DisplayAPI:
    def __init__(self, code, reported, rect):
        self.code, self.reported, self.rect = code, reported, rect

    def CGGetActiveDisplayList(self, maximum, ids, count):
        import ctypes
        ctypes.cast(count, ctypes.POINTER(ctypes.c_uint32)).contents.value = self.reported
        return self.code

    def CGDisplayBounds(self, display):
        from types import SimpleNamespace
        x, y, width, height = self.rect
        return SimpleNamespace(origin=SimpleNamespace(x=x, y=y),
                               size=SimpleNamespace(width=width, height=height))


def test_display_failure_does_not_masquerade_as_successful_zero_display_read():
    from types import SimpleNamespace
    from scripts.profile_bundle_teaser import display_snapshot
    error = display_snapshot(SimpleNamespace(cg=DisplayAPI(1000, 0, (0, 0, 1920, 1080))))
    zero = display_snapshot(SimpleNamespace(cg=DisplayAPI(0, 0, (0, 0, 1920, 1080))))
    assert error['bounds'] == zero['bounds'] == []
    assert error['cg_error'] == 1000 and zero['cg_error'] == 0
    invalid = display_snapshot(SimpleNamespace(cg=DisplayAPI(0, 1, (0, 0, 0, 1080))))
    assert invalid['reported_display_count'] == 1 and invalid['valid_display_count'] == 0
    valid = display_snapshot(SimpleNamespace(cg=DisplayAPI(0, 1, (-720, -461, 720, 1280))))
    assert valid['bounds'] == [[-720, -461, 720, 1280]]


def test_early_missing_input_is_reported_and_still_invalidates_whole_play():
    phases = [{'phase': 'play', 'monotonic': 10}, {'phase': 'cleanup', 'monotonic': 25}]
    rows = good_native_rows()
    rows[0]['native']['screen_sized_window_ids'] = []
    coverage = native_coverage(rows, phases)
    assert not coverage['valid']
    assert coverage['observation_failures'] == [
        {'seconds_after_play_started': 0, 'reasons': ['input_window_not_observed']}]
    rows[0]['native']['display_query'] = {'cg_error': 0, 'reported_display_count': 0,
                                         'valid_display_count': 0}
    assert 'zero_active_displays' in native_coverage(rows, phases)['observation_failures'][0]['reasons']
    rows[0]['native']['display_query']['cg_error'] = 1000
    reasons = native_coverage(rows, phases)['observation_failures'][0]['reasons']
    assert 'display_query_error' in reasons and 'zero_active_displays' not in reasons
