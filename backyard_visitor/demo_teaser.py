"""Opt-in two-cat demo observation, using the ordinary controller and real time.

This module is loaded only by --demo --demo-scene teaser. It never imports a
memory sampler, moves the OS pointer, or assigns visitor positions/states.
"""
from __future__ import annotations

from datetime import datetime, timezone
import json
import os
from pathlib import Path
import sys
import time
import traceback

from PyQt6 import sip
from PyQt6.QtCore import QObject, QTimer
from PyQt6.QtWidgets import QApplication

PAIR = {'amber', 'mist'}


class DemoReport:
    """Claim a fresh explicit path before launching any scene; update atomically."""
    def __init__(self, path):
        self.path = Path(path).expanduser().absolute()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.path.open('x', encoding='utf-8') as stream:
            json.dump({'state': 'starting', 'pid': os.getpid()}, stream)

    def write(self, report):
        temporary = self.path.with_name(f'.{self.path.name}.{os.getpid()}.tmp')
        created = False
        try:
            with temporary.open('x', encoding='utf-8') as stream:
                created = True
                json.dump(report, stream, ensure_ascii=False, indent=2)
                stream.write('\n')
            temporary.replace(self.path)
        finally:
            if created and temporary.exists():
                temporary.unlink()


def full_pair(rows):
    return set(rows) == PAIR and all(row['fully_inside'] and row['qt_visible'] for row in rows.values())


def eligible(visitor):
    return (visitor.state in {'idle', 'sleeping', 'purring', 'walking', 'running', 'playing', 'resting'}
            and visitor.route_motion not in {'leaving', 'fleeing'}
            and visitor.posture_remaining <= 0 and not visitor.carrying_gift
            and not visitor.demo_gift_pending and not visitor.true_name_pending
            and visitor.true_name_remaining <= 0)


def scene_checks(report):
    active = [row for row in report['samples'] if row['phase'] == 'play']
    return {
        'real_full_15_seconds': report.get('finished_reason') == 'completed' and 15.0-1e-6 <= report.get('active_seconds', 0) <= 17,
        'two_cats_observed_through_active_samples': len(active) >= 40 and all(row['two_fully_visible'] for row in active),
        'canvas_visible_during_active_samples': bool(active) and all(row['canvas_visible'] for row in active),
        'session_model_canvas_and_timer_cleared': report.get('play_cleared') is True,
        'rest_arrived': report.get('rest_arrived') is True,
        'no_global_cursor_or_journal': report.get('no_global_override') is True and report.get('no_journal') is True,
        'normal_tick_only': report.get('clock_acceleration_seen') is False,
    }


