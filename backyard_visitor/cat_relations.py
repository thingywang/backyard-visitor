"""Directed cat-to-cat familiarity, independent of visit and animation state.

Each direction starts at an integer from 0 through 100. One completed shared
meal grants each direction up to 3 points, with a lifetime meal gain of at most
30 and a total score of at most 100. The caller owns meal completion/deduplication.
Restoring a save copies the cumulative gain; it never replays meal rewards.

The lower directional score controls pair behavior. Co-visit factors range
from .2 to 1, so a relationship never raises the existing visit opportunity.
Fight probabilities range from .56 at score 0 to .14 at 100: familiarity reduces
conflict without making a friendly pair completely unable to squabble. These
step sizes and factors are implementation choices, separate from human trust.
"""
from __future__ import annotations

from dataclasses import dataclass
import math
import random
from typing import Iterable

SCHEMA_VERSION = 1
SCORE_LIMIT = 100
MEAL_STEP = 3
MEAL_GAIN_LIMIT = 30
Number = int | float


@dataclass(frozen=True, slots=True)
class _Relation:
    initial: Number
    meal_gain: Number = 0

    @property
    def score(self) -> Number:
        return self.initial + self.meal_gain


def _bounded_number(value, maximum) -> bool:
    # Check the bounds before isfinite: an arbitrary-sized JSON integer must
    # be rejected without overflowing a conversion to a floating-point number.
    return (type(value) in (int, float) and 0 <= value <= maximum
            and math.isfinite(value))


class CatRelations:
    """Public IDs only; an owned RNG cannot consume the engine's random stream."""
    def __init__(self, cat_ids: Iterable[str], seed=None, state=None):
        if isinstance(cat_ids, (str, bytes)):
            raise TypeError("cat_ids must be a collection of public IDs")
        ids = tuple(cat_ids)
        if any(type(cat_id) is not str or not cat_id for cat_id in ids):
            raise ValueError("Each cat ID must be a nonempty string")
        self.cat_ids = tuple(sorted(set(ids)))
        rng = random.Random(seed)
        self._relations = {
            source: {target: _Relation(rng.randint(0, SCORE_LIMIT))
                     for target in self.cat_ids if target != source}
            for source in self.cat_ids
        }
        self.restore(state)

    def _known_pair(self, a, b) -> bool:
        return (type(a) is str and type(b) is str and a != b
                and a in self._relations and b in self._relations)

    def _relation(self, a: str, b: str) -> _Relation:
        if not self._known_pair(a, b):
            raise ValueError("A relationship requires two distinct known cat IDs")
        return self._relations[a][b]

    def score(self, a: str, b: str) -> Number:
        return self._relation(a, b).score

    def meal_gain(self, a: str, b: str) -> Number:
        """Cumulative gain for this direction, for display without private data."""
        return self._relation(a, b).meal_gain

    def pair_score(self, a: str, b: str) -> Number:
        return min(self.score(a, b), self.score(b, a))

    def co_visit_factor(self, a: str, b: str) -> float:
        return .2 + .8*self.pair_score(a, b)/SCORE_LIMIT

    def fight_chance(self, a: str, b: str) -> float:
        return .35*(1.6 - 1.2*self.pair_score(a, b)/SCORE_LIMIT)

    def share_meal(self, a: str, b: str) -> dict[str, Number]:
        """Grant one completed meal; return only actual positive gains by ID.

        An invalid/self pair has no effect. Repeated timer ticks for the same
        meal must be filtered by the caller, which knows the meal's lifetime.
        """
        if not self._known_pair(a, b):
            return {}
        changes = {}
        for source, target in ((a, b), (b, a)):
            relation = self._relations[source][target]
            gain = min(MEAL_STEP, MEAL_GAIN_LIMIT-relation.meal_gain,
                       SCORE_LIMIT-relation.score)
            if gain > 0:
                self._relations[source][target] = _Relation(
                    relation.initial, relation.meal_gain+gain)
                changes[source] = gain
        return changes

    def restore(self, state) -> None:
        """Copy valid saved directions; invalid entries leave current values intact."""
        if (not isinstance(state, dict)
                or type(state.get("schema_version")) is not int
                or state["schema_version"] != SCHEMA_VERSION
                or not isinstance(state.get("relations"), dict)):
            return
        for source, targets in state["relations"].items():
            if source not in self._relations or not isinstance(targets, dict):
                continue
            for target, row in targets.items():
                if not self._known_pair(source, target) or not isinstance(row, dict):
                    continue
                initial, gain = row.get("initial"), row.get("meal_gain")
                if (not _bounded_number(initial, SCORE_LIMIT)
                        or not _bounded_number(gain, min(MEAL_GAIN_LIMIT, SCORE_LIMIT-initial))):
                    continue
                # Replace a direction atomically. A malformed gain must not
                # partially overwrite that direction's freshly generated value.
                self._relations[source][target] = _Relation(initial, gain)

    def export(self) -> dict:
        """Return detached JSON data; no names, object references, or RNG state."""
        return {
            "schema_version": SCHEMA_VERSION,
            "relations": {
                source: {target: {"initial": relation.initial,
                                  "meal_gain": relation.meal_gain}
                         for target, relation in targets.items()}
                for source, targets in self._relations.items()
            },
        }
