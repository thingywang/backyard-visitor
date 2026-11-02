"""Scene units and relative artwork preference never double-apply Retina DPR."""
import math
import os
from types import SimpleNamespace

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
import pytest
from PyQt6.QtCore import QCoreApplication, QEvent, Qt
from PyQt6.QtWidgets import QApplication

from backyard_visitor.desktop import Rect
from backyard_visitor.display_scale import (SceneTransform, automatic_scale,
    artwork_scale, normalize_user_scale)
from backyard_visitor.panel import ArtworkScaleControl


@pytest.mark.parametrize("width,height,expected", [
    (1440, 900, 1.), (900, 1440, 1.),
    (1920, 1080, math.sqrt(1.6)), (1080, 1920, math.sqrt(1.6)),
    (720, 450, .75), (5760, 3600, 1.75),
])
def test_reference_equal_area_orientation_and_auto_bounds(width, height, expected):
    assert automatic_scale(width, height) == pytest.approx(expected)


@pytest.mark.parametrize("width,height", [(0, 900), (-1440, 900), (1440, 0),
    (math.nan, 900), (1440, math.inf), (True, 900), (1440, "900"), (None, 900)])
def test_unavailable_or_invalid_screen_is_neutral_not_tiny_or_infinite(width, height):
    assert automatic_scale(width, height) == 1.


@pytest.mark.parametrize("raw", [None, "1.2", True, False, [], {}, math.nan,
                                  math.inf, -math.inf, 10**1000])
def test_malformed_saved_user_scale_is_default(raw):
    assert normalize_user_scale(raw) == 1.


@pytest.mark.parametrize("raw,expected", [(-4, .75), (0, .75), (.75, .75),
                                          (1, 1.), (1.23, 1.23), (1.5, 1.5), (9, 1.5)])
def test_valid_user_scale_is_bounded(raw, expected):
    assert normalize_user_scale(raw) == expected


def test_combined_scale_is_bounded_and_user_setting_is_relative_to_display():
    assert artwork_scale(720, 450, .75) == .6
    assert artwork_scale(5760, 3600, 1.5) == 2.5
    assert artwork_scale(1920, 1080, 1.2) == pytest.approx(math.sqrt(1.6)*1.2)
    assert artwork_scale(1920, 1080, True) == automatic_scale(1920, 1080)


def test_same_logical_resolution_has_same_size_without_a_dpr_multiplier():
    low_density = SimpleNamespace(width=1440, height=900, dpr=1.)
    retina = SimpleNamespace(width=1440, height=900, dpr=2.)
    assert artwork_scale(low_density.width, low_density.height) == artwork_scale(retina.width, retina.height)
    assert automatic_scale(1e308, 1e308) == 1.75
    assert automatic_scale(1e-300, 1e-300) == .75


@pytest.mark.parametrize("scale", [.6, .75, 1., 1.25, 1.5, 2.5])
@pytest.mark.parametrize("origin", [(0., 0.), (-1920., 48.), (1440., -900.)])
def test_transform_roundtrip_preserves_negative_origins_and_relative_lengths(scale, origin):
    t = SceneTransform(scale, *origin)
    assert t.to_display(*origin) == origin
    x, y = origin[0]+123.25, origin[1]-76.5
    displayed = t.to_display(x, y)
    assert displayed == pytest.approx((origin[0]+123.25*scale, origin[1]-76.5*scale))
    assert t.to_scene(*displayed) == pytest.approx((x, y))
    assert t.to_display(x+144, y+112) == pytest.approx((displayed[0]+144*scale, displayed[1]+112*scale))


def test_native_work_area_maps_to_scene_with_menu_bar_insets_scaled_once():
    t = SceneTransform(1.5, -1920, 0)
    native = Rect(-1920, 48, 1920, 1032)
    scene = t.rect_to_scene(native)
    assert scene == Rect(-1920, 32, 1280, 688)
    assert t.rect_to_display(scene) == native
    assert t.rect_to_display(Rect(-1800, 70, 0, 0)) == Rect(-1740, 105, 0, 0)


