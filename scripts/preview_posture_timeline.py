"""Replay real Engine posture paths through production CatArt (no OS capture).

Natural arrivals and seeded decisions establish the fixtures. Only public
interaction methods and Engine.tick advance them; no visitor positions, clocks
or posture fields are assigned. Output canvases are aligned for comparison,
while JSON preserves the actual global coordinates and production paint kwargs.
"""
from __future__ import annotations
import argparse
import hashlib
import json
import os
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
from PyQt6.QtCore import QRectF
from PyQt6.QtGui import QColor, QFont, QImage, QPainter
from PyQt6.QtWidgets import QApplication
from backyard_visitor.art import CatArt
from backyard_visitor.interaction_model import (
    BELLY_ROLL_SECONDS, CROUCH_RISE_SECONDS, SWAT_SECONDS, TEASER_SECONDS)
from backyard_visitor.model import (
    Engine, LANDING_SECONDS, RISE_SECONDS, SETTLE_SECONDS, TAKEOFF_SECONDS)

DT = .005
CASES = (
    ("crouch-rise-chase", "amber", 71, "idle", "crouch_rise"),
    ("swat-roll-rest", "mist", 3, "sleeping", "belly_roll"),
    ("belly-roll-watch", "ink", 2, "sleeping", "belly_roll"),
)


def snapshot(engine, visitor, elapsed):
    """Match relevant CatWindow.paintEvent parameters, including priority clocks."""
    def progress(remaining, duration):
        return 1 - remaining / duration if remaining > 0 else None
    v = visitor
    pose = ("running" if engine.teaser_phase == "chase" else "play_crouch") if v.state == "teasing" else v.state
    paint = dict(
        state=pose, age=v.age, facing=v.facing, coat=engine.cats[v.cat_id].coat,
        gait_phase=v.gait_phase, run_phase=v.run_phase,
        land_progress=progress(v.landing_remaining, LANDING_SECONDS),
        posture_kind=v.posture_kind, posture_source=v.posture_source,
        posture_target=v.posture_target,
        posture_progress=progress(v.posture_remaining, v.posture_duration),
        settling=v.settle_remaining > 0, turning=v.turn_remaining > 0,
        settle_progress=progress(v.settle_remaining, SETTLE_SECONDS),
        rise_progress=progress(v.rise_remaining, RISE_SECONDS),
        takeoff_progress=progress(v.takeoff_remaining, TAKEOFF_SECONDS))
    offset = (6 if v.takeoff_edge_id else 0) if v.takeoff_remaining > 0 else (6 if v.window_edge_id or v.window_target_id else 0)
    return dict(elapsed=round(elapsed, 6), x=v.x, y=v.y, state=v.state,
                teaser_phase=engine.teaser_phase, ground_speed=v.ground_speed,
                posture_remaining=v.posture_remaining, posture_duration=v.posture_duration,
                render_offset_y=offset, paint=paint)


def natural_setup(cat_id, seed, initial):
    engine = Engine(seed=seed, demo=True, visit_limit=1)
    engine.set_bounds(0, 0, 1600, 1000)
    engine.open_gate()
    engine.invite(cat_id)
    engine.demo_scene("trust")
    v = engine.visitors[cat_id]
    elapsed = 0.
    while v.state != initial or (initial == "idle" and v.settle_remaining > 0):
        engine.tick(DT)
        elapsed += DT
        if cat_id not in engine.visitors or elapsed > 42:
            raise RuntimeError("Natural initial state did not arrive")
    engine.drain_events()
    return engine, v, elapsed


def replay(spec):
    name, cid, seed, initial, kind = spec
    engine, v, setup_seconds = natural_setup(cid, seed, initial)
    before = snapshot(engine, v, 0)
    if name == "swat-roll-rest":
        assert not engine.pet(cid) and v.state == "swatting"
    else:
        if kind == "crouch_rise":
            engine.cursor_moved(v.x - 500 + 72, v.y + 90, speed=0)
        assert engine.start_teaser(cid)
    path = [snapshot(engine, v, 0)]
    selected = {}
    elapsed = 0.
    first = ended = None
    while elapsed < 2:
        row = path[-1]
        p = row["paint"]["posture_progress"]
        if row["paint"]["posture_kind"] == kind and p is not None:
            if first is None:
                first = row
            index = min(4, int(p * 5))
            selected.setdefault(index, row)
        elif first is None:
            before = row
        elif ended is None:
            ended = elapsed
        if ended is not None and elapsed >= ended + .02 - 1e-8:
            break
        engine.tick(DT)
        elapsed += DT
        path.append(snapshot(engine, v, elapsed))
    assert first is not None and ended is not None and set(selected) == set(range(5)), name
    active = [r for r in path if r["paint"]["posture_kind"] == kind]
    reference = active[0]
    drift = max(max(abs(r["x"] - reference["x"]), abs(r["y"] - reference["y"])) for r in active)
    phase_drift = max(max(abs(r["paint"][key] - reference["paint"][key]) for key in ("gait_phase", "run_phase")) for r in active)
    assert drift < 1e-8 and phase_drift < 1e-8, "Transition moved its support/locomotion phase"
    duration = CROUCH_RISE_SECONDS if kind == "crouch_rise" else BELLY_ROLL_SECONDS
    began = first["elapsed"] - first["paint"]["posture_progress"] * duration
    assert abs((ended - began) - duration) <= DT + 1e-8
    if name == "swat-roll-rest":
        assert abs(began - SWAT_SECONDS) <= DT + 1e-8
        assert path[-1]["state"] == "resting"
    else:
        assert path[-1]["teaser_phase"] == ("chase" if kind == "crouch_rise" else "watch")
    if kind == "crouch_rise":
        assert path[-1]["ground_speed"] > 0 and path[-1]["x"] < reference["x"]
    deadline = None
    if name != "swat-roll-rest":
        while engine.active_teaser_id is not None and elapsed < TEASER_SECONDS + .1:
            engine.tick(DT)
            elapsed += DT
        assert engine.active_teaser_id is None and abs(elapsed - TEASER_SECONDS) <= DT + 1e-8
        deadline = round(elapsed, 6)
        assert sum(e["kind"] == "play_finished" for e in engine.drain_events()) == 1
    frames = [dict(label="before", **before)]
    frames += [dict(label=("source" if i == 0 else "endpoint" if i == 4 else "middle " + str(i)), **selected[i]) for i in range(5)]
    frames.append(dict(label="after", **path[-1]))
    return dict(name=name, cat_id=cid, seed=seed, setup_seconds=round(setup_seconds, 6),
                transition_start_seconds=round(began, 6), transition_end_seconds=round(ended, 6),
                transition_duration_seconds=duration, max_position_drift=drift,
                max_locomotion_phase_drift=phase_drift, teaser_deadline_seconds=deadline,
                keyframes=frames, trajectory=path)


