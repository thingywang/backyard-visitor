"""Directional parkour contracts using synthetic Qt PNGs, not accepted artwork."""
import hashlib
import json
import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
import pytest
from PyQt6.QtCore import QRect, QRectF, QSize, Qt
from PyQt6.QtGui import QColor, QImage, QPainter, QTransform
from PyQt6.QtWidgets import QApplication

from backyard_visitor.art import CatArt
from backyard_visitor.parkour_art import ParkourArt, select_parkour_pose, KINDS


@pytest.fixture(scope="module")
def qt():
    return QApplication.instance() or QApplication(["parkour-art-contract"])


@pytest.fixture
def authored(qt, tmp_path):
    frames = []
    for group, kind in enumerate(KINDS):
        for index in (0, 1):
            variants = {}
            for coat in ("base", "black"):
                image = QImage(288, 224, QImage.Format.Format_ARGB32_Premultiplied)
                image.fill(Qt.GlobalColor.transparent)
                x, y, w, h = 50 + index * 8 + (5 if coat == "black" else 0), 20, 160, 188
                color = QColor(24 + group * 5, 29 + index * 5, 36) if coat == "black" else QColor(155 + group * 15, 100 + index * 15, 65)
                painter = QPainter(image)
                painter.fillRect(QRect(x, y, w, h), color)
                painter.fillRect(QRect(x+8, y+5, 10, 12), QColor(90, 105, 60))
                painter.end()
                name = f"{kind}-{index:02d}-{coat}@2x.png"
                path = tmp_path / name
                assert image.save(str(path), "PNG")
                variants[coat] = dict(file=name, sha256=hashlib.sha256(path.read_bytes()).hexdigest(),
                                      alpha_bounds=[x, y, x+w, y+h],
                                      mouth_anchor=[72+index+(2 if coat == "black" else 0), 22+group*6],
                                      climb_contact_x=88 if kind == "climb" else None)
            frames.append(dict(kind=kind, index=index, variants=variants))
    data = dict(schema_version=1, kind="backyard-visitor-parkour", accepted=True,
                display_size=[144, 112], pixel_size=[288, 224], pixel_ratio=2,
                native_facing=-1, frames=frames)
    (tmp_path / "manifest.json").write_text(json.dumps(data))
    return tmp_path, data


def rgba(frame):
    image = frame.toImage().convertToFormat(QImage.Format.Format_RGBA8888)
    image.setDevicePixelRatio(1)
    return image


def alpha(frame):
    image = frame.toImage().convertToFormat(QImage.Format.Format_Alpha8)
    image.setDevicePixelRatio(1)
    return image


def test_short_horizontal_hops_keep_existing_pose(qt):
    art = CatArt(prefer_compiled=False)
    old = art.frame("jumping", 0, -1)
    assert art.frame("jumping", 0, -1, jump_vy=-100, jump_pose_phase=.9,
                     jump_vertical_span=79).cacheKey() == old.cacheKey()
    assert art._parkour is None


@pytest.mark.parametrize("vy,kind", [(-100, "jump_up"), (0, "jump_down"), (100, "jump_down")])
@pytest.mark.parametrize("phase,index", [(0, 0), (.49, 0), (.5, 1), (1, 1)])
def test_actual_vertical_velocity_selects_group_and_segment_phase_selects_frame(vy, kind, phase, index):
    assert select_parkour_pose("jumping", jump_vy=vy, jump_pose_phase=phase,
                               jump_vertical_span=80) == (kind, index)


@pytest.mark.parametrize("phase,index", [(0, 0), (.49, 0), (.5, 1), (.99, 1), (1, 0), (1.6, 1)])
def test_climb_uses_model_phase_without_an_animation_clock(phase, index):
    assert select_parkour_pose("climbing", climb_phase=phase) == ("climb", index)


