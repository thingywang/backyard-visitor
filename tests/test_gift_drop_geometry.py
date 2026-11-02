"""Release is anchored to the visible mouth, never a remembered desktop point."""
import math

import pytest

from backyard_visitor.gift_geometry import carried_gift_rect, drop_position, mouth_anchor
from backyard_visitor.model import Engine, GIFT_SIZE, SPRITE_WIDTH, Visitor


@pytest.mark.parametrize('facing,center,rect_x', [(-1, 21, 2), (1, 123, 104)])
@pytest.mark.parametrize('surface', [0, 6])
def test_carried_and_dropped_centers_share_mouth(facing, center, rect_x, surface):
    assert mouth_anchor(facing, surface_offset=surface) == (center, 74+surface)
    assert carried_gift_rect(facing, surface_offset=surface) == (rect_x, 55+surface, 38, 38)
    x, y = drop_position(300, 200, facing, (0, 0, 1280, 720), surface_offset=surface)
    assert x+GIFT_SIZE/2 == 300+center
    assert y == 274+surface
    assert y+GIFT_SIZE/2 - (200+74+surface) == 19


def staged(*, facing=-1, bounds=(0, 0, 1280, 720), state='walking', surface=False):
    engine = Engine(seed=4, demo=True, visit_limit=1)
    engine.set_bounds(*bounds)
    engine.open_gate()
    left, top, width, height = bounds
    x, y = left+width/2-72, top+height/2-56
    visitor = Visitor('amber', x, y, state=state, facing=facing,
                      target_x=x, target_y=y, carrying_gift='toy_mouse', stay_remaining=1000)
    if surface:
        engine.set_window_edges([('window', left+100, left+width-100, y+112)])
        visitor.window_target_id = 'window'
    engine.visitors['amber'] = visitor
    engine._visit_countdown = 1000
    return engine, visitor


def assert_under_mouth(engine, visitor, *, surface=0):
    gift = engine.gifts[0]
    left, top, width, height = engine.bounds
    mouth_x = visitor.x+(21 if visitor.facing < 0 else 123)
    assert gift.x+19 == pytest.approx(max(left+19, min(left+width-19, mouth_x)))
    assert gift.y == pytest.approx(max(top, min(top+height-38, visitor.y+74+surface)))
    assert left <= gift.x <= left+width-38
    assert top <= gift.y <= top+height-38
    return gift


@pytest.mark.parametrize('bounds', [(0, 0, 1280, 720), (-1920, -100, 1920, 1080)])
@pytest.mark.parametrize('facing', [-1, 1])
@pytest.mark.parametrize('surface', [False, True])
def test_natural_arrival_releases_beneath_current_head(bounds, facing, surface):
    engine, visitor = staged(facing=facing, bounds=bounds, surface=surface)
    engine.tick(.04)
    assert len(engine.gifts) == 1
    assert_under_mouth(engine, visitor, surface=6 if surface else 0)
    assert visitor.carrying_gift is None
    assert engine.gifts[0].facing == visitor.facing


@pytest.mark.parametrize('facing', [-1, 1])
@pytest.mark.parametrize('scene,kind', [('mouse', 'toy_mouse'), ('gift', 'feather')])
def test_present_demo_uses_same_release_geometry(facing, scene, kind):
    engine, visitor = staged(facing=facing, bounds=(-1920, -100, 1920, 1080), state='idle')
    visitor.carrying_gift = None
    assert engine.demo_scene(scene)
    assert len(engine.gifts) == 1
    gift = assert_under_mouth(engine, visitor)
    assert gift.kind == kind


@pytest.mark.parametrize('facing', [-1, 1])
def test_meal_arrival_turns_before_releasing(facing):
    engine, visitor = staged(facing=-facing)
    visitor.arrival_state = 'eating'
    visitor.arrival_facing = facing
    engine.food = 1
    engine.tick(.04)
    assert visitor.facing == facing
    assert visitor.state == 'eating'
    assert_under_mouth(engine, visitor)


@pytest.mark.parametrize('start_facing', [-1, 1])
@pytest.mark.parametrize('change', ['none', 'removed', 'moved', 'edge'])
def test_icon_routes_release_from_actual_mouth_after_changes(start_facing, change):
    engine = Engine(seed=4, demo=True, visit_limit=1)
    engine.set_bounds(-1920, -100, 1920, 1080)
    engine.open_gate()
    from_left = start_facing > 0
    x = -1920-SPRITE_WIDTH+18 if from_left else -18
    visitor = Visitor('amber', x, engine.floor_y, facing=start_facing,
                      target_x=-900, target_y=engine.floor_y,
                      carrying_gift='toy_mouse', stay_remaining=1000)
    engine.visitors['amber'] = visitor
    engine._visit_countdown = 1000
    engine.set_gift_spots([(-1100, 220)])
    for _ in range(40):
        engine.tick(.04)
    before = visitor.x, visitor.y
    if change == 'removed':
        engine.set_gift_spots([])
    elif change == 'moved':
        engine.set_gift_spots([(-850, 280)])
    elif change == 'edge':
        engine.set_gift_spots([(-1920 if from_left else -38, -100)])
    assert (visitor.x, visitor.y) == before
    for _ in range(1600):
        before = visitor.x, visitor.y
        jumping = visitor.state == 'jumping'
        phase = visitor.gait_phase
        speed = (math.hypot(visitor.target_x-visitor.jump_start_x,
                 abs(visitor.target_y-visitor.jump_start_y)+4*visitor.jump_arc)/visitor.jump_duration
                 if jumping else 66)
        engine.tick(.04)
        assert math.dist(before, (visitor.x, visitor.y)) <= speed*.04+1e-8
        if jumping:
            assert visitor.gait_phase == phase
        else:
            assert visitor.y == before[1]
        if engine.gifts:
            break
    assert len(engine.gifts) == 1
    gift = assert_under_mouth(engine, visitor)
    if change in {'none', 'moved'}:
        assert (gift.x, gift.y) == pytest.approx(engine.gift_spots[0])
    elif change == 'edge':
        # The icon is too high for a full cat: releasing under the actual head
        # takes precedence over teleporting the gift to that unreachable spot.
        assert gift.y > engine.gift_spots[0][1]
    assert visitor.carrying_gift is None and visitor.gift_spot is None


def test_stale_icon_hint_cannot_teleport_release():
    engine, visitor = staged(facing=-1)
    engine.set_gift_spots([(1000, 100)])
    visitor.gift_spot = engine.gift_spots[0]
    engine.tick(.04)
    gift = assert_under_mouth(engine, visitor)
    assert (gift.x, gift.y) != (1000, 100)


@pytest.mark.parametrize('facing,x', [(-1, -1920), (1, -144)])
def test_negative_screen_edge_release_preserves_head_side(facing, x):
    engine, visitor = staged(facing=facing, bounds=(-1920, -100, 1920, 1080))
    visitor.x = visitor.target_x = x
    visitor.y = visitor.target_y = engine.floor_y
    engine.tick(.04)
    gift = assert_under_mouth(engine, visitor)
    assert (gift.x+19 < visitor.x+72) == (facing < 0)
    assert gift.y+GIFT_SIZE == 980
