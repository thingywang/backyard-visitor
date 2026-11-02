"""Validated black-fur variants using the original pose canvases and anchors.

Only manifest metadata lives here. CatArt/RearArt/DepthArt retain their usual
small pixmap caches; build-time image libraries are not runtime dependencies.
"""
from __future__ import annotations

import hashlib
import json
import logging
from pathlib import Path
import re

from PyQt6.QtGui import QImage, QImageReader, QPixmap, qRgb

ASSETS = Path(__file__).with_name("assets") / "black-coat"
_SHA = re.compile(r"[0-9a-f]{64}\Z")
_LOG = logging.getLogger(__name__)


def source_key(frame: QPixmap) -> str:
    image = frame.toImage().convertToFormat(QImage.Format.Format_RGBA8888_Premultiplied)
    raw = image.constBits().asstring(image.sizeInBytes())
    return hashlib.sha256(f"{image.width()}x{image.height()}:".encode() + raw).hexdigest()


def _alpha(image: QImage) -> QImage:
    alpha = image.convertToFormat(QImage.Format.Format_Alpha8)
    alpha.setDevicePixelRatio(1)
    return alpha


def fallback_frame(original: QPixmap) -> QPixmap:
    """Native Qt-only dark neutral shading if an authored file cannot load."""
    if original.isNull():
        return QPixmap(original)
    source = original.toImage()
    source.setDevicePixelRatio(1)
    alpha = _alpha(source)
    gray = source.convertToFormat(QImage.Format.Format_Grayscale8)
    # The grayscale byte is the palette index; all filtering/conversion stays
    # in Qt. The small table preserves luminance variation without a warm coat.
    indexed = QImage(gray.constBits(), gray.width(), gray.height(), gray.bytesPerLine(),
                     QImage.Format.Format_Indexed8).copy()
    levels = [round(8 + 64 * (i / 255) ** 1.2) for i in range(256)]
    indexed.setColorTable([qRgb(value, value + 1, value + 3) for value in levels])
    image = indexed.convertToFormat(QImage.Format.Format_ARGB32_Premultiplied)
    image.setAlphaChannel(alpha)
    image.setDevicePixelRatio(original.devicePixelRatio())
    return QPixmap.fromImage(image)


def _alpha_geometry(image: QImage) -> tuple[str, list[int]]:
    """Hash packed Alpha8 rows and measure nonzero alpha; no row padding.

    Bounds use physical pixels and half-open [left, top, right, bottom] edges.
    An empty silhouette returns [0, 0, 0, 0], rejected by authored manifests.
    """
    alpha = _alpha(image)
    width, height, stride = alpha.width(), alpha.height(), alpha.bytesPerLine()
    raw = alpha.constBits().asstring(alpha.sizeInBytes())
    digest = hashlib.sha256()
    left, top, right, bottom = width, height, 0, 0
    for y in range(height):
        row = raw[y * stride:y * stride + width]
        digest.update(row)
        tail = row.lstrip(b"\x00")
        if tail:
            left = min(left, width - len(tail))
            right = max(right, len(row.rstrip(b"\x00")))
            top = min(top, y)
            bottom = y + 1
    bounds = [left, top, right, bottom] if bottom else [0, 0, 0, 0]
    return digest.hexdigest(), bounds


