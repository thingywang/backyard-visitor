"""Independent food quantity art keeps the ceramic bowl and anchor unchanged."""
import hashlib
import json
import os
import shutil

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PyQt6.QtCore import QRectF, Qt
from PyQt6.QtGui import QImage, QPainter
from PyQt6.QtWidgets import QApplication

from backyard_visitor import bowl_art
from backyard_visitor.bowl_art import BowlArt


@pytest.fixture(scope="module")
def qt():
    return QApplication.instance() or QApplication(["bowl-portions-test"])


def pixels(image):
    return [image.pixelColor(x, y).getRgb()
            for y in range(image.height()) for x in range(image.width())]


def test_three_independent_transparent_source_images(qt):
    manifest = json.loads((bowl_art.ASSETS / "bowl-portions.json").read_text())
    rows = manifest["frames"]
    assert [row["food"] for row in rows] == [1, 2, 3]
    assert len({row["file"] for row in rows}) == 3
    digests = set()
    for row in rows:
        file = bowl_art.ASSETS / row["file"]
        digests.add(hashlib.sha256(file.read_bytes()).hexdigest())
        image = QImage(str(file))
        assert image.hasAlphaChannel()
        assert (image.width(), image.height()) == (row["width"], row["height"])
        assert image.pixelColor(0, 0).alpha() < 5
    assert len(digests) == 3


def test_all_quantities_are_distinct_and_food_coverage_grows(qt):
    art = BowlArt()
    frames = [art.frame(food=food) for food in range(4)]
    assert art.load_error is None and art.portion_load_error is None
    assert all((frame.width(), frame.height(), frame.devicePixelRatio()) == (124, 80, 2)
               for frame in frames)
    states = [pixels(frame.toImage()) for frame in frames]
    assert len({tuple(state) for state in states}) == 4
    coverage = [sum(max(abs(a - b) for a, b in zip(old[:3], new[:3])) > 12
                    for old, new in zip(states[0], state)) for state in states[1:]]
    assert 200 < coverage[0] < coverage[1] < coverage[2]
    assert coverage[1] > coverage[0] * 1.5
    assert coverage[2] > coverage[1] * 1.2


def test_food_does_not_change_outer_silhouette_or_front_ceramic(qt):
    art = BowlArt()
    empty = art.frame(food=0).toImage()
    for food in (1, 2, 3):
        image = art.frame(food=food).toImage()
        for y in range(image.height()):
            for x in range(image.width()):
                old, new = empty.pixelColor(x, y), image.pixelColor(x, y)
                assert new.alpha() == old.alpha()
                # All food is confined to the existing interior; rim/body remain exact.
                if y >= 47 or y < 13 or x < 13 or x >= 111:
                    assert new == old


def test_quantity_frames_are_cached_without_redecoding(qt, monkeypatch):
    art = BowlArt()
    frames = art.load_portions()
    assert sorted(frames) == [0, 1, 2, 3]
    assert frames[0] is art.frame(False)
    monkeypatch.setattr(bowl_art, "QImage", lambda *args: pytest.fail("redecoded food artwork"))
    assert art.load_portions() is frames
    for food in range(4):
        assert art.frame(food=food) is frames[food]


@pytest.mark.parametrize("food", [True, False, -1, 4, 1.0, "1", float("nan")])
def test_invalid_quantities_cannot_silently_become_a_full_bowl(qt, food):
    with pytest.raises(ValueError, match="integer from 0 to 3"):
        BowlArt().frame(food=food)


@pytest.mark.parametrize("scale", [.6, 1, 1.5, 2.5])
def test_paint_preserves_state_and_scales_exact_common_anchor(qt, scale):
    art = BowlArt()
    boundaries = []
    for food in range(4):
        canvas = QImage(220, 150, QImage.Format.Format_ARGB32_Premultiplied)
        canvas.fill(Qt.GlobalColor.transparent)
        painter = QPainter(canvas)
        painter.setOpacity(.7)
        assert art.paint(painter, QRectF(15, 20, 62 * scale, 40 * scale), food=food)
        assert painter.opacity() == .7
        painter.end()
        occupied = [(x, y) for y in range(canvas.height()) for x in range(canvas.width())
                    if canvas.pixelColor(x, y).alpha() > 20]
        boundaries.append((min(x for x, y in occupied), min(y for x, y in occupied),
                           max(x for x, y in occupied), max(y for x, y in occupied)))
    assert len(set(boundaries)) == 1


@pytest.mark.parametrize("damage", ["missing", "no_alpha", "duplicate", "bad_crop", "bad_target", "bad_clip", "order"])
def test_bad_quantity_group_fails_atomically_and_preserves_empty(qt, tmp_path, damage):
    for name in ("bowl.json", "bowl.png", "bowl-food-1.png", "bowl-food-2.png", "bowl-food-3.png"):
        shutil.copy2(bowl_art.ASSETS / name, tmp_path / name)
    manifest = json.loads((bowl_art.ASSETS / "bowl-portions.json").read_text())
    if damage == "missing":
        (tmp_path / "bowl-food-3.png").unlink()
    elif damage == "no_alpha":
        path = tmp_path / "bowl-food-3.png"
        image = QImage(str(path)).convertToFormat(QImage.Format.Format_RGB32)
        assert image.save(str(path))
    elif damage == "duplicate":
        manifest["frames"][1]["file"] = manifest["frames"][0]["file"]
    elif damage == "bad_crop":
        manifest["frames"][2]["source_rect"][2] = 9999
    elif damage == "bad_target":
        manifest["frames"][2]["target_rect"][2] = float("nan")
    elif damage == "bad_clip":
        manifest["opening_clip"][1] = -1
    elif damage == "order":
        manifest["frames"].reverse()
    (tmp_path / "bowl-portions.json").write_text(json.dumps(manifest))
    art = BowlArt(tmp_path)
    assert art.load_portions() == {}
    assert art.portion_load_error and art.load_error is None
    assert all(art.frame(food=n) is None for n in (1, 2, 3))
    assert art.frame(food=0) is not None
    assert art.frame(full=True) is not None
