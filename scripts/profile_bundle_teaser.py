"""Profile the actual frozen app's opt-in, isolated two-cat 15-second demo.

Default invocation submits a detached supervisor. It samples main-PID RSS every
.25 seconds, corroborates own WindowServer geometry, and owns a 60-second bound.
No measurement library is imported into the application. No OS input is sent.
"""
from __future__ import annotations
import argparse
import ctypes
from datetime import datetime, timezone
import hashlib
import json
import math
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import traceback

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from scripts.probe_overlay_visibility import read_json, write_json, stop_owned
from scripts.check_native_lifecycle import watchdog_reason
from scripts.profile_bundle import own_cat_window_metadata
from scripts.profile_multi_teaser import assess_rss, diagnostic_state, play_coverage, summarize
from scripts.probe_fight_interaction import hashes as base_source_hashes



def source_hashes():
    result = base_source_hashes()
    for name in ('scripts/profile_bundle_teaser.py', 'scripts/profile_multi_teaser.py', 'scripts/profile_bundle.py'):
        result[name] = hashlib.sha256((ROOT/name).read_bytes()).hexdigest()
    return result


def fingerprints(bundle):
    # Includes embedded Python, resources and symlink destinations, without
    # reading aliased file content twice. Runs outside the measured PID.
    files = sorted(p for p in bundle.rglob('*') if p.is_file() or p.is_symlink())
    result = {}
    for path in files:
        if path.is_symlink():
            result[str(path.relative_to(bundle))] = hashlib.sha256(b'symlink\0'+os.readlink(path).encode()).hexdigest()
            continue
        with path.open('rb') as stream:
            digest = hashlib.sha256()
            for chunk in iter(lambda: stream.read(1024*1024), b''):
                digest.update(chunk)
            result[str(path.relative_to(bundle))] = digest.hexdigest()
    return result


def display_snapshot(graphics):
    """Keep a failed native query distinct from a successful zero-display read."""
    ids = (ctypes.c_uint32 * 32)()
    count = ctypes.c_uint32()
    code = int(graphics.cg.CGGetActiveDisplayList(32, ids, ctypes.byref(count)))
    bounds = []
    if code == 0:
        for display in ids[:min(count.value, 32)]:
            rect = graphics.cg.CGDisplayBounds(display)
            values = [rect.origin.x, rect.origin.y, rect.size.width, rect.size.height]
            if all(math.isfinite(value) for value in values) and values[2] > 0 and values[3] > 0:
                bounds.append(values)
    return {'cg_error': code, 'reported_display_count': int(count.value),
            'valid_display_count': len(bounds), 'bounds': bounds}


def native_sample(graphics, pid, screen):
    displays = display_snapshot(graphics)
    records = graphics.window_info()
    result = native_metadata(records, pid, displays['bounds'], screen)
    result['display_query'] = {key: value for key, value in displays.items() if key != 'bounds'}
    result['window_record_count'] = len(records)
    return result


def native_metadata(records, pid, displays, screen):
    cats = own_cat_window_metadata(records, pid, displays)
    canvases = []
    for row in records:
        if row.get('kCGWindowOwnerPID') != pid or not row.get('kCGWindowIsOnscreen'):
            continue
        bounds = row.get('kCGWindowBounds', {})
        try:
            rect = [float(bounds[k]) for k in ('X', 'Y', 'Width', 'Height')]
            if float(row.get('kCGWindowAlpha', 0)) > 0 and len(screen) == 4 and all(abs(a-b) <= .5 for a, b in zip(rect, screen)):
                canvases.append(int(row['kCGWindowNumber']))
        except (TypeError, ValueError, KeyError, OverflowError):
            continue
    return {'cats': cats, 'screen_sized_window_ids': sorted(set(canvases)),
            'scope': 'Own-PID numeric window metadata; not pixels, occlusion, or physical input routing.'}


