"""Six authored directional jump/climb poses on the shared Retina canvas.

Pose choice uses model motion only; this module owns no animation clock.
Black fur has separately authored PNGs and never comes from darkening tabby.
"""
from __future__ import annotations

from collections import OrderedDict
import hashlib
import json
import logging
import math
from pathlib import Path
import re

from PyQt6.QtCore import QSize
from PyQt6.QtGui import QColor, QImage, QImageReader, QPainter, QPixmap, QTransform

from .black_coat import _alpha_geometry

from .coat_traits import CoatSpec

ASSETS = Path(__file__).with_name("assets") / "parkour"
DISPLAY_SIZE = (144, 112)
PIXEL_SIZE = (288, 224)
CLIMB_CONTACT_X = 88
HIGH_JUMP_SPAN = 80
KINDS = ("jump_up", "jump_down", "climb")
_SHA = re.compile(r"[0-9a-f]{64}\Z")
_LOG = logging.getLogger(__name__)


def _finite(value):
    return type(value) in (int, float) and math.isfinite(value)


def select_parkour_pose(state, *, jump_vy=None, jump_pose_phase=None,
                        jump_vertical_span=None, climb_phase=None):
    """Return kind/index, or None for ordinary poses and short horizontal hops."""
    if state == "climbing":
        phase = climb_phase % 1 if _finite(climb_phase) else 0.
        return "climb", min(1, int(phase * 2))
    if (state != "jumping" or not _finite(jump_vy)
            or not _finite(jump_vertical_span) or jump_vertical_span < HIGH_JUMP_SPAN):
        return None
    phase = max(0., min(1., jump_pose_phase)) if _finite(jump_pose_phase) else 0.
    return ("jump_up" if jump_vy < 0 else "jump_down"), min(1, int(phase * 2))


