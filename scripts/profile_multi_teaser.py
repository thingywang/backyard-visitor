"""One bounded source Cocoa two-cat teaser RSS diagnostic.

Natural entry only; a full 15-second active game temporarily receives clicks on
the selected screen (right click cancels). Parent RSS sampling starts with Popen,
every 0.25 seconds, without importing psutil into the child. No production files,
OS pointer, permissions or formal saved progress are changed. Ineligible entry
or manual cancellation is inconclusive, never forced into a passing case.
"""
from __future__ import annotations
import argparse
from datetime import datetime,timezone
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import traceback

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from scripts.probe_overlay_visibility import read_json,write_json,stop_owned
from scripts.check_native_lifecycle import watchdog_reason
from scripts.probe_fight_interaction import hashes as base_hashes


def hashes():
    result=base_hashes();result[str(Path(__file__).relative_to(ROOT))]=hashlib.sha256(Path(__file__).read_bytes()).hexdigest();return result


def run_child(options):
    from PyQt6 import sip
    from PyQt6.QtCore import QRect,QTimer
    from PyQt6.QtWidgets import QApplication
    from backyard_visitor.app import Controller
    from backyard_visitor.desktop import DesktopAdapter
    from scripts.probe_overlay_visibility import Cocoa
    tempfile.tempdir=str(options.child.parent)
    qt=QApplication(['BackyardMultiTeaserRSS']);qt.setQuitOnLastWindowClosed(False)
    c=Controller(qt,demo=True,hidden=True,adapter=DesktopAdapter(),enable_native=False,seed=7,visit_limit=2)
    c.engine.calm_mode=c.settings['calm_mode']=True
    for p in c.engine.cats.values():p.trust=60
    c.engine.open_gate();c.engine.invite('amber');c.engine.invite('mist');c.changed()
    cocoa=Cocoa();started=time.monotonic();running=True;session=None;canvas=None
    def rectangle(r):return [r.x(),r.y(),r.width(),r.height()]
    report={'state':'running','phase':'entry','pid':os.getpid(),'samples':[],'captures':[],'phase_events':[{'phase':'entry','monotonic':started}],
        'screen':{'geometry':rectangle(c.screen.geometry()),'availableGeometry':rectangle(c.screen.availableGeometry()),'devicePixelRatio':c.screen.devicePixelRatio()},
        'fixture':'seed7, visit_limit2, trust60, calm mode prevents fights. Two invite calls; no injected model tick/xy/state. Start only when both naturally entered fully and one eligible.',
        'scope':'Source Cocoa main PID; adapters disabled; natural states remain variable. Widget grabs are own small renders (not desktop screenshots) and their overhead is included. No frozen-build conclusion.',
        'no_global_override':True,'no_journal':True,'clock_acceleration_seen':False}
    frames=options.output.parent/(options.output.stem+'-frames');frames.mkdir(parents=True,exist_ok=True)
    def alive(widget):return widget is not None and not sip.isdeleted(widget)
    def change(phase, at=None):
        report['phase']=phase;report['phase_events'].append({'phase':phase,'monotonic':time.monotonic() if at is None else at})
    def checkpoint():
        now=time.monotonic();report.update(heartbeat_monotonic=now,elapsed_seconds=now-started);write_json(options.child,report)
    def cats():
        rows={}
        for cid,v in c.engine.visitors.items():
            w=c.cat_windows.get(cid)
            native=cocoa.describe(w) if w is not None and w.isVisible() else None
            rows[cid]={'state':v.state,'arrival_state':v.arrival_state,'x':v.x,'y':v.y,'qt_visible':w is not None and w.isVisible(),
                'qt_geometry':rectangle(w.geometry()) if w is not None else None,'native':native,
                'fully_inside':bool(w is not None and c.screen.geometry().contains(w.geometry()) and v.state not in {'peeking','entering','leaving','fleeing'})}
        return rows
    def full_pair(rows):
        return set(rows)=={'amber','mist'} and all(r['fully_inside'] and r['qt_visible'] and r['native'] and r['native']['native_visible'] for r in rows.values())
    def capture(label):
        for cid,w in c.cat_windows.items():
            path=frames/(label+'-'+cid+'.png');pix=w.grab()
            if pix.isNull() or not pix.save(str(path),'PNG'):raise RuntimeError('Cat grab failed')
            report['captures'].append({'label':label,'cat_id':cid,'path':str(path),'sha256':hashlib.sha256(path.read_bytes()).hexdigest(),'pixel_size':[pix.width(),pix.height()],'dpr':pix.devicePixelRatio(),'monotonic':time.monotonic()})
        if alive(canvas):
            hint=getattr(session,'_hint',None)
            if alive(hint):
                pix=hint.grab();kind='hint_widget'
                rect=hint.geometry()
            else:
                rect=QRect(max(0,(canvas.width()-360)//2),8,min(360,canvas.width()),80)
                pix=canvas.grab(rect);kind='canvas_hint_crop'
            path=frames/(label+'-canvas-hint.png')
            if pix.isNull() or not pix.save(str(path),'PNG'):raise RuntimeError('Hint grab failed')
            report['captures'].append({'label':label,'kind':kind,'rect':rectangle(rect),'path':str(path),'sha256':hashlib.sha256(path.read_bytes()).hexdigest(),'pixel_size':[pix.width(),pix.height()],'monotonic':time.monotonic()})
    def finish(error=None,inconclusive=None):
        nonlocal running
        if not running:return
        running=False;timer.stop()
        if error:report['error']=str(error)
        if inconclusive:report['inconclusive_reason']=inconclusive
        c.shutdown()
        report['shutdown_verified']=c._quitting and c._teaser_session is None and not c._workers and c.pool.activeThreadCount()==0 and not any(t.isActive() for t in (c.timer,c.native_timer,c.save_timer,c.recycle_timer)) and not any(w.isVisible() for w in [c.gate,c.bowl,c.bin,*c.cat_windows.values()]) and (not alive(canvas) or not canvas.isVisible())
        report['state']=('failed' if error or not report['shutdown_verified'] else 'inconclusive' if inconclusive else 'checks_complete' if report.get('checks') and all(report['checks'].values()) else 'failed')
        checkpoint();qt.exit(0 if report['state'] in ('checks_complete','inconclusive') else 1)
    def ended(reason):
        report['finished_reason']=reason;report['play_ended_monotonic']=time.monotonic();report['active_seconds']=report['play_ended_monotonic']-report['play_started_monotonic'];change('cleanup')
    def pulse():
        nonlocal session,canvas
        if not running:return
        try:
            now=time.monotonic();elapsed=now-started
            if elapsed>56:finish('Child deadline exceeded');return
            rows=cats();pair=full_pair(rows)
            report['no_global_override'] &= qt.overrideCursor() is None;report['no_journal'] &= c._journal is None
            report['clock_acceleration_seen'] |= c.engine._time>elapsed+1
            sample={'monotonic':now,'elapsed_seconds':elapsed,'phase':report['phase'],'cats':rows,'two_fully_visible':pair,'model_active_id':c.engine.active_teaser_id,'model_teaser_phase':c.engine.teaser_phase}
            if alive(canvas):sample['canvas']={'geometry':rectangle(canvas.geometry()),'devicePixelRatio':canvas.devicePixelRatio(),'native':cocoa.describe(canvas),'local_cursor_bitmap':not canvas.cursor().pixmap().isNull()}
            report['samples'].append(sample)
            if report['phase']=='entry':
                eligible=[cid for cid,v in c.engine.visitors.items() if v.state in {'idle','sleeping','purring','walking','running','playing','resting'} and v.route_motion not in {'leaving','fleeing'} and v.posture_remaining<=0 and not v.carrying_gift and not v.demo_gift_pending and not v.true_name_pending and v.true_name_remaining<=0]
                if pair and eligible:
                    selected='amber' if 'amber' in eligible else eligible[0];report['selected_id']=selected;report['entry_two_cat_observation']=sample
                    capture('before')
                    if not c.start_play(selected):finish(inconclusive='Natural pair observed but Controller rejected start_play');return
                    session=c._teaser_session;canvas=session._canvas
                    report['play_started_monotonic']=session._started;change('play',session._started);session.finished.connect(ended)
                    report['canvas_initial']={'geometry':rectangle(canvas.geometry()),'devicePixelRatio':canvas.devicePixelRatio(),'native':cocoa.describe(canvas)}
                elif elapsed>=35:finish(inconclusive='No eligible fully visible two-cat pair within 35 real seconds');return
            elif report['phase']=='play':
                if now-report['play_started_monotonic']>=7 and not report.get('mid_grab'):
                    capture('middle');report['mid_grab']=True
                if not session.active and 'play_ended_monotonic' not in report:finish('Session stopped without finished signal');return
            elif report['phase']=='cleanup':
                since=now-report['play_ended_monotonic'];v=c.engine.visitors.get(report['selected_id'])
                report['rest_arrived']=report.get('rest_arrived',False) or bool(v and v.state=='resting')
                report['rest_observation']={'state':v.state if v else None,'arrival_state':v.arrival_state if v else None}
                if since>=3 and (report['rest_arrived'] or since>=5):
                    active=[r for r in report['samples'] if r['phase']=='play']
                    timer_stopped=not alive(session.timer) or not session.timer.isActive()
                    report['checks']={'real_full_15_seconds':report.get('finished_reason')=='completed' and 14.5<=report.get('active_seconds',0)<=17,
                        'two_cats_observed_through_active_samples':len(active)>=40 and all(r['two_fully_visible'] for r in active),
                        'canvas_visible_during_active_samples':bool(active) and all(r.get('canvas',{}).get('native',{}).get('native_visible') for r in active),
                        'session_model_canvas_and_timer_cleared':c._teaser_session is None and c.engine.active_teaser_id is None and (not alive(canvas) or not canvas.isVisible()) and timer_stopped,
                        'rest_arrived':report['rest_arrived'],'small_before_and_middle_grabs':report.get('mid_grab') is True and len(report['captures'])>=5,
                        'no_global_cursor_or_journal':report['no_global_override'] and report['no_journal'],'normal_tick_only':not report['clock_acceleration_seen']}
                    if report.get('finished_reason')!='completed':finish(inconclusive='Game interrupted: '+str(report.get('finished_reason')));return
                    if not report['checks']['two_cats_observed_through_active_samples']:finish(inconclusive='Active samples did not maintain actual two-cat visibility');return
                    finish();return
            checkpoint()
        except Exception:finish(traceback.format_exc())
    timer=QTimer();timer.setInterval(250);timer.timeout.connect(pulse);timer.start();checkpoint();code=qt.exec()
    if running:finish('Unexpected Qt exit');return 1
    return code


def summarize(rows):
    result={}
    for phase in sorted({r['phase'] for r in rows}):
        values=[r['rss_bytes'] for r in rows if r['phase']==phase]
        result[phase]={'samples':len(values),'peak_rss_bytes':max(values),'mean_rss_bytes':sum(values)/len(values)}
    return result



def assess_rss(rows, state):
    values = [r.get('rss_bytes') for r in rows]
    valid = bool(values) and all(type(v) is int and v >= 0 for v in values)
    peak = max(values) if valid else None
    # Retain partial-run measurements, but do not label an unproven two-cat
    # play scenario as passing or failing its 150 MB requirement.
    assessment = 'inconclusive'
    if valid and state == 'complete':
        assessment = ('exceeded_150MB_in_observed_source_scenario' if peak > 150000000
                      else 'within_150MB_in_observed_source_scenario')
    return {'limit_bytes':150000000, 'peak_rss_bytes':peak,
            'observed_peak_exceeds_limit':peak > 150000000 if peak is not None else None,
            'assessment':assessment}



def play_coverage(rows, phases):
    start = next((p['monotonic'] for p in phases if p['phase']=='play'), None)
    end = next((p['monotonic'] for p in phases if p['phase']=='cleanup'), None)
    result = {'valid':False, 'sample_count':0, 'span_seconds':0.0, 'max_gap_seconds':None}
    if start is None or end is None or end <= start:
        return result
    times = sorted(r['monotonic'] for r in rows if start <= r['monotonic'] <= end)
    points = [start, *times, end]
    gap = max(b-a for a,b in zip(points, points[1:]))
    span = times[-1]-times[0] if times else 0.0
    result.update(sample_count=len(times), span_seconds=span, max_gap_seconds=gap,
                  play_seconds=end-start,
                  valid=len(times)>=50 and span>=14.0 and gap<=.75)
    return result


def diagnostic_state(child, exit_code, unchanged, errors, termination, coverage):
    if (exit_code != 0 or not unchanged or errors or termination
            or child.get('shutdown_verified') is not True):
        return 'failed'
    if child.get('state') == 'inconclusive':
        return 'inconclusive'
    if child.get('state') == 'checks_complete':
        return 'complete' if coverage.get('valid') else 'inconclusive'
    return 'failed'


def supervise(options):
    import fcntl
    import psutil
    with options.output.with_suffix('.lock').open('a') as lock:
        try:fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
        except BlockingIOError:return 2
        start=time.monotonic();before=hashes();process=None
        report={'schema_version':1,'state':'running','supervisor_pid':os.getpid(),'source_hashes':before,'started_utc':datetime.now(timezone.utc).isoformat(),
                'hard_deadline_seconds':60,'termination_reason':None,'samples':[],'sampling_errors':[],
                'metric':'Main PID RSS only, externally sampled every .25s from Popen. Decimal MB=1000000 bytes; no footprint substitution. Grabs/native metadata observer overhead included.',
                'scope':'Source Cocoa two-cat full-teaser diagnostic only. No single-cat target assessment and no frozen-app extrapolation.'}
        try:
            with tempfile.TemporaryDirectory(prefix='multi-teaser-',dir=options.output.parent) as job:
                child=Path(job)/'child.json';env=dict(os.environ,QT_QPA_PLATFORM='cocoa',PYTHONDONTWRITEBYTECODE='1')
                popen_at=time.monotonic();report['popen_monotonic']=popen_at
                process=subprocess.Popen([sys.executable,'-B','-u',str(Path(__file__).resolve()),'--child',str(child),'--output',str(options.output)],cwd=ROOT,env=env)
                report['child_pid']=process.pid;observed=psutil.Process(process.pid);report['child_create_time']=observed.create_time();next_sample=time.monotonic()
                while process.poll() is None:
                    data=read_json(child);now=time.monotonic();beat=data.get('heartbeat_monotonic')
                    if now>=next_sample:
                        try:report['samples'].append({'monotonic':now,'seconds_after_popen':now-popen_at,'rss_bytes':observed.memory_info().rss,'phase':data.get('phase','startup'),'child_sample_time':beat})
                        except psutil.NoSuchProcess:break
                        except psutil.Error as error:report['sampling_errors'].append(str(error))
                        next_sample=now+.25
                    reason=watchdog_reason(now-start,None if beat is None else now-beat,57,heartbeat_timeout=5)
                    report.update(child=data,elapsed_seconds=now-start,heartbeat_age_seconds=None if beat is None else now-beat);write_json(options.output,report)
                    if reason:report['termination_reason']=reason;break
                    time.sleep(.025)
                stop_owned(process);report['child']=read_json(child);report['child_exit_code']=process.returncode
                # Use final authoritative phase transition timestamps, not stale
                # checkpoint labels, to classify all retained parent RSS samples.
                phases=report['child'].get('phase_events',[])
                for row in report['samples']:
                    applicable=[p for p in phases if p['monotonic']<=row['monotonic']]
                    row['phase']=applicable[-1]['phase'] if applicable else 'startup'
                report['source_hashes_after']=hashes();report['sources_unchanged']=before==report['source_hashes_after']
                report['play_rss_coverage']=play_coverage(report['samples'], phases)
                report['state']=diagnostic_state(report['child'], process.returncode, report['sources_unchanged'], report['sampling_errors'], report['termination_reason'], report['play_rss_coverage'])
                report['by_phase']=summarize(report['samples']);peak=max((r['rss_bytes'] for r in report['samples']),default=None)
                report['rss_assessment']=assess_rss(report['samples'], report['state'])
        except Exception:report.update(state='failed',error=traceback.format_exc())
        finally:
            stop_owned(process);report.update(elapsed_seconds=time.monotonic()-start,finished_utc=datetime.now(timezone.utc).isoformat());write_json(options.output,report)
        print(json.dumps({'state':report['state'],'rss':report.get('rss_assessment')}),flush=True);return 0 if report['state'] in ('complete','inconclusive') else 1


def main():
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--output',type=Path,default=ROOT/'output/v09/multi-teaser-memory.json')
    parser.add_argument('--child',type=Path,help=argparse.SUPPRESS);parser.add_argument('--supervisor',action='store_true',help=argparse.SUPPRESS)
    options=parser.parse_args();options.output=options.output.resolve()
    if sys.platform!='darwin':parser.error('Cocoa required')
    options.output.parent.mkdir(parents=True,exist_ok=True)
    if options.child:return run_child(options)
    if options.supervisor:return supervise(options)
    if options.output.exists() or options.output.with_suffix('.job.json').exists():
        parser.error('Diagnostic output already exists; choose a fresh --output path.')
    log=options.output.with_suffix('.log')
    with log.open('a') as stream:process=subprocess.Popen([sys.executable,'-B','-u',str(Path(__file__).resolve()),'--supervisor','--output',str(options.output)],cwd=ROOT,stdin=subprocess.DEVNULL,stdout=stream,stderr=subprocess.STDOUT,start_new_session=True)
    job={'state':'submitted','supervisor_pid':process.pid,'report':str(options.output),'log':str(log),'submitted_utc':datetime.now(timezone.utc).isoformat()}
    write_json(options.output.with_suffix('.job.json'),job);print(json.dumps(job),flush=True);return 0


if __name__=='__main__':raise SystemExit(main())