def native_coverage(rows, phases):
    # Native queries finish slightly after the RSS read, so use their own clock.
    start = next((p['monotonic'] for p in phases if p['phase'] == 'play'), None)
    end = next((p['monotonic'] for p in phases if p['phase'] == 'cleanup'), None)
    active = [row for row in rows if start is not None and end is not None
              and start <= row.get('native_monotonic', -1) <= end]
    coverage = play_coverage([{'monotonic': row['native_monotonic']} for row in active], phases)
    # A missing native observation cannot be repaired by later good frames.
    coverage['two_cat_windows_and_input_visible'] = bool(active) and all(
        row.get('native', {}).get('cats', {}).get('max_count_on_one_display', 0) >= 2
        and bool(row.get('native', {}).get('screen_sized_window_ids')) for row in active)
    coverage['valid'] &= coverage['two_cat_windows_and_input_visible']
    failures = []
    for row in active:
        native = row.get('native', {})
        reasons = []
        query = native.get('display_query', {})
        if query.get('cg_error', 0) != 0:
            reasons.append('display_query_error')
        elif query.get('reported_display_count') == 0:
            reasons.append('zero_active_displays')
        elif query.get('valid_display_count') == 0:
            reasons.append('no_valid_display_bounds')
        if native.get('cats', {}).get('max_count_on_one_display', 0) < 2:
            reasons.append('fewer_than_two_contained_cat_windows')
        if not native.get('screen_sized_window_ids'):
            reasons.append('input_window_not_observed')
        if reasons:
            failures.append({'seconds_after_play_started': row['native_monotonic']-start,
                             'reasons': reasons})
    coverage['observation_failures'] = failures
    coverage['scope'] = 'Every active sample must still pass; diagnostics never excuse a missing window.'
    return coverage


def frozen_assessment(rows, state):
    result = assess_rss(rows, state)
    result['assessment'] = result['assessment'].replace('_source_scenario', '_frozen_demo_scenario')
    return result


def command(executable, app_report):
    return [str(executable), '--demo', '--demo-scene', 'teaser', '--demo-cat-limit', '2',
            '--hidden', '--demo-teaser-report', str(app_report), '--smoke-seconds', '56']


