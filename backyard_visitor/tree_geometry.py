"""Finite, independently mapped supports for authored cat-tree scenery.

Coordinates are global Qt logical pixels. The foreground/window navigation graph
is intentionally not reused: each excursion has one fixed far-layer scale and
an authored path from a real base to the open upper bed.
"""
from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import math
from typing import Mapping

TREE_SCALE = .65
SPRITE_WIDTH = 144.0
SPRITE_HEIGHT = 112.0
FOOT_X = 72.0
FOOT_Y = 108.0
SUPPORT_MARGIN = 3.0
_SUPPORT_IDS = ('tree-base', 'tree-roof', 'tree-step', 'tree-bed')
_REQUIRED_ANCHORS = ('tree-entry', 'tree-approach', 'tree-scratch-post')


def _finite(value):
    if type(value) not in (int, float):
        return False
    try:
        return math.isfinite(value)
    except OverflowError:
        return False


def _point(value):
    return isinstance(value, (list, tuple)) and len(value) == 2 and all(_finite(v) for v in value)


@dataclass(frozen=True, slots=True)
class TreeSupport:
    surface_id: str
    left: float
    right: float
    y: float
    kind: str = 'platform'

    @property
    def id(self):
        return self.surface_id

    @property
    def center(self):
        return ((self.left+self.right)/2, self.y)

    def foot_range(self, scale=TREE_SCALE):
        half = FOOT_X*scale+SUPPORT_MARGIN
        return self.left+half, self.right-half

    def supports_foot(self, foot, scale=TREE_SCALE):
        if not _point(foot):
            return False
        left, right = self.foot_range(scale)
        return left <= foot[0] <= right and abs(foot[1]-self.y) <= 1e-6


@dataclass(frozen=True, slots=True)
class TreeLayout:
    scene_id: str
    layout_id: str
    fingerprint: str
    screen_rect: tuple[float, float, float, float]
    scale: float
    supports: tuple[TreeSupport, ...]
    neighbors: Mapping[str, tuple[str, ...]]
    entry: tuple[float, float]
    approach: tuple[float, float]
    scratch_anchor: tuple[float, float]
    scratch_facing: int = 1
    scratch_contact: tuple[float, float] | None = None

    @property
    def by_id(self):
        return {surface.surface_id: surface for surface in self.supports}

    @property
    def base_id(self):
        return 'tree-base'

    @property
    def bed_id(self):
        return 'tree-bed'

    @property
    def scratch_support_id(self):
        return self.base_id


def build_tree_layout(scene, screen_rect) -> TreeLayout | None:
    """Map reviewed normalized support metadata, refusing unfit screens.

    Invalid metadata is a disabled interaction, not a partially trusted graph.
    A narrow display never shrinks the cat to force it into a platform. Scene
    loading has independently established that the PNG canvas really exists.
    """
    if (not isinstance(scene, dict) or scene.get('theme_id') != 'cat-tree'
            or scene.get('fit') != 'stretch'
            or not isinstance(scene.get('id'), str)
            or not isinstance(scene.get('layout_id'), str)
            or not isinstance(screen_rect, (tuple, list)) or len(screen_rect) != 4
            or not all(_finite(v) for v in screen_rect)):
        return None
    sx, sy, sw, sh = map(float, screen_rect)
    if sw <= 0 or sh <= 0:
        return None
    orientation = scene.get('orientation')
    if orientation not in {'portrait', 'landscape'} or (orientation == 'portrait') != (sh > sw):
        return None
    geometry = scene.get('layout_geometry')
    if not isinstance(geometry, dict):
        return None
    raw_supports, anchors = geometry.get('supports'), geometry.get('anchors')
    if not isinstance(raw_supports, list) or not isinstance(anchors, dict):
        return None
    rows = {}
    for row in raw_supports:
        if (not isinstance(row, dict) or row.get('surface_id') not in _SUPPORT_IDS
                or row['surface_id'] in rows
                or not all(_finite(row.get(n)) and 0 <= row[n] <= 1 for n in ('left', 'right', 'y'))
                or row['left'] >= row['right']):
            return None
        rows[row['surface_id']] = row
    if set(rows) != set(_SUPPORT_IDS):
        return None
    if any(not _point(anchors.get(n)) or not all(0 <= v <= 1 for v in anchors[n]) for n in _REQUIRED_ANCHORS):
        return None
    def mapped(point):
        return sx+point[0]*sw, sy+point[1]*sh
    supports = tuple(TreeSupport(sid, sx+rows[sid]['left']*sw,
                                sx+rows[sid]['right']*sw, sy+rows[sid]['y']*sh,
                                'bed' if sid == 'tree-bed' else 'base' if sid == 'tree-base' else 'platform')
                     for sid in _SUPPORT_IDS)
    for support in supports:
        lo, hi = support.foot_range()
        if (lo > hi or support.y-FOOT_Y*TREE_SCALE < sy
                or support.y+(SPRITE_HEIGHT-FOOT_Y)*TREE_SCALE > sy+sh):
            return None
    # Each adjacent board must be above the previous one and within a finite
    # high-jump envelope; unusually stretched displays cannot invent a route.
    for lower, upper in zip(supports, supports[1:]):
        dy = lower.y-upper.y
        lower_left, lower_right = lower.foot_range()
        upper_left, upper_right = upper.foot_range()
        dx = max(0., upper_left-lower_right, lower_left-upper_right)
        if (not (0 < dy <= 440*TREE_SCALE and dx <= 360*TREE_SCALE)
                or (dy < SPRITE_HEIGHT*TREE_SCALE/2 and dx > 0)):
            return None
    entry, approach = mapped(anchors['tree-entry']), mapped(anchors['tree-approach'])
    if not supports[0].supports_foot(entry):
        return None
    if not (sx+FOOT_X <= approach[0] <= sx+sw-FOOT_X
            and entry[1] < approach[1] < sy+sh
            and sx <= approach[0] < entry[0]):
        return None
    # The static scratching pose's hands lie 6.5 source units toward the post
    # and 73 units above its supported hind foot (the renderer uses the same
    # anchor). Its feet remain on the real base; no vertical air climbing.
    post = mapped(anchors['tree-scratch-post'])
    scratch = post[0]-6.5*TREE_SCALE, supports[0].y
    contact = post[0], supports[0].y-73*TREE_SCALE
    if not supports[0].supports_foot(scratch) or not (post[1] <= contact[1] <= supports[0].y):
        return None
    neighbors = {sid: tuple(_SUPPORT_IDS[j] for j in (i-1, i+1) if 0 <= j < len(_SUPPORT_IDS))
                 for i, sid in enumerate(_SUPPORT_IDS)}
    # The fingerprint contains authored geometry, not transient screen origin.
    # Pair-preserving switches require the catalog's independent fingerprint
    # too; this value additionally identifies the navigation contract.
    canonical = {'layout_id': scene['layout_id'], 'scale': TREE_SCALE,
                 'supports': [rows[sid] for sid in _SUPPORT_IDS],
                 'anchors': {n: anchors[n] for n in _REQUIRED_ANCHORS}}
    fingerprint = hashlib.sha256(json.dumps(canonical, sort_keys=True, separators=(',', ':'), allow_nan=False).encode()).hexdigest()
    return TreeLayout(scene['id'], scene['layout_id'], fingerprint, (sx, sy, sw, sh),
                      TREE_SCALE, supports, neighbors, entry, approach, scratch, 1, contact)
