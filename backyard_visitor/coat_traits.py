"""Deterministic appearance traits and canonical coat fields, not a genetics model.

Unreleased layout version 1 is fixed in anatomical coordinates, never recalibrated against
an animation frame. Pigment and white probabilities are product design choices;
this module does not model sex, carrier genotypes or breeding probabilities.

Canonical parts: body u=shoulder->rump; head u=nose->back of head;
front_leg/hind_leg u=root->paw; tail u=root->tip. v runs dorsal/outer->ventral/
inner. Left and right identify the animal's own sides, not screen directions.
Protected eyes/nose/inner ears/pads belong to the renderer's material mask.
"""
from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache
import hashlib
import math
from typing import Iterable, NamedTuple
from .spotted_coat import spotted_limb_pattern
from .classic_coat import classic_body_pattern
from .tabby_face import tabby_side_face
from .calico_coat import calico_body_fields, calico_patch_fields

COAT_VERSION = 1
COAT_MAP_VERSION = 1
MAX_SEED = 0xFFFFFFFF
PATTERNS = ("solid", "mackerel", "spotted", "classic", "ticked")
PIGMENTS = ("dark", "orange", "tortie")
WHITE_MODES = ("none", "spotting", "all-white")
PARTS = ("body", "head", "tail", "front_leg", "hind_leg")
SIDES = ("left", "right")
_KEYS = frozenset(("version", "map_version", "seed", "pigment", "dilute",
                   "dark_pattern", "orange_pattern", "white_mode", "white_coverage"))


def _seed(value: int) -> int:
    if type(value) is not int or not 0 <= value <= MAX_SEED:
        raise ValueError("Coat seed must be an unsigned 32-bit integer")
    return value


def _unit(value: float, label: str) -> float:
    if type(value) not in (int, float) or not 0 <= value <= 1 or not math.isfinite(value):
        raise ValueError(f"{label} must be a finite number in [0, 1]")
    return float(value)


@dataclass(frozen=True, slots=True)
class CoatSpec:
    """Complete, hashable persisted appearance; no traits are redrawn on load."""
    seed: int
    pigment: str = "dark"
    dilute: bool = False
    dark_pattern: str = "solid"
    orange_pattern: str = "mackerel"
    white_mode: str = "none"
    white_coverage: float = 0.0
    version: int = COAT_VERSION
    map_version: int = COAT_MAP_VERSION

    def __post_init__(self):
        _seed(self.seed)
        if type(self.version) is not int or self.version != COAT_VERSION:
            raise ValueError("Unsupported coat generator version")
        if type(self.map_version) is not int or self.map_version != COAT_MAP_VERSION:
            raise ValueError("Unsupported canonical coat map version")
        if not isinstance(self.pigment, str) or self.pigment not in PIGMENTS:
            raise ValueError("Invalid coat pigment")
        if type(self.dilute) is not bool:
            raise ValueError("Coat dilution must be a boolean")
        for value in (self.dark_pattern, self.orange_pattern):
            if not isinstance(value, str) or value not in PATTERNS:
                raise ValueError("Invalid coat pattern")
        if not isinstance(self.white_mode, str) or self.white_mode not in WHITE_MODES:
            raise ValueError("Invalid white coverage mode")
        coverage = _unit(self.white_coverage, "White coverage")
        if ((self.white_mode == "none" and coverage != 0)
                or (self.white_mode == "all-white" and coverage != 1)
                or (self.white_mode == "spotting" and not 0 < coverage < 1)):
            raise ValueError("White mode and coverage disagree")
        object.__setattr__(self, "white_coverage", coverage)

    def to_dict(self) -> dict:
        return dict(version=self.version, map_version=self.map_version, seed=self.seed,
                    pigment=self.pigment, dilute=self.dilute,
                    dark_pattern=self.dark_pattern, orange_pattern=self.orange_pattern,
                    white_mode=self.white_mode, white_coverage=self.white_coverage)

    @classmethod
    def from_dict(cls, data: dict) -> "CoatSpec":
        if not isinstance(data, dict) or set(data) != _KEYS:
            raise ValueError("Coat data must contain exactly the supported trait fields")
        return cls(**data)


