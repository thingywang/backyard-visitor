"""Black coats preserve source canvases while validating either alpha policy."""
import hashlib
import json
import logging
import os
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
import pytest
from PyQt6.QtCore import QRectF, Qt
from PyQt6.QtGui import QColor, QImage, QPainter, QPixmap, QTransform
from PyQt6.QtWidgets import QApplication

from backyard_visitor import black_coat
from backyard_visitor.art import CatArt
from backyard_visitor.depth_art import COUNTS, DepthArt
from backyard_visitor.rear_art import RearArt


@pytest.fixture(scope="module")
def qt():
    return QApplication.instance() or QApplication(["black-coat-art-test"])


def alpha(frame):
    image = frame.toImage().convertToFormat(QImage.Format.Format_Alpha8)
    image.setDevicePixelRatio(1)
    return image


def rgba(frame):
    image = frame.toImage().convertToFormat(QImage.Format.Format_RGBA8888)
    image.setDevicePixelRatio(1)
    return image


def alpha_metadata(frame):
    """Independent pixel-wise expectation, including odd-width Alpha8 images."""
    image = frame.toImage()
    values = bytes(image.pixelColor(x, y).alpha()
                   for y in range(image.height()) for x in range(image.width()))
    visible = [(x, y) for y in range(image.height()) for x in range(image.width())
               if image.pixelColor(x, y).alpha()]
    bounds = ([min(x for x, _ in visible), min(y for _, y in visible),
               max(x for x, _ in visible) + 1, max(y for _, y in visible) + 1]
              if visible else [0, 0, 0, 0])
    return dict(alpha_sha256=hashlib.sha256(values).hexdigest(), alpha_bounds=bounds,
                alignment_offset_px=[0, 0])


def authored_fixture(folder, *, geometry_policy=None, width=8):
    source = QImage(width, 4, QImage.Format.Format_ARGB32_Premultiplied)
    target = QImage(width, 4, QImage.Format.Format_ARGB32_Premultiplied)
    for y in range(4):
        for x in range(width):
            a = (0, 32, 128, 255)[y]
            source.setPixelColor(x, y, QColor(145 + x * 10, 80 + x * 8, 25 + x * 5, a))
            authored_alpha = ((0, 28, 112, 255)[y] if 0 < x < width - 1 else 0)
            target_alpha = authored_alpha if geometry_policy == "authored-alpha-aligned" else a
            target.setPixelColor(x, y, QColor(20 + x * 5, 21 + x * 5, 23 + x * 5, target_alpha))
    original = QPixmap.fromImage(source)
    original.setDevicePixelRatio(2)
    expected = QPixmap.fromImage(target)
    expected.setDevicePixelRatio(2)
    key = black_coat.source_key(original)
    path = folder / (key + ".png")
    assert expected.save(str(path), "PNG")
    row = dict(file=path.name, sha256=hashlib.sha256(path.read_bytes()).hexdigest(), size=[width, 4], uses=["fixture"])
    manifest = dict(schema_version=1, kind="backyard-visitor-black-coat", frames={key: row})
    if geometry_policy is not None:
        manifest["geometry_policy"] = geometry_policy
    if geometry_policy == "authored-alpha-aligned":
        row.update(alpha_metadata(expected))
    (folder / "manifest.json").write_text(json.dumps(manifest))
    return original, expected, path, manifest


@pytest.mark.parametrize("policy", [None, "source-alpha"])
def test_authored_pixels_keep_original_alpha_canvas_and_dpr(qt, tmp_path, policy):
    original, expected, _, _ = authored_fixture(tmp_path, geometry_policy=policy)
    loader = black_coat.BlackCoat(tmp_path)
    actual = loader.frame(original)
    assert loader.load_error is None
    assert rgba(actual) == rgba(expected)
    assert alpha(actual) == alpha(original)
    assert actual.size() == original.size()
    assert actual.devicePixelRatio() == original.devicePixelRatio() == 2


