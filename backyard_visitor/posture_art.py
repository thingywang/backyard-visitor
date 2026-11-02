"""Actual intermediate joint/roll poses; existing endpoints belong to CatArt."""
from __future__ import annotations

import json
import math
from pathlib import Path

from PyQt6.QtCore import QRectF, Qt
from PyQt6.QtGui import QImage, QPainter, QPixmap

ASSETS = Path(__file__).with_name("assets")
GROUPS = ("crouch_rise", "belly_roll")


class PostureArt:
    """Return two atomic groups of three 288x224/DPR2 intermediate poses."""

    def __init__(self, assets: Path | None = None):
        self.assets = Path(assets) if assets is not None else ASSETS
        self.native_facing = -1
        self._loaded = False
        self._frames: dict[str, list[QPixmap]] = {}
        self.load_error: str | None = None

    def load_frames(self) -> dict[str, list[QPixmap]]:
        if self._loaded:
            return self._frames
        self._loaded = True
        try:
            data = json.loads((self.assets / "posture.json").read_text(encoding="utf-8"))
            if (data["schema_version"] != 1 or data["accepted"] is not True
                    or set(data["groups"]) != set(GROUPS)
                    or data["logical_size"] != [144,112] or data["pixel_ratio"] != 2
                    or data["native_facing"] != -1):
                raise ValueError("Posture manifest does not match the two-group canvas contract")
            staged = {}
            for group in GROUPS:
                info = data["groups"][group]
                name = info["file"]
                if not isinstance(name,str) or Path(name).name != name:
                    raise ValueError("Posture file must be an asset filename")
                width,height = info["width"],info["height"]
                if any(type(v) is not int or not 1 <= v <= 4096 for v in (width,height)):
                    raise ValueError("Invalid posture atlas dimensions")
                scale,destination = info["render_scale"],info["render_anchor"]
                if type(scale) not in (int,float) or not math.isfinite(scale) or not 0 < scale <= 1:
                    raise ValueError("Invalid posture scale")
                if len(info["frames"]) != 3:
                    raise ValueError("Each posture group needs three intermediate poses")
                atlas = QImage(str(self.assets/name))
                if (atlas.isNull() or not atlas.hasAlphaChannel()
                        or (atlas.width(),atlas.height()) != (width,height)):
                    raise ValueError("Posture atlas is missing, changed or lacks alpha")
                frames = []
                for row in info["frames"]:
                    crop,anchor = row["rect"],row["foot_anchor"]
                    if (len(crop) != 4 or any(type(v) is not int for v in crop)
                            or min(crop[:2]) < 0 or min(crop[2:]) <= 0
                            or crop[0]+crop[2] > width or crop[1]+crop[3] > height):
                        raise ValueError("Posture crop lies outside its atlas")
                    for point in (anchor,destination):
                        if (len(point) != 2 or any(type(v) not in (int,float)
                                or not math.isfinite(v) for v in point)):
                            raise ValueError("Invalid posture landmark")
                    if not (0 <= anchor[0] <= crop[2] and 0 <= anchor[1] <= crop[3]
                            and 0 <= destination[0] <= 144 and 0 <= destination[1] <= 112):
                        raise ValueError("Posture landmark lies outside canvas")
                    frame = QPixmap(288,224);frame.fill(Qt.GlobalColor.transparent)
                    p = QPainter(frame);p.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
                    p.drawImage(QRectF(destination[0]*2-anchor[0]*scale,
                                       destination[1]*2-anchor[1]*scale,
                                       crop[2]*scale,crop[3]*scale),atlas,QRectF(*crop))
                    p.end();frame.setDevicePixelRatio(2)
                    frames.append(frame)
                staged[group] = frames
                del atlas
            self._frames = staged
        except (OSError,ValueError,TypeError,KeyError,AttributeError,OverflowError) as error:
            self.load_error = f"Posture artwork unavailable: {error}"
        return self._frames