class ParkourArt:
    """Validate all twelve PNGs atomically; missing art returns None visibly."""

    def __init__(self, assets: Path | None = None):
        self.assets = Path(assets) if assets is not None else ASSETS
        self.load_error: str | None = None
        self._checked = False
        self._rows = {}
        self._cache = OrderedDict()
        self.cache_limit = 16

    def _fail(self, error):
        self.load_error = f"跑酷素材不可用：{error}"
        self._rows.clear()
        self._cache.clear()
        _LOG.warning(self.load_error)

    @property
    def available(self):
        self._load_manifest()
        return self.load_error is None

    def _load_manifest(self):
        if self._checked:
            return
        self._checked = True
        try:
            manifest = self.assets / "manifest.json"
            if manifest.is_symlink() or manifest.stat().st_size > 128 * 1024:
                raise ValueError("清单文件无效")
            data = json.loads(manifest.read_text(encoding="utf-8"))
            if (data.get("schema_version") != 1 or data.get("kind") != "backyard-visitor-parkour"
                    or data.get("accepted") is not True
                    or data.get("display_size") != list(DISPLAY_SIZE)
                    or data.get("pixel_size") != list(PIXEL_SIZE)
                    or data.get("pixel_ratio") != 2 or data.get("native_facing") != -1):
                raise ValueError("画布、方向或验收标记不符")
            frames = data["frames"]
            if not isinstance(frames, list) or len(frames) != 6:
                raise ValueError("需要两张上跳、两张下跳和两张攀爬姿势")
            staged, filenames = {}, set()
            for row in frames:
                kind, index = row["kind"], row["index"]
                if kind not in KINDS or type(index) is not int or index not in (0, 1):
                    raise ValueError("姿势类型或索引无效")
                variants = row["variants"]
                if not isinstance(variants, dict) or set(variants) != {"base", "black"}:
                    raise ValueError("每个姿势需要基础及独立纯黑素材")
                for coat, info in variants.items():
                    key = (kind, index, coat)
                    name, digest = info["file"], info["sha256"]
                    if (key in staged or not isinstance(name, str) or Path(name).name != name
                            or not name.endswith(".png") or name in filenames
                            or not isinstance(digest, str) or _SHA.fullmatch(digest) is None):
                        raise ValueError("重复姿势或素材文件无效")
                    bounds, mouth, contact = info["alpha_bounds"], info["mouth_anchor"], info["climb_contact_x"]
                    if (not isinstance(bounds, list) or len(bounds) != 4
                            or any(type(n) is not int for n in bounds)
                            or not 0 <= bounds[0] < bounds[2] <= PIXEL_SIZE[0]
                            or not 0 <= bounds[1] < bounds[3] <= PIXEL_SIZE[1]
                            or not isinstance(mouth, list) or len(mouth) != 2
                            or any(not _finite(n) for n in mouth)
                            or not 0 <= mouth[0] <= DISPLAY_SIZE[0]
                            or not 0 <= mouth[1] <= DISPLAY_SIZE[1]):
                        raise ValueError("透明边界或嘴部锚点无效")
                    if ((kind == "climb" and (not _finite(contact) or contact != CLIMB_CONTACT_X))
                            or (kind != "climb" and contact is not None)):
                        raise ValueError("攀爬接触线必须统一为逻辑 x=88")
                    path = self.assets / name
                    if path.is_symlink() or not path.is_file() or path.stat().st_size > 4 * 1024 * 1024:
                        raise ValueError(f"素材文件不可用：{name}")
                    raw = path.read_bytes()
                    if hashlib.sha256(raw).hexdigest() != digest:
                        raise ValueError(f"素材校验失败：{name}")
                    if QImageReader(str(path)).size() != QSize(*PIXEL_SIZE):
                        raise ValueError(f"素材画布不符：{name}")
                    image = QImage.fromData(raw, "PNG")
                    if (image.isNull() or not image.hasAlphaChannel()
                            or _alpha_geometry(image)[1] != bounds):
                        raise ValueError(f"素材透明边界不符：{name}")
                    staged[key] = dict(info, path=path)
                    filenames.add(name)
            if set(staged) != {(kind, i, coat) for kind in KINDS for i in (0, 1) for coat in ("base", "black")}:
                raise ValueError("姿势清单不完整")
            self._rows = staged
        except (OSError, ValueError, KeyError, TypeError, AttributeError, RuntimeError) as error:
            self._fail(error)

    def frame(self, kind, index, facing=-1, coat="ginger") -> QPixmap | None:
        self._load_manifest()
        if self.load_error is not None or kind not in KINDS or index not in (0, 1):
            return None
        key = (kind, index, facing, coat)
        if key in self._cache:
            self._cache.move_to_end(key)
            return self._cache[key]
        info = self._rows[(kind, index, "black" if coat == "black" else "base")]
        frame = QPixmap(str(info["path"]))
        if frame.isNull() or frame.size() != QSize(*PIXEL_SIZE) or not frame.hasAlphaChannel():
            self._fail(f"素材解码失败：{info['file']}")
            return None
        frame.setDevicePixelRatio(2)
        if isinstance(coat, CoatSpec):
            from .procedural_coat import procedural_frame
            neutral = QPixmap(str(self._rows[(kind, index, "black")]["path"]))
            neutral.setDevicePixelRatio(2)
            frame = procedural_frame(frame, coat, pose_key=f"parkour:{kind}:{index}", neutral=neutral)
        elif coat == "gray":
            image = frame.toImage()
            image.setDevicePixelRatio(1)
            alpha = image.convertToFormat(QImage.Format.Format_Alpha8)
            image = image.convertToFormat(QImage.Format.Format_Grayscale8).convertToFormat(QImage.Format.Format_ARGB32_Premultiplied)
            image.setAlphaChannel(alpha)
            image.setDevicePixelRatio(2)
            frame = QPixmap.fromImage(image)
        elif coat == "tuxedo":
            image = frame.toImage()
            painter = QPainter(image)
            painter.setCompositionMode(QPainter.CompositionMode.CompositionMode_SourceAtop)
            painter.fillRect(image.rect(), QColor(33, 31, 29, 94))
            painter.end()
            frame = QPixmap.fromImage(image)
        if facing != -1:
            frame = frame.transformed(QTransform().scale(-1, 1))
        self._cache[key] = frame
        while len(self._cache) > self.cache_limit:
            self._cache.popitem(last=False)
        return frame

    def mouth_anchor(self, kind, index, facing=-1, coat="ginger"):
        if self.frame(kind, index, facing, coat) is None:
            return None
        variant = "black" if coat == "black" or isinstance(coat, CoatSpec) else "base"
        x, y = self._rows[(kind, index, variant)]["mouth_anchor"]
        return (DISPLAY_SIZE[0] - x if facing != -1 else x), y
