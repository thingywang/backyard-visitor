"""Validated, lazy access to disposable small-PNG sprite build artifacts.

Source atlases are hashed in chunks, never decoded here. No QPixmap is retained:
CatArt owns the lifetime of requested frames and its color-variant cache.
"""
from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import math
from pathlib import Path
import re

from PyQt6.QtGui import QGuiApplication, QImageReader, QPixmap

MAX_MANIFEST_BYTES = 128 * 1024
MAX_PNG_BYTES = 2 * 1024 * 1024
HASH_CHUNK_BYTES = 256 * 1024
MAX_FRAMES = 64
_KIND = "backyard-visitor-compiled-sprite-cache"
_SHA = re.compile(r"[0-9a-f]{64}\Z")
_NAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.@-]{0,127}\Z")
_STATE = re.compile(r"[a-z][a-z0-9_]{0,63}\Z")
_SOURCE_MANIFESTS = ("sprites.json", "extra.json", "interactions.json", "locomotion.json")
_ERRORS = (OSError, ValueError, TypeError, KeyError, IndexError, AttributeError,
           OverflowError, RecursionError, RuntimeError)


def _require(condition):
    if not condition:
        raise ValueError("Invalid compiled sprite cache")


def _no_duplicate_keys(pairs):
    result = {}
    for key, value in pairs:
        _require(key not in result)
        result[key] = value
    return result


def _read_json(path):
    _require(path.is_file() and not path.is_symlink())
    with path.open("rb") as stream:
        raw = stream.read(MAX_MANIFEST_BYTES + 1)
    _require(len(raw) <= MAX_MANIFEST_BYTES)
    value = json.loads(raw.decode("utf-8"), object_pairs_hook=_no_duplicate_keys)
    _require(isinstance(value, dict))
    return value


def _sha256(path, max_bytes=None):
    _require(path.is_file() and not path.is_symlink())
    if max_bytes is not None:
        _require(path.stat().st_size <= max_bytes)
    digest = hashlib.sha256()
    total = 0
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(HASH_CHUNK_BYTES), b""):
            total += len(block)
            if max_bytes is not None:
                _require(total <= max_bytes)
            digest.update(block)
    return digest.hexdigest()


def _sha(value):
    _require(isinstance(value, str) and _SHA.fullmatch(value) is not None)
    return value


def _child(directory, name, *, png=False):
    # The compiler emits flat filenames. No configurable directory, traversal,
    # platform-specific separator, drive prefix, or symlink is permitted.
    _require(isinstance(name, str) and _NAME.fullmatch(name) is not None)
    _require(name not in (".", "..") and (not png or name.endswith(".png")))
    path = directory / name
    _require(path.is_file() and not path.is_symlink())
    _require(path.resolve(strict=True).parent == directory)
    return path


def _integer(value, minimum, maximum):
    _require(type(value) is int and minimum <= value <= maximum)
    return value


def _finite(value):
    _require(type(value) in (int, float) and math.isfinite(value))
    return value


def _indices(value, minimum, maximum, *, empty=False, unique=False):
    _require(isinstance(value, list) and len(value) <= MAX_FRAMES)
    _require(empty or bool(value))
    for index in value:
        _integer(index, minimum, maximum)
    if unique:
        _require(len(value) == len(set(value)))
    return value


def _states(value, count):
    _require(isinstance(value, dict) and 0 < len(value) <= MAX_FRAMES)
    for state, indices in value.items():
        _require(isinstance(state, str) and _STATE.fullmatch(state) is not None)
        _indices(indices, 0, count-1)
    return value


def _validate_sources(assets, fingerprints):
    _require(isinstance(fingerprints, dict))
    files = fingerprints["files"]
    _require(isinstance(files, dict) and 1 <= len(files) <= 128)
    _require(_sha(fingerprints["combined_sha256"]) == hashlib.sha256(
        json.dumps(files, sort_keys=True).encode()).hexdigest())
    _require("backyard_visitor/art.py" in files)
    expected = set()
    # Validate current source coverage as well as listed hashes. Removing a
    # changed atlas from the fingerprint list must not make stale frames valid.
    for name in _SOURCE_MANIFESTS:
        path = assets / name
        if not path.exists():
            _require(name != "sprites.json")
            continue
        data = _read_json(_child(assets, name))
        expected.add(f"backyard_visitor/assets/{name}")
        if "file" in data:
            _child(assets, data["file"])
            expected.add(f"backyard_visitor/assets/{data['file']}")
        rows = data.get("frames", [])
        _require(isinstance(rows, list) and len(rows) <= MAX_FRAMES)
        for row in rows:
            _require(isinstance(row, dict))
            if "file" in row:
                _child(assets, row["file"])
                expected.add(f"backyard_visitor/assets/{row['file']}")
        if name == "locomotion.json":
            _require("backyard_visitor/motion_art.py" in files)
    _require(expected.issubset(files))
    for relative, digest in files.items():
        _sha(digest)
        if relative in ("backyard_visitor/art.py", "backyard_visitor/motion_art.py"):
            path = assets.parent / relative.split("/")[-1]
            # PyInstaller need not ship loose Python sources. Existing local
            # sources must match; only these two absent code files may be skipped.
            if not path.exists():
                _require(not path.is_symlink())
                continue
            path = _child(assets.parent, path.name)
        else:
            _require(isinstance(relative, str) and relative.startswith("backyard_visitor/assets/"))
            name = relative.removeprefix("backyard_visitor/assets/")
            path = _child(assets, name)
        _require(_sha256(path) == digest)