class BlackCoat:
    """Load validated black coats without cropping, resizing, or relocating.

    The default source-alpha policy preserves every source alpha pixel.
    Authored-alpha-aligned permits independently authored fur edges on the
    same canvas. Existing Art anchors remain authoritative; the loader does
    not infer anatomical alignment from an alpha silhouette.
    """

    def __init__(self, assets: Path | None = None):
        self.assets = Path(assets) if assets is not None else ASSETS
        self.load_error: str | None = None
        self._rows: dict = {}
        self._geometry_policy = "source-alpha"
        self._signature = None
        self._warned = False

    def _manifest(self):
        path = self.assets / "manifest.json"
        stat = path.stat()
        signature = (stat.st_mtime_ns, stat.st_size)
        if signature == self._signature:
            return
        if path.is_symlink() or stat.st_size > 256 * 1024:
            raise ValueError("黑猫清单不可用")
        data = json.loads(path.read_text(encoding="utf-8"))
        rows = data["frames"]
        policy = data.get("geometry_policy", "source-alpha")
        if (data.get("schema_version") != 1 or data.get("kind") != "backyard-visitor-black-coat"
                or policy not in ("source-alpha", "authored-alpha-aligned")
                or not isinstance(rows, dict) or not 1 <= len(rows) <= 256):
            raise ValueError("黑猫清单格式不符")
        for key, row in rows.items():
            if (not isinstance(key, str) or _SHA.fullmatch(key) is None or not isinstance(row, dict)
                    or row.get("file") != key + ".png"
                    or not isinstance(row.get("sha256"), str) or _SHA.fullmatch(row["sha256"]) is None
                    or not isinstance(row.get("size"), list) or len(row["size"]) != 2
                    or any(type(n) is not int or not 0 < n <= 4096 for n in row["size"])):
                raise ValueError("黑猫姿势清单无效")
            if policy == "authored-alpha-aligned":
                bounds, offset = row.get("alpha_bounds"), row.get("alignment_offset_px")
                if (not isinstance(row.get("alpha_sha256"), str)
                        or _SHA.fullmatch(row["alpha_sha256"]) is None
                        or not isinstance(bounds, list) or len(bounds) != 4
                        or any(type(n) is not int for n in bounds)
                        or not 0 <= bounds[0] < bounds[2] <= row["size"][0]
                        or not 0 <= bounds[1] < bounds[3] <= row["size"][1]
                        or not isinstance(offset, list) or len(offset) != 2
                        or any(type(n) is not int or n != 0 for n in offset)):
                    raise ValueError("黑猫独立毛边清单无效")
        self._rows, self._geometry_policy, self._signature = rows, policy, signature

    def frame(self, original: QPixmap) -> QPixmap:
        if original.isNull():
            return QPixmap(original)
        try:
            self._manifest()
            row = self._rows[source_key(original)]
            size = [original.width(), original.height()]
            if row["size"] != size:
                raise ValueError("黑猫素材尺寸与原动作不符")
            path = self.assets / row["file"]
            if path.is_symlink() or not path.is_file() or path.stat().st_size > 4 * 1024 * 1024:
                raise ValueError("黑猫素材文件不可用")
            if hashlib.sha256(path.read_bytes()).hexdigest() != row["sha256"]:
                raise ValueError("黑猫素材校验失败")
            reader = QImageReader(str(path))
            expected = original.size()
            if reader.size() != expected:
                raise ValueError("黑猫素材画布不符")
            image = reader.read()
            if image.isNull() or not image.hasAlphaChannel():
                raise ValueError("黑猫素材透明度不可用")
            if self._geometry_policy == "source-alpha":
                if _alpha(image) != _alpha(original.toImage()):
                    raise ValueError("黑猫素材透明度与原动作不符")
            else:
                alpha_sha256, bounds = _alpha_geometry(image)
                if alpha_sha256 != row["alpha_sha256"] or bounds != row["alpha_bounds"]:
                    raise ValueError("黑猫独立毛边透明度校验失败")
            image.setDevicePixelRatio(original.devicePixelRatio())
            result = QPixmap.fromImage(image)
            if result.isNull():
                raise ValueError("黑猫素材无法显示")
            self.load_error = None
            return result
        except (OSError, ValueError, KeyError, TypeError, AttributeError, RuntimeError) as error:
            self.load_error = f"黑猫素材暂不可用，已使用保留明暗的黑毛备用绘制：{error}"
            if not self._warned:
                _LOG.warning(self.load_error)
                self._warned = True
            return fallback_frame(original)


_loader = BlackCoat()
load_error: str | None = None


def black_frame(original: QPixmap) -> QPixmap:
    global load_error
    result = _loader.frame(original)
    load_error = _loader.load_error
    return result