def derive_seed(seed: int, namespace: str) -> int:
    """Stable named substream, independent of Python hash and random call order."""
    _seed(seed)
    if not isinstance(namespace, str) or not namespace or len(namespace) > 200 or "\0" in namespace:
        raise ValueError("Coat seed namespace must be a nonempty short string")
    payload = b"BackyardVisitor/coat/v1\0" + seed.to_bytes(4, "big") + namespace.encode("utf-8")
    return int.from_bytes(hashlib.sha256(payload).digest()[:4], "big")


def _random(seed: int, namespace: str) -> float:
    return derive_seed(seed, namespace) / 4294967296.0


def _choice(value: float, rows: tuple[tuple[str, float], ...]) -> str:
    cumulative = 0.0
    for name, weight in rows:
        cumulative += weight
        if value < cumulative:
            return name
    return rows[-1][0]


def generate_coat(seed: int) -> CoatSpec:
    """Create version-1 traits once. Weights are aesthetic choices, not census data."""
    _seed(seed)
    pigment = _choice(_random(seed, "trait/pigment"),
                      (("dark", .58), ("orange", .28), ("tortie", .14)))
    tabbies = (("mackerel", .5), ("spotted", .2), ("classic", .2), ("ticked", .1))
    dark = ("solid" if _random(seed, "trait/dark-visible") < .35
            else _choice(_random(seed, "trait/dark-pattern"), tabbies))
    # Orange pigment retains visible, separately controlled tabby marks.
    orange = _choice(_random(seed, "trait/orange-pattern"), tabbies)
    white = _random(seed, "trait/white-mode")
    if white < .48:
        mode, coverage = "none", 0.0
    elif white >= .98:
        mode, coverage = "all-white", 1.0
    else:
        mode = "spotting"
        bucket = _random(seed, "trait/white-amount")
        low, span = ((.06, .20) if bucket < .50 else
                     (.28, .30) if bucket < .84 else (.62, .28))
        coverage = round(low + span * _random(seed, "trait/white-coverage"), 6)
    return CoatSpec(seed=seed, pigment=pigment,
                    dilute=_random(seed, "trait/dilute") < .20,
                    dark_pattern=dark, orange_pattern=orange,
                    white_mode=mode, white_coverage=coverage)


_PATTERN_NAMES = {"solid": "纯色", "mackerel": "鱼骨纹", "spotted": "点斑",
                  "classic": "古典斑", "ticked": "麻色"}


def describe_coat(spec: CoatSpec) -> str:
    """Concise phenotype labels; they are coat descriptions, not breed claims."""
    if not isinstance(spec, CoatSpec):
        raise ValueError("Expected CoatSpec")
    if spec.white_mode == "all-white":
        return "纯白"
    has_white = spec.white_mode == "spotting"
    if spec.pigment == "tortie":
        name = ("淡三花" if spec.dilute else "三花") if has_white and spec.white_coverage >= .2 else (
            "蓝奶油玳瑁" if spec.dilute else "玳瑁")
        if has_white and spec.white_coverage < .2:
            name += "加白"
        if spec.dark_pattern != "solid":
            name += " · " + _PATTERN_NAMES[spec.dark_pattern]
        return name
    if spec.pigment == "orange":
        name, pattern = ("奶油" if spec.dilute else "橘"), spec.orange_pattern
        name += "白" if has_white else "色"
    else:
        name, pattern = ("蓝灰" if spec.dilute else "黑"), spec.dark_pattern
        if has_white:
            name += "白"
        elif pattern == "solid":
            name = "纯" + name
        elif not spec.dilute:
            name = "黑棕"
    return name if pattern == "solid" else name + " · " + _PATTERN_NAMES[pattern]


class CoatSample(NamedTuple):
    dark_tabby: float
    orange_tabby: float
    orange: float
    white: float