@dataclass(frozen=True)
class _ImageRecord:
    filename: str
    width: int
    height: int
    dpr: float
    sha256: str


def _record(row, cache_dir, *, index=None, tail=False):
    _require(isinstance(row, dict))
    if tail:
        _require(row.get("index") is None)
        width = _integer(row["width"], 1, 512)
        height = _integer(row["height"], 1, 512)
        _require(_finite(row["device_pixel_ratio"]) == 1)
    else:
        _require(type(row.get("index")) is int and row["index"] == index)
        width = _integer(row["width"], 288, 288)
        height = _integer(row["height"], 224, 224)
        _require(_finite(row["device_pixel_ratio"]) == 2)
    _child(cache_dir, row["file"], png=True)
    digest = _sha(row["png_sha256"])
    # These compiler diagnostics are optional, but may never contradict shape.
    if "pixel_sha256" in row:
        _sha(row["pixel_sha256"])
    if "pixel_bytes" in row:
        _require(type(row["pixel_bytes"]) is int and row["pixel_bytes"] == width*height*4)
    if "pixel_format" in row:
        _require(row["pixel_format"] == "RGBA8888_Premultiplied")
    if "pixel_roundtrip_equal" in row:
        _require(row["pixel_roundtrip_equal"] is True)
    return _ImageRecord(row["file"], width, height, row["device_pixel_ratio"], digest)


def _validate_manifest(data, cache_dir):
    _require(type(data.get("schema_version")) is int and data["schema_version"] == 1)
    _require(data.get("kind") == _KIND)
    _require(data.get("logical_frame_size") == [144, 112])
    _require(type(data.get("native_facing")) is int and data["native_facing"] in (-1, 1))
    count = _integer(data["frame_count"], 1, MAX_FRAMES)
    base = _integer(data["base_frame_count"], 1, count)
    rows = data["frames"]
    _require(isinstance(rows, list) and len(rows) == count)
    records = tuple(_record(row, cache_dir, index=i) for i, row in enumerate(rows))
    tail = _record(data["tail"], cache_dir, tail=True)
    _require(len({row.filename for row in (*records, tail)}) == count+1)
    base_states = _states(data["base_states"], base)
    states = _states(data["states"], count)
    _require(base_states.keys() <= states.keys())
    groups = data["optional_frame_groups"]
    _require(isinstance(groups, dict) and len(groups) <= MAX_FRAMES)
    used = set()
    for state, indices in groups.items():
        _require(state in states)
        _indices(indices, base, count-1, empty=True, unique=True)
        _require(not used.intersection(indices))
        used.update(indices)
    _require(used == set(range(base, count)))
    actual = _indices(data["interaction_frame_indices"], base, count-1, empty=True, unique=True)
    _require(actual == groups.get("window_peeking", []) + groups.get("purring", []))
    blinks = data["blink_windows"]
    _require(isinstance(blinks, dict) and len(blinks) <= MAX_FRAMES)
    for state, blink in blinks.items():
        _require(state in states and len(states[state]) >= 2 and isinstance(blink, dict))
        period = _finite(blink["period"])
        start, end = _finite(blink["closed_from"]), _finite(blink["closed_until"])
        _require(0 < period <= 3600 and 0 <= start < end <= period)
    cycle = data["locomotion_cycle_seconds"]
    if cycle is not None:
        _require(0 < _finite(cycle) <= 60)
    if groups.get("walking"):
        _require(cycle is not None)
    return records, tail


class CompiledSprites:
    """Metadata plus lazy accessors; intentionally owns no decoded Qt frames."""

    def __init__(self, manifest, cache_dir, records, tail_record):
        self.manifest = manifest
        self._cache_dir = cache_dir
        self._records = records
        self._tail_record = tail_record

    def _read(self, record):
        try:
            if QGuiApplication.instance() is None:
                return None
            path = _child(self._cache_dir, record.filename, png=True)
            _require(_sha256(path, MAX_PNG_BYTES) == record.sha256)
            reader = QImageReader(str(path))
            _require(bytes(reader.format()).lower() == b"png")
            size = reader.size()
            _require((size.width(), size.height()) == (record.width, record.height))
            image = reader.read()
            _require(not image.isNull() and image.hasAlphaChannel())
            _require((image.width(), image.height()) == (record.width, record.height))
            frame = QPixmap.fromImage(image)
            _require(not frame.isNull())
            frame.setDevicePixelRatio(record.dpr)
            return frame
        except _ERRORS:
            return None

    def frame(self, index) -> QPixmap | None:
        if type(index) is not int or not 0 <= index < len(self._records):
            return None
        return self._read(self._records[index])

    def tail(self) -> QPixmap | None:
        return self._read(self._tail_record)


def load_compiled(assets: Path) -> CompiledSprites | None:
    """Validate cache/source metadata without decoding any atlas or small PNG."""
    try:
        assets = Path(assets).resolve(strict=True)
        _require(assets.is_dir())
        cache_dir = assets / "compiled-sprites"
        _require(cache_dir.is_dir() and not cache_dir.is_symlink())
        _require(cache_dir.resolve(strict=True).parent == assets)
        data = _read_json(_child(cache_dir, "manifest.json"))
        records, tail = _validate_manifest(data, cache_dir)
        _validate_sources(assets, data["source_fingerprints"])
        return CompiledSprites(data, cache_dir, records, tail)
    except _ERRORS:
        return None
