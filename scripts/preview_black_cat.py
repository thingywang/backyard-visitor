"""Standalone black-cat colour preview using the production Qt renderers.

Default: a closable ordinary window. --seconds 20 exits after a bounded native
preview. --offscreen writes PNG/JSON only. No desktop capture, saved pet state,
system wallpaper, permissions, input injection, or memory measurements.
"""
from __future__ import annotations

import argparse
from collections import Counter, defaultdict
from datetime import datetime, timezone
import json
import logging
import math
import os
from pathlib import Path
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
OUTPUT = ROOT / "output" / "v09" / "black-cat" / "preview"
BACKGROUNDS = (("浅米色", "#eee9df", "#34433d"), ("深绿", "#24312d", "#dce1d9"),
               ("近黑", "#101315", "#c9d0ce"))
SEQUENCE = (
    ("standing", "站立"), ("walking", "行走 · 既有四帧"), ("running", "跑步 · 既有四帧"),
    ("resting", "趴下休息"), ("sleeping", "露腹卧睡"), ("window_peeking", "窗沿偷看"),
    ("peeking", "边缘尾尖"), ("rear", "背坐垂尾"), ("away", "走远"),
    ("toward", "走近"), ("front", "正面转身"), ("carried", "被抱起"),
)
SLOT_SECONDS = 1.6
TIMER_MS = round(1000 / 24)


