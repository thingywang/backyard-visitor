"""Material, anatomical mapping and Qt-safe fallbacks for generated coats."""
import hashlib
import json
import os
from pathlib import Path
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
import pytest
from PyQt6.QtGui import QColor, QImage, QPixmap
from PyQt6.QtWidgets import QApplication
from backyard_visitor import procedural_coat as renderer
from backyard_visitor.black_coat import source_key
from backyard_visitor.coat_mapping import MappingCatalog, CoatMappingError, MAPS, PART_NAMES
from backyard_visitor.coat_traits import CoatSpec
from backyard_visitor.parkour_art import ParkourArt, KINDS
from scripts.compile_black_coat import collect


@pytest.fixture(scope="module")
def qt():
    return QApplication.instance() or QApplication(["procedural-coat-material-test"])


@pytest.fixture(scope="module")
def sources(qt):
    art, frames = collect()
    park = ParkourArt()
    for kind in KINDS:
        for index in (0, 1):
            frames[f"parkour-{kind}-{index}"] = park.frame(kind, index, -1, "ginger")
    return frames


@pytest.fixture(autouse=True)
def clean_renderer():
    renderer.clear_procedural_cache()
    yield
    renderer.clear_procedural_cache()


def image_bytes(frame):
    image = frame.toImage().convertToFormat(QImage.Format.Format_RGBA8888)
    return image.constBits().asstring(image.sizeInBytes())


def alpha_bytes(frame):
    return image_bytes(frame)[3::4]


def test_catalog_covers_every_actual_source_and_is_rebuildable_without_output(sources):
    inventory = json.loads((MAPS / "source-inventory.json").read_text())
    rows = MappingCatalog().rows()
    assert inventory["count"] == len(inventory["frames"]) == len(rows) == len(sources) == 60
    assert {source_key(frame) for frame in sources.values()} == set(rows)
    assert {row["key"] for row in inventory["frames"]} == set(rows)
    for item in inventory["frames"]:
        assert not Path(item["neutral_file"]).is_absolute()
        assert ".." not in Path(item["neutral_file"]).parts
    # Locate script from repository root, not the generated output inventory.
    builder = MAPS.parents[2] / "scripts" / "build_coat_maps.py"
    assert "output/procedural" not in builder.read_text()


@pytest.mark.parametrize("label", [f"cat-{i:03d}" for i in range(39)] + ["tail", "rear"] +
                         [f"depth-away-{i}" for i in range(8)] +
                         [f"depth-toward-{i}" for i in range(4)] + ["depth-front-0"] +
                         [f"parkour-{kind}-{i}" for kind in KINDS for i in (0, 1)])
def test_every_map_covers_exact_neutral_silhouette_and_all_uvs_are_anatomical(sources, label):
    source = sources[label]
    catalog = MappingCatalog()
    row, mapping = catalog.get(source_key(source), (source.width(), source.height()))
    neutral = QPixmap(str(catalog.neutral_path(row)))
    raw = image_bytes(neutral)
    assert source.size() == neutral.size()
    for offset in range(0, len(raw), 4):
        code, u, v, valid = mapping[offset:offset + 4]
        if raw[offset + 3]:
            assert valid == 255
            assert 1 <= code & 7 <= len(PART_NAMES)
        else:
            assert valid == 0


def test_climb_body_and_moving_legs_do_not_collapse_into_canvas_center(sources):
    catalog = MappingCatalog()
    for index in (0, 1):
        source = sources[f"parkour-climb-{index}"]
        row, mapping = catalog.get(source_key(source), (source.width(), source.height()))
        counts = [sum(mapping[o + 3] > 0 and mapping[o] & 7 == part for o in range(0, len(mapping), 4)) for part in range(1, 6)]
        assert min(counts) > 100, counts
        body_uv = [(mapping[o + 1], mapping[o + 2]) for o in range(0, len(mapping), 4) if mapping[o + 3] and mapping[o] & 7 == 1]
        assert max(u for u, v in body_uv) - min(u for u, v in body_uv) > 150
        assert max(v for u, v in body_uv) - min(v for u, v in body_uv) > 150


def test_walking_torso_uses_same_anatomical_coordinates_while_paws_move(sources):
    catalog = MappingCatalog()
    samples = []
    for i in range(10, 14):
        source = sources[f"cat-{i:03d}"]
        row, mapping = catalog.get(source_key(source), (source.width(), source.height()))
        x, y = 70 * 2, 67 * 2
        pixel = mapping[(y * source.width() + x) * 4: (y * source.width() + x) * 4 + 4]
        assert pixel[0] & 7 == 1
        samples.append(pixel)
    assert len(set(samples)) == 1
    # Distinct moving paw endpoints are calibrated in the shared leg axis.
    rows = [catalog.rows()[source_key(sources[f"cat-{i:03d}"])] for i in range(10, 14)]
    endpoints = [next(r for r in row["regions"] if r["part"] == "front_leg")["path"][-1] for row in rows]
    assert len({tuple(p) for p in endpoints}) >= 3


def test_generated_canvas_alpha_and_seed_identity_survive_order_and_cache_reset(sources):
    spec = CoatSpec(120, pigment="tortie", white_mode="spotting", white_coverage=.45)
    labels = ["cat-010", "rear", "depth-toward-0", "parkour-climb-1", "tail"]
    expected = {}
    catalog = MappingCatalog()
    for label in labels:
        source = sources[label]
        result = renderer.procedural_frame(source, spec)
        neutral = QPixmap(str(catalog.neutral_path(catalog.rows()[source_key(source)])))
        assert result.size() == source.size()
        assert result.devicePixelRatio() == source.devicePixelRatio()
        assert alpha_bytes(result) == alpha_bytes(neutral)
        assert renderer.load_error is None
        expected[label] = image_bytes(result)
    renderer.clear_procedural_cache()
    for label in reversed(labels):
        assert image_bytes(renderer.procedural_frame(sources[label], spec)) == expected[label]