@pytest.mark.parametrize("damage", ["missing", "checksum", "size", "alpha", "manifest", "filename"])
def test_invalid_asset_returns_black_shading_and_warns_once(qt, tmp_path, caplog, damage):
    original, _, path, manifest = authored_fixture(tmp_path)
    row = next(iter(manifest["frames"].values()))
    if damage == "missing":
        path.unlink()
    elif damage == "checksum":
        path.write_bytes(b"broken png")
    elif damage == "size":
        row["size"] = [8, 5]
    elif damage == "alpha":
        image = QImage(str(path))
        image.setPixelColor(0, 0, QColor(20, 20, 20, 255))
        assert image.save(str(path), "PNG")
        row["sha256"] = hashlib.sha256(path.read_bytes()).hexdigest()
    elif damage == "manifest":
        manifest["schema_version"] = 99
    elif damage == "filename":
        row["file"] = "../outside.png"
    (tmp_path / "manifest.json").write_text(json.dumps(manifest))
    loader = black_coat.BlackCoat(tmp_path)
    with caplog.at_level(logging.WARNING, logger=black_coat.__name__):
        actual = loader.frame(original)
        repeated = loader.frame(original)
    assert loader.load_error
    assert len([r for r in caplog.records if r.name == black_coat.__name__]) == 1
    assert rgba(actual) == rgba(repeated)
    assert alpha(actual) == alpha(original)
    assert actual.devicePixelRatio() == 2
    colors = [actual.toImage().pixelColor(x, 3) for x in range(8)]
    assert len({c.red() for c in colors}) > 1  # Still shaded, not a flat silhouette.
    assert all(max(c.red(), c.green(), c.blue()) < 100 for c in colors)
    assert all(c.blue() >= c.red() for c in colors)  # No brown coat on failure.


@pytest.mark.parametrize("policy", [None, "authored-alpha-aligned"])
def test_repaired_asset_can_load_without_restarting_loader(qt, tmp_path, policy):
    original, expected, path, _ = authored_fixture(tmp_path, geometry_policy=policy)
    packed = path.read_bytes()
    path.unlink()
    loader = black_coat.BlackCoat(tmp_path)
    assert alpha(loader.frame(original)) == alpha(original)
    assert loader.load_error
    path.write_bytes(packed)
    assert rgba(loader.frame(original)) == rgba(expected)
    assert loader.load_error is None


def test_manifest_source_key_matches_offline_compiler(qt, tmp_path):
    from scripts.compile_black_coat import source_key
    original, _, _, _ = authored_fixture(tmp_path)
    assert black_coat.source_key(original) == source_key(original)


@pytest.mark.parametrize("width", [7, 8])
def test_authored_alpha_keeps_own_pixels_on_original_canvas_and_dpr(qt, tmp_path, width):
    original, expected, _, _ = authored_fixture(
        tmp_path, geometry_policy="authored-alpha-aligned", width=width)
    original.setDevicePixelRatio(1.5)
    expected.setDevicePixelRatio(1.5)
    loader = black_coat.BlackCoat(tmp_path)
    actual = loader.frame(original)
    assert loader.load_error is None
    assert rgba(actual) == rgba(expected)  # No crop, resize, or offset.
    assert alpha(actual) != alpha(original)
    assert actual.size() == original.size()
    assert actual.devicePixelRatio() == 1.5


@pytest.mark.parametrize("damage", ["alpha_digest", "alpha_digest_format", "bounds", "bounds_outside",
                                    "bounds_empty", "bounds_type", "offset", "offset_type",
                                    "missing_metadata", "changed_alpha", "empty_alpha", "canvas",
                                    "source_alpha_policy"])
def test_invalid_authored_geometry_falls_back_and_recovers(qt, tmp_path, caplog, damage):
    original, expected, path, manifest = authored_fixture(
        tmp_path, geometry_policy="authored-alpha-aligned", width=7)
    valid_manifest = json.dumps(manifest)
    packed = path.read_bytes()
    row = next(iter(manifest["frames"].values()))
    if damage == "alpha_digest":
        row["alpha_sha256"] = "0" * 64
    elif damage == "alpha_digest_format":
        row["alpha_sha256"] = "not-a-sha256"
    elif damage == "bounds":
        row["alpha_bounds"][0] = 0
    elif damage == "bounds_outside":
        row["alpha_bounds"][2] = 8
    elif damage == "bounds_empty":
        row["alpha_bounds"] = [0, 0, 0, 0]
    elif damage == "bounds_type":
        row["alpha_bounds"][0] = 1.0
    elif damage == "offset":
        row["alignment_offset_px"] = [1, 0]
    elif damage == "offset_type":
        row["alignment_offset_px"] = [False, 0]
    elif damage == "missing_metadata":
        del row["alpha_sha256"]
    elif damage in {"changed_alpha", "empty_alpha", "canvas"}:
        image = QImage(str(path))
        if damage == "changed_alpha":
            image.setPixelColor(0, 0, QColor(20, 20, 20, 255))
        elif damage == "empty_alpha":
            image.fill(Qt.GlobalColor.transparent)
            row.update(alpha_metadata(QPixmap.fromImage(image)))
        else:
            image = image.copy(0, 0, 6, 4)
        assert image.save(str(path), "PNG")
        row["sha256"] = hashlib.sha256(path.read_bytes()).hexdigest()
    elif damage == "source_alpha_policy":
        manifest["geometry_policy"] = "source-alpha"
    (tmp_path / "manifest.json").write_text(json.dumps(manifest))
    loader = black_coat.BlackCoat(tmp_path)
    with caplog.at_level(logging.WARNING, logger=black_coat.__name__):
        actual = loader.frame(original)
        repeated = loader.frame(original)
    assert loader.load_error
    assert rgba(actual) == rgba(repeated) == rgba(black_coat.fallback_frame(original))
    assert alpha(actual) == alpha(original)
    assert actual.size() == original.size()
    assert actual.devicePixelRatio() == original.devicePixelRatio()
    assert len([r for r in caplog.records if r.name == black_coat.__name__]) == 1
    path.write_bytes(packed)
    (tmp_path / "manifest.json").write_text(valid_manifest)
    assert rgba(loader.frame(original)) == rgba(expected)
    assert loader.load_error is None


