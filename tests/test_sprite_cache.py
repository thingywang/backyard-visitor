"""Untrusted/stale build artifacts must fail closed without decoding source atlases."""
import os
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import hashlib
import json
from pathlib import Path
from types import SimpleNamespace

import pytest
from PyQt6.QtGui import QColor, QImage, QPixmap
from PyQt6.QtWidgets import QApplication

from backyard_visitor import sprite_cache
from backyard_visitor.sprite_cache import load_compiled


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def rehash_sources(data):
    source = data["source_fingerprints"]
    source["combined_sha256"] = hashlib.sha256(json.dumps(source["files"], sort_keys=True).encode()).hexdigest()


@pytest.fixture(scope="module")
def qt():
    return QApplication.instance() or QApplication(["backyard-compiled-cache-test"])


@pytest.fixture
def cache_fixture(qt, tmp_path):
    package = tmp_path / "backyard_visitor"
    assets = package / "assets"
    directory = assets / "compiled-sprites"
    directory.mkdir(parents=True)
    (package / "art.py").write_text("renderer version 1\n")
    (package / "motion_art.py").write_text("motion renderer version 1\n")
    sources = {
        "sprites.json": {"file": "cats.png", "frames": []},
        "interactions.json": {"file": "interactions.png", "frames": []},
        "locomotion.json": {"frames": [{"file": "walk.png"}]},
    }
    for filename, data in sources.items():
        (assets / filename).write_text(json.dumps(data))
    # These deliberately are not decodable images. A successful load proves
    # source fingerprints use bytes, not accidental full-atlas QImage decoding.
    for filename in ("cats.png", "interactions.png", "walk.png"):
        (assets / filename).write_bytes(b"source pixels are not decoded in this test")
    source_paths = [package / "art.py", package / "motion_art.py", *assets.iterdir()]
    files = {str(path.relative_to(tmp_path)): sha(path) for path in source_paths if path.is_file()}

    def png_record(index, filename, width, height, dpr):
        image = QImage(width, height, QImage.Format.Format_ARGB32_Premultiplied)
        image.fill(QColor(80 + (index or 0)*10, 120, 70, 160))
        assert image.save(str(directory / filename))
        return {"index": index, "file": filename, "width": width, "height": height,
                "device_pixel_ratio": dpr, "png_sha256": sha(directory / filename)}

    frames = [png_record(i, f"frame-{i:02d}@2x.png", 288, 224, 2) for i in range(5)]
    data = {
        "schema_version": 1, "kind": "backyard-visitor-compiled-sprite-cache",
        "source_fingerprints": {"files": files}, "native_facing": -1,
        "logical_frame_size": [144, 112], "frame_count": 5, "frames": frames,
        "tail": png_record(None, "tail.png", 12, 20, 1), "base_frame_count": 2,
        "base_states": {"idle": [0], "walking": [1]},
        "states": {"idle": [0], "walking": [2], "window_peeking": [3, 4], "purring": [3, 4]},
        "optional_frame_groups": {"walking": [2], "window_peeking": [3, 4], "purring": []},
        "interaction_frame_indices": [3, 4],
        "blink_windows": {"window_peeking": {"period": 5, "closed_from": 3.6, "closed_until": 4}},
        "locomotion_cycle_seconds": .8,
    }
    rehash_sources(data)
    def save():
        (directory / "manifest.json").write_text(json.dumps(data))
    save()
    return SimpleNamespace(package=package, assets=assets, directory=directory, data=data, save=save)


def test_metadata_load_is_lazy_and_never_decodes_source_or_compiled_png(cache_fixture, monkeypatch):
    f = cache_fixture
    def unexpected_reader(*args):
        pytest.fail("Metadata validation must not invoke an image decoder")
    monkeypatch.setattr(sprite_cache, "QImageReader", unexpected_reader)
    compiled = load_compiled(f.assets)
    assert compiled is not None and compiled.manifest["base_frame_count"] == 2