def supervise(options):
    import psutil
    from backyard_visitor.desktop import _CoreGraphics
    process = None
    app_report = options.output.with_suffix('.app.json')
    report = {'schema_version': 1, 'state': 'running', 'supervisor_pid': os.getpid(),
        'started_utc': datetime.now(timezone.utc).isoformat(), 'samples': [], 'sampling_errors': [],
        'termination_reason': None, 'hard_deadline_seconds': 60,
        'metric': 'Actual frozen application main PID RSS, external .25-second sampling; decimal MB; no footprint substitution.',
        'scope': 'Normal frozen entry with opt-in demo fixture and small JSON observations; no single-cat claim, screenshots, or physical input proof.'}
    # Reserve before any launch. Existing app/outer reports are never reused.
    with options.output.open('x', encoding='utf-8') as stream:
        json.dump(report, stream)
    started = None
    try:
        if app_report.exists():
            raise FileExistsError(app_report)
        executable = options.bundle/'Contents/MacOS/BackyardVisitor'
        if not executable.is_file():
            raise FileNotFoundError(executable)
        report['bundle'] = str(options.bundle)
        report['bundle_hashes'] = fingerprints(options.bundle)
        report['source_hashes'] = source_hashes()
        report['observer_sha256'] = hashlib.sha256(Path(__file__).read_bytes()).hexdigest()
        graphics = _CoreGraphics()
        with tempfile.TemporaryDirectory(prefix='frozen-teaser-', dir=options.output.parent) as temporary:
            environment = dict(os.environ, QT_QPA_PLATFORM='cocoa', TMPDIR=temporary)
            started = time.monotonic()
            report['popen_monotonic'] = started
            process = subprocess.Popen(command(executable, app_report), cwd=ROOT, env=environment,
                                       stdin=subprocess.DEVNULL, stdout=sys.stdout, stderr=subprocess.STDOUT)
            report['child_pid'] = process.pid
            observed = psutil.Process(process.pid)
            report['child_create_time'] = observed.create_time()
            next_sample = started
            while process.poll() is None:
                data, now = read_json(app_report), time.monotonic()
                beat = data.get('heartbeat_monotonic')
                if now >= next_sample:
                    try:
                        sample = {'monotonic': now, 'seconds_after_popen': now-started,
                                  'rss_bytes': observed.memory_info().rss}
                        sample['native_query_started_monotonic'] = time.monotonic()
                        sample['native'] = native_sample(graphics, process.pid,
                                                         data.get('screen', {}).get('geometry', []))
                        sample['native_monotonic'] = time.monotonic()
                        report['samples'].append(sample)
                    except psutil.NoSuchProcess:
                        break
                    except (psutil.Error, OSError, ValueError, TypeError) as failure:
                        report['sampling_errors'].append(str(failure))
                    next_sample = now+.25
                reason = watchdog_reason(now-started, None if beat is None else now-beat, 57,
                                         startup_grace=15, heartbeat_timeout=5)
                report.update(child=data, elapsed_seconds=now-started,
                              heartbeat_monotonic=now, heartbeat_age_seconds=None if beat is None else now-beat)
                write_json(options.output, report)
                if reason:
                    report['termination_reason'] = reason
                    break
                time.sleep(.025)
            stop_owned(process)
            report['child_exit_code'] = process.wait(timeout=1)
            report['owned_process_seconds'] = time.monotonic()-started
            report['child'] = data = read_json(app_report)
        report['bundle_hashes_after'] = fingerprints(options.bundle)
        report['source_hashes_after'] = source_hashes()
        report['bundle_unchanged'] = report['bundle_hashes'] == report['bundle_hashes_after']
        report['sources_unchanged'] = report['source_hashes'] == report['source_hashes_after']
        phases = data.get('phase_events', [])
        for row in report['samples']:
            applicable = [p for p in phases if p['monotonic'] <= row['monotonic']]
            row['phase'] = applicable[-1]['phase'] if applicable else 'startup'
        report['play_rss_coverage'] = play_coverage(report['samples'], phases)
        report['native_coverage'] = native_coverage(report['samples'], phases)
        identity = data.get('frozen') is True and data.get('pid') == process.pid and Path(data.get('executable', '')).resolve() == executable.resolve()
        report['frozen_identity_verified'] = identity
        report['state'] = diagnostic_state(data, process.returncode,
            report['sources_unchanged'] and report['bundle_unchanged'] and identity,
            report['sampling_errors'], report['termination_reason'], report['play_rss_coverage'])
        if report['state'] == 'complete' and not report['native_coverage']['valid']:
            report['state'] = 'inconclusive'
        report['by_phase'] = summarize(report['samples'])
        report['rss_assessment'] = frozen_assessment(report['samples'], report['state'])
    except Exception:
        report.update(state='failed', error=traceback.format_exc())
    finally:
        stop_owned(process)
        if process is not None:
            report['child_exit_code'] = process.poll()
        report.update(elapsed_seconds=time.monotonic()-started if started else None,
                      finished_utc=datetime.now(timezone.utc).isoformat())
        write_json(options.output, report)
    print(json.dumps({'state': report['state'], 'rss': report.get('rss_assessment')}), flush=True)
    return 0 if report['state'] in {'complete', 'inconclusive'} else 1


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--bundle', type=Path, default=ROOT/'dist/BackyardVisitor.app')
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--supervisor', action='store_true', help=argparse.SUPPRESS)
    options = parser.parse_args(argv)
    if sys.platform != 'darwin':
        parser.error('Cocoa macOS required')
    options.output = options.output.expanduser().absolute()
    options.bundle = options.bundle.expanduser().resolve()
    options.output.parent.mkdir(parents=True, exist_ok=True)
    if options.supervisor:
        return supervise(options)
    job_path, log = options.output.with_suffix('.job.json'), options.output.with_suffix('.log')
    for path in (options.output, job_path, log, options.output.with_suffix('.app.json')):
        if path.exists():
            parser.error(f'Diagnostic output already exists: {path}')
    # Claim the job path atomically so concurrent submissions cannot share it.
    with job_path.open('x', encoding='utf-8') as stream:
        json.dump({'state': 'preparing'}, stream)
    try:
        with log.open('x', encoding='utf-8') as stream:
            process = subprocess.Popen([sys.executable, '-B', '-u', str(Path(__file__).resolve()), '--supervisor',
                '--bundle', str(options.bundle), '--output', str(options.output)], cwd=ROOT,
                stdin=subprocess.DEVNULL, stdout=stream, stderr=subprocess.STDOUT, start_new_session=True)
    except OSError as failure:
        write_json(job_path, {'state': 'failed', 'error': str(failure), 'report': str(options.output)})
        print(f'Diagnostic submission failed: {failure}', file=sys.stderr)
        return 1
    job = {'state': 'submitted', 'supervisor_pid': process.pid, 'report': str(options.output),
           'app_report': str(options.output.with_suffix('.app.json')), 'log': str(log),
           'submitted_utc': datetime.now(timezone.utc).isoformat()}
    write_json(job_path, job)
    print(json.dumps(job), flush=True)
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