@pytest.mark.parametrize("policy", ["unknown", None, [], {}])
def test_unknown_geometry_policy_does_not_bypass_alpha_validation(qt, tmp_path, policy):
    original, _, _, manifest = authored_fixture(tmp_path)
    manifest["geometry_policy"] = policy
    (tmp_path / "manifest.json").write_text(json.dumps(manifest))
    loader = black_coat.BlackCoat(tmp_path)
    actual = loader.frame(original)
    assert loader.load_error
    assert rgba(actual) == rgba(black_coat.fallback_frame(original))


def assert_production_geometry(original, dark):
    manifest = json.loads((black_coat.ASSETS / "manifest.json").read_text())
    row = manifest["frames"][black_coat.source_key(original)]
    assert dark.size() == original.size()
    assert dark.devicePixelRatio() == original.devicePixelRatio()
    assert row["size"] == [original.width(), original.height()]
    if manifest.get("geometry_policy", "source-alpha") == "source-alpha":
        assert alpha(dark) == alpha(original)
    else:
        assert manifest["geometry_policy"] == "authored-alpha-aligned"
        for name, expected in alpha_metadata(dark).items():
            assert row[name] == expected


@pytest.mark.parametrize("state", ["standing", "idle", "walking", "running", "jumping", "takeoff", "landing",
                                   "sleeping", "belly", "resting", "play_crouch", "swatting", "eating",
                                   "grooming", "carried", "window_peeking", "purring"])
def test_production_states_keep_canvas_and_mirror_the_same_black_pose(qt, state):
    art = CatArt(prefer_compiled=False)
    original = art.frame(state, 0, -1)
    dark = art.frame(state, 0, -1, "black")
    assert black_coat.load_error is None, black_coat.load_error
    assert_production_geometry(original, dark)
    assert rgba(dark) != rgba(original)
    right = art.frame(state, 0, 1, "black")
    expected = dark.transformed(QTransform().scale(-1, 1))
    assert rgba(right) == rgba(expected)
    assert art.frame(state, 0, -1, "black").cacheKey() == dark.cacheKey()


def paint_tail(art, facing, coat):
    image = QImage(288, 224, QImage.Format.Format_ARGB32_Premultiplied)
    image.fill(Qt.GlobalColor.transparent)
    image.setDevicePixelRatio(2)
    painter = QPainter(image)
    art.paint(painter, QRectF(0, 0, 144, 112), "peeking", .25, facing, coat)
    painter.end()
    return image


@pytest.mark.parametrize("facing", [-1, 1])
def test_edge_tail_uses_black_variant_before_existing_rotation(qt, facing):
    art = CatArt(prefer_compiled=False)
    actual = paint_tail(art, facing, "black")
    original = art.tail
    recolored = black_coat.black_frame(original)
    assert black_coat.load_error is None, black_coat.load_error
    art.tail = recolored
    expected = paint_tail(art, facing, "ginger")
    art.tail = original
    assert actual == expected
    assert len(art._tail_coat_cache) == 1
    assert actual != paint_tail(art, facing, "ginger")


def test_rear_and_all_depth_frames_use_same_validated_black_assets(qt):
    rear = RearArt()
    original = rear.frame("ginger")
    dark = rear.frame("black")
    assert black_coat.load_error is None, black_coat.load_error
    assert rgba(dark) == rgba(black_coat.black_frame(original))
    assert_production_geometry(original, dark)
    assert rear.frame("black") is dark
    depth = DepthArt()
    for kind, count in COUNTS.items():
        for index in range(count):
            original = depth.frame(kind, index)
            dark = depth.frame(kind, index, "black")
            assert black_coat.load_error is None, black_coat.load_error
            assert rgba(dark) == rgba(black_coat.black_frame(original))
            assert_production_geometry(original, dark)
            assert depth.frame(kind, index, "black").cacheKey() == dark.cacheKey()