def _smoothstep(low: float, high: float, value: float) -> float:
    t = min(1.0, max(0.0, (value - low) / (high - low)))
    return t * t * (3.0 - 2.0 * t)


def _hash(seed: int, x: int, y: int) -> float:
    n = (seed ^ (x * 0x9E3779B1) ^ (y * 0x85EBCA77)) & MAX_SEED
    n ^= n >> 16
    n = (n * 0x7FEB352D) & MAX_SEED
    n ^= n >> 15
    n = (n * 0x846CA68B) & MAX_SEED
    n ^= n >> 16
    return n / 4294967295.0


def _noise(seed: int, x: float, y: float) -> float:
    ix, iy = math.floor(x), math.floor(y)
    tx, ty = x - ix, y - iy
    sx, sy = tx * tx * (3 - 2 * tx), ty * ty * (3 - 2 * ty)
    a = _hash(seed, ix, iy) * (1 - sx) + _hash(seed, ix + 1, iy) * sx
    b = _hash(seed, ix, iy + 1) * (1 - sx) + _hash(seed, ix + 1, iy + 1) * sx
    return a * (1 - sy) + b * sy


def _fbm(seed: int, x: float, y: float) -> float:
    return (.60 * _noise(seed, x, y) + .28 * _noise(seed ^ 0xB5297A4D, x * 2.13, y * 2.13)
            + .12 * _noise(seed ^ 0x68E31DA4, x * 4.61, y * 4.61))


def _white_bias(part: str, u: float, v: float) -> float:
    if part == "body":
        chest = math.exp(-((u - .13) / .23) ** 2) * v
        return .88 * v ** 1.7 + .33 * chest
    if part == "head":
        return .55 * v ** 1.4 + .50 * (1 - u) ** 2 * (.25 + .75 * v)
    if part in ("front_leg", "hind_leg"):
        return 1.15 * u ** 2.2 + .16 * v
    return .12 * u + .08 * v  # Tail usually retains colour until broader coverage.


# Shared anatomical area weights sum to 1 across both sides. Sampling is fixed
# by map_version, rather than the current pose's visible image pixels.
_DOMAIN = (("body", 32, 24, .54), ("head", 16, 16, .14),
           ("tail", 20, 10, .10), ("front_leg", 12, 20, .10),
           ("hind_leg", 14, 20, .12))
_CANONICAL_GRID = tuple((part, (x + .5) / nx, (y + .5) / ny, side, area / (2 * nx * ny))
                        for part, nx, ny, area in _DOMAIN for side in SIDES
                        for y in range(ny) for x in range(nx))


def _quantile(scores: list[tuple[float, float]], fraction_below: float) -> float:
    accumulated = 0.0
    for value, weight in sorted(scores):
        accumulated += weight
        if accumulated >= fraction_below:
            return value
    return max(value for value, _ in scores)