class DemoTeaser(QObject):
    def __init__(self, controller, writer, clock=time.monotonic):
        if not controller.demo or controller.engine.visitor_limit != 2:
            raise ValueError('Teaser diagnostic requires an isolated two-cat demo')
        super().__init__(controller)
        self.c, self.writer, self.clock = controller, writer, clock
        self.running = False
        self.session = self.canvas = None
        self.timer = QTimer(self)
        self.timer.setInterval(250)
        self.timer.timeout.connect(self.pulse)
        controller.qt.aboutToQuit.connect(self.unexpected_exit)

    @staticmethod
    def alive(obj):
        return obj is not None and not sip.isdeleted(obj)

    @staticmethod
    def rect(rect):
        return [rect.x(), rect.y(), rect.width(), rect.height()]

    def start(self):
        c = self.c
        self.started = self.clock()
        self.initial_model_time = c.engine._time
        self.running = True
        self.report = {
            'schema_version': 1, 'state': 'running', 'phase': 'entry', 'pid': os.getpid(),
            'frozen': bool(getattr(sys, 'frozen', False)), 'executable': sys.executable,
            'started_utc': datetime.now(timezone.utc).isoformat(), 'samples': [],
            'phase_events': [{'phase': 'entry', 'monotonic': self.started}],
            'screen': {'geometry': self.rect(c.screen.geometry()), 'availableGeometry': self.rect(c.screen.availableGeometry()), 'devicePixelRatio': c.screen.devicePixelRatio()},
            'fixture': 'seed7; two-cat limit; trust60; calm mode; two public invites. No injected dt, positions or intermediate states.',
            'scope': 'Opt-in demo in the normal application entry. Qt visibility/geometry only; external observer must corroborate own native window metadata. No screenshot or physical input proof.',
            'no_global_override': True, 'no_journal': True, 'clock_acceleration_seen': False,
        }
        try:
            c.engine.calm_mode = c.settings['calm_mode'] = True
            for cat in c.engine.cats.values():
                cat.trust = 60
            c.engine.open_gate()
            c.engine.invite('amber')
            c.engine.invite('mist')
            c.changed()
            self.checkpoint()
            self.timer.start()
        except Exception:
            self.finish(error=traceback.format_exc())

    def checkpoint(self):
        now = self.clock()
        self.report.update(heartbeat_monotonic=now, elapsed_seconds=now-self.started)
        self.writer.write(self.report)

    def ended(self, reason):
        now = self.clock()
        self.report.update(finished_reason=reason, play_ended_monotonic=now,
                           active_seconds=now-self.report['play_started_monotonic'], phase='cleanup')
        self.report['phase_events'].append({'phase': 'cleanup', 'monotonic': now})

    def pulse(self):
        if not self.running:
            return
        try:
            c, now = self.c, self.clock()
            elapsed = now-self.started
            if elapsed > 56:
                self.finish(error='Demo deadline exceeded')
                return
            rows = {}
            for cid, visitor in c.engine.visitors.items():
                window = c.cat_windows.get(cid)
                rows[cid] = {'state': visitor.state, 'arrival_state': visitor.arrival_state,
                    'qt_visible': bool(window and window.isVisible()),
                    'qt_geometry': self.rect(window.geometry()) if window else None,
                    'fully_inside': bool(window and c.screen.geometry().contains(window.geometry())
                        and visitor.state not in {'peeking', 'entering', 'leaving', 'fleeing'})}
            pair = full_pair(rows)
            self.report['no_global_override'] &= QApplication.overrideCursor() is None
            self.report['no_journal'] &= c._journal is None
            self.report['clock_acceleration_seen'] |= c.engine._time-self.initial_model_time > elapsed+1
            self.report['samples'].append({'monotonic': now, 'phase': self.report['phase'], 'cats': rows,
                'two_fully_visible': pair, 'canvas_visible': self.alive(self.canvas) and self.canvas.isVisible(),
                'model_active_id': c.engine.active_teaser_id, 'model_teaser_phase': c.engine.teaser_phase})
            if self.report['phase'] == 'entry':
                choices = [cid for cid, visitor in c.engine.visitors.items() if eligible(visitor)]
                if pair and choices:
                    selected = 'amber' if 'amber' in choices else choices[0]
                    self.report['selected_id'] = selected
                    if not c.start_play(selected):
                        self.finish(inconclusive='Controller rejected eligible natural pair')
                        return
                    self.session = c._teaser_session
                    self.canvas = self.session._canvas
                    self.report.update(play_started_monotonic=self.session._started, phase='play')
                    self.report['phase_events'].append({'phase': 'play', 'monotonic': self.session._started})
                    self.session.finished.connect(self.ended)
                elif elapsed >= 35:
                    self.finish(inconclusive='No eligible fully visible natural pair within 35 seconds')
                    return
            elif self.report['phase'] == 'play':
                if not self.session.active and 'play_ended_monotonic' not in self.report:
                    self.finish(error='Session stopped without finished signal')
                    return
            else:
                since = now-self.report['play_ended_monotonic']
                visitor = c.engine.visitors.get(self.report['selected_id'])
                self.report['rest_arrived'] = self.report.get('rest_arrived', False) or bool(visitor and visitor.state == 'resting')
                self.report['play_cleared'] = (c._teaser_session is None and c.engine.active_teaser_id is None
                    and (not self.alive(self.canvas) or not self.canvas.isVisible())
                    and (not self.alive(self.session.timer) or not self.session.timer.isActive()))
                if since >= 3 and (self.report['rest_arrived'] or since >= 5):
                    self.report['checks'] = scene_checks(self.report)
                    if self.report['finished_reason'] != 'completed':
                        self.finish(inconclusive='Game interrupted: '+self.report['finished_reason'])
                    elif not self.report['checks']['two_cats_observed_through_active_samples']:
                        self.finish(inconclusive='Two-cat visibility not maintained during play')
                    else:
                        self.finish()
                    return
            self.checkpoint()
        except Exception:
            self.finish(error=traceback.format_exc())

    def finish(self, error=None, inconclusive=None):
        if not self.running:
            return
        self.running = False
        self.timer.stop()
        if error:
            self.report['error'] = str(error)
        if inconclusive:
            self.report['inconclusive_reason'] = inconclusive
        c = self.c
        try:
            c.shutdown()
            self.report['shutdown_verified'] = (c._quitting and c._teaser_session is None and not c._workers
                and c.pool.activeThreadCount() == 0 and not any(t.isActive() for t in (c.timer, c.native_timer, c.save_timer, c.recycle_timer))
                and not any(w.isVisible() for w in [c.gate, c.bowl, c.bin, *c.cat_windows.values()])
                and (not self.alive(self.canvas) or not self.canvas.isVisible()))
        except Exception:
            self.report.update(shutdown_verified=False, cleanup_error=traceback.format_exc())
        self.report['state'] = ('failed' if error or not self.report['shutdown_verified'] else 'inconclusive' if inconclusive
            else 'checks_complete' if self.report.get('checks') and all(self.report['checks'].values()) else 'failed')
        try:
            self.checkpoint()
        except OSError as failure:
            print(f'Teaser diagnostic report failed: {failure}', file=sys.stderr)
            self.report['state'] = 'failed'
        c.qt.exit(0 if self.report['state'] in {'checks_complete', 'inconclusive'} else 1)

    def unexpected_exit(self):
        if self.running:
            self.finish(error='Application exited before diagnostic completion')