def test_requested_frames_and_tail_restore_dimensions_dpr_without_retaining_qt_frames(cache_fixture):
    compiled = load_compiled(cache_fixture.assets)
    first, second = compiled.frame(0), compiled.frame(0)
    assert (first.width(), first.height(), first.devicePixelRatio()) == (288, 224, 2)
    assert first.cacheKey() != second.cacheKey()
    tail = compiled.tail()
    assert (tail.width(), tail.height(), tail.devicePixelRatio()) == (12, 20, 1)
    assert not any(isinstance(value, QPixmap) for value in vars(compiled).values())
    for invalid in (-1, 5, True, "0", None):
        assert compiled.frame(invalid) is None


@pytest.mark.parametrize("field", ["optional_frame_groups", "interaction_frame_indices", "blink_windows", "locomotion_cycle_seconds"])
def test_required_schema_fields_cannot_be_omitted(cache_fixture, field):
    f = cache_fixture; del f.data[field]; f.save()
    assert load_compiled(f.assets) is None


@pytest.mark.parametrize("path,value", [
    (("schema_version",), True), (("kind",), "other-cache"),
    (("frame_count",), 65), (("base_frame_count",), 0),
    (("frames", 0, "width"), 289), (("frames", 0, "device_pixel_ratio"), 1),
    (("frames", 0, "index"), 1), (("tail", "height"), 513),
    (("tail", "device_pixel_ratio"), 0), (("base_states", "idle"), [2]),
    (("states", "walking"), [5]), (("states", "walking"), [True]),
    (("optional_frame_groups", "walking"), [2, 3]),
    (("optional_frame_groups", "window_peeking"), [3]),
    (("interaction_frame_indices",), [4, 3]),
    (("blink_windows", "window_peeking", "closed_until"), 6),
    (("blink_windows", "window_peeking", "period"), float("nan")),
    (("locomotion_cycle_seconds",), None),
])
def test_invalid_geometry_states_groups_or_timing_rejects_whole_cache(cache_fixture, path, value):
    f = cache_fixture; target = f.data
    for key in path[:-1]: target = target[key]
    target[path[-1]] = value; f.save()
    assert load_compiled(f.assets) is None


@pytest.mark.parametrize("filename", ["../outside.png", "/tmp/outside.png", "nested/frame.png", "nested\\frame.png", "C:frame.png"])
def test_compiled_png_path_must_be_plain_filename(cache_fixture, filename):
    f = cache_fixture; f.data["frames"][0]["file"] = filename; f.save()
    assert load_compiled(f.assets) is None


def test_asset_path_traversal_in_source_fingerprints_is_rejected(cache_fixture):
    f = cache_fixture
    f.data["source_fingerprints"]["files"]["backyard_visitor/assets/../outside.png"] = "0"*64
    rehash_sources(f.data); f.save()
    assert load_compiled(f.assets) is None


def test_changed_source_asset_or_existing_renderer_invalidates_cache(cache_fixture):
    f = cache_fixture; target = f.assets / "cats.png"; original = target.read_bytes()
    target.write_bytes(original + b"changed")
    assert load_compiled(f.assets) is None
    target.write_bytes(original)
    (f.package / "art.py").write_text("new renderer\n")
    assert load_compiled(f.assets) is None


def test_packaged_absent_py_sources_are_skipped_but_assets_remain_required(cache_fixture):
    f = cache_fixture
    (f.package / "art.py").unlink(); (f.package / "motion_art.py").unlink()
    assert load_compiled(f.assets) is not None
    (f.assets / "cats.png").unlink()
    assert load_compiled(f.assets) is None


def test_omitting_atlas_fingerprint_cannot_hide_changed_source(cache_fixture):
    f = cache_fixture
    del f.data["source_fingerprints"]["files"]["backyard_visitor/assets/cats.png"]
    rehash_sources(f.data); f.save()
    assert load_compiled(f.assets) is None


