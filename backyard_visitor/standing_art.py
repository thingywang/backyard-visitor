"""One real standing pose aligned with the four walking frames, loaded on demand."""
from __future__ import annotations

import json
import math
from pathlib import Path

from PyQt6.QtCore import QRectF, QSize, Qt
from PyQt6.QtGui import QImageReader, QPainter, QPixmap

ASSETS = Path(__file__).with_name("assets")


class StandingArt:
    """Keep one small Retina frame; CatArt owns coat and direction variants."""

    def __init__(self, assets: Path | None = None):
        self.assets = Path(assets) if assets is not None else ASSETS
        self._loaded = False
        self._frame: QPixmap | None = None
        self.load_error: str | None = None

    def frame(self) -> QPixmap | None:
        if self._loaded:
            return self._frame
        self._loaded = True
        try:
            data = json.loads((self.assets / "standing.json").read_text(encoding="utf-8"))
            if data["schema_version"] != 1 or data["accepted"] is not True:
                raise ValueError("Standing pose is not accepted")
            name = data["file"]
            if not isinstance(name, str) or Path(name).name != name:
                raise ValueError("Standing file must be an asset filename")
            width, height = data["width"], data["height"]
            if (type(width) is not int or type(height) is not int
                    or not 1 <= width <= 4096 or not 1 <= height <= 4096):
                raise ValueError("Invalid standing source dimensions")
            if data["rect"] != [0, 0, width, height]:
                raise ValueError("Standing source requires its full canvas")
            scale = float(data["render_scale"])
            if not math.isfinite(scale) or not 0 < scale <= 1:
                raise ValueError("Invalid standing scale")
            anchor, destination = data["foot_anchor"], data["render_anchor"]
            if (len(anchor) != 2 or len(destination) != 2
                    or not all(type(v) in (int, float) and math.isfinite(v)
                               for v in [*anchor, *destination])):
                raise ValueError("Invalid standing anchor")
            if data["logical_size"] != [144, 112] or data["pixel_ratio"] != 2:
                raise ValueError("Standing pose must use the shared Retina canvas")
            reader = QImageReader(str(self.assets / name))
            if reader.size() != QSize(width, height):
                raise ValueError("Standing source dimensions changed")
            reader.setScaledSize(QSize(round(width*scale), round(height*scale)))
            image = reader.read()
            if image.isNull() or not image.hasAlphaChannel():
                raise ValueError("Standing source missing or without alpha")
            frame = QPixmap(288, 224)
            frame.fill(Qt.GlobalColor.transparent)
            painter = QPainter(frame)
            painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
            painter.drawImage(QRectF(destination[0]*2-anchor[0]*scale,
                                     destination[1]*2-anchor[1]*scale,
                                     width*scale, height*scale), image)
            painter.end()
            frame.setDevicePixelRatio(2)
            self._frame = frame
        except (OSError, ValueError, TypeError, KeyError, AttributeError, OverflowError) as error:
            self.load_error = f"Standing artwork unavailable: {error}"
        return self._frame
