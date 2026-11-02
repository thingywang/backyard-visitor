"""Actual CatWindow paint acceptance for natural sleeping social responses.

No test selects a pose for CatArt. Recording wraps the real art painter, and
pixels come from CatWindow.grab() dispatching its production paintEvent.
"""
import os
os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
from unittest.mock import patch
import pytest
from PyQt6.QtCore import QRect
from PyQt6.QtGui import QImage
from PyQt6.QtWidgets import QApplication
from backyard_visitor.app import Controller
from backyard_visitor.desktop import DesktopAdapter

BODY = QRect(0, 48, 144, 64)
BUBBLE = QRect(0, 0, 144, 48)


def advance(engine, seconds):
    remaining = seconds
    while remaining > 1e-9:
        dt = min(.05, remaining)
        engine.tick(dt)
        remaining -= dt


def until(engine, predicate, seconds=400):
    for _ in range(round(seconds/.05)):
        if predicate():
            return
        engine.tick(.05)
    assert predicate(), 'Natural scene did not reach observation'


def prepare(c, *, sleeping=True):
    e = c.engine
    e.calm_mode = True
    e.cats['mist'].trust = 100
    e.open_gate(); assert e.invite('mist') == 'mist'
    e.name_cat('mist', 'Audit nickname'); assert e.adopt('mist')
    v = e.visitors['mist']
    if sleeping:
        until(e, lambda: v.state == 'sleeping')
        advance(e, 1)
    else:
        until(e, lambda: v.state == 'idle' and v.settle_remaining == 0)
    c.changed()
    return v


def respond(c, v, action):
    if action == 'pet':
        c.pet(v.cat_id)
    elif action == 'call':
        c.call_cat(v.cat_id)
    else:
        result = c.engine.name_cat(v.cat_id, c.engine.cats[v.cat_id].true_name)
        assert result == 'true_name'
        # Restore the benign user nickname BEFORE Controller can save/log;
        # this ordinary rename does not restart or clear the active response.
        c.engine.name_cat(v.cat_id, 'Audit nickname')
        c.changed()
    assert v.state == 'purring'


def grab_actual(c, cid='mist'):
    poses = []
    original = c.art.paint
    def observed(*args, **kwargs):
        poses.append(args[2])
        return original(*args, **kwargs)
    with patch.object(c.art, 'paint', observed):
        image = c.cat_windows[cid].grab().toImage()
    assert poses, 'CatWindow.grab did not invoke the production CatArt painter'
    return image, poses


def changed_pixels(a, b, rect):
    left = a.copy(rect).convertToFormat(QImage.Format.Format_RGBA8888)
    right = b.copy(rect).convertToFormat(QImage.Format.Format_RGBA8888)
    assert left.size() == right.size()
    x = left.constBits().asstring(left.sizeInBytes())
    y = right.constBits().asstring(right.sizeInBytes())
    return sum(x[i:i+4] != y[i:i+4] for i in range(0, len(x), 4))


@pytest.fixture
def c(tmp_path):
    qt = QApplication.instance() or QApplication([])
    controller = Controller(qt, state_dir=tmp_path, hidden=True,
                            adapter=DesktopAdapter(), enable_native=False,
                            seed=0, visit_limit=1)
    yield controller
    controller.shutdown()
    qt.processEvents()


@pytest.mark.parametrize('action', ['pet', 'call', 'true_name'])
def test_real_overlay_keeps_belly_pixels_bubble_and_original_nap(c, action):
    v = prepare(c)
    original_age, original_next = v.state_age, v.next_action
    before, before_poses = grab_actual(c)
    assert set(before_poses) == {'sleeping'}
    respond(c, v, action)
    during, poses = grab_actual(c)
    assert set(poses) == {'sleeping'}
    assert changed_pixels(before, during, BODY) == 0
    assert changed_pixels(before, during, BUBBLE) > 0
    duration = 7 if action == 'true_name' else 4
    advance(c.engine, duration + .05); c.changed()
    after, after_poses = grab_actual(c)
    assert v.state == 'sleeping' and not v.sleeping_purr
    assert set(after_poses) == {'sleeping'}
    assert changed_pixels(before, after, BODY) == 0
    assert changed_pixels(before, after, BUBBLE) == 0
    assert v.next_action == original_next
    assert v.state_age == pytest.approx(original_age + duration + .05, abs=.051)


def test_real_overlay_ordinary_seated_purr_still_uses_purring_art(c):
    v = prepare(c, sleeping=False)
    assert not v.on_back
    before, _ = grab_actual(c)
    respond(c, v, 'pet')
    during, poses = grab_actual(c)
    assert set(poses) == {'purring'}
    assert not v.sleeping_purr and not v.on_back
    assert changed_pixels(before, during, BUBBLE) > 0
    advance(c.engine, 4.05); c.changed()
    assert v.state == 'idle' and not v.sleeping_purr
    _, after_poses = grab_actual(c)
    assert set(after_poses) == {'idle'}