def parse_args(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--offscreen", action="store_true", help="Only write a production-rendered PNG contact sheet and JSON")
    parser.add_argument("--seconds", type=float, help="Close the native preview after this many seconds")
    args = parser.parse_args(argv)
    if args.seconds is not None and (not math.isfinite(args.seconds) or args.seconds <= 0):
        parser.error("--seconds must be a finite positive number")
    return args


def main(argv=None):
    args = parse_args(argv)
    if args.offscreen:
        os.environ["QT_QPA_PLATFORM"] = "offscreen"
    from PyQt6.QtCore import QRectF, QTimer, Qt
    from PyQt6.QtGui import QColor, QFont, QImage, QPainter
    from PyQt6.QtWidgets import QApplication, QHBoxLayout, QLabel, QPushButton, QVBoxLayout, QWidget
    from backyard_visitor.art import CatArt
    from backyard_visitor.rear_art import RearArt
    from backyard_visitor.depth_art import COUNTS, DepthArt
    from backyard_visitor import black_coat

    OUTPUT.mkdir(parents=True, exist_ok=True)
    mode = "offscreen" if args.offscreen else "native"
    log_path = OUTPUT / f"{mode}.log"
    handler = logging.FileHandler(log_path, encoding="utf-8")
    handler.setFormatter(logging.Formatter("%(levelname)s %(name)s: %(message)s"))
    logging.getLogger("backyard_visitor").addHandler(handler)
    app = QApplication.instance() or QApplication(["BackyardBlackCatPreview"])
    app.setApplicationName("后院访客 · 纯黑猫预览")
    art, rear, depth = CatArt(), RearArt(), DepthArt()
    errors = set()
    counts = Counter()
    slots_seen = defaultdict(set)
    started = time.monotonic()
    native_paints = 0
    finished = False

    def paint_pose(painter, state, age, facing=-1, fixed_phase=None):
        """Paint at production logical coordinates; only the surrounding view scales."""
        phase = fixed_phase
        if state in COUNTS:
            period = .8
            phase = age / period % 1 if phase is None else phase
            slot = min(COUNTS[state] - 1, int(phase * COUNTS[state]))
            frame = depth.frame(state, slot, "black")
            if frame is not None:
                painter.drawPixmap(QRectF(0, 0, 144, 112), frame, QRectF(frame.rect()))
            else:
                errors.add(depth.load_error or "Depth frame unavailable")
            slots_seen[state].add(slot)
        elif state == "rear":
            rear.paint(painter, age, "black", 1.)
            slots_seen[state].add(0)
        else:
            options = {}
            if state == "walking":
                art._load_motion()
                phase = age / art._motion_cycle % 1 if phase is None else phase
                options["gait_phase"] = phase
                slots_seen[state].add(min(3, int(phase * 4 + 1e-9)))
            elif state == "running":
                art._load_running()
                phase = age / art._run_cycle % 1 if phase is None else phase
                options["run_phase"] = phase
                slots_seen[state].add(min(3, int(phase * 4 + 1e-9)))
            else:
                slots_seen[state].add(0)
            art.paint(painter, QRectF(0, 0, 144, 112), state, age, facing, "black", **options)
        counts[state] += 1
        if black_coat.load_error:
            errors.add(black_coat.load_error)

    def paint_card(painter, rect, bg, state, label, age, facing=-1, fixed_phase=None, scale=1.5):
        name, color, text = bg
        painter.fillRect(rect, QColor(color))
        painter.setPen(QColor(text))
        painter.setFont(QFont("PingFang SC", 11))
        painter.drawText(QRectF(rect.x()+12, rect.y()+10, rect.width()-24, 26), Qt.AlignmentFlag.AlignLeft, name)
        painter.setFont(QFont("PingFang SC", 10))
        painter.drawText(QRectF(rect.x()+12, rect.y()+38, rect.width()-24, 24), Qt.AlignmentFlag.AlignLeft, label)
        painter.save()
        painter.translate(rect.center().x()-72*scale, rect.y()+72)
        painter.scale(scale, scale)
        paint_pose(painter, state, age, facing, fixed_phase)
        painter.restore()

    def save_report(reason):
        nonlocal finished
        if finished:
            return
        finished = True
        expected = {state for state, _ in SEQUENCE}
        observed = set(counts)
        complete = expected.issubset(observed) and not errors
        for state in ("walking", "running"):
            complete = complete and slots_seen[state] == {0, 1, 2, 3}
        report = dict(
            generated_at=datetime.now(timezone.utc).isoformat(), mode=mode, reason=reason,
            elapsed_seconds=round(time.monotonic()-started, 3), renderers=["CatArt", "RearArt", "DepthArt"],
            canvas_backgrounds=[bg[1] for bg in BACKGROUNDS], requested_fps=24,
            timer_interval_ms=TIMER_MS if not args.offscreen else None, native_paint_events=native_paints,
            rendered_states=sorted(observed), render_counts=dict(counts),
            observed_pose_slots={state:sorted(slots) for state, slots in slots_seen.items()},
            load_errors=sorted(errors), no_fallback_used=not errors, complete_black_coat_preview=bool(complete),
            compiled_base=art._compiled is not None, physical_desktop_acceptance=False,
            screenshot_capture=False, user_save_or_wallpaper_changed=False, memory_measurement=False,
            qt_platform=app.platformName(), native_window=not args.offscreen and app.platformName() not in {"offscreen", "minimal"}, note="Production art rendered into an isolated preview; no new motion frames or engine playback.")
        (OUTPUT / f"{mode}-report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2)+"\n")
        print(json.dumps({"mode":mode,"complete_black_coat_preview":bool(complete),"load_error_count":len(errors),
                          "report":str(OUTPUT / f"{mode}-report.json")},ensure_ascii=False))

    if args.offscreen:
        rows = [(state,label,0.,None) for state,label in SEQUENCE]
        rows += [(state,f"{label} · 第 {i+1} 帧",0.,i/4) for state,label in SEQUENCE if state in ("walking","running") for i in range(4)]
        card_w, card_h = 300, 340
        canvas = QImage(card_w*3, card_h*len(rows), QImage.Format.Format_ARGB32_Premultiplied)
        canvas.fill(QColor("#eee9df"))
        painter = QPainter(canvas)
        painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
        try:
            for row,(state,label,age,phase) in enumerate(rows):
                for column,bg in enumerate(BACKGROUNDS):
                    paint_card(painter,QRectF(column*card_w,row*card_h,card_w,card_h),bg,state,label,age,
                               fixed_phase=phase)
        finally:
            painter.end()
        if not canvas.save(str(OUTPUT / "black-cat-contact.png"),"PNG"):
            raise RuntimeError("Could not save black-cat contact sheet")
        save_report("offscreen_contact_sheet")
        logging.getLogger("backyard_visitor").removeHandler(handler)
        handler.close()
        return 0 if not errors else 1

    class PreviewCanvas(QWidget):
        def __init__(self):
            super().__init__()
            self.setMinimumSize(900, 340)
            self.paused = False
            self.pause_started = 0.
            self.paused_time = 0.
            self.facing = -1
            self.started = time.monotonic()
            self.timer = QTimer(self)
            self.timer.setTimerType(Qt.TimerType.PreciseTimer)
            self.timer.setInterval(TIMER_MS)
            self.timer.timeout.connect(self.update)
            self.timer.start()

        def pause(self):
            if self.paused:
                self.paused_time += time.monotonic()-self.pause_started
                self.paused = False
                self.timer.start()
            else:
                self.pause_started = time.monotonic()
                self.paused = True
                self.timer.stop()
            self.update()

        def paintEvent(self, event):
            nonlocal native_paints
            native_paints += 1
            now = self.pause_started if self.paused else time.monotonic()
            age = max(0., now-self.started-self.paused_time)
            state,label = SEQUENCE[int(age/SLOT_SECONDS) % len(SEQUENCE)]
            local_age = age % SLOT_SECONDS
            painter = QPainter(self)
            painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
            try:
                width=self.width()/3
                for column,bg in enumerate(BACKGROUNDS):
                    paint_card(painter,QRectF(column*width,0,width,self.height()),bg,state,label,local_age,self.facing)
            finally:
                painter.end()
            status.setText("黑毛素材载入有缺项：当前为备用绘制" if errors else "黑毛光影对照 · 动作沿用既有贴图与速度")

    window = QWidget()
    window.setWindowTitle("后院访客 · 纯黑猫光影预览")
    layout = QVBoxLayout(window)
    title = QLabel("纯黑猫 · 三种背景下的光影")
    title.setFont(QFont("PingFang SC", 17))
    layout.addWidget(title)
    note = QLabel("1.5×放大对照；轮播现有动作，不改变桌面猫的速度。可随时关闭此窗口。")
    note.setWordWrap(True)
    layout.addWidget(note)
    status = QLabel()
    canvas = PreviewCanvas()
    layout.addWidget(canvas)
    buttons=QHBoxLayout()
    pause_button=QPushButton("暂停／继续")
    pause_button.clicked.connect(canvas.pause)
    buttons.addWidget(pause_button)
    flip=QPushButton("切换侧面朝向")
    def flip_pose():
        canvas.facing *= -1
        canvas.update()
    flip.clicked.connect(flip_pose)
    buttons.addWidget(flip)
    close=QPushButton("关闭预览")
    close.clicked.connect(window.close)
    buttons.addWidget(close)
    layout.addLayout(buttons)
    layout.addWidget(status)
    window.resize(930, 490)
    app.aboutToQuit.connect(lambda:save_report("closed_or_time_limit"))
    if args.seconds is not None:
        QTimer.singleShot(round(args.seconds*1000), window.close)
    window.show()
    result=app.exec()
    save_report("event_loop_ended")
    logging.getLogger("backyard_visitor").removeHandler(handler)
    handler.close()
    return result


if __name__ == "__main__":
    raise SystemExit(main())
