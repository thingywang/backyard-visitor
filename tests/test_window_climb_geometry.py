"""Window sides use the same visible snapshot as tops, without native calls."""
import math

import pytest

from backyard_visitor.desktop import DesktopTarget, Rect, visible_window_climbs

SCREEN = Rect(0, 0, 1280, 800)


def target(identity, x, y, width, height):
    return DesktopTarget(identity, identity, Rect(x, y, width, height))


def sides(windows, identity='editor', screen=SCREEN, **kwargs):
    return [row for row in visible_window_climbs(windows, screen, **kwargs)
            if row[0].startswith(identity+':side:')]


def test_two_sides_connect_to_real_top_and_keep_56_point_mantle():
    result = sides([target('editor', 300, 200, 400, 400)])
    assert result == [('editor:side:left', 'editor:top:0', 300., 200., 600., 1),
                      ('editor:side:right', 'editor:top:0', 700., 200., 600., -1)]
    for _, _, wall, _, _, facing in result:
        x = wall - (56 if facing > 0 else 88)
        landing = wall if facing > 0 else wall-144
        assert abs(landing-x) == 56
        assert SCREEN.left <= x and x+144 <= SCREEN.right


def test_front_window_in_side_lane_blocks_only_that_side():
    front = target('dialog', 720, 420, 30, 100)
    editor = target('editor', 300, 200, 400, 400)
    assert [row[-1] for row in sides([front, editor])] == [1]
    # Behind windows cannot hide an outer side of the foremost editor.
    assert len(sides([editor, front])) == 2


def test_mantle_translation_lane_also_must_remain_clear():
    front = target('dialog', 390, 420, 30, 100)
    editor = target('editor', 300, 200, 400, 400)
    # x390 is outside the left climb canvas [244,388], but inside the
    # top-out union [244,444]. A diagonal top-out must not cross the dialog.
    assert [row[-1] for row in sides([front, editor])] == [-1]


def test_split_top_retains_its_actual_side_segment_identity():
    front = target('dialog', 450, 100, 80, 180)
    editor = target('editor', 300, 200, 400, 400)
    result = sides([front, editor])
    assert [row[1] for row in result] == ['editor:top:0', 'editor:top:1']
    assert [row[2] for row in result] == [300, 700]


def test_side_without_a_complete_landing_width_is_rejected():
    front = target('dialog', 430, 100, 100, 180)
    editor = target('editor', 300, 200, 400, 400)
    assert [row[-1] for row in sides([front, editor])] == [-1]


def test_touching_lane_boundary_does_not_occlude():
    front = target('dialog', 756, 420, 30, 100)
    editor = target('editor', 300, 200, 400, 400)
    assert len(sides([front, editor])) == 2


def test_offscreen_side_is_not_invented_at_screen_clip_and_bottom_is_bounded():
    result = sides([target('editor', -20, 200, 420, 1000)])
    assert result == [('editor:side:right', 'editor:top:0', 400., 200., 800., -1)]
    assert sides([target('editor', 0, 200, 1280, 500)]) == []


def test_low_headroom_short_narrow_and_journal_windows_are_not_climb_routes():
    assert sides([target('editor', 0, 0, 1280, 800)]) == []
    assert sides([target('editor', 300, 111, 400, 400)]) == []
    assert sides([target('editor', 300, 200, 400, 111)]) == []
    assert sides([target('editor', 300, 200, 143, 400)]) == []
    journal = target('visitor-journal', 250, 420, 30, 100)
    result = visible_window_climbs([journal, target('editor', 300, 200, 400, 400)], SCREEN)
    assert all(not row[0].startswith('visitor-journal:') for row in result)
    assert [row[-1] for row in result] == [-1]


def test_screen_menu_headroom_and_negative_coordinates():
    available = Rect(-1200, -760, 1200, 760)
    screen = Rect(-1200, -800, 1200, 800)
    result = sides([target('editor', -900.5, -500.25, 400, 350)], screen=available, screen_rect=screen)
    assert [row[2] for row in result] == [-900.5, -500.5]
    assert all(row[3:5] == (-500.25, -150.25) for row in result)
    assert sides([target('editor', -900, -660, 400, 350)], screen=available, screen_rect=screen) == []


def test_invalid_and_duplicate_windows_do_not_generate_routes():
    editor = target('editor', 300, 200, 400, 400)
    windows = [None, target('bad', 0, 0, math.nan, 500), target('flat', 0, 0, 1000, 0),
               target('overflow', 1e308, 0, 1e308, 500), editor, editor]
    assert sides(windows) == sides([editor])


@pytest.mark.parametrize('value', [0, -1, math.nan, math.inf, None, 'invalid', True])
@pytest.mark.parametrize('dimension', ['sprite_width', 'sprite_height'])
def test_invalid_dimensions_cannot_produce_a_route(dimension, value):
    assert sides([target('editor', 300, 200, 400, 400)], **{dimension: value}) == []


def test_invalid_screen_cannot_produce_a_route():
    windows = [target('editor', 300, 200, 400, 400)]
    assert visible_window_climbs(windows, Rect(0, 0, math.nan, 800)) == []
    assert visible_window_climbs(windows, SCREEN, screen_rect=Rect(0, 0, 0, 0)) == []
