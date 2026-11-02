"""Isolated source-controller teaser acceptance probe (never moves the cursor).

Default Cocoa mode temporarily captures clicks on the selected screen for one
real 15-second game. Inform the user before launching; right-click cancels it.
The independent supervisor returns immediately and bounds its own child to 38s.
No permission requests, native desktop adapters, memory sampling, or external
app activation are used. --interruptions-only is restricted to offscreen Qt.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import traceback

ROOT = Path(__file__).resolve().parents[1]
TIMEOUT = 38.0
sys.path.insert(0, str(ROOT))
from scripts.probe_overlay_visibility import read_json, write_json, stop_owned


def source_hashes():
    files = sorted((ROOT / 'backyard_visitor').glob('*.py'))
    files += [Path(__file__).resolve(), ROOT / 'scripts/probe_overlay_visibility.py']
    return {str(p.relative_to(ROOT)):hashlib.sha256(p.read_bytes()).hexdigest() for p in files}


def final_state(child, exit_code, timed_out, unchanged):
    return ('complete' if child.get('state') == 'checks_complete' and exit_code == 0
            and not timed_out and unchanged else 'failed')


def watchdog_reason(elapsed, heartbeat_age):
    if elapsed > TIMEOUT:
        return 'overall_timeout'
    if heartbeat_age is None and elapsed > 12:
        return 'startup_without_checkpoint'
    if heartbeat_age is not None and heartbeat_age > 4:
        return 'event_loop_heartbeat_stale'
    return None


def rest_observation(visitor):
    """Only explicit fatigue/rest evidence counts; ordinary idle never does."""
    if visitor is None:
        return {'arrived':False, 'pending':False, 'state':None}
    state, arrival = getattr(visitor,'state',None), getattr(visitor,'arrival_state',None)
    clocks = {key:getattr(visitor,key) for key in
              ('rest_remaining','teaser_rest_remaining','play_rest_remaining','fatigue_remaining')
              if isinstance(getattr(visitor,key,None),(int,float))}
    return {'arrived':state == 'resting',
            'pending':arrival == 'resting',
            'state':state, 'arrival_state':arrival, 'clocks':clocks}


def natural_checks(case):
    samples = case.get('samples',[])
    active = [s for s in samples if s.get('session_active')]
    return {
        'real_15_second_completion':case.get('reason') == 'completed' and 14.5 <= case.get('active_seconds',0) <= 18,
        'observed_multiple_native_ticks':len(active) >= 20,
        'no_global_cursor_override':bool(samples) and all(s.get('global_override_none') for s in samples),
        'local_cursor_bitmap_seen':any(s.get('cursor_pixmap') for s in active),
        'canvas_visible_during_play':bool(active) and all(s.get('canvas_visible') for s in active),
        'canvas_never_qt_active_window':bool(samples) and not any(s.get('canvas_is_active_window') for s in samples),
        'native_canvas_visible_and_not_key':case.get('platform') != 'cocoa' or (bool(active) and all(
            s.get('native_canvas',{}).get('native_visible') is True
            and s.get('native_canvas',{}).get('key') is False for s in active)),
        'model_and_session_agreed_during_play':bool(active) and all(s.get('model_active_id') == 'amber' for s in active),
        'session_stopped':case.get('final',{}).get('session_active') is False,
        'model_stopped':case.get('final',{}).get('model_active_id') is None,
        'canvas_removed':case.get('final',{}).get('canvas_visible') is False,
        'timer_stopped':case.get('final',{}).get('timer_active') is False,
        'model_rest_arrived':case.get('final',{}).get('rest',{}).get('arrived') is True,
        'no_session_error':all(s.get('session_error') is None for s in samples),
    }


def interruption_checks(case):
    final = case.get('final',{})
    return {'started':bool(case.get('samples')) and case['samples'][0].get('session_active') is True,
            'ended_before_natural_deadline':case.get('active_seconds',99) < 3,
            'right_click_is_cancelled':case.get('name') != 'right_click' or case.get('reason') == 'cancelled',
            'session_stopped':final.get('session_active') is False,
            'model_stopped':final.get('model_active_id') is None,
            'canvas_removed':final.get('canvas_visible') is False,
            'timer_stopped':final.get('timer_active') is False,
            'global_override_none':final.get('global_override_none') is True,
            'no_session_error':final.get('session_error') is None}


def run_child(options):
    from PyQt6 import sip
    from PyQt6.QtCore import QEvent, QPoint, QPointF, QRect, QTimer, Qt
    from PyQt6.QtGui import QCursor, QMouseEvent, QWindow
    from PyQt6.QtWidgets import QApplication
    from backyard_visitor.app import Controller
    from backyard_visitor.desktop import DesktopAdapter
    from scripts.probe_overlay_visibility import Cocoa

    qt = QApplication(['BackyardTeaserAcceptance'])
    qt.setQuitOnLastWindowClosed(False)
    cocoa = Cocoa() if options.platform == 'cocoa' else None
    report = {'state':'starting', 'pid':os.getpid(), 'qt_platform':qt.platformName(), 'cases':[],
              'timing':'Only initial entry is logically accelerated. Active games use the real Qt event loop.',
              'cursor_policy':'QCursor.pos is read only. No warp, global override, or OS mouse injection.',
              'max_observer_gap_seconds':0.0}
    names = ['right_click','pause','close_gate'] if options.interruptions_only else ['natural']
    context = {'controller':None, 'session':None, 'canvas':None, 'case':None, 'stage':'setup',
               'index':0, 'started':0.0, 'ended':None, 'prepare_until':0.0}
    started, last_pulse = time.monotonic(), time.monotonic()

    def checkpoint():
        report['heartbeat_monotonic'] = time.monotonic()
        report['elapsed_seconds'] = time.monotonic()-started
        write_json(options.child,report)

    def snapshot():
        c,s,w = context['controller'],context['session'],context['canvas']
        canvas_alive = w is not None and not sip.isdeleted(w)
        timer_alive = s is not None and not sip.isdeleted(s.timer)
        result = {'elapsed_seconds':time.monotonic()-context['started'],
                  'session_active':s.active if s else False,
                  'remaining':s.remaining if s else 0,
                  'session_error':s.error if s else None,
                  'model_active_id':getattr(c.engine,'active_teaser_id',None),
                  'model_teaser_phase':getattr(c.engine,'teaser_phase',None),
                  'canvas_visible':canvas_alive and w.isVisible(),
                  'cursor_pixmap':canvas_alive and not w.cursor().pixmap().isNull(),
                  'global_override_none':QApplication.overrideCursor() is None,
                  'canvas_is_active_window':canvas_alive and QApplication.focusWindow() is (w if isinstance(w,QWindow) else w.windowHandle()),
                  'timer_active':timer_alive and s.timer.isActive(),
                  'rest':rest_observation(c.engine.visitors.get('amber'))}
        if canvas_alive and cocoa is not None:
            result['native_canvas'] = cocoa.describe(w)
        return result

    def finish(error=None):
        timer.stop()
        report['error'] = error
        if context['controller'] is not None:
            context['controller'].shutdown()
        checks = [value for case in report['cases'] for value in case.get('checks',{}).values()]
        report['state'] = 'checks_complete' if error is None and checks and all(checks) and len(report['cases']) == len(names) else 'failed'
        report['shutdown_completed'] = context['controller'] is None or context['controller']._quitting
        checkpoint()
        qt.quit()

    def prepare():
        c = Controller(qt,demo=True,hidden=True,adapter=DesktopAdapter(),enable_native=False,seed=31,visit_limit=1)
        context['controller'] = c
        # Select the display already containing the real pointer; never warp it
        # or change an OS display setting. This is only the isolated demo's choice.
        for index, screen in enumerate(qt.screens()):
            if screen.geometry().contains(QCursor.pos()):
                c.change_screen(index)
                break
        c.set_paused(True)
        c.engine.open_gate();c.engine.invite('amber')
        ticks = 0
        while c.engine.visitors['amber'].state in {'peeking','entering'} and ticks < 800:
            c.engine.tick(.05);ticks += 1
        if c.engine.visitors['amber'].state != 'idle':
            raise RuntimeError('Logical entry did not reach idle')
        c.changed()
        case = {'name':names[context['index']], 'platform':qt.platformName(), 'logical_entry_seconds':ticks*.05, 'samples':[]}
        context.update(case=case,stage='prepared',prepare_until=time.monotonic()+.5,session=None,canvas=None,ended=None)
        report['cases'].append(case)
        report['state'] = 'running'

    def start_case():
        c,case = context['controller'],context['case']
        c.set_paused(False)
        c.start_play('amber')
        session = getattr(c,'_teaser_session',None)
        if session is None or not session.active:
            raise RuntimeError('Controller.start_play did not create an active _teaser_session')
        context.update(session=session,canvas=session._canvas,started=time.monotonic(),stage='active')
        def ended(reason):
            context['ended'] = time.monotonic()
            case['reason'] = reason
            case['active_seconds'] = context['ended']-context['started']
        session.finished.connect(ended)
        case['samples'].append(snapshot())

    def interrupt_case():
        c,case = context['controller'],context['case']
        if case['name'] == 'right_click':
            w = context['canvas']
            local = QPointF(QRect(QPoint(),w.size()).center())
            global_pos = QPointF(w.mapToGlobal(local.toPoint()))
            event = QMouseEvent(QEvent.Type.MouseButtonPress,local,global_pos,
                                Qt.MouseButton.RightButton,Qt.MouseButton.RightButton,Qt.KeyboardModifier.NoModifier)
            QApplication.sendEvent(w,event)  # Offscreen only; no system mouse event.
        elif case['name'] == 'pause':
            c.set_paused(True)
        else:
            c.toggle_gate()
        context['stage'] = 'interrupted'

    def pulse():
        nonlocal last_pulse
        try:
            now=time.monotonic()
            report['max_observer_gap_seconds'] = max(report['max_observer_gap_seconds'],now-last_pulse)
            last_pulse=now
            if now-started > 34:
                finish('Child deadline exceeded');return
            if context['stage']=='setup':
                prepare()
            elif context['stage']=='prepared':
                if now>=context['prepare_until']:start_case()
            else:
                case=context['case'];sample=snapshot();case['samples'].append(sample)
                if options.interruptions_only and context['stage']=='active' and now-context['started'] >= .3:
                    interrupt_case()
                if not context['session'].active:
                    # Root may deleteLater the session on finished; its simple
                    # Python properties remain available, Qt children are guarded.
                    if context['ended'] is None:
                        raise RuntimeError('Inactive session did not emit finished')
                    elapsed_since_end=now-context['ended']
                    rest_ready=sample['rest']['arrived']
                    if options.interruptions_only or rest_ready or elapsed_since_end>8:
                        case['final']=snapshot()
                        case['checks']=interruption_checks(case) if options.interruptions_only else natural_checks(case)
                        if not all(case['checks'].values()):
                            finish('Acceptance checks failed: '+', '.join(k for k,v in case['checks'].items() if not v));return
                        context['controller'].shutdown()
                        context['controller'].deleteLater()
                        context['controller']=None
                        context['index']+=1
                        if context['index']==len(names):finish();return
                        context['stage']='setup'
            checkpoint()
        except Exception:
            finish(traceback.format_exc())

    timer=QTimer();timer.setInterval(100);timer.timeout.connect(pulse);timer.start()
    checkpoint()
    qt.exec()
    return 0 if report['state']=='checks_complete' else 1


def supervise(options):
    start=time.monotonic();before=source_hashes();process=None
    report={'schema_version':1,'state':'running','supervisor_pid':os.getpid(),
            'started_utc':datetime.now(timezone.utc).isoformat(),'source_hashes':before,
            'platform':options.platform,'mode':'interruptions_only' if options.interruptions_only else 'real_15_seconds',
            'limitations':'Source-controller bounded run, not a frozen app, screenshot, memory profile or long-term endurance test.'}
    try:
        if options.platform=='cocoa' and sys.platform!='darwin':raise RuntimeError('Cocoa requires macOS')
        with tempfile.TemporaryDirectory(prefix='backyard-teaser-probe-') as folder:
            child=Path(folder)/'child.json'
            args=[sys.executable,__file__,'--child',str(child),'--platform',options.platform]
            if options.interruptions_only:args.append('--interruptions-only')
            process=subprocess.Popen(args,cwd=ROOT,env=dict(os.environ,QT_QPA_PLATFORM=options.platform,PYTHONDONTWRITEBYTECODE='1'))
            report['child_pid']=process.pid
            reason=None
            while process.poll() is None:
                data=read_json(child);now=time.monotonic()
                report['child']=data
                heartbeat=data.get('heartbeat_monotonic')
                reason=watchdog_reason(now-start,None if heartbeat is None else now-heartbeat)
                report['elapsed_seconds']=now-start;write_json(options.output,report)
                if reason:break
                time.sleep(.2)
            stop_owned(process)
            report['child']=read_json(child)
            report['child_exit_code']=process.returncode
            report['termination_reason']=reason
            report['sources_unchanged']=before==source_hashes()
            report['state']=final_state(report['child'],process.returncode,reason is not None,report['sources_unchanged'])
    except Exception:
        report['state'],report['error']='failed',traceback.format_exc()
    finally:
        stop_owned(process)
        report['elapsed_seconds']=time.monotonic()-start
        report['finished_utc']=datetime.now(timezone.utc).isoformat()
        write_json(options.output,report)
    return 0 if report['state']=='complete' else 1


def parse_args(argv=None):
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output',type=Path,default=ROOT/'output/v07/teaser-native.json')
    parser.add_argument('--platform',choices=('cocoa','offscreen'),default='cocoa')
    parser.add_argument('--interruptions-only',action='store_true',help='Offscreen right-click, pause and gate-close checks only')
    parser.add_argument('--supervisor',action='store_true',help=argparse.SUPPRESS)
    parser.add_argument('--child',type=Path,help=argparse.SUPPRESS)
    options=parser.parse_args(argv)
    if options.interruptions_only and options.platform!='offscreen':parser.error('--interruptions-only requires --platform offscreen')
    options.output=options.output.resolve()
    return options


def main():
    options=parse_args()
    if options.child:return run_child(options)
    options.output.parent.mkdir(parents=True,exist_ok=True)
    if options.supervisor:return supervise(options)
    args=[sys.executable,__file__,'--supervisor','--output',str(options.output),'--platform',options.platform]
    if options.interruptions_only:args.append('--interruptions-only')
    log=options.output.with_suffix('.log')
    with log.open('a') as stream:
        process=subprocess.Popen(args,cwd=ROOT,stdout=stream,stderr=subprocess.STDOUT,start_new_session=True)
    print(json.dumps({'state':'launched','supervisor_pid':process.pid,'output':str(options.output),'log':str(log)}))
    return 0


if __name__=='__main__':
    raise SystemExit(main())
