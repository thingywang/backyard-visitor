"""One bounded Cocoa case: open shared smoke choice, select nothing, let it end.

Production timers drive natural entry, the five-second encounter and departures.
Only own widgets receive Qt clicks. No global input, permissions, model dt or
position injection, product edits, memory sampling or repeated full-fight suite.
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
from scripts.probe_fight_interaction import hashes as fight_hashes


def hashes():
    result=fight_hashes()
    result[str(Path(__file__).relative_to(ROOT))]=hashlib.sha256(Path(__file__).read_bytes()).hexdigest()
    return result


def run_child(options):
    from PyQt6 import sip
    from PyQt6.QtCore import QEvent,QPointF,QTimer,Qt
    from PyQt6.QtGui import QMouseEvent
    from PyQt6.QtWidgets import QApplication
    from backyard_visitor.app import Controller
    from backyard_visitor.desktop import DesktopAdapter
    from scripts.probe_overlay_visibility import Cocoa
    tempfile.tempdir=str(options.child.parent)
    qt=QApplication(['BackyardFightMenuTimeout']);qt.setQuitOnLastWindowClosed(False)
    c=Controller(qt,demo=True,hidden=True,adapter=DesktopAdapter(),enable_native=False,seed=7,visit_limit=2)
    cocoa=Cocoa();started=time.monotonic();running=True;last_write=0.;stage='entry';overlay=None;menu=None
    report={'state':'running','pid':os.getpid(),'qt_platform':qt.platformName(),'events':[],'samples':[],'captures':[],
        'action_triggered_ids':[],'destroyed':{},'no_global_override':True,'no_extra_ui':True,'clock_acceleration_seen':False,
        'fixture':'seed7 guided fight, trust60 for all cats to prevent unrelated cursor startle; natural edge entry and production ticks. No model positions/states/dt assigned.',
        'limitations':'Source Cocoa with native adapters disabled. Qt sendEvent is not OS input/hit-testing. Captures are own QWidget renders, not final desktop compositor screenshots. Only this one popup-timeout case.'}
    frames=options.output.parent/(options.output.stem+'-frames');frames.mkdir(parents=True,exist_ok=True)
    def alive(widget):return widget is not None and not sip.isdeleted(widget)
    def visible(widget):return alive(widget) and widget.isVisible()
    def checkpoint(force=False):
        nonlocal last_write
        now=time.monotonic()
        if force or now-last_write>=.5:
            report.update(stage=stage,heartbeat_monotonic=now,elapsed_seconds=now-started)
            write_json(options.child,report);last_write=now
    def capture(widget,label):
        path=frames/(label+'.png');native=cocoa.describe(widget);pix=widget.grab()
        if pix.isNull() or not pix.save(str(path),'PNG'):raise RuntimeError('Own capture failed')
        report['captures'].append({'label':label,'native':native,'path':str(path),'sha256':hashlib.sha256(path.read_bytes()).hexdigest(),'elapsed_seconds':time.monotonic()-started})
    def click(widget):
        point=QPointF(widget.rect().center());glob=QPointF(widget.mapToGlobal(point.toPoint()));types=[]
        for kind,buttons in ((QEvent.Type.MouseButtonPress,Qt.MouseButton.LeftButton),(QEvent.Type.MouseButtonRelease,Qt.MouseButton.NoButton)):
            qt.sendEvent(widget,QMouseEvent(kind,point,glob,Qt.MouseButton.LeftButton,buttons,Qt.KeyboardModifier.NoModifier));types.append(kind.name)
        return {'event_types':types,'local':[point.x(),point.y()],'elapsed_seconds':time.monotonic()-started}
    def finish(error=None):
        nonlocal running
        if not running:return
        running=False;timer.stop()
        if error:report['error']=str(error)
        c.shutdown()
        report['shutdown_verified']=c._quitting and c.fight_overlay is None and not c._workers and c.pool.activeThreadCount()==0 and not any(t.isActive() for t in (c.timer,c.native_timer,c.save_timer,c.recycle_timer)) and not any(w.isVisible() for w in [c.gate,c.bowl,c.bin,*c.cat_windows.values(),*c.gift_windows.values()]) and not visible(menu)
        report['state']='checks_complete' if error is None and report['shutdown_verified'] and all(report.get('checks',{}).values()) else 'failed'
        checkpoint(True);qt.exit(0 if report['state']=='checks_complete' else 1)
    original=c.engine.drain_events
    def events():
        rows=original()
        report['events'].extend({'kind':r['kind'],'cat_id':r.get('cat_id'),'elapsed_seconds':time.monotonic()-started,'model_seconds':c.engine._time} for r in rows)
        return rows
    c.engine.drain_events=events
    for profile in c.engine.cats.values():profile.trust=60
    report['trust_before']={cid:p.trust for cid,p in c.engine.cats.items()}
    if not c.engine.demo_scene('fight'):raise RuntimeError('Scene rejected')
    ids=tuple(c.engine.visitors);report['cat_ids']=ids;c.changed()
    def pulse():
        nonlocal stage,overlay,menu
        if not running:return
        try:
            now=time.monotonic();elapsed=now-started
            if elapsed>51:raise RuntimeError('Case deadline: '+stage)
            report['no_global_override'] &= qt.overrideCursor() is None
            report['no_extra_ui'] &= c._journal is None and c._teaser_session is None and not c.native_timer.isActive()
            report['clock_acceleration_seen'] |= c.engine._time>elapsed+1
            row={'elapsed_seconds':elapsed,'model_seconds':c.engine._time,'stage':stage,
                 'states':{cid:v.state for cid,v in c.engine.visitors.items()},'menu_visible':visible(menu),
                 'overlay_visible':visible(c.fight_overlay),'menu_deleted':menu is not None and sip.isdeleted(menu),
                 'overlay_deleted':overlay is not None and sip.isdeleted(overlay)}
            report['samples'].append(row)
            if stage=='entry' and c.fight_overlay is not None and all(c.engine.visitors[cid].state=='fighting' for cid in ids):
                overlay=c.fight_overlay;report['fight_seen_at']=elapsed;stage='waiting_click'
                overlay.destroyed.connect(lambda:report['destroyed'].update(overlay=time.monotonic()-started))
            elif stage=='waiting_click' and elapsed-report['fight_seen_at']>=.4:
                capture(overlay,'shared-smoke');report['smoke_click']=click(overlay);menu=overlay._menu
                if menu is None:raise RuntimeError('Qt click did not create popup')
                menu.destroyed.connect(lambda:report['destroyed'].update(menu=time.monotonic()-started))
                for action in menu.actions():action.triggered.connect(lambda checked=False,cid=action.data():report['action_triggered_ids'].append(cid))
                report['menu_entries']=[{'cat_id':a.data(),'text':a.text(),'enabled':a.isEnabled()} for a in menu.actions()]
                report['expected_labels']={cid:c.engine.cats[cid].display_name for cid in ids}
                report['menu_opened_at']=time.monotonic()-started;stage='menu_open'
            elif stage=='menu_open':
                if not report.get('menu_capture') and visible(menu):
                    capture(menu,'popup-no-selection');report['menu_capture']=True
                if c.engine._fight_pair:
                    if not visible(menu):raise RuntimeError('Popup closed before the natural fight ended')
                else:
                    report['fight_ended_observed_at']=elapsed
                    report['overlay_none_at_end']=c.fight_overlay is None
                    report['menu_hidden_at_end']=not visible(menu)
                    report['restored_fleeing']={cid:cid in c.engine.visitors and c.engine.visitors[cid].state=='fleeing' and c.cat_windows[cid].isVisible() for cid in ids}
                    fur=[g for g in c.engine.gifts if g.kind=='fur'];report['fur_count']=len(fur)
                    if len(fur)!=1:raise RuntimeError('Expected one fur drop')
                    report['fur_id']=fur[0].id;capture(c.gift_windows[fur[0].id],'fur');stage='departing'
            elif stage=='departing' and not any(cid in c.engine.visitors for cid in ids):
                report['cats_departed']=all(cid not in c.cat_windows for cid in ids)
                c.close_gate();report['fur_click']=click(c.gift_windows[report['fur_id']]);stage='cleaned'
            elif stage=='cleaned':
                report['trust_after']={cid:p.trust for cid,p in c.engine.cats.items()}
                start=next(e for e in report['events'] if e['kind']=='fight_start');end=next(e for e in report['events'] if e['kind']=='fur')
                report['fight_real_seconds']=end['elapsed_seconds']-start['elapsed_seconds'];report['fight_model_seconds']=end['model_seconds']-start['model_seconds']
                report['menu_wait_seconds']=report['fight_ended_observed_at']-report['menu_opened_at']
                kinds=[e['kind'] for e in report['events']]
                entries=report['menu_entries'];menu_samples=[r for r in report['samples'] if r['stage']=='menu_open' and all(r['states'].get(cid)=='fighting' for cid in ids)]
                report['checks']={'qt_click_opened_popup':report['smoke_click']['event_types']==['MouseButtonPress','MouseButtonRelease'] and report.get('menu_capture') is True,
                    'explicit_two_cat_choices':{e['cat_id'] for e in entries}==set(ids) and len(entries)==2 and all(e['enabled'] and report['expected_labels'][e['cat_id']] in e['text'] for e in entries),
                    'no_action_selected':not report['action_triggered_ids'] and 'intervene' not in kinds,
                    'popup_remained_visible_until_fight_end':len(menu_samples)>20 and all(r['menu_visible'] for r in menu_samples),
                    'natural_five_seconds_not_extended':4.8<=report['fight_real_seconds']<=7 and 4.8<=report['fight_model_seconds']<=5.3 and 3.5<=report['menu_wait_seconds']<=6.5,
                    'menu_and_overlay_hidden_at_end':report['overlay_none_at_end'] and report['menu_hidden_at_end'],
                    'menu_and_overlay_destroyed':all(key in report['destroyed'] for key in ('menu','overlay')) and sip.isdeleted(menu) and sip.isdeleted(overlay),
                    'both_cats_restored_fleeing':all(report['restored_fleeing'].values()),'both_cats_departed':report['cats_departed'],
                    'one_fur_drop':report['fur_count']==1 and kinds.count('fur')==1,
                    'qt_click_cleans_fur':report['fur_click']['event_types']==['MouseButtonPress','MouseButtonRelease'] and kinds.count('cleaned')==1 and not c.engine.gifts and report['fur_id'] not in c.gift_windows,
                    'all_trust_unchanged':report['trust_after']==report['trust_before'],
                    'scope_and_real_time_preserved':report['no_global_override'] and report['no_extra_ui'] and not report['clock_acceleration_seen']}
                finish();return
            checkpoint()
        except Exception:finish(traceback.format_exc())
    timer=QTimer();timer.setInterval(100);timer.timeout.connect(pulse);timer.start();checkpoint(True)
    code=qt.exec()
    if running:finish('Unexpected Qt exit');return 1
    return code


def supervise(options):
    import fcntl
    with options.output.with_suffix('.lock').open('a') as lock:
        try:fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
        except BlockingIOError:return 2
        started=time.monotonic();before=hashes();process=None
        report={'schema_version':1,'state':'running','supervisor_pid':os.getpid(),'started_utc':datetime.now(timezone.utc).isoformat(),'source_hashes':before,'hard_deadline_seconds':60,'termination_reason':None}
        try:
            with tempfile.TemporaryDirectory(prefix='fight-menu-timeout-',dir=options.output.parent) as job:
                child=Path(job)/'child.json'
                process=subprocess.Popen([sys.executable,'-B','-u',str(Path(__file__).resolve()),'--child',str(child),'--output',str(options.output)],cwd=ROOT,env=dict(os.environ,QT_QPA_PLATFORM='cocoa',PYTHONDONTWRITEBYTECODE='1'))
                report['child_pid']=process.pid
                while process.poll() is None:
                    data=read_json(child);now=time.monotonic();beat=data.get('heartbeat_monotonic')
                    reason=watchdog_reason(now-started,None if beat is None else now-beat,55,heartbeat_timeout=5)
                    report.update(child=data,elapsed_seconds=now-started,heartbeat_age_seconds=None if beat is None else now-beat);write_json(options.output,report)
                    if reason:report['termination_reason']=reason;break
                    time.sleep(.2)
                stop_owned(process);report['child']=read_json(child);report['child_exit_code']=process.returncode
                report['source_hashes_after']=hashes();report['sources_unchanged']=before==report['source_hashes_after']
                report['state']='complete' if report['child'].get('state')=='checks_complete' and process.returncode==0 and report['sources_unchanged'] and not report['termination_reason'] else 'failed'
        except Exception:report.update(state='failed',error=traceback.format_exc())
        finally:
            stop_owned(process);report.update(elapsed_seconds=time.monotonic()-started,finished_utc=datetime.now(timezone.utc).isoformat());write_json(options.output,report)
        print(json.dumps({'state':report['state'],'seconds':report['elapsed_seconds']}),flush=True)
        return 0 if report['state']=='complete' else 1


def main():
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--output',type=Path,default=ROOT/'output/v09/fight-menu-timeout.json')
    parser.add_argument('--child',type=Path,help=argparse.SUPPRESS);parser.add_argument('--supervisor',action='store_true',help=argparse.SUPPRESS)
    options=parser.parse_args();options.output=options.output.resolve()
    if sys.platform!='darwin':parser.error('Cocoa required')
    options.output.parent.mkdir(parents=True,exist_ok=True)
    if options.child:return run_child(options)
    if options.supervisor:return supervise(options)
    log=options.output.with_suffix('.log')
    with log.open('a') as stream:
        process=subprocess.Popen([sys.executable,'-B','-u',str(Path(__file__).resolve()),'--supervisor','--output',str(options.output)],cwd=ROOT,stdin=subprocess.DEVNULL,stdout=stream,stderr=subprocess.STDOUT,start_new_session=True)
    job={'state':'submitted','supervisor_pid':process.pid,'report':str(options.output),'log':str(log),'submitted_utc':datetime.now(timezone.utc).isoformat()}
    write_json(options.output.with_suffix('.job.json'),job);print(json.dumps(job),flush=True);return 0


if __name__=='__main__':raise SystemExit(main())
