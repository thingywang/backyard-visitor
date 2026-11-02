"""Isolated parkour preview driven by the production Engine and CatArt.

The two windows are explicitly synthetic rectangles in this ordinary preview,
not native application-window observations. No visitor coordinate/state/clock is
assigned. A fixed seed, gate/invitation, validated geometry and existing route
requests stage the sequence; Engine.tick advances every movement normally.
"""
from __future__ import annotations

import argparse
from collections import defaultdict
from datetime import datetime, timezone
import json
import math
import os
from pathlib import Path
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
OUTPUT = ROOT / 'output/v09/parkour-q11/preview'
SCENE_SIZE = (960, 600)
FPS = 24
SEED = 16
NAMES = {'ginger': '棕纹猫', 'black': '纯黑猫'}
PUBLIC_FIELDS = ('cat_id', 'state', 'x', 'y', 'facing', 'target_x', 'target_y',
    'route_stage', 'window_edge_id', 'window_target_id', 'climb_side_id',
    'climb_phase', 'jump_start_x', 'jump_start_y', 'jump_duration', 'jump_elapsed',
    'jump_arc', 'takeoff_remaining', 'landing_remaining', 'jump_vy',
    'jump_pose_phase', 'jump_vertical_span', 'gait_phase', 'run_phase')


class RoutePreview:
    """One deterministic route cycle; scene commands never mutate a Visitor."""

    def __init__(self, coat='ginger'):
        from backyard_visitor.model import Engine
        from backyard_visitor.desktop import DesktopTarget, Rect, visible_window_supports, visible_window_climbs
        if coat not in NAMES:
            raise ValueError('Preview coat must be ginger or black')
        self.coat = coat
        self.engine = Engine(seed=SEED, demo=True, visit_limit=1)
        self.engine.set_bounds(0, 0, *SCENE_SIZE)
        self.windows = [DesktopTarget('preview-A', '模拟窗口 A', Rect(135,150,270,420)),
                        DesktopTarget('preview-B', '模拟窗口 B', Rect(555,170,270,410))]
        rect = Rect(0, 0, *SCENE_SIZE)
        edges, low = visible_window_supports(self.windows, rect)
        visible_sides = visible_window_climbs(self.windows, rect)
        self.sides = [side for side in visible_sides if side[0] in
                      {'preview-A:side:left', 'preview-B:side:right'}]
        if len(self.sides) != 2:
            raise RuntimeError('Synthetic preview geometry has no complete pair of climb lanes')
        self.engine.set_window_edges(edges, low_headroom_ids=low, climb_sides=self.sides)
        self.cat_id = 'onyx' if coat == 'black' else 'amber'
        self.engine.open_gate()
        if self.engine.invite(self.cat_id) != self.cat_id:
            raise RuntimeError('Could not invite preview cat')
        self.phase = 'natural_entry'
        self.model_seconds = 0.0
        self.completed_at = None
        self.rows = []
        self.commands = []
        self.failures = []
        self.arrivals = set()
        self._snapshot()

    @property
    def visitor(self):
        return self.engine.visitors.get(self.cat_id)

    def _route(self, side_id, phase):
        side = self.engine.window_climb_sides[side_id]
        x = side.landing_x + (20 if side.facing > 0 else -20)
        accepted = self.engine._move_to_window(self.visitor, side.edge_id, x, motion='running')
        self.commands.append({'at': self.model_seconds, 'command': '_move_to_window',
                              'side': side_id, 'edge_id': side.edge_id, 'accepted': accepted})
        if accepted:
            self.phase = phase
        else:
            self.failures.append('A valid synthetic side route was refused')
            self.phase = 'failed'

    def _descend(self, phase):
        visitor = self.visitor
        self.engine._walk(visitor, visitor.x, self.engine.floor_y)
        self.commands.append({'at': self.model_seconds, 'command': '_walk_to_floor'})
        self.phase = phase

    def tick(self, dt):
        self.engine.tick(dt)
        self.model_seconds += dt
        visitor = self.visitor
        if visitor is None:
            if self.phase not in {'complete', 'failed'}:
                self.failures.append('Preview visitor departed before completing the requested sequence')
                self.phase = 'failed'
            return
        settled = visitor.state == 'idle' and not visitor.route_stage
        if self.phase == 'natural_entry' and settled:
            self._route('preview-A:side:left', 'left_climb')
        elif self.phase == 'left_climb' and settled and visitor.window_edge_id == 'preview-A:top:0':
            self.arrivals.add('left')
            self._descend('left_descent')
        elif self.phase == 'left_descent' and settled and abs(visitor.y-self.engine.floor_y) < 1e-6:
            self._route('preview-B:side:right', 'right_climb')
        elif self.phase == 'right_climb' and settled and visitor.window_edge_id == 'preview-B:top:0':
            self.arrivals.add('right')
            self._descend('right_descent')
        elif self.phase == 'right_descent' and settled and abs(visitor.y-self.engine.floor_y) < 1e-6:
            self.phase = 'complete'
            self.completed_at = self.model_seconds
        self._snapshot()

    def _snapshot(self):
        visitor = self.visitor
        if visitor is None:
            return
        row = {key:getattr(visitor, key) for key in PUBLIC_FIELDS}
        row.update(model_seconds=round(self.model_seconds, 5), phase=self.phase, coat=self.coat)
        self.rows.append(row)

    def checks(self):
        from backyard_visitor.parkour_art import select_parkour_pose
        contacts = []
        pose_slots = defaultdict(set)
        climb_slots = defaultdict(set)
        for row in self.rows:
            if row['state'] == 'climbing':
                wall = 135. if row['facing'] > 0 else 825.
                contact = 56. if row['facing'] > 0 else 88.
                contacts.append(abs(row['x']+contact-wall) < 1e-6)
                climb_slots['left' if row['facing'] > 0 else 'right'].add(int(row['climb_phase']*2)%2)
            if row['takeoff_remaining'] <= 0:
                pose = select_parkour_pose(row['state'], jump_vy=row['jump_vy'],
                    jump_pose_phase=row['jump_pose_phase'], jump_vertical_span=row['jump_vertical_span'],
                    climb_phase=row['climb_phase'])
                if pose:
                    pose_slots[pose[0]].add(pose[1])
        mantle = [r for r in self.rows if r['state']=='jumping' and r['route_stage']=='jump'
                  and r['window_target_id'] in {'preview-A:top:0','preview-B:top:0'}]
        return {'natural_edge_entry': {'peeking','entering'}.issubset({r['state'] for r in self.rows}),
                'both_window_tops_reached': self.arrivals == {'left','right'},
                'both_climb_phases_each_side': all(climb_slots[s] == {0,1} for s in ('left','right')),
                'up_and_down_both_poses': all(pose_slots[k] == {0,1} for k in ('jump_up','jump_down')),
                'wall_contact_preserved': bool(contacts) and all(contacts),
                'short_mantle_hops': bool(mantle) and all(abs(r['target_x']-r['jump_start_x'])<=64+1e-6 for r in mantle),
                'positions_finite': bool(self.rows) and all(math.isfinite(r[k]) for r in self.rows for k in ('x','y')),
                'returned_to_floor': self.completed_at is not None,
                'route_commands_accepted': not self.failures}, {k:sorted(v) for k,v in pose_slots.items()}

    def report(self, reason):
        checks, poses = self.checks()
        return {'coat':self.coat, 'reason':reason, 'model_seconds':round(self.model_seconds,4),
                'completed_at':self.completed_at, 'checks':checks, 'all_checks_passed':all(checks.values()),
                'observed_pose_slots':poses, 'commands':self.commands, 'failures':self.failures, 'samples':self.rows}