def fingerprints():
    names = ["art.py", "model.py", "interaction_model.py", "overlays.py", "posture_art.py"]
    names += ["assets/" + n for n in ("activity.png", "activity.json", "standing.png", "standing.json", "posture.png", "posture-belly.png", "posture.json", "run.png", "run.json")]
    return {n: hashlib.sha256((ROOT / "backyard_visitor" / n).read_bytes()).hexdigest() for n in names}


def render(cases, target):
    art = CatArt()
    image = QImage(2160, 1020, QImage.Format.Format_ARGB32_Premultiplied)
    image.fill(QColor("#eee9df"))
    painter = QPainter(image)
    painter.setFont(QFont("Arial", 17))
    painter.setPen(QColor("#233d30"))
    painter.drawText(20, 28, "Production CatArt / real Engine ticks / three posture paths")
    painter.setFont(QFont("Arial", 12))
    painter.drawText(20, 50, "Aligned local canvases, not a system recording. Three actual coat parameters; tuxedo is a darker tabby tint.")
    for row_index, case in enumerate(cases):
        top = 100 + row_index * 300
        painter.setFont(QFont("Arial", 15))
        painter.drawText(20, top - 15, case["name"] + " | " + case["keyframes"][0]["paint"]["coat"] +
                         " | posture " + str(case["transition_duration_seconds"]) + " s")
        for column, frame in enumerate(case["keyframes"]):
            x = 20 + 304 * column
            painter.setPen(QColor("#b6beae"))
            painter.drawLine(x, top + 212, x + 288, top + 212)
            painter.save()
            painter.translate(x, top)
            painter.scale(2, 2)
            art.paint(painter, QRectF(0, frame["render_offset_y"], 144, 112), **frame["paint"])
            painter.restore()
            painter.setPen(QColor("#233d30"))
            painter.setFont(QFont("Arial", 12))
            p = frame["paint"]["posture_progress"]
            suffix = "" if p is None else " / p=" + format(p, ".3f")
            painter.drawText(x, top + 242, frame["label"] + " / t=" + format(frame["elapsed"], ".3f") + suffix)
            painter.drawText(x, top + 260, "x=" + format(frame["x"], ".2f") + " y=" + format(frame["y"], ".2f") + " / facing " + str(frame["paint"]["facing"]))
    painter.setFont(QFont("Arial", 12))
    painter.drawText(20, 1000, "Actual discrete source + 3 intermediates + endpoint; no blending or generated in-between frames.")
    painter.end()
    assert image.save(str(target))
    for case in cases:
        first = case["keyframes"][1]["paint"]
        state = art.posture_state(first["posture_kind"], first["posture_source"], first["posture_target"])
        assert state and len(art.states[state]) == 5, "Production renderer fell back instead of loading posture frames"


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", type=Path, default=ROOT / "output/v08")
    options = parser.parse_args()
    app = QApplication.instance() or QApplication(["posture-timeline"])
    hashes = fingerprints()
    cases = [replay(spec) for spec in CASES]
    output = options.output_dir
    output.mkdir(parents=True, exist_ok=True)
    render(cases, output / "posture-timeline.png")
    assert fingerprints() == hashes, "Product changed while preview was being recorded"
    report = dict(schema=1, dt_seconds=DT, description=__doc__, passed=True,
                  source_sha256=hashes, cases=cases,
                  limitations=["Synthetic scene, not native-window or OS recording.",
                               "Five discrete production poses per transition; no interpolation.",
                               "Runtime tuxedo coat is a dark tabby tint, not new white markings."])
    (output / "posture-timeline.json").write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    print(json.dumps([dict(name=c["name"], duration=c["transition_duration_seconds"],
                          position_drift=c["max_position_drift"], deadline=c["teaser_deadline_seconds"])
                      for c in cases]))


if __name__ == "__main__":
    main()