def test_mouth_and_floor_anchors_share_same_transform_as_sprite_rect():
    t = SceneTransform(1.5, -1440, 24)
    canvas = Rect(-1300, 600, 144, 112)
    actual = t.rect_to_display(canvas)
    foot = t.to_display(canvas.x+72, canvas.y+108)
    assert foot == pytest.approx((actual.x+72*1.5, actual.y+108*1.5))
    mouth = t.to_display(canvas.x+118, canvas.y+72)
    assert t.to_scene(*mouth) == pytest.approx((canvas.x+118, canvas.y+72))


@pytest.mark.parametrize("args", [(0, 0, 0), (-1, 0, 0), (True, 0, 0),
    (math.inf, 0, 0), (1, math.nan, 0), (1, 0, "2"), (None, 0, 0)])
def test_invalid_transform_cannot_reach_native_window_geometry(args):
    with pytest.raises(ValueError): SceneTransform(*args)


@pytest.mark.parametrize("point", [(True, 0), (0, math.nan), (math.inf, 0), ("12", 0)])
def test_invalid_point_is_rejected_in_both_directions(point):
    t = SceneTransform(1.5, -1440, 0)
    with pytest.raises(ValueError): t.to_display(*point)
    with pytest.raises(ValueError): t.to_scene(*point)


@pytest.mark.parametrize("rect", [None, (0, 0, 10, 10), Rect(0, 0, -1, 1),
    Rect(0, 0, 1, -1), Rect(0, 0, math.inf, 10), Rect(True, 0, 10, 10)])
def test_invalid_rectangle_is_rejected_in_both_directions(rect):
    t = SceneTransform(1.5, 0, 0)
    with pytest.raises(ValueError): t.rect_to_display(rect)
    with pytest.raises(ValueError): t.rect_to_scene(rect)


def test_unrepresentable_coordinate_is_rejected_before_native_call():
    with pytest.raises(ValueError): SceneTransform(2., 0, 0).to_display(1e308, 0)
    with pytest.raises(ValueError): SceneTransform(.1, 0, 0).to_scene(1e308, 0)


@pytest.fixture
def control():
    qt = QApplication.instance() or QApplication([])
    calls = []
    c = SimpleNamespace(settings={"artwork_scale": 1.2})
    def setting(value):
        c.settings["artwork_scale"] = value
        calls.append(value)
    c.set_artwork_scale = setting
    widget = ArtworkScaleControl(c)
    yield widget, c, calls
    widget.close(); widget.deleteLater()
    QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
    qt.processEvents()


def test_slider_initializes_saved_preference_without_system_or_setting_callback(control):
    w, c, calls = control
    assert w.slider.orientation() == Qt.Orientation.Horizontal
    assert (w.slider.minimum(), w.slider.maximum(), w.slider.value()) == (75, 150, 120)
    assert w.value_label.text() == "120%（自动适配基础上）"
    assert calls == []
    w.slider.setValue(135)
    assert calls == [1.35] and c.settings["artwork_scale"] == 1.35
    assert w.value_label.text() == "135%（自动适配基础上）"


def test_refresh_updates_saved_value_without_echoing_callback(control):
    w, c, calls = control
    c.settings["artwork_scale"] = .9
    w.refresh()
    assert w.slider.value() == 90 and calls == []
    c.settings["artwork_scale"] = "broken"
    w.refresh()
    assert w.slider.value() == 100 and calls == []


def test_periodic_refresh_does_not_pull_slider_out_of_users_drag(control):
    w, c, calls = control
    w.slider.setSliderDown(True)
    w.slider.setValue(140)
    c.settings["artwork_scale"] = 1.
    w.refresh()
    assert w.slider.value() == 140 and calls == [1.4]
    w.slider.setSliderDown(False)
    w.refresh()
    assert w.slider.value() == 100 and calls == [1.4]


def test_refresh_preserves_existing_signal_blocking_state(control):
    w, c, calls = control
    w.slider.blockSignals(True)
    c.settings["artwork_scale"] = 1.5
    w.refresh()
    assert w.slider.signalsBlocked() and w.slider.value() == 150 and calls == []