def parse_args(argv=None):
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--offscreen',action='store_true',help='Accelerate fixed-dt model runs and save images/JSON only')
    parser.add_argument('--seconds',type=float,help='Close the ordinary native preview after a bounded number of real seconds')
    parser.add_argument('--coat',choices=('ginger','black','both'),default='ginger')
    parser.add_argument('--output',type=Path,default=OUTPUT)
    args=parser.parse_args(argv)
    if args.seconds is not None and (not math.isfinite(args.seconds) or args.seconds<=0):
        parser.error('--seconds must be finite and positive')
    if args.coat=='both' and not args.offscreen:
        parser.error('--coat both is for --offscreen; the native window has a coat selector')
    return args


def main(argv=None):
    args=parse_args(argv)
    if args.offscreen:os.environ['QT_QPA_PLATFORM']='offscreen'
    from PyQt6.QtCore import QRectF,QTimer,Qt
    from PyQt6.QtGui import QColor,QFont,QImage,QPainter,QPen
    from PyQt6.QtWidgets import QApplication,QWidget,QVBoxLayout,QHBoxLayout,QLabel,QPushButton,QComboBox
    from backyard_visitor.art import CatArt
    from backyard_visitor.model import TAKEOFF_SECONDS,LANDING_SECONDS,RISE_SECONDS,SETTLE_SECONDS
    from backyard_visitor.parkour_art import select_parkour_pose
    from backyard_visitor import black_coat

    output=args.output.resolve();output.mkdir(parents=True,exist_ok=True)
    qt=QApplication.instance() or QApplication(['BackyardParkourPreview'])
    qt.setApplicationName('后院访客 · 跑酷预览')
    art=CatArt();errors=set();screenshots={};completed=[]
    native_paints=0;started=time.monotonic();finished=False
    preview=RoutePreview(args.coat if args.coat!='both' else 'ginger')

    def pose_now(visitor):
        if visitor is None or visitor.takeoff_remaining>0:return None
        return select_parkour_pose(visitor.state,jump_vy=visitor.jump_vy,
            jump_pose_phase=visitor.jump_pose_phase,jump_vertical_span=visitor.jump_vertical_span,
            climb_phase=visitor.climb_phase)

    def paint_scene(painter, sequence, width, height):
        painter.fillRect(QRectF(0,0,width,height),QColor('#ebe8df'))
        scale=min(width/SCENE_SIZE[0],height/SCENE_SIZE[1])
        painter.save();painter.scale(scale,scale)
        painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
        painter.setFont(QFont('PingFang SC',12))
        painter.setPen(QColor('#59665c'))
        painter.drawText(22,28,'模拟场景 · 生产动作与正常速度')
        painter.setFont(QFont('PingFang SC',10))
        painter.drawText(22,49,'左侧攀爬 → 跳下 → 右侧攀爬 → 跳下；这里的窗口是预览图形。')
        painter.setPen(QPen(QColor('#b9b9aa'),1));painter.drawLine(0,598,960,598)
        for window in sequence.windows:
            rect=QRectF(window.rect.x,window.rect.y,window.rect.width,window.rect.height)
            painter.setPen(QPen(QColor('#a5afa9'),1.4));painter.setBrush(QColor('#f7f6ef'))
            painter.drawRoundedRect(rect,7,7)
            painter.setPen(QColor('#738279'))
            painter.drawText(QRectF(rect.x()+14,rect.y()+10,rect.width()-28,30),window.label)
        visitor=sequence.visitor
        if visitor:
            options=dict(gait_phase=visitor.gait_phase,run_phase=visitor.run_phase,
                jump_vy=visitor.jump_vy,jump_pose_phase=visitor.jump_pose_phase,
                jump_vertical_span=visitor.jump_vertical_span,climb_phase=visitor.climb_phase,
                takeoff_progress=1-visitor.takeoff_remaining/TAKEOFF_SECONDS if visitor.takeoff_remaining>0 else None,
                land_progress=1-visitor.landing_remaining/LANDING_SECONDS if visitor.landing_remaining>0 else None,
                rise_progress=1-visitor.rise_remaining/RISE_SECONDS if visitor.rise_remaining>0 else None,
                settle_progress=1-visitor.settle_remaining/SETTLE_SECONDS if visitor.settle_remaining>0 else None)
            art.paint(painter,QRectF(visitor.x,visitor.y,144,112),visitor.state,visitor.age,visitor.facing,sequence.coat,**options)
        if art.parkour_load_error:errors.add(art.parkour_load_error)
        if black_coat.load_error:errors.add(black_coat.load_error)
        painter.restore()

    def capture(sequence):
        visitor=sequence.visitor;pose=pose_now(visitor)
        if not pose:return
        direction='right' if visitor.facing>0 else 'left'
        key=f'{sequence.coat}-{pose[0]}-{pose[1]}-{direction}'
        if key in screenshots:return
        image=QImage(*SCENE_SIZE,QImage.Format.Format_ARGB32_Premultiplied)
        painter=QPainter(image)
        try:paint_scene(painter,sequence,*SCENE_SIZE)
        finally:painter.end()
        path=output/f'{key}.png'
        if not image.save(str(path),'PNG'):raise RuntimeError('Could not save preview image')
        screenshots[key]=path.name

    def finish(reason):
        nonlocal finished
        if finished:return
        finished=True
        runs=completed+[preview.report(reason)]
        passes=[run for run in runs if run['all_checks_passed']]
        result={'generated_at':datetime.now(timezone.utc).isoformat(),
            'mode':'offscreen' if args.offscreen else 'native','reason':reason,
            'elapsed_real_seconds':round(time.monotonic()-started,3),
            'normal_speed_native':not args.offscreen,'fixed_dt_accelerated':args.offscreen,
            'synthetic_window_geometry':True,'physical_desktop_acceptance':False,
            'visitor_coordinates_or_states_assigned':False,'formal_save_changed':False,
            'wallpaper_or_permissions_changed':False,'memory_measurement':False,
            'requested_fps':FPS,'qt_platform':qt.platformName(),
            'native_paint_events':native_paints,'completed_runs':len(passes),
            'completed_coats':sorted({run['coat'] for run in passes}),
            'all_requested_sequences_completed':bool(passes) and (args.coat!='both' or {run['coat'] for run in passes}==set(NAMES)),
            'load_errors':sorted(errors),'no_missing_art':not errors,
            'snapshots':screenshots,'runs':runs}
        file=output/('offscreen-report.json' if args.offscreen else 'native-report.json')
        file.write_text(json.dumps(result,ensure_ascii=False,indent=2)+'\n')
        print(json.dumps({'completed_runs':len(passes),'completed_coats':result['completed_coats'],
            'load_errors':len(errors),'report':str(file)},ensure_ascii=False))
        return result

    if args.offscreen:
        coats=list(NAMES) if args.coat=='both' else [args.coat]
        for index,coat in enumerate(coats):
            preview=RoutePreview(coat)
            for _ in range(FPS*60):
                preview.tick(1/FPS)
                capture(preview)
                if preview.phase in {'complete','failed'}:break
            if index<len(coats)-1:completed.append(preview.report('offscreen_sequence'))
        result=finish('offscreen_fixed_dt')
        return 0 if result['all_requested_sequences_completed'] and not errors else 1

    labels={'natural_entry':'等一次自然进场','left_climb':'沿左侧上爬','left_descent':'从左侧窗顶跳下',
            'right_climb':'沿右侧上爬','right_descent':'从右侧窗顶跳下','complete':'一轮完成，即将重新开始','failed':'路线未完成，请查看检查记录'}
    class Canvas(QWidget):
        def __init__(self):
            super().__init__();self.setMinimumSize(*SCENE_SIZE)
            self.paused=False;self.previous=time.monotonic()
            self.timer=QTimer(self);self.timer.setTimerType(Qt.TimerType.PreciseTimer)
            self.timer.setInterval(round(1000/FPS));self.timer.timeout.connect(self.advance);self.timer.start()
        def advance(self):
            nonlocal preview
            now=time.monotonic();dt=now-self.previous;self.previous=now
            if not self.paused:
                if preview.completed_at is not None and preview.model_seconds-preview.completed_at>2:
                    completed.append(preview.report('loop_completed'))
                    if len(completed)>4:completed.pop(0)
                    preview=RoutePreview(preview.coat)
                preview.tick(dt);capture(preview)
            status.setText(labels.get(preview.phase,preview.phase)+( ' · 已暂停' if self.paused else ''))
            self.update()
        def paintEvent(self,event):
            nonlocal native_paints
            native_paints+=1;painter=QPainter(self)
            try:paint_scene(painter,preview,self.width(),self.height())
            finally:painter.end()
        def toggle(self):
            self.paused=not self.paused;self.previous=time.monotonic()
    window=QWidget();window.setWindowTitle('后院访客 · 跑酷动作预览')
    layout=QVBoxLayout(window);title=QLabel('上跃、下落与窗口侧面攀爬');title.setFont(QFont('PingFang SC',16));layout.addWidget(title)
    note=QLabel('独立模拟场景，不操作桌面上的其他窗口。切换猫咪会从自然进场重新开始。');note.setWordWrap(True);layout.addWidget(note)
    status=QLabel();layout.addWidget(status);canvas=Canvas();layout.addWidget(canvas)
    buttons=QHBoxLayout();chooser=QComboBox()
    for coat,name in NAMES.items():chooser.addItem(name,coat)
    chooser.setCurrentIndex(0 if preview.coat=='ginger' else 1)
    def switch():
        nonlocal preview
        completed.append(preview.report('user_changed_coat'))
        if len(completed)>4:completed.pop(0)
        preview=RoutePreview(chooser.currentData());canvas.previous=time.monotonic();canvas.update()
    chooser.currentIndexChanged.connect(switch);buttons.addWidget(chooser)
    pause=QPushButton('暂停／继续');pause.clicked.connect(canvas.toggle);buttons.addWidget(pause)
    close=QPushButton('关闭');close.clicked.connect(window.close);buttons.addStretch();buttons.addWidget(close);layout.addLayout(buttons)
    qt.aboutToQuit.connect(lambda:finish('window_closed_or_time_limit'))
    if args.seconds is not None:QTimer.singleShot(round(args.seconds*1000),window.close)
    window.show();qt.exec()
    finish('event_loop_finished')
    return 1 if errors else 0


if __name__=='__main__':
    raise SystemExit(main())