def test_white_fur_keeps_iris_and_nose_pixels_and_gains_light_without_flattening(sources):
    source = sources["cat-018"]
    catalog = MappingCatalog()
    row, mapping = catalog.get(source_key(source), (source.width(), source.height()))
    material = image_bytes(QPixmap(str(catalog.neutral_path(row))))
    result = image_bytes(renderer.procedural_frame(source, CoatSpec(21, white_mode="all-white", white_coverage=1)))
    protected = [o for o in range(0, len(material), 4) if mapping[o] >> 4 == 15 and material[o + 3] >= 250]
    assert len(protected) > 10
    assert all(abs(result[o+i]-material[o+i]) <= 1 for o in protected for i in range(3))
    fur = [o for o in range(0, len(material), 4) if mapping[o] >> 4 == 0 and material[o + 3] >= 250]
    values = sorted(result[o] for o in fur)
    assert 190 <= values[len(values)//2] <= 235
    assert values[-1] - values[0] > 50


def test_white_shadow_lift_never_changes_dark_material_response():
    for light in (0, 16, 32, 48, 64, 96):
        baseline = min(1.08, .30 + .68 * (light / 64) ** .72)
        assert renderer._fur_light(light, (43, 44, 47)) == baseline
        assert renderer._fur_light(light, (247, 243, 234)) >= baseline


@pytest.mark.parametrize("pattern", ["solid", "mackerel", "spotted", "classic", "ticked"])
def test_each_pattern_renders_a_distinct_field_over_identical_fur_geometry(sources, pattern):
    source = sources["cat-018"]
    generated = renderer.procedural_frame(source, CoatSpec(39, dark_pattern=pattern))
    assert renderer.load_error is None
    assert generated.size() == source.size()
    if pattern != "solid":
        plain = renderer.procedural_frame(source, CoatSpec(39))
        assert image_bytes(generated) != image_bytes(plain)


def test_cache_is_immune_to_caller_pixmap_mutation(sources):
    source = sources["cat-018"]
    spec = CoatSpec(13)
    first = renderer.procedural_frame(source, spec)
    expected = image_bytes(first)
    first.fill(QColor("magenta"))
    second = renderer.procedural_frame(source, spec)
    assert image_bytes(second) == expected
    second.fill(QColor("red"))
    assert image_bytes(renderer.procedural_frame(source, spec)) == expected


def test_mapping_failure_falls_back_to_plain_black_and_latches_between_good_frames(sources, caplog):
    source = sources["cat-018"]
    spec = CoatSpec(13)
    catalog = MappingCatalog()
    neutral = QPixmap(str(catalog.neutral_path(catalog.rows()[source_key(source)])))
    with patch.object(renderer._catalog, "get", side_effect=CoatMappingError("broken test map")):
        result = renderer.procedural_frame(source, spec)
        renderer.procedural_frame(source, spec)
    assert image_bytes(result) == image_bytes(neutral)
    assert result.devicePixelRatio() == source.devicePixelRatio()
    assert "随机毛色素材暂不可用" in renderer.load_error
    latched = renderer.pending_error
    renderer.procedural_frame(sources["cat-010"], spec)
    assert renderer.load_error is None
    assert renderer.pending_error == latched
    assert sum("broken test map" in record.message for record in caplog.records) == 1
    renderer.clear_procedural_cache()
    assert renderer.load_error is None and renderer.pending_error is None


def test_unknown_source_uses_flat_gray_rgb_not_original_tabby(qt):
    image = QImage(8, 4, QImage.Format.Format_ARGB32_Premultiplied)
    for y in range(4):
        for x in range(8):
            image.setPixelColor(x, y, QColor(220 if x%2 else 10, 80, 10, (0, 64, 128, 255)[y]))
    source = QPixmap.fromImage(image); source.setDevicePixelRatio(2)
    result = renderer.procedural_frame(source, CoatSpec(7))
    assert result.size() == source.size() and result.devicePixelRatio() == 2
    assert alpha_bytes(result) == alpha_bytes(source)
    raw = image_bytes(result)
    assert all(raw[o:o+3] == bytes((70,72,76)) for o in range(8*3*4, len(raw), 4))
    assert renderer.pending_error


def test_catalog_rejects_bad_map_before_qt_rendering(qt, tmp_path):
    image = QImage(2, 2, QImage.Format.Format_RGBA8888);image.fill(QColor(1, 40, 80, 255))
    image.save(str(tmp_path / "key.png"))
    data = {"version": 1, "kind": "backyard-anatomical-coat-maps", "frames": {"key": {"size": [2, 2], "map_sha256": "0" * 64}}}
    (tmp_path / "manifest.json").write_text(json.dumps(data))
    catalog = MappingCatalog(tmp_path)
    with pytest.raises(CoatMappingError, match="checksum"):
        catalog.get("key", (2, 2))
    with pytest.raises(CoatMappingError, match="canvas"):
        catalog.get("key", (3, 2))


def test_dpr_metadata_does_not_change_material_pixels_after_cache_reset(sources):
    spec = CoatSpec(120, pigment="tortie", white_mode="spotting", white_coverage=.45)
    source = sources["cat-018"]
    expected = image_bytes(renderer.procedural_frame(source, spec))
    renderer.clear_procedural_cache()
    other_dpr = QPixmap(source);other_dpr.setDevicePixelRatio(1)
    actual = renderer.procedural_frame(other_dpr, spec)
    assert actual.devicePixelRatio() == 1
    assert image_bytes(actual) == expected