def test_combined_source_digest_must_match_file_map(cache_fixture):
    f = cache_fixture; f.data["source_fingerprints"]["combined_sha256"] = "0"*64; f.save()
    assert load_compiled(f.assets) is None


def test_source_hashes_use_bounded_reads(cache_fixture, monkeypatch):
    f = cache_fixture
    source = f.assets / "cats.png"
    source.write_bytes(b"x"*(sprite_cache.HASH_CHUNK_BYTES*2+11))
    f.data["source_fingerprints"]["files"]["backyard_visitor/assets/cats.png"] = sha(source)
    rehash_sources(f.data); f.save()
    opened = Path.open; reads = []
    class BoundedReader:
        def __init__(self, stream): self.stream = stream
        def __enter__(self): self.stream.__enter__(); return self
        def __exit__(self, *args): return self.stream.__exit__(*args)
        def read(self, size=-1):
            assert 0 < size <= sprite_cache.HASH_CHUNK_BYTES
            reads.append(size)
            return self.stream.read(size)
    def guarded(path, mode="r", *args, **kwargs):
        stream = opened(path, mode, *args, **kwargs)
        return BoundedReader(stream) if mode == "rb" else stream
    monkeypatch.setattr(Path, "open", guarded)
    assert load_compiled(f.assets) is not None
    assert reads.count(sprite_cache.HASH_CHUNK_BYTES) > 2


@pytest.mark.parametrize("target", ["directory", "frame", "source"])
def test_symlinks_cannot_redirect_cache_or_source_files(cache_fixture, tmp_path, target):
    f = cache_fixture
    path = {"directory": f.directory, "frame": f.directory / f.data["frames"][0]["file"],
            "source": f.assets / "cats.png"}[target]
    outside = tmp_path / f"outside-{target}"
    path.rename(outside); path.symlink_to(outside, target_is_directory=(target == "directory"))
    assert load_compiled(f.assets) is None


def test_corrupt_optional_png_is_lazy_and_never_raises_to_caller(cache_fixture):
    f = cache_fixture
    (f.directory / f.data["frames"][4]["file"]).write_bytes(b"broken PNG")
    compiled = load_compiled(f.assets)
    assert compiled is not None and compiled.frame(0) is not None
    assert compiled.frame(4) is None


def test_png_dimension_mismatch_is_rejected_even_with_matching_file_hash(cache_fixture):
    f = cache_fixture; row = f.data["frames"][0]
    image = QImage(32, 32, QImage.Format.Format_ARGB32); image.fill(QColor("red"))
    assert image.save(str(f.directory / row["file"]))
    row["png_sha256"] = sha(f.directory / row["file"]); f.save()
    compiled = load_compiled(f.assets)
    assert compiled is not None and compiled.frame(0) is None


def test_cached_png_tampering_after_metadata_load_is_rechecked(cache_fixture):
    f = cache_fixture; compiled = load_compiled(f.assets)
    target = f.directory / f.data["frames"][0]["file"]
    target.write_bytes(target.read_bytes() + b"appended")
    assert compiled.frame(0) is None


def test_mutating_public_manifest_cannot_redirect_validated_frame_path(cache_fixture):
    compiled = load_compiled(cache_fixture.assets)
    compiled.manifest["frames"][0]["file"] = "/tmp/untrusted.png"
    assert compiled.frame(0) is not None


@pytest.mark.parametrize("contents", [b"[]", b"{", b"\xff", b" "*(128*1024+1), b'{"schema_version":1,"schema_version":1}'])
def test_malformed_oversized_or_duplicate_manifest_returns_none(cache_fixture, contents):
    f = cache_fixture; (f.directory / "manifest.json").write_bytes(contents)
    assert load_compiled(f.assets) is None


def test_missing_cache_returns_none(tmp_path):
    assert load_compiled(tmp_path) is None
