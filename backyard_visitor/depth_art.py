"""Fixed-canvas depth poses; ordinary side views remain owned by CatArt.

Artwork comes only from packaged assets. Route timing and body positioning are
not changed by this renderer. Every pose shares the 144x112 logical canvas and
(72, 108) foot anchor; no per-frame crop, flip, warp or colour correction occurs.
"""
from __future__ import annotations

from collections import OrderedDict
import hashlib
import json
import math
from pathlib import Path
import re

from PyQt6.QtCore import QSize
from PyQt6.QtGui import QColor, QImage, QImageReader, QPainter, QPixmap

from .coat_traits import CoatSpec

ASSETS = Path(__file__).with_name("assets") / "depth-sprites"
DISPLAY_SIZE = (144, 112)
FOOT_ANCHOR = (72, 108)
PIXEL_SIZE = (288, 224)
COUNTS = {"away": 8, "toward": 4, "front": 1}


def _view(heading):
    heading = (heading + 180.) % 360. - 180.
    if -70. < heading < -10.:
        return "away"
    if 110. < heading < 170.:
        return "toward"
    return "side_left" if abs(heading) > 90. else "side_right"


def select_depth_pose(snapshot, *, corridor_supported=True):
    """Choose an authored view, or None for the caller's normal CatArt state.

Only the authored right/up corridor is supported. The caller should set
corridor_supported from its route rather than mirror these perspective poses.
During a turn, side_left/side_right deliberately delegate idle art to CatArt.
    """
    if not corridor_supported:
        return None
    phase = snapshot.get("phase")
    if phase not in {"recede", "advance", "turn"}:
        return None
    gait = snapshot.get("gait_phase", 0.)
    if type(gait) not in (int, float) or not math.isfinite(gait) or not 0 <= gait < 1:
        return None
    if phase == "recede":
        if snapshot.get("facing") != 1:
            return None
        kind = "away"
    elif phase == "advance":
        kind = "toward"
    else:
        motion = snapshot.get("turn_motion_progress")
        source = snapshot.get("turn_from_degrees")
        target = snapshot.get("turn_to_degrees")
        if (any(type(v) not in (int, float) or not math.isfinite(v)
                for v in (motion, source, target)) or not 0 <= motion <= 1):
            return None
        if abs(target - source) > 100. and .25 <= motion < .75:
            kind = "front"
        else:
            kind = _view(source if motion < .5 else target)
    count = COUNTS.get(kind, 1)
    return {"kind": kind, "index": min(count - 1, int(gait * count))}


def _coat_frame(frame, coat):
    """The same native Qt coat rules as CatArt.frame; preserve sprite alpha."""
    if isinstance(coat, CoatSpec):
        from .procedural_coat import procedural_frame
        return procedural_frame(frame, coat)
    if coat == "black":
        from .black_coat import black_frame
        return black_frame(frame)
    if coat == "gray":
        source = frame.toImage()
        source.setDevicePixelRatio(1)
        alpha = source.convertToFormat(QImage.Format.Format_Alpha8)
        image = source.convertToFormat(QImage.Format.Format_Grayscale8)
        image = image.convertToFormat(QImage.Format.Format_ARGB32_Premultiplied)
        image.setAlphaChannel(alpha)
        image.setDevicePixelRatio(frame.devicePixelRatio())
        return QPixmap.fromImage(image)
    if coat == "tuxedo":
        # Existing dark-tabby variant; no invented white tuxedo markings.
        image = frame.toImage()
        painter = QPainter(image)
        painter.setCompositionMode(QPainter.CompositionMode.CompositionMode_SourceAtop)
        painter.fillRect(image.rect(), QColor(33, 31, 29, 94))
        painter.end()
        return QPixmap.fromImage(image)
    return frame


class DepthArt:
    """Lazy validated small PNGs and CatArt-compatible coat variants.

`frame` returns None on missing/invalid artwork and sets `load_error`; the
caller can show that error and keep the normal foreground behaviour. Returned
pixmaps are shared cached values and should be treated as read-only.
    """

    def __init__(self, assets: Path | None = None):
        self.assets = Path(assets) if assets is not None else ASSETS
        self.load_error: str | None = None
        self._checked = False
        self._paths = {}
        self._originals = {}
        self._variants = OrderedDict()
        self.cache_limit = 16

    @property
    def available(self):
        self._load_manifest()
        return self.load_error is None

    def _load_manifest(self):
        if self._checked:
            return
        self._checked = True
        try:
            data = json.loads((self.assets / "manifest.json").read_text(encoding="utf-8"))
            if (data.get("schema_version") != 1
                    or data.get("kind") != "backyard-visitor-depth-sprites"
                    or data.get("pixel_size") != list(PIXEL_SIZE)
                    or data.get("display_size") != list(DISPLAY_SIZE)
                    or data.get("foot_anchor") != list(FOOT_ANCHOR)
                    or data.get("pixel_ratio") != 2 or data.get("counts") != COUNTS):
                raise ValueError("纵深素材画布或锚点不符")
            rows = data["frames"]
            if not isinstance(rows, list) or len(rows) != sum(COUNTS.values()):
                raise ValueError("纵深素材需要 8 走远、4 走近和 1 转身姿势")
            paths = {}
            for row in rows:
                kind, index = row["kind"], row["index"]
                if kind not in COUNTS or type(index) is not int or not 0 <= index < COUNTS[kind]:
                    raise ValueError("纵深姿势索引无效")
                key = (kind, index)
                name = f"{kind}-{index:02d}@2x.png"
                digest = row["sha256"]
                if (key in paths or row["file"] != name or not isinstance(digest, str)
                        or re.fullmatch(r"[0-9a-f]{64}", digest) is None):
                    raise ValueError("纵深素材清单重复或文件名无效")
                path = self.assets / name
                if (path.is_symlink() or not path.is_file()
                        or hashlib.sha256(path.read_bytes()).hexdigest() != digest):
                    raise ValueError(f"纵深素材校验失败：{name}")
                if QImageReader(str(path)).size() != QSize(*PIXEL_SIZE):
                    raise ValueError(f"纵深素材尺寸不符：{name}")
                paths[key] = path
            self._paths = paths
        except (OSError, ValueError, TypeError, KeyError, AttributeError) as error:
            self.load_error = f"纵深素材不可用：{error}"
            self._paths.clear()

    def frame(self, kind, index=0, coat="ginger") -> QPixmap | None:
        if kind not in COUNTS or type(index) is not int or not 0 <= index < COUNTS[kind]:
            return None  # Side views and unrecognised selections belong to CatArt.
        self._load_manifest()
        if self.load_error is not None:
            return None
        key = (kind, index)
        if key not in self._originals:
            frame = QPixmap(str(self._paths[key]))
            if frame.isNull() or not frame.hasAlphaChannel():
                self.load_error = f"纵深素材解码失败：{self._paths[key].name}"
                self._originals.clear()
                self._variants.clear()
                return None
            frame.setDevicePixelRatio(2)
            self._originals[key] = frame
        original = self._originals[key]
        if coat not in {"gray", "tuxedo", "black"} and not isinstance(coat, CoatSpec):
            return original
        variant_key = (kind, index, coat)
        if variant_key not in self._variants:
            self._variants[variant_key] = _coat_frame(original, coat)
        self._variants.move_to_end(variant_key)
        while len(self._variants) > self.cache_limit:
            self._variants.popitem(last=False)
        return self._variants[variant_key]