@pytest.mark.parametrize("kind", KINDS)
@pytest.mark.parametrize("coat", ["ginger", "black"])
def test_canvas_mirror_cache_and_pose_specific_mouth_are_consistent(authored, kind, coat):
    directory, data = authored
    art = ParkourArt(directory)
    assert art.available and art.load_error is None
    for index in (0, 1):
        left = art.frame(kind, index, -1, coat)
        right = art.frame(kind, index, 1, coat)
        assert (left.width(), left.height(), left.devicePixelRatio()) == (288, 224, 2)
        assert rgba(right) == rgba(left.transformed(QTransform().scale(-1, 1)))
        assert art.frame(kind, index, -1, coat).cacheKey() == left.cacheKey()
        row = next(r for r in data["frames"] if (r["kind"], r["index"]) == (kind, index))
        point = row["variants"]["black" if coat == "black" else "base"]["mouth_anchor"]
        assert art.mouth_anchor(kind, index, -1, coat) == tuple(point)
        assert art.mouth_anchor(kind, index, 1, coat) == (144-point[0], point[1])


def test_black_is_independent_art_while_older_variants_keep_base_alpha(authored, monkeypatch):
    from backyard_visitor import black_coat
    monkeypatch.setattr(black_coat, "black_frame", lambda *a: pytest.fail("Black parkour must use authored PNGs"))
    directory, _ = authored
    art = ParkourArt(directory)
    base = art.frame("jump_up", 0)
    dark = art.frame("jump_up", 0, coat="black")
    assert alpha(dark) != alpha(base)  # Natural independently authored fur edge.
    for coat in ("gray", "tuxedo"):
        converted = art.frame("jump_up", 0, coat=coat)
        assert alpha(converted) == alpha(base)
        assert rgba(converted) != rgba(base)
        assert converted.devicePixelRatio() == 2
    routed = CatArt(prefer_compiled=False)
    routed._parkour = art
    assert rgba(routed.frame("jumping", 0, -1, "black", jump_vy=-20,
                            jump_pose_phase=0, jump_vertical_span=120)) == rgba(dark)


@pytest.mark.parametrize("damage", ["missing_black", "sha", "size", "bounds", "mouth", "contact",
                                    "variant", "duplicate", "unaccepted", "filename"])
def test_bad_group_is_reported_atomically_without_old_pose_substitution(authored, damage):
    directory, data = authored
    row = data["frames"][-1]
    info = row["variants"]["black"]
    path = directory / info["file"]
    if damage == "missing_black":
        path.unlink()
    elif damage == "sha":
        info["sha256"] = "0" * 64
    elif damage == "size":
        image = QImage(str(path)).copy(0, 0, 280, 224)
        image.save(str(path), "PNG")
        info["sha256"] = hashlib.sha256(path.read_bytes()).hexdigest()
    elif damage == "bounds":
        info["alpha_bounds"][0] += 1
    elif damage == "mouth":
        info["mouth_anchor"] = [145, 22]
    elif damage == "contact":
        info["climb_contact_x"] = 48
    elif damage == "variant":
        del row["variants"]["black"]
    elif damage == "duplicate":
        data["frames"][-1] = data["frames"][0]
    elif damage == "unaccepted":
        data["accepted"] = False
    elif damage == "filename":
        info["file"] = "../outside.png"
    (directory / "manifest.json").write_text(json.dumps(data))
    art = ParkourArt(directory)
    assert not art.available
    assert art.load_error and art._rows == {}
    assert art.frame("jump_up", 0, coat="ginger") is None
    assert art.frame("climb", 1, coat="black") is None
    assert art.mouth_anchor("climb", 1) is None


def test_missing_parkour_is_explicit_and_warns_once(qt, tmp_path, caplog):
    art = CatArt(prefer_compiled=False)
    art._parkour = ParkourArt(tmp_path)
    for _ in range(2):
        assert art.frame("jumping", 0, -1, jump_vy=-40, jump_vertical_span=150) is None
    assert art.parkour_load_error
    assert len([r for r in caplog.records if r.name == "backyard_visitor.parkour_art"]) == 1


