"""Paint a distant tree visitor on the existing fixed logical cat canvas.

The model owns position and scale. Normal motion, directional jumps, four coat
variants and perspective walking all reuse the established packaged artwork.
The simple scratching pose holds one climbing keyframe with its planted hind
paw moved to the shared foot anchor; it does not run a climbing animation.
"""
from __future__ import annotations

import math

from PyQt6.QtCore import QRectF
from PyQt6.QtGui import QPainter

FOOT_X = 72.0
FOOT_Y = 108.0
WIDTH = 144.0
HEIGHT = 112.0
SCRATCH_SOURCE_FOOT = (94.5, 97.0)
SCRATCH_CONTACT_OFFSET = 6.5


def _number(value):
    return type(value) in (int, float) and math.isfinite(value)


def canvas_rect(snapshot):
    """Scaled visual/input bounds relative to the unscaled visitor origin."""
    if not isinstance(snapshot, dict):
        return QRectF()
    scale = snapshot.get('scale')
    if not _number(scale) or not 0 < scale <= 1:
        return QRectF()
    return QRectF(FOOT_X*(1-scale), FOOT_Y*(1-scale), WIDTH*scale, HEIGHT*scale)


def paint_tree_visit(painter, snapshot, cat_art, depth_art, coat='ginger'):
    bounds = canvas_rect(snapshot)
    if bounds.isEmpty():
        return False
    scale = snapshot['scale']
    pose = snapshot.get('pose', 'idle')
    phase = snapshot.get('phase', '')
    if phase == 'takeoff':
        pose = 'jumping'  # CatArt selects its supported crouch transition.
    elif phase == 'landing':
        pose = 'idle'  # CatArt overlays the existing landing recovery.
    facing = -1 if snapshot.get('facing', 1) < 0 else 1
    age = snapshot.get('age', snapshot.get('state_age', 0.))
    age = max(0., age) if _number(age) else 0.
    gait = snapshot.get('gait_phase', 0.)
    gait = gait % 1 if _number(gait) else 0.
    painter.save()
    painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
    painter.translate(bounds.x(), bounds.y())
    painter.scale(scale, scale)
    try:
        if phase in {'recede', 'advance', 'turn'}:
            from .depth_art import select_depth_pose
            selection = select_depth_pose(dict(snapshot, gait_phase=gait))
            if selection is not None:
                frame = depth_art.frame(selection['kind'], selection['index'], coat)
                if frame is not None:
                    painter.drawPixmap(QRectF(0, 0, WIDTH, HEIGHT), frame, QRectF(frame.rect()))
                    return True
        if pose == 'scratching':
            # The existing climbing artwork has an explicit contact line.
            # Hold keyframe zero and align its lower hind paw, not the bottom
            # of its transparent frame or dangling tail, with the platform.
            frame = cat_art.frame('climbing', 0., facing, coat, climb_phase=0.)
            if frame is not None:
                foot_x = SCRATCH_SOURCE_FOOT[0] if facing < 0 else WIDTH-SCRATCH_SOURCE_FOOT[0]
                target = QRectF(FOOT_X-foot_x, FOOT_Y-SCRATCH_SOURCE_FOOT[1], WIDTH, HEIGHT)
                painter.drawPixmap(target, frame, QRectF(frame.rect()))
                return True
            # Missing authored artwork remains diagnosed by CatArt's normal
            # error path instead of substituting a silhouette or screen claw.
            cat_art.paint(painter, QRectF(0, 0, WIDTH, HEIGHT), 'climbing', 0., facing,
                          coat, climb_phase=0.)
            return False
        options = {'gait_phase': gait, 'run_phase': snapshot.get('run_phase', 0.),
                   'jump_pose_phase': snapshot.get('jump_pose_phase'),
                   'takeoff_progress': snapshot.get('takeoff_progress'),
                   'land_progress': snapshot.get('land_progress')}
        for key in ('jump_vy', 'jump_vertical_span'):
            value = snapshot.get(key)
            options[key] = value/scale if _number(value) else None
        cat_art.paint(painter, QRectF(0, 0, WIDTH, HEIGHT), pose, age, facing, coat, **options)
        return True
    finally:
        painter.restore()
