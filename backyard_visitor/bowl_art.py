"""Stoneware bowl with separate photographic sprites for each food quantity."""
from __future__ import annotations

import json
import math
from pathlib import Path

from PyQt6.QtCore import QRectF, Qt
from PyQt6.QtGui import QImage, QPainter, QPainterPath, QPixmap

ASSETS = Path(__file__).with_name("assets")


class BowlArt:
    """Lazy bowl/portion loader; source images are released after frame creation."""

    def __init__(self, assets: Path | None = None):
        self.assets = Path(assets) if assets is not None else ASSETS
        self._loaded = False
        self._frames: list[QPixmap] = []
        self._portions_loaded = False
        self._portion_frames: dict[int, QPixmap] = {}
        self.portion_load_error: str | None = None
        self.load_error: str | None = None

    def load_frames(self) -> list[QPixmap]:
        if self._loaded:
            return self._frames
        self._loaded = True
        try:
            data = json.loads((self.assets / "bowl.json").read_text(encoding="utf-8"))
            if (data["schema_version"] != 1 or data["accepted"] is not True
                    or tuple(row["name"] for row in data["frames"]) != ("empty", "full")
                    or data["logical_size"] != [62, 40] or data["pixel_ratio"] != 2):
                raise ValueError("Bowl manifest must define two accepted matching states")
            name = data["file"]
            if not isinstance(name, str) or Path(name).name != name:
                raise ValueError("Bowl file must be an asset filename")
            width, height = data["width"], data["height"]
            if any(type(v) is not int or not 1 <= v <= 4096 for v in (width, height)):
                raise ValueError("Invalid bowl atlas dimensions")
            scale, destination = data["render_scale"], data["render_anchor"]
            if type(scale) not in (int, float) or not math.isfinite(scale) or not 0 < scale <= 1:
                raise ValueError("Invalid bowl source scale")
            atlas = QImage(str(self.assets / name))
            if atlas.isNull() or not atlas.hasAlphaChannel() or (atlas.width(), atlas.height()) != (width, height):
                raise ValueError("Bowl atlas is missing, changed or lacks alpha")
            staged = []
            for row in data["frames"]:
                crop, anchor = row["rect"], row["foot_anchor"]
                if (len(crop) != 4 or any(type(v) is not int for v in crop)
                        or min(crop[:2]) < 0 or min(crop[2:]) <= 0
                        or crop[0]+crop[2] > width or crop[1]+crop[3] > height):
                    raise ValueError("Bowl crop lies outside atlas")
                for point in (destination, anchor):
                    if (len(point) != 2 or any(type(v) not in (int, float)
                            or not math.isfinite(v) for v in point)):
                        raise ValueError("Invalid bowl landmark")
                if not (0 <= anchor[0] <= crop[2] and 0 <= anchor[1] <= crop[3]):
                    raise ValueError("Bowl anchor lies outside crop")
                frame = QPixmap(124, 80)
                frame.fill(Qt.GlobalColor.transparent)
                painter = QPainter(frame)
                painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
                painter.drawImage(QRectF(destination[0]*2-anchor[0]*scale,
                                         destination[1]*2-anchor[1]*scale,
                                         crop[2]*scale, crop[3]*scale),
                                  atlas, QRectF(*crop))
                painter.end()
                frame.setDevicePixelRatio(2)
                staged.append(frame)
            self._frames = staged
        except (OSError, ValueError, TypeError, KeyError, AttributeError, OverflowError) as error:
            self.load_error = f"Bowl artwork unavailable: {error}"
        return self._frames

    def load_portions(self) -> dict[int, QPixmap]:
        """Compose each independent food bitmap into the unchanged empty bowl."""
        if self._portions_loaded:
            return self._portion_frames
        self._portions_loaded = True
        bowls = self.load_frames()
        if not bowls:
            self.portion_load_error = self.load_error
            return self._portion_frames
        try:
            data = json.loads((self.assets / "bowl-portions.json").read_text(encoding="utf-8"))
            if (data["schema_version"] != 1 or data["accepted"] is not True
                    or data["logical_size"] != [62, 40]
                    or data["pixel_ratio"] != 2
                    or tuple(row["food"] for row in data["frames"]) != (1, 2, 3)):
                raise ValueError("Food manifest must define three accepted independent portions")
            clip = data["opening_clip"]
            self._validate_rect(clip, 62, 40, "bowl opening")
            names = set()
            staged = {0: bowls[0]}
            for row in data["frames"]:
                name = row["file"]
                if (not isinstance(name, str) or Path(name).name != name
                        or name in names):
                    raise ValueError("Food portions require separate asset filenames")
                names.add(name)
                width, height = row["width"], row["height"]
                if any(type(v) is not int or not 1 <= v <= 4096 for v in (width, height)):
                    raise ValueError("Invalid food source dimensions")
                crop, target = row["source_rect"], row["target_rect"]
                self._validate_rect(crop, width, height, "food source")
                self._validate_rect(target, 62, 40, "food target")
                source = QImage(str(self.assets / name))
                if (source.isNull() or not source.hasAlphaChannel()
                        or (source.width(), source.height()) != (width, height)):
                    raise ValueError("Food image is missing, changed or lacks alpha")
                frame = bowls[0].copy()
                painter = QPainter(frame)
                painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
                opening = QPainterPath()
                opening.addEllipse(QRectF(*clip))
                painter.setClipPath(opening)
                painter.setCompositionMode(QPainter.CompositionMode.CompositionMode_SourceAtop)
                painter.drawImage(QRectF(*target), source, QRectF(*crop))
                painter.end()
                staged[row["food"]] = frame
            self._portion_frames = staged
        except (OSError, ValueError, TypeError, KeyError, AttributeError, OverflowError) as error:
            self.portion_load_error = f"Food quantity artwork unavailable: {error}"
        return self._portion_frames

    @staticmethod
    def _validate_rect(rect, width, height, label):
        if (len(rect) != 4 or any(type(v) not in (int, float)
                or not math.isfinite(v) for v in rect)
                or min(rect[:2]) < 0 or min(rect[2:]) <= 0
                or rect[0] + rect[2] > width or rect[1] + rect[3] > height):
            raise ValueError(f"Invalid {label} rectangle")

    def frame(self, full: bool = False, *, food: int | None = None) -> QPixmap | None:
        if food is not None:
            if type(food) is not int or not 0 <= food <= 3:
                raise ValueError("Food quantity must be an integer from 0 to 3")
            if food == 0:
                frames = self.load_frames()
                return frames[0] if frames else None
            return self.load_portions().get(food)
        frames = self.load_frames()
        return frames[1 if full else 0] if frames else None

    def paint(self, painter: QPainter, rect, full: bool = False, *, food: int | None = None) -> bool:
        rect = QRectF(rect)
        if rect.isEmpty():
            return False
        frame = self.frame(full, food=food)
        if frame is None:
            return False
        scale = min(rect.width()/62, rect.height()/40)
        target = QRectF(rect.center().x()-31*scale, rect.bottom()-40*scale, 62*scale, 40*scale)
        painter.save()
        painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
        painter.drawPixmap(target, frame, QRectF(frame.rect()))
        painter.restore()
        return True

    draw = paint