def test_climb_canvas_is_drawn_without_legacy_vertical_offset_and_time_does_not_advance_pose(authored):
    directory, _ = authored
    art = CatArt(prefer_compiled=False)
    art._parkour = ParkourArt(directory)
    reference = art.frame("climbing", 0, -1, "black", climb_phase=.6)
    for age in (0, 100):
        canvas = QImage(288, 224, QImage.Format.Format_ARGB32_Premultiplied)
        canvas.fill(Qt.GlobalColor.transparent)
        canvas.setDevicePixelRatio(2)
        painter = QPainter(canvas)
        art.paint(painter, QRectF(0, 0, 144, 112), "climbing", age, -1, "black", climb_phase=.6)
        painter.end()
        expected = reference.toImage().convertToFormat(canvas.format())
        assert canvas == expected
    assert art.parkour_mouth("climbing", -1, "black", climb_phase=.6) == art._parkour.mouth_anchor("climb", 1, -1, "black")


def test_takeoff_retains_existing_preparation_before_new_high_jump(authored):
    directory, _ = authored
    art = CatArt(prefer_compiled=False)
    art._parkour = ParkourArt(directory)
    def render(**options):
        image = QImage(144, 112, QImage.Format.Format_ARGB32_Premultiplied)
        image.fill(Qt.GlobalColor.transparent)
        painter = QPainter(image)
        art.paint(painter, QRectF(0, 0, 144, 112), "jumping", 0, -1, **options)
        painter.end()
        return image
    assert render(takeoff_progress=.5, jump_vy=-100, jump_pose_phase=.2,
                  jump_vertical_span=200) == render(takeoff_progress=.5)
    assert render(jump_vy=-100, jump_pose_phase=.2, jump_vertical_span=200) != render(takeoff_progress=.5)


@pytest.mark.parametrize("state,offset,vy", [("climbing", 0, 0), ("jumping", 6, -150)])
def test_overlay_forwards_model_kinematics_and_uses_matching_gift_anchor(qt, state, offset, vy):
    from types import SimpleNamespace
    from PyQt6.QtCore import QPoint
    from backyard_visitor.overlays import CatWindow
    seen, gifts = [], []
    options = dict(jump_vy=vy, jump_pose_phase=.7, jump_vertical_span=200, climb_phase=.6)
    visitor = SimpleNamespace(state=state, age=10, clip_y=None, rear_blend=0, depth_trip=None,
                              takeoff_remaining=0, takeoff_edge_id=None, window_edge_id=None,
                              window_target_id="destination", landing_remaining=0, on_back=False,
                              facing=-1, gait_phase=0, run_phase=0, posture_remaining=0,
                              settle_remaining=0, turn_remaining=0, rise_remaining=0,
                              carrying_gift="feather", **options)
    class Recorder:
        def paint(self, painter, rect, *args, **kwargs):
            seen.append((rect.y(), kwargs))
        def parkour_mouth(self, actual_state, facing, coat, **kwargs):
            assert (actual_state, facing, coat) == (state, -1, "black")
            assert kwargs == options
            return (76, 24)
    controller = SimpleNamespace(art=Recorder(), screen=SimpleNamespace(geometry=lambda: QRect(0, 0, 500, 500)),
                                 cat_clip=lambda *a: None, artwork_scale=1.,
                                 to_display=lambda x, y: (x, y), to_scene=lambda x, y: (x, y),
                                 engine=SimpleNamespace(cats={"fixture": SimpleNamespace(coat="black", render_coat="black")}),
                                 paint_gift=lambda painter, rect, *a, **kw: gifts.append(QRectF(rect)))
    class Canvas(QImage):
        def __init__(self):
            super().__init__(144, 112, QImage.Format.Format_ARGB32_Premultiplied)
            self.fill(Qt.GlobalColor.transparent)
            self.c, self.cat_id = controller, "fixture"
            self.base_size = QSize(144, 112)
            self.drawing_scale = controller.artwork_scale
        def x(self):
            return self.pos().x()
        def y(self):
            return self.pos().y()
        def scene_size(self):
            return QSize(self.base_size)
        def visitor(self):
            return visitor
        def scene_position(self):
            return QPoint(0, 0)
        def pos(self):
            return QPoint(0, 0)
    canvas = Canvas()
    CatWindow.paintEvent(canvas, None)
    assert seen[-1][0] == offset
    assert {key: seen[-1][1][key] for key in options} == options
    assert gifts[-1].center().x() == 76
    assert gifts[-1].center().y() == 24 + offset