class CoatField:
    """Canonical Tdark/Torange/O/W fields with one fixed area calibration.

    Calling sample on different poses never recalculates thresholds. Renderers
    must map source pixels to these parts/coordinates before requesting values.
    """
    __slots__ = ("_spec", "_seeds", "_white_threshold", "_orange_threshold", "_calico_seed", "_limb_roots")

    def __init__(self, spec: CoatSpec):
        if not isinstance(spec, CoatSpec):
            raise ValueError("Expected CoatSpec")
        self._spec = spec
        self._seeds = {(part, side, channel): derive_seed(spec.seed, f"layout/{channel}/{part}/{side}")
                       for part in PARTS for side in SIDES
                       for channel in ("pattern", "orange", "white")}
        self._white_threshold = self._orange_threshold = .5
        self._calico_seed = derive_seed(spec.seed, "layout/calico/whole-body")
        self._limb_roots = {}
        if spec.white_mode == "spotting":
            scores = [(self._white_score(part, u, v, side), weight)
                      for part, u, v, side, weight in _CANONICAL_GRID]
            self._white_threshold = _quantile(scores, 1 - spec.white_coverage)
            if spec.white_coverage >= .2:
                # Uniform inherited limb roots create score plateaus. Match
                # the final smooth white area, not a hard rank cutoff whose
                # later feathering can change the requested coverage.
                low, high = self._white_threshold-.045, self._white_threshold+.045
                for _ in range(18):
                    middle = (low+high)/2
                    area = sum(_smoothstep(middle-.045, middle+.045, value)*weight
                               for value, weight in scores)
                    if area > spec.white_coverage:
                        low = middle
                    else:
                        high = middle
                self._white_threshold = (low+high)/2
        if spec.pigment == "tortie":
            if spec.white_mode == "spotting" and spec.white_coverage >= .2:
                # A complete calico lobe owns one colour. Moving the orange
                # threshold to force an exact ratio would carve dark slivers
                # into an orange patch, even after white has framed it.
                self._orange_threshold = .5
            else:
                orange_coverage = .36 + .28 * _random(spec.seed, "layout/orange-coverage")
                scores = [(self._orange_score(part, u, v, side), weight)
                          for part, u, v, side, weight in _CANONICAL_GRID]
                self._orange_threshold = _quantile(scores, 1 - orange_coverage)

    @property
    def spec(self) -> CoatSpec:
        return self._spec

    def _attached_score(self, part: str, u: float, v: float, side: str,
                        value: float, channel: str) -> float:
        """Share pigment at joints without sharing the whole limb's pattern.

        Values are joined before thresholding: pigment edges remain one clean
        contour instead of two unrelated colour patches blurred at a seam.
        """
        if part == "body":
            return value
        if part == "head":
            amount = _smoothstep(.52, .96, u)
            body_u, body_v = 0.0, v * .90 + .05
        elif part == "tail":
            amount = 1 - _smoothstep(.03, .22, u)
            body_u, body_v = 1.0, .33 + (v - .5) * .12
        else:
            amount = 1 - _smoothstep(.02, .31, u)
            body_u = (.07 if part == "front_leg" else .91) + (v - .5) * .10
            body_v = .74
        if not amount:
            return value
        score = self._white_score if channel == "white" else self._orange_score
        anchor = score("body", body_u, body_v, side)
        return value * (1 - amount) + anchor * amount

    def _limb_root(self, part: str, side: str) -> tuple[float, float]:
        key = part, side
        if key not in self._limb_roots:
            center = .24 if part == "front_leg" else .82
            # Average a small physical root neighbourhood once. Copying a
            # transverse body boundary down leg-v made long narrow shards.
            points = ((center-.015, .70), (center, .685),
                      (center, .715), (center+.015, .70))
            white = sum(self._white_score("body", u, v, side) for u, v in points)/len(points)
            orange = sum(self._orange_score("body", u, v, side) for u, v in points)/len(points)
            self._limb_roots[key] = white, orange
        return self._limb_roots[key]

    def _white_score(self, part: str, u: float, v: float, side: str) -> float:
        seed = self._seeds[part, side, "white"]
        if (self.spec.white_mode == "spotting" and self.spec.white_coverage >= .2
                and part in ("front_leg", "hind_leg")):
            # Continue the coat at the updated body-longitude attachment.
            # Only a gradual white sock may develop down the limb; a white
            # root cannot turn into a detached dark/coloured island below.
            root = self._limb_root(part, side)[0]
            sock_start = .36 + .18 * _hash(seed, 71, 0) + .055*(v-.5)**2
            return root + 1.25 * _smoothstep(sock_start, .97, u)
        if (self.spec.white_mode == "spotting" and self.spec.white_coverage >= .2
                and part == "body"):
            # Bicolour coats share the same rounded white interspaces. A
            # calico uses its orange seed so all three colours compose one
            # layout; black/white and orange/white use their own white seed.
            if self.spec.pigment == "tortie":
                return calico_patch_fields(self._calico_seed, u, v, side)[0]
            return calico_body_fields(seed, u, v)[0]
        # Broad connected patches; high octaves used to cut tiny bays and
        # islands into every boundary. Fine fur belongs to the material, not
        # to the topology of a pigment patch.
        broad = _noise(seed, u * 1.9 + .31, v * 1.55 + .19)
        contour = _noise(seed ^ 0xB5297A4D, u * 3.1 + .2, v * 2.7 + .4)
        value = _white_bias(part, u, v) + .92 * broad + .10 * contour
        return self._attached_score(part, u, v, side, value, "white")

    def _orange_score(self, part: str, u: float, v: float, side: str) -> float:
        seed = self._seeds[part, side, "orange"]
        calico = self.spec.white_mode == "spotting" and self.spec.white_coverage >= .2
        if calico and part in ("front_leg", "hind_leg"):
            # Pigment ownership follows the attached body region throughout
            # this leg. Never reroll orange/black noise in the upper limb.
            return self._limb_root(part, side)[1]
        if calico and part == "body":
            value = calico_patch_fields(self._calico_seed, u, v, side)[1]
            return value
        # Different cats must not all inherit a dark head / orange saddle /
        # dark rump. Rotate and translate the pigment field independently of
        # anatomical tabby bands. The same phase is reused for every pose.
        angle = _hash(seed, 21, 0)*math.tau
        x = u*math.cos(angle)-v*math.sin(angle) + 7*_hash(seed,22,0)
        y = u*math.sin(angle)+v*math.cos(angle) + 7*_hash(seed,23,0)
        scale = 2.6 if calico else 5.2
        broad = _noise(seed, x*scale, y*scale*.83)
        secondary = _noise(seed ^ 0x68E31DA4, x*scale*1.57+.8,
                           y*scale*1.41+.1)
        # Calico photos have a few broad colour islands. Tortoiseshell has
        # interleaved large and smaller patches, but not pixel-scale confetti.
        # Restrained extra frequency changes the organisation, not fur detail.
        mix = .10 if calico else .34
        value = (1-mix)*broad + mix*secondary
        return self._attached_score(part, u, v, side, value, "orange")

    def _pattern(self, kind: str, part: str, u: float, v: float, side: str) -> float:
        if kind == "solid":
            return 0.0
        seed = self._seeds[part, side, "pattern"]
        if kind == "ticked" and part == "body":
            # Small tonal variation is carried by individual hairs in photos;
            # it must not become another set of broad body spots or bars.
            return .10 + .16 * _noise(seed, u * 93, v * 81)
        if kind == "spotted" and part in ("front_leg", "hind_leg"):
            return spotted_limb_pattern(seed, u, v)
        if kind == "spotted" and part == "body":
            # Ocicat photographs show staggered, elongated dashes/commas,
            # following the flank, with uneven sizes rather than a dot grid.
            x, y = u * 8.4, v * 4.4
            x += .20 * math.sin(v * 5.1) + .22 * (_noise(seed, u*2, v*2)-.5)
            ix, iy = math.floor(x), math.floor(y)
            closest = 10.0
            for dy in (-1, 0, 1):
                for dx in (-1, 0, 1):
                    cx, cy = ix + dx, iy + dy
                    px = cx + .16 + .68 * _hash(seed, cx, cy)
                    py = cy + .15 + .70 * _hash(seed ^ 0xA511E9B3, cx, cy)
                    radius = .23 + .14 * _hash(seed ^ 0x63D83595, cx, cy)
                    tilt = -.65 + .95 * _hash(seed ^ 0x298F1D31, cx, cy) + .38 * u
                    ox, oy = x-px, y-py
                    along = ox*math.cos(tilt) + oy*math.sin(tilt)
                    across = -ox*math.sin(tilt) + oy*math.cos(tilt)
                    # A restrained asymmetric waist makes some dashes commas,
                    # not identical circles or noisy, ragged islands.
                    along += .18 * across * across
                    length = 1.2 + .65 * _hash(seed ^ 0xBC416891, cx, cy)
                    closest = min(closest, math.hypot(along/length, across*1.15)/radius)
            return (1-_smoothstep(.65, 1.18, closest)) * (1-.45*_smoothstep(.80,1.,v))
        if kind == "classic" and part == "body":
            return classic_body_pattern(seed, u, v)
        if kind == "mackerel" and part == "body":
            # Uneven, gently bending pencillings remain anatomically ordered.
            # Width varies along each stripe without adding random holes.
            count = 9
            stripe = 0.0
            for i in range(count):
                center = (.026 + i*.117 + .062*(_hash(seed,i,0)-.5))
                bend = (.035+.080*_hash(seed,i,1))*math.sin(math.pi*v*.92)
                center += bend*(1.0-.55*i/(count-1))
                center += .023*math.sin(v*5.0+i*.9)*v
                length = .48+.46*_hash(seed,i,2)
                taper = 1-_smoothstep(length-.22,length+.04,v)
                width = (.010+.016*_hash(seed,i,3))*(.95-.32*v)*taper
                width *= .83+.25*math.sin(v*4.7+_hash(seed,i,4)*math.tau)**2
                mark = (1-_smoothstep(width,width+.013,abs(u-center)))*taper
                # A few pencillings fork as they bend down the flank. The
                # branch grows from its parent rather than floating as a dot.
                if _hash(seed,i,5)>.72:
                    branch = _smoothstep(.20,.58,v)
                    offset = (.040+.025*_hash(seed,i,6))*branch
                    mark = max(mark,(1-_smoothstep(width*.65,width*.65+.012,
                                                  abs(u-center+offset)))*taper*branch)
                stripe = max(stripe,mark)
            spine = (1-_smoothstep(.035,.078,v))*.85
            return max(stripe,spine)
        # Narrower face markings and curved leg bracelets, with gently varying
        # ring spacing. They stay subordinate to the flank rather than looking
        # like a black collar or solid horizontal cuffs.
        if part == "head":
            return tabby_side_face(seed, u, v)
        frequency = (7.5 if part == "tail" else 3.25) + .65 * _hash(seed, 0, 1)
        if kind == "classic":
            frequency *= .82
        phase = 2 * math.pi * (u * frequency + .11 * math.sin(v * math.pi * 1.5)
                              + .06 * (_noise(seed, u * 2, v * 1.5) - .5))
        return _smoothstep(.52, .86, math.cos(phase))

    def _sample(self, part: str, u: float, v: float, side: str) -> CoatSample:
        spec = self.spec
        orange_threshold = self._orange_threshold
        orange = (0.0 if spec.pigment == "dark" else 1.0 if spec.pigment == "orange" else
                  _smoothstep(orange_threshold - .035, orange_threshold + .035,
                              self._orange_score(part, u, v, side)))
        white = (0.0 if spec.white_mode == "none" else 1.0 if spec.white_mode == "all-white" else
                 _smoothstep(self._white_threshold - .045, self._white_threshold + .045,
                             self._white_score(part, u, v, side)))
        return CoatSample(self._pattern(spec.dark_pattern, part, u, v, side),
                          self._pattern(spec.orange_pattern, part, u, v, side), orange, white)

    def sample(self, part: str, u: float, v: float, side: str = "left") -> CoatSample:
        if not isinstance(part, str) or part not in PARTS:
            raise ValueError("Unknown canonical body part")
        if not isinstance(side, str) or side not in SIDES:
            raise ValueError("Canonical side must be left or right")
        return self._sample(part, _unit(u, "Canonical u"), _unit(v, "Canonical v"), side)

    def sample_many(self, points: Iterable[tuple]) -> list[CoatSample]:
        """Each tuple is (part,u,v) or (part,u,v,side); ordering does not affect layout."""
        return [self.sample(*point) for point in points]


@lru_cache(maxsize=32)
def coat_field(spec: CoatSpec) -> CoatField:
    return CoatField(spec)


def field(spec: CoatSpec, part: str, u: float, v: float, *, side: str = "left") -> CoatSample:
    return coat_field(spec).sample(part, u, v, side)
