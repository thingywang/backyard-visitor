"""Small, deterministic desktop-cat simulation, independent of any GUI toolkit.

Coordinates are logical desktop pixels and visitor positions are sprite top-lefts.
Only relationships, bowl contents, bed placement and dropped objects persist;
opening the gate is always an explicit action in a new session.
"""
from __future__ import annotations

from dataclasses import dataclass, field, replace
from copy import deepcopy
import hashlib
import json
import math
import random
import unicodedata
from typing import Any, Iterable

from .interaction_model import (current_session, cancel_teaser, start_teaser,
                                stop_teaser, scratch_on_pet, tick_interaction, begin_daily_wake)
from .gift_geometry import GIFT_SIZE, WINDOW_SURFACE_OFFSET, drop_position, mouth_anchor
from . import resident_bed as bed_model
from . import top_entry, climb_routes, neighbor_lifecycle
from .depth_visit import DepthRoute, TurningDepthVisit
from .cat_relations import CatRelations
from .coat_traits import CoatSpec, generate_coat, describe_coat
from .tree_geometry import TreeLayout
from .tree_visit import TreeVisit

SPRITE_WIDTH = 144
SPRITE_HEIGHT = 112
SHALLOW_MOVE_HEIGHT = SPRITE_HEIGHT / 2
MOUSE_WAIT_MIN = 8.0
MOUSE_WAIT_MAX = 22.0
MOUSE_ESCAPE_SPEED = 220.0
# Logical geometry contract with CatArt's normalized 144x112 eating frame.
# The native left-facing muzzle is 14.3px from the sprite origin; mirroring
# moves it to 129.7px. Keep these offsets in sync if that artwork changes.
EATING_MUZZLE_LEFT = 14.3
EATING_MUZZLE_RIGHT = SPRITE_WIDTH - EATING_MUZZLE_LEFT
STATE_VERSION = 1
PET_TRUST = 55.0
NAME_TRUST = 35.0
GIFT_TRUST = 75.0
MAX_VISITORS = 3
GENERATED_CAT_IDS = ("generated-a", "generated-b")
MAX_IDENTITY_SEED = 0xFFFFFFFF
MAX_GIFTS = 100
MAX_VISITS = 1_000_000
MAX_COLLECTION_COUNT = 1_000_000_000
# One initial visit after 20 active, open-gate seconds leaves time for the
# existing edge peek and continuous entrance to become visible before 30s.
WELCOME_VISIT_DELAY = 20.0
GIFT_DISPLAY_NAMES = {"feather": "羽毛", "bottle_cap": "瓶盖", "toy_mouse": "小老鼠"}
COLLECTIBLE_GIFTS = tuple(GIFT_DISPLAY_NAMES)
PLAY_RADIUS = 240.0
PLAY_SPEED = 90.0
PLAY_RETARGET_INTERVAL = 0.8
PLAY_DEAD_ZONE = 18.0
# At full window peek only the top 34px of the sprite remain above the edge.
WINDOW_PEEK_DEPTH = 78.0
WINDOW_PEEK_SECONDS = 1.25
REAR_BLEND_SECONDS = .40
TAIL_EXTENT = 56.0
LOW_HEADROOM_TOLERANCE = 12.0
WINDOW_JUMP_X = 64.0
WINDOW_JUMP_Y = 220.0
# Initial climbs must reach real application title bars, often 700-900px above
# the desktop floor. Give that one ascent its own distance-scaled duration;
# this does not increase the reach of later jumps between window tops.
WINDOW_FLOOR_LAUNCH_SPEED = 170.0
# The accepted sprites' near front paw travels source x205 -> x455 -> x677
# across its support half-cycle: (677-205)*.15625/2 = 36.875 logical pixels.
# A 74px stride matches that support distance; at 66px/s a cycle lasts 1.12s.
# Phase belongs to actual travel, never wall-clock age or the current state.
GAIT_STRIDE = 74.0
RUN_STRIDE = 100.0
RUN_SPEED = 210.0
PARKOUR_CHANCE = {"mischievous": .18, "graceful": .09, "shy": .025}
GROUND_ACCELERATION = 300.0
GROUND_DECELERATION = 360.0
TURN_SECONDS = 0.10
TAKEOFF_SECONDS = 0.20
LANDING_SECONDS = 0.24
SETTLE_SECONDS = 0.7
RISE_SECONDS = 0.36
TRUE_NAME_SECONDS = 7.0
MEAL_COOLDOWN_SECONDS = 180.0
GIFT_RESPONSE_SECONDS = 3.0
GROUND_STATES = frozenset({"entering", "walking", "running", "leaving", "fleeing", "playing", "teasing"})


def _number(value: Any, default: float, low: float, high: float) -> float:
    if isinstance(value, bool):
        return default
    try:
        result = float(value)
    except (TypeError, ValueError, OverflowError):
        return default
    return min(high, max(low, result)) if math.isfinite(result) else default


def normalized_name(value: str) -> str:
    """Compare Unicode names without punctuation, spacing or letter case."""
    return "".join(c for c in unicodedata.normalize("NFKC", value).casefold() if c.isalnum())


def names_match(nickname: str, true_name: str) -> bool:
    """Exact normalized matches, or one typo in a long Latin-alphabet name.

    Short names (including the built-in Chinese names) must match exactly: a
    generic fuzzy similarity score would reveal the secret far too often.
    """
    a, b = normalized_name(nickname), normalized_name(true_name)
    if not a or not b:
        return False
    if a == b:
        return True
    if min(len(a), len(b)) < 6 or abs(len(a) - len(b)) > 1:
        return False
    if not all("a" <= c <= "z" for c in a + b):
        return False
    previous = list(range(len(b) + 1))
    for i, ca in enumerate(a, 1):
        current = [i]
        for j, cb in enumerate(b, 1):
            current.append(min(current[-1] + 1, previous[j] + 1, previous[j - 1] + (ca != cb)))
        previous = current
    return previous[-1] <= 1


@dataclass(slots=True)
class CatProfile:
    id: str
    coat: str
    personality: str
    true_name: str = field(repr=False)
    trust: float = 0.0
    nickname: str = ""
    adopted: bool = False
    visits: int = 0
    discovered: bool = False
    meal_cooldown_remaining: float = 0.0
    neighbor_status: str = "guest"
    meal_count: int = 0
    exposure_seconds: float = 0.0
    last_seen_order: int = 0
    identity_seed: int | None = field(default=None, repr=False)
    coat_profile: CoatSpec | None = None
    coat_warning: str | None = None
    _unreadable_coat_profile: Any = field(default=None, repr=False)
    _coat_profile_unavailable: bool = field(default=False, repr=False)

    @property
    def is_neighbor(self) -> bool:
        return self.neighbor_status == "neighbor"

    @property
    def is_departed(self) -> bool:
        return self.neighbor_status == "departed"

    @property
    def render_coat(self) -> CoatSpec | str:
        return self.coat_profile if self.coat_profile is not None else self.coat

    @property
    def coat_description(self) -> str:
        if self.coat_profile is not None:
            return describe_coat(self.coat_profile)
        if self._coat_profile_unavailable:
            return "毛色待恢复的"
        return {"ginger": "棕纹", "gray": "灰色", "tuxedo": "深灰", "black": "黑色"}.get(self.coat, "")

    @property
    def display_name(self) -> str:
        return self.nickname or self.coat_description + "访客"

    def identity_record(self) -> dict[str, Any]:
        """Stable public appearance and identity seeds, never the private name."""
        if self.identity_seed is None:
            return {}
        profile = (deepcopy(self._unreadable_coat_profile) if self._coat_profile_unavailable
                   else self.coat_profile.to_dict())
        return {"identity_seed": self.identity_seed, "personality": self.personality,
                "coat_profile": profile}

    @property
    def recognizes_name(self) -> bool:
        return self.discovered and bool(self.nickname) and self.trust >= NAME_TRUST


@dataclass(slots=True)
class Visitor:
    cat_id: str
    x: float
    y: float
    state: str = "peeking"
    facing: int = 1
    age: float = 0.0
    state_age: float = 0.0
    target_x: float = 0.0
    target_y: float = 0.0
    stay_remaining: float = 120.0
    pet_cooldown: float = 0.0
    has_eaten: bool = False
    meal_bowl: tuple[float, float] | None = None
    carrying_gift: str | None = None
    next_action: float = 6.0
    arrival_state: str = "idle"
    arrival_facing: int | None = None
    jump_start_x: float = 0.0
    jump_start_y: float = 0.0
    jump_duration: float = 1.0
    jump_arc: float = 64.0
    takeoff_remaining: float = 0.0
    jump_elapsed: float = 0.0
    takeoff_edge_id: str | None = None
    jump_vy: float = 0.0
    jump_pose_phase: float = 0.0
    jump_vertical_span: float = 0.0
    climb_side_id: str | None = None
    climb_phase: float = 0.0
    play_retarget_in: float = 0.0
    play_speed: float = 45.0
    gift_spot: tuple[float, float] | None = None
    window_edge_id: str | None = None
    window_target_id: str | None = None
    peek_progress: float = 0.0
    peek_phase: str = ""
    clip_y: float | None = None
    peek_elapsed: float = 0.0
    peek_hold: float = 3.0
    peek_start: float = 0.0
    peek_after: str = "idle"
    rear_blend: float = 0.0
    gait_phase: float = 0.0
    run_phase: float = 0.0
    parkour_remaining: float = 0.0
    parkour_cooldown: float = 0.0
    ground_speed: float = 0.0
    turn_remaining: float = 0.0
    turn_target_facing: int | None = None
    landing_remaining: float = 0.0
    settle_remaining: float = 0.0
    rise_remaining: float = 0.0
    true_name_pending: bool = False
    true_name_remaining: float = 0.0
    # Retain the belly pose and the original nap clock during social feedback.
    sleeping_purr: bool = False
    sleeping_purr_age: float = 0.0
    bed_route_revision: int | None = None
    bed_nap: bool = False
    gift_response_pending: bool = False
    gift_response_remaining: float = 0.0
    demo_gift_pending: str | None = None
    # Explicit sprite transitions; progress is 1-remaining/duration. Runtime
    # only, with stable source/target names for shared rendering endpoints.
    posture_kind: str = ""
    posture_remaining: float = 0.0
    posture_duration: float = 0.0
    posture_source: str = ""
    posture_target: str = ""
    # A vertical route consists of a supported approach, a short lateral hop,
    # then a horizontal finish. These plans never survive a process restart.
    route_final_x: float | None = None
    route_final_y: float | None = None
    route_edge_id: str | None = None
    route_stage: str = ""
    route_motion: str = "walking"

    # Runtime only: full-screen normalized route and its fixed-window rendering
    # snapshot. Nothing here is included in Engine.export().
    depth_trip: TurningDepthVisit | None = field(default=None, repr=False)
    depth_scale: float = 1.0
    depth_layer: str = "front"
    depth_pose: dict | None = None
    depth_screen: tuple[float, float, float, float] | None = None
    depth_pending_action: str = ""
    depth_viewport: tuple[float, float, float, float] | None = None
    tree_trip: TreeVisit | None = field(default=None, repr=False)
    tree_pose: dict | None = None
    tree_scale: float = 1.0
    tree_pending_action: str = ""

    @property
    def on_back(self) -> bool:
        return self.state == "sleeping" or (self.state == "purring" and self.sleeping_purr)

    @property
    def moving(self) -> bool:
        return self.state in {"entering", "walking", "running", "jumping", "climbing", "fleeing", "leaving", "top_entering", "top_leaving"}


@dataclass(slots=True)
class Gift:
    id: str
    kind: str
    x: float
    y: float
    cat_id: str
    # Runtime-only behavior; legacy toy_mouse saves start a fresh safe delay.
    state: str = "idle"
    wait_remaining: float = 16.0
    escape_direction: int = 0
    facing: int = -1


class Engine:
    """Ecology clock and user actions. Call ``tick`` with monotonic elapsed time.

    ``tick`` caps a single elapsed interval at five seconds, so waking from
    system sleep cannot cause a burst of arrivals or invisible interactions.
    Actions report their outcome and enqueue human-readable Chinese events.
    """

    def __init__(self, state: dict[str, Any] | None = None, seed: int | None = None,
                 demo: bool = False, visit_limit: int = MAX_VISITORS):
        if type(visit_limit) is not int or not 1 <= visit_limit <= MAX_VISITORS:
            raise ValueError("visit_limit must be an integer from 1 to 3")
        self.visitor_limit = visit_limit
        self.rng = random.Random(seed)
        self.demo = bool(demo)
        self.cats = {
            "amber": CatProfile("amber", "ginger", "mischievous", "栗子"),
            "mist": CatProfile("mist", "gray", "shy", "薄荷"),
            "ink": CatProfile("ink", "tuxedo", "graceful", "露娜"),
            "onyx": CatProfile("onyx", "black", "graceful", '墨影'),
        }
        # Preserve the original four neighbours' deterministic relations too:
        # inserting sorted generated IDs must not reshuffle their initial draws.
        original_relations = CatRelations(self.cats, seed=seed).export()
        self._add_generated_cats(state, seed)
        # Identity-level relations own their RNG, preserving the established
        # visit, movement and gift random stream for the same engine seed.
        self.relations = CatRelations(self.cats, seed=seed, state=original_relations)
        self.visitors: dict[str, Visitor] = {}
        self._meal_serial = 0
        self._meal_tokens: dict[str, tuple[int, tuple[float, float]]] = {}
        self._shared_meal_tokens: dict[tuple[str, str], dict[str, Any]] = {}
        self.gifts: list[Gift] = []
        self._collection_counts = {kind: 0 for kind in COLLECTIBLE_GIFTS}
        self.gate_open = False
        self.calm_mode = False
        self.food = 0
        self.bounds = (0.0, 0.0, 1280.0, 720.0)
        self.bowl = (640.0, 682.0)
        self.bed: bed_model.ResidentBed | None = None
        self._bed_revision = 0
        self.paths: list[tuple[float, float]] = []
        self.window_edges: dict[str, tuple[float, float, float]] = {}
        self.low_headroom_edges: set[str] = set()
        self.window_climb_sides: dict[str, climb_routes.Side] = {}
        self.screen_top = self.bounds[1]
        self.gift_spots: list[tuple[float, float]] = []
        self._cursor: tuple[float, float] | None = None
        self._events: list[dict[str, Any]] = []
        self._next_gift_id = 1
        self._visit_countdown = self._visit_delay()
        self._welcome_visit_remaining = None if self.demo else WELCOME_VISIT_DELAY
        self._welcome_regular_delay = self._visit_countdown
        self._fight_countdown = self.rng.uniform(20, 45)
        self._fight_pair: tuple[str, str] | None = None
        self._time = 0.0
        self._demo_pending: str | None = None
        self._teaser = None
        self._restore(state)
        neighbor_lifecycle.initialize(self, state, seed)
        for cat in self.cats.values():
            if cat.coat_warning:
                self._emit("coat_warning", cat.id, cat.coat_warning)

    def _add_generated_cats(self, state, seed, cat_ids=None):
        """Create two per-save identities without touching the ecology RNG."""
        rows = {}
        if isinstance(state, dict):
            version = state.get("version", 0)
            if type(version) is int and 0 <= version <= STATE_VERSION:
                rows = state.get("cats", {})
                if isinstance(rows, list):
                    rows = {row.get("id"): row for row in rows
                            if isinstance(row, dict) and isinstance(row.get("id"), str)}
        if not isinstance(rows, dict):
            rows = {}
        source = random.Random(None if seed is None else f"neighbour-slots-v1:{seed!r}")
        private_names = tuple(self.cats[cid].true_name for cid in ("amber", "mist", "ink", "onyx"))
        personalities = ("mischievous", "shy", "graceful")
        for cid in (cat_ids if cat_ids is not None else GENERATED_CAT_IDS + neighbor_lifecycle.dynamic_ids(rows)):
            default_seed = source.getrandbits(32)
            row = rows.get(cid, {})
            if not isinstance(row, dict):
                row = {}
            stored_seed = row.get("identity_seed")
            valid_seed = type(stored_seed) is int and 0 <= stored_seed <= MAX_IDENTITY_SEED
            warning = None
            if valid_seed:
                identity_seed = stored_seed
            elif "identity_seed" in row or "coat_profile" in row or cid not in GENERATED_CAT_IDS:
                # Invalid partial identity data must not draw another neighbour
                # on every restart. Preserve its raw coat and recover a stable
                # seed from just the identity fields (not nickname or trust).
                raw = {key: row.get(key) for key in ("identity_seed", "coat_profile")}
                try:
                    payload = json.dumps(raw, sort_keys=True, ensure_ascii=False,
                                         default=lambda value: type(value).__name__)
                except (ValueError, TypeError, RecursionError, OverflowError):
                    payload = "unreadable-identity"
                digest = hashlib.sha256(f"neighbour-recovery-v1:{cid}:{payload}".encode()).digest()
                identity_seed = int.from_bytes(digest[:4], "big")
                warning = "随机访客的身份记录已安全恢复；原有毛色数据仍保留。"
            else:
                identity_seed = default_seed
            identity_rng = random.Random(f"neighbour-identity-v1:{identity_seed}")
            personality = identity_rng.choice(personalities)
            if row.get("personality") in personalities:
                personality = row["personality"]
            private_name = identity_rng.choice(private_names)
            coat_seed = random.Random(f"neighbour-coat-v1:{identity_seed}").getrandbits(32)
            profile = None
            unreadable = False
            if "coat_profile" in row:
                try:
                    profile = CoatSpec.from_dict(row["coat_profile"])
                except (ValueError, TypeError, OverflowError):
                    unreadable = True
                    warning = "一位访客的毛色记录暂时无法读取；原数据已保留，暂用灰色外观。"
            else:
                profile = generate_coat(coat_seed)
            cat = CatProfile(cid, "gray", personality, private_name,
                             identity_seed=identity_seed, coat_profile=profile,
                             coat_warning=warning)
            if unreadable:
                cat._unreadable_coat_profile = deepcopy(row["coat_profile"])
                cat._coat_profile_unavailable = True
            self.cats[cid] = cat

    @property
    def active_teaser_id(self) -> str | None:
        session = current_session(self)
        return session.cat_id if session is not None else None

    @property
    def teaser_phase(self) -> str | None:
        session = current_session(self)
        return session.phase if session is not None else None

    def start_teaser(self, cat_id: str) -> bool:
        if not isinstance(cat_id, str):
            return False
        if self.request_tree_return(cat_id, "play"):
            return False
        if cat_id in self.visitors and self.visitors[cat_id].tree_trip is not None:
            return False
        return start_teaser(self, cat_id)

    def stop_teaser(self, cat_id: str) -> bool:
        return stop_teaser(self, cat_id)

    def cat_reference(self, cat_id: str | None) -> str:
        """Use only a discovered visitor's user-supplied name in journal prose.

        This deliberately never reads the hidden name. Call sites insert the
        subject explicitly, so a nickname containing pronouns or braces is
        ordinary text and cannot rewrite another visitor's identity.
        """
        cat = self.cats.get(cat_id) if isinstance(cat_id, str) else None
        return cat.nickname if cat and cat.discovered and cat.nickname else "它"

    def is_discovered(self, cat_id: str) -> bool:
        """Whether a real visit (or a validated saved visit) unlocked this cat."""
        cat = self.cats.get(cat_id) if isinstance(cat_id, str) else None
        return bool(cat and cat.discovered)

    def discovered_cat_ids(self) -> tuple[str, ...]:
        """Stable roster IDs only; no hidden names or unseen profile details."""
        return tuple(cid for cid, cat in self.cats.items() if cat.discovered)

    def roster_cat_ids(self) -> tuple[str, ...]:
        return neighbor_lifecycle.roster_ids(self)

    def guest_waiting_for_slot(self, cat_id: str) -> bool:
        return neighbor_lifecycle.waiting_for_slot(self, cat_id)

    def account_guest_exposure(self, dt, visible_ids, display_awake=True) -> bool:
        return neighbor_lifecycle.account_exposure(self, dt, visible_ids, display_awake)

    @property
    def collection_counts(self) -> dict[str, int]:
        """A detached snapshot of cumulative gifts explicitly collected."""
        return self._collection_counts.copy()

    def collected_count(self, kind: str) -> int:
        return self._collection_counts.get(kind, 0) if isinstance(kind, str) else 0

    @property
    def floor_y(self) -> float:
        return self.bounds[1] + self.bounds[3] - SPRITE_HEIGHT

    @property
    def resident_id(self) -> str | None:
        return next((cat.id for cat in self.cats.values() if cat.adopted), None)

    @property
    def bed_y(self) -> float:
        return self.bounds[1] + self.bounds[3] - bed_model.BED_HEIGHT/2

    def place_bed(self, cat_id: str, x: float | None = None) -> bool:
        return bed_model.place_bed(self, cat_id, x)

    def move_bed(self, x: float) -> bool:
        return bed_model.move_bed(self, x)

    def remove_bed(self) -> bool:
        return bed_model.remove_bed(self)

    @property
    def moving(self) -> bool:
        return (any((v.depth_trip is not None and not v.depth_trip.paused)
                    or (v.tree_trip is not None and v.tree_trip.moving) or v.moving or v.posture_remaining > 0 or v.landing_remaining > 0 or v.settle_remaining > 0
                    or v.rise_remaining > 0 or v.true_name_remaining > 0 or v.gift_response_remaining > 0 or v.state in {"fighting", "playing", "carried", "teasing", "swatting"}
                    or (v.state == "window_peeking" and v.peek_phase != "holding")
                    or abs(v.rear_blend-(1.0 if v.state == "window_rear" else 0.0)) > 1e-9
                    for v in self.visitors.values())
                or any(g.kind == "toy_mouse" and g.state == "escaping" for g in self.gifts))

    def can_begin_depth_visit(self, cat_id: str) -> bool:
        """Only an otherwise uncommitted, fully supported ground cat can leave."""
        v = self.visitors.get(cat_id)
        return bool(self.gate_open and v is not None and v.depth_trip is None and v.tree_trip is None
                    and v.state in {"idle", "walking"} and abs(v.y-self.floor_y) <= 1e-7
                    and v.arrival_state == "idle" and v.route_motion == "walking"
                    and not any((v.window_edge_id, v.window_target_id, v.takeoff_edge_id,
                                 v.route_stage, v.route_edge_id, v.route_final_x is not None,
                                 v.bed_route_revision is not None, v.bed_nap, v.carrying_gift,
                                 v.demo_gift_pending, v.true_name_pending, v.gift_response_pending,
                                 v.posture_remaining, v.landing_remaining, v.settle_remaining,
                                 v.rise_remaining, v.takeoff_remaining, v.turn_remaining,
                                 v.true_name_remaining, v.gift_response_remaining, v.rear_blend,
                                 v.meal_bowl, self.active_teaser_id == cat_id)))

    def can_begin_tree_visit(self, cat_id: str) -> bool:
        v = self.visitors.get(cat_id) if isinstance(cat_id, str) else None
        return bool(v is not None and self.can_begin_depth_visit(cat_id)
                    and (self.cats[cat_id].adopted or v.stay_remaining >= 45.))

    def _tree_occupied(self, except_id=None):
        return set().union(*(v.tree_trip.reserved_supports for cid, v in self.visitors.items()
                             if cid != except_id and v.tree_trip is not None))

    def begin_tree_visit(self, cat_id: str, layout: TreeLayout) -> bool:
        if not self.can_begin_tree_visit(cat_id) or not isinstance(layout, TreeLayout):
            return False
        if layout.base_id in self._tree_occupied(cat_id):
            return False
        if any(v.tree_trip is not None and v.tree_trip.layout.fingerprint != layout.fingerprint
               for v in self.visitors.values()):
            return False
        v = self.visitors[cat_id]
        sleep_seconds = None
        if self.bed is not None and self.bed.cat_id == cat_id and self.cats[cat_id].adopted:
            # Both pieces of furniture draw on the same effective-time credit.
            # Travel also earns credit, but does not grant a second full budget.
            sleep_seconds = min(40., max(0., self.bed.credit) / (1-self.bed.sleep_fraction))
        try:
            trip = TreeVisit(layout, (v.x+72, v.y+108), facing=v.facing,
                             gait_phase=v.gait_phase, personality=self.cats[cat_id].personality,
                             sleep_seconds=sleep_seconds)
        except (TypeError, ValueError, OverflowError, KeyError):
            return False
        self.cancel_parkour(v)
        self._clear_route(v)
        self._state(v, "tree_visiting")
        v.tree_trip, v.tree_pending_action = trip, ""
        self._sync_tree(v)
        self._emit("tree_visit", cat_id, f"{self.cat_reference(cat_id)}朝后院的猫爬架走去了。")
        return True

    def request_tree_return(self, cat_id: str, reason="called") -> bool:
        v = self.visitors.get(cat_id) if isinstance(cat_id, str) else None
        if v is None or v.tree_trip is None:
            return False
        changed = v.tree_trip.request_return(reason)
        if changed:
            self._sync_tree(v)
        return changed

    def request_tree_return_all(self, reason="called") -> bool:
        changed = False
        for cid in tuple(self.visitors):
            changed = self.request_tree_return(cid, reason) or changed
        return changed

    def _queue_tree_action(self, v: Visitor, action: str, reason: str):
        priority = {"": 0, "called": 1, "pet": 2, "leaving": 3, "fleeing": 4}
        if priority[action] > priority[v.tree_pending_action]:
            v.tree_pending_action = action
        if action in {"fleeing", "leaving"}:
            v.true_name_pending = v.gift_response_pending = False
            v.true_name_remaining = v.gift_response_remaining = 0.
        self.request_tree_return(v.cat_id, reason)

    def _sync_tree(self, v: Visitor):
        snapshot = v.tree_trip.snapshot()
        v.x, v.y = snapshot["foot"][0]-72, snapshot["foot"][1]-108
        v.tree_scale, v.tree_pose = snapshot["scale"], snapshot
        v.facing, v.gait_phase, v.run_phase = snapshot["facing"], snapshot["gait_phase"], snapshot["run_phase"]
        v.target_x, v.target_y = v.x, v.y

    def _tick_tree_visit(self, v: Visitor, dt: float):
        v.tree_trip.step(dt, self._tree_occupied(v.cat_id))
        self._sync_tree(v)
        if not v.tree_pose["done"]:
            return
        action = v.tree_pending_action
        v.tree_trip = v.tree_pose = None
        v.tree_scale, v.tree_pending_action = 1., ""
        self._state(v, "idle")
        v.next_action = max(3., v.next_action)
        if action in {"leaving", "fleeing"} or not self.gate_open:
            self._leave(v, fleeing=action == "fleeing")
        elif abs(v.y-self.floor_y) > 1e-7 or self._inside_x(v.x) != v.x:
            self._walk(v, self._inside_x(v.x), self.floor_y,
                       arrival="purring" if action in {"pet", "called"} else "idle")
        elif action == "pet":
            self.pet(v.cat_id)
        elif action == "called":
            self._affection(v)
        self._emit("tree_return", v.cat_id, f"{self.cat_reference(v.cat_id)}从爬架回到你身边。")

    def begin_depth_visit(self, cat_id: str, route: DepthRoute, *,
                          screen_rect: tuple[float, float, float, float]) -> bool:
        """Start at the actual current foot; no random triggering or teleport.

        The caller supplies selected *full-screen* geometry, rather than the
        work area. Changing start/near height is revalidated against all masks.
        A route incompatible with this cat's current position is simply refused.
        """
        if not self.can_begin_depth_visit(cat_id) or not isinstance(route, DepthRoute):
            return False
        if (not isinstance(screen_rect, (tuple, list)) or len(screen_rect) != 4
                or any(type(n) not in (int, float) or not math.isfinite(n) for n in screen_rect)):
            return False
        sx, sy, sw, sh = map(float, screen_rect)
        if sw <= SPRITE_WIDTH or sh < SPRITE_HEIGHT:
            return False
        v = self.visitors[cat_id]
        start = ((v.x+72-sx)/sw, (v.y+108-sy)/sh)
        try:
            route = replace(route, start=start, near=(route.near[0], start[1]),
                            cat_width=SPRITE_WIDTH/sw, aspect_ratio=sw/sh)
            trip = TurningDepthVisit(route, initial_heading=0. if v.facing > 0 else 180.)
        except (TypeError, ValueError, OverflowError):
            return False
        trip._gait = v.gait_phase
        self.cancel_parkour(v)
        self._clear_route(v)
        self._state(v, "depth_visiting")
        v.depth_trip, v.depth_screen = trip, (sx, sy, sw, sh)
        v.depth_viewport = (sx, sy, sw, sh)
        v.depth_pending_action = ""
        self._sync_depth(v)
        return True

    def request_depth_return(self, cat_id: str, reason: str = "called") -> bool:
        """Request one continuous return without restarting repeated requests."""
        v = self.visitors.get(cat_id)
        if v is None or v.depth_trip is None:
            return False
        changed = v.depth_trip.request_return(reason)
        if changed:
            self._sync_depth(v)
        return changed

    def set_depth_screen_rect(self, screen_rect: tuple[float, float, float, float]) -> None:
        """Rebase active trips after set_bounds using actual full-screen origin.

        Preserve each active route's pixel scale/extent and phase, translate by
        the display-origin delta, and return. A resized screen does not stretch
        a cat mid-trip. The UI must disable old masks on context invalidation.
        """
        if (not isinstance(screen_rect, (tuple, list)) or len(screen_rect) != 4
                or any(type(n) not in (int, float) or not math.isfinite(n) for n in screen_rect)
                or screen_rect[2] < SPRITE_WIDTH or screen_rect[3] < SPRITE_HEIGHT):
            raise ValueError("Depth screen must be a finite full-screen rectangle")
        sx, sy, sw, sh = map(float, screen_rect)
        for v in self.visitors.values():
            if v.tree_trip is not None:
                viewport = (sx, sy, sw, sh)
                if v.tree_trip.screen_rect != viewport:
                    v.tree_trip.rebase_screen(viewport)
                    self._sync_tree(v)
            if v.depth_trip is None:
                continue
            viewport = (sx, sy, sw, sh)
            if v.depth_screen == viewport and v.depth_viewport == viewport:
                continue
            _, _, old_w, old_h = v.depth_screen
            v.depth_screen = (sx, sy, old_w, old_h)
            v.depth_viewport = viewport
            v.depth_trip.rebase_escape(v.depth_screen, viewport)
            self._sync_depth(v)
            self.request_depth_return(v.cat_id, "screen_changed")

    def _queue_depth_action(self, v: Visitor, action: str, reason: str) -> None:
        if v.depth_trip.escaping:
            return  # Calls, guard invalidation and gate-close never slow panic.
        if action == "fleeing":
            v.depth_trip.begin_escape(v.depth_screen, v.depth_viewport or v.depth_screen,
                                      run_phase=v.run_phase, speed=RUN_SPEED, stride=RUN_STRIDE)
            v.depth_pending_action = "fleeing"
            v.true_name_pending = v.gift_response_pending = False
            v.true_name_remaining = v.gift_response_remaining = 0.
            self._sync_depth(v)
            return
        priority = {"": 0, "called": 1, "pet": 2, "leaving": 3, "fleeing": 4}
        if priority[action] > priority[v.depth_pending_action]:
            v.depth_pending_action = action
        self.request_depth_return(v.cat_id, reason)

    def _sync_depth(self, v: Visitor) -> None:
        snapshot = v.depth_trip.snapshot()
        sx, sy, sw, sh = v.depth_screen
        v.x, v.y = sx+snapshot["foot"][0]*sw-72, sy+snapshot["foot"][1]*sh-108
        v.depth_scale, v.depth_layer = snapshot["scale"], snapshot["layer"]
        v.depth_pose = snapshot
        v.facing, v.gait_phase = snapshot["facing"], snapshot["gait_phase"]
        if snapshot.get("escaping"):
            v.run_phase = snapshot["run_phase"]
        v.target_x, v.target_y = v.x, v.y

    def _tick_depth_visit(self, v: Visitor, dt: float) -> None:
        v.depth_trip.step(dt)
        self._sync_depth(v)
        if not v.depth_pose["done"]:
            return
        escaped = v.depth_pose.get("escaping", False)
        action = v.depth_pending_action
        v.depth_trip = v.depth_screen = v.depth_pose = v.depth_viewport = None
        v.depth_scale, v.depth_layer, v.depth_pending_action = 1.0, "front", ""
        if escaped:
            # The entire scaled canvas has crossed the chosen screen edge.
            # Never reveal a scale-1 frame or send it back to the old origin.
            self.visitors.pop(v.cat_id, None)
            self._emit("departure", v.cat_id, f"{self.cats[v.cat_id].display_name}回邻居家去了。")
            return
        self._state(v, "idle")
        v.next_action = max(3.0, v.next_action)
        if action in {"leaving", "fleeing"} or not self.gate_open:
            self._leave(v, fleeing=action == "fleeing")
        elif abs(v.y-self.floor_y) > 1e-7 or self._inside_x(v.x) != v.x:
            # A work-area change during the trip never clamps its moving foot.
            # Only after arriving at its original point does ordinary movement
            # bring it onto the new floor, retaining accepted social feedback.
            if action == "pet":
                self.pet(v.cat_id)
            self._walk(v, self._inside_x(v.x), self.floor_y,
                       arrival="purring" if action in {"pet", "called"} else "idle")
        elif action == "pet":
            self.pet(v.cat_id)
        elif action == "called":
            self._affection(v)

    @property
    def has_gift_activity(self) -> bool:
        """Keep a low-rate clock alive for waiting mice, even with the gate shut.

        ``moving`` becomes true only while a mouse is actually running. Held
        mice are excluded so dragging cannot advance their automatic behavior.
        """
        return any(g.kind == "toy_mouse" and g.state in {"idle", "escaping"} for g in self.gifts)

    def set_bounds(self, left: float, top: float, width: float, height: float) -> None:
        values = (left, top, width, height)
        if not all(isinstance(x, (int, float)) and math.isfinite(x) for x in values):
            raise ValueError("Bounds must contain finite numeric coordinates")
        if width < SPRITE_WIDTH or height < SPRITE_HEIGHT:
            raise ValueError("Desktop is smaller than the cat sprite")
        old_bounds, old_floor = self.bounds, self.floor_y
        self.bounds = tuple(float(v) for v in values)
        dx, dy = left - old_bounds[0], top - old_bounds[1]
        self.screen_top += dy
        self.set_bowl(self.bowl[0] + dx, self.bowl[1] + dy)
        if self.bed is not None and old_bounds != self.bounds:
            bed_model.invalidate_bed_intent(self)
            self.bed.x = bed_model.clamped_x(self, self.bed.x + dx)
        for v in self.visitors.values():
            if v.tree_trip is not None:
                if old_bounds != self.bounds:
                    self.request_tree_return(v.cat_id, "screen_changed")
                continue
            if v.depth_trip is not None:
                # Work-area bounds are not full-screen geometry. Leave this
                # route intact; set_depth_screen_rect handles monitor origins.
                if old_bounds != self.bounds:
                    self.request_depth_return(v.cat_id, "screen_changed")
                continue
            # An entering visitor may still be mostly outside its source edge.
            # Preserve that exposed portion through display rebasing; the usual
            # inside clamp would reveal its whole body without a movement tick.
            entering_from_left = v.state == "entering" and v.x < old_bounds[0]
            entering_from_right = (v.state == "entering"
                                   and v.x + SPRITE_WIDTH > old_bounds[0] + old_bounds[2])
            if old_bounds != self.bounds:
                self._clear_posture(v)
                if self.active_teaser_id == v.cat_id and self._teaser.phase == "rise":
                    self._teaser.phase, self._teaser.wait = "watch", .4
            if v.route_final_x is not None:
                v.route_final_x = self._inside_x(v.route_final_x+dx)
                v.route_final_y = (self.floor_y if abs(v.route_final_y-old_floor)<1
                                   else min(self.floor_y, v.route_final_y+dy if v.route_final_y < old_bounds[1]
                                            else max(top, v.route_final_y+dy)))
            v.x += dx
            v.y = self.floor_y if abs(v.y - old_floor) < 1 else min(
                self.floor_y, v.y+dy if v.y < old_bounds[1] else max(top, v.y+dy))
            if v.state in {"leaving", "fleeing"}:
                v.target_x = left - SPRITE_WIDTH - 2 if v.facing < 0 else left + width + 2
            elif v.state == "peeking":
                v.x = left - SPRITE_WIDTH + 18 if v.facing > 0 else left + width - 18
                v.target_x = self._inside_x(v.target_x + dx)
            elif entering_from_left or entering_from_right:
                if entering_from_right:
                    v.x += width - old_bounds[2]
                v.target_x = self._inside_x(v.target_x + dx)
            else:
                v.x = self._inside_x(v.x)
                v.target_x = self._inside_x(v.target_x + dx)
            v.target_y = self.floor_y if abs(v.target_y-old_floor)<1 else min(
                self.floor_y, v.target_y+dy if v.target_y < old_bounds[1] else max(top, v.target_y+dy))
            # An in-flight arc has a start position as well as a destination.
            # Rebase it too when changing monitors, then discard native edges.
            v.jump_start_x += dx
            v.jump_start_y += dy
            if old_bounds != self.bounds and v.state == "jumping" and v.window_target_id is None:
                # A smaller screen can clamp the current position and target,
                # leaving the former arc origin outside the new play area.
                # Resume from the rebased position, never replay that old arc.
                # Window-bound arcs are replanned by set_window_edges below.
                if math.hypot(v.target_x-v.x, v.target_y-v.y) <= 1e-7:
                    self._state(v, "walking")  # Let normal arrival handling run, with zero travel.
                elif (abs(v.y-self.floor_y) <= 1e-7 and abs(v.target_y-self.floor_y) <= 1e-7
                      and self.active_teaser_id != v.cat_id):
                    # Both ends are now on the same real floor. Continue the
                    # horizontal route instead of replaying a decorative arc.
                    self._state(v, v.route_motion)
                else:
                    self._begin_jump(v, support=(False, None))
        for gift in self.gifts:
            gift.x = min(left + width - GIFT_SIZE, max(left, gift.x + dx))
            gift.y = min(top + height - GIFT_SIZE, max(top, gift.y + dy))
            if gift.kind == "toy_mouse" and gift.state != "held" and old_bounds != self.bounds:
                self._schedule_mouse(gift)
        self.paths = []
        self.set_window_edges([])
        self.gift_spots = []
        self._cursor = None
        for visitor in self.visitors.values():
            visitor.gift_spot = None

    def set_bowl(self, x: float, y: float) -> None:
        left, top, width, height = self.bounds
        self.bowl = (_number(x, left + width / 2, left + 24, left + width - 24),
                     _number(y, top + height - 38, top + 24, top + height - 24))

    def set_paths(self, points: Iterable[tuple[float, float]]) -> None:
        """Accept optional, already permission-checked icon or legacy waypoints."""
        left, top, width, _ = self.bounds
        cleaned = []
        for point in points:
            try:
                x, y = point
                if all(isinstance(n, (int, float)) and math.isfinite(n) for n in (x, y)):
                    if left <= x <= left + width - SPRITE_WIDTH and top <= y <= self.floor_y:
                        cleaned.append((float(x), float(y)))
            except (TypeError, ValueError):
                continue
        self.paths = cleaned[:32]

    def _preserve_window_segments(self, v: Visitor, previous, current, previous_low=()) -> set[str]:
        """Rebind split/merged tops only when the remaining footprint is intact.

        Gap numbers are not physical supports: a dialog elsewhere on a window
        can renumber the segment under this cat. Preserve its action and clock
        when that same window still supports the entire remaining path.
        """
        stable = set()
        references = {
            "window_edge_id": [v.x],
            "takeoff_edge_id": [v.x],
            "window_target_id": [v.target_x],
            "route_edge_id": [v.route_final_x],
        }
        if v.moving and v.state != "jumping":
            references["window_edge_id"].append(v.target_x)
        if v.route_stage in {"jump", "step", "finish"}:
            references["route_edge_id"].append(v.target_x)
            if v.route_stage == "finish":
                references["route_edge_id"].append(v.x)
        elif v.route_stage == "approach" and v.route_final_x is not None:
            # The remaining approach ends at target_x, not at the cat's
            # current x. A preserved destination must still accept the short
            # hop planned from that launch point; a valid final x alone is
            # insufficient after a segment shrinks.
            old_destination = previous.get(v.route_edge_id)
            if old_destination is not None:
                landing = min(v.target_x+WINDOW_JUMP_X,
                              max(v.target_x-WINDOW_JUMP_X, v.route_final_x))
                landing = min(old_destination[1]-SPRITE_WIDTH,
                              max(old_destination[0], landing))
                references["route_edge_id"].append(landing)
        if v.route_final_x is not None:
            references["window_target_id"].append(v.route_final_x)
        for attribute, positions in references.items():
            old_id = getattr(v, attribute)
            old = previous.get(old_id)
            if old is None:
                continue
            xs = [x for x in positions if x is not None]
            if not xs or not all(old[0] <= x <= old[1]-SPRITE_WIDTH for x in xs):
                continue
            family = old_id.rsplit(":top:", 1)[0] if ":top:" in old_id else old_id
            for key, edge in current.items():
                candidate_family = key.rsplit(":top:", 1)[0] if ":top:" in key else key
                if (candidate_family == family and edge[2] == old[2]
                        and (old_id in previous_low) == (key in self.low_headroom_edges)
                        and all(edge[0] <= x <= edge[1]-SPRITE_WIDTH for x in xs)):
                    setattr(v, attribute, key)
                    stable.add(attribute)
                    break
        return stable

    def set_window_edges(self, edges: Iterable[tuple[str, float, float, float]],
                         *, low_headroom_ids: Iterable[str] = (), screen_top: float | None = None,
                         climb_sides: Iterable[tuple] = ()) -> None:
        """Replace permission-checked, visible window tops in desktop pixels.

        Each interval is ``(stable_id, left, right, top)``. Ordinary supports
        require a full body above the selected screen; explicitly classified
        low-headroom supports permit only the rear-sitting fallback. Native geometry
        is transient and is never exported. Repeated identical polls preserve
        an in-progress jump or peek. Changed support is followed from the cat's
        current position, never by teleporting it with its former window.
        """
        left, top, width, height = self.bounds
        cleaned: dict[str, tuple[float, float, float]] = {}
        if type(screen_top) in (int, float) and math.isfinite(screen_top):
            self.screen_top = min(top, float(screen_top))
        requested_low = {key for key in low_headroom_ids if isinstance(key, str)}
        cleaned_low = set()
        for row in edges:
            try:
                edge_id, x1, x2, y = row
                if (not isinstance(edge_id, str) or not edge_id or len(edge_id) > 128
                        or edge_id in cleaned
                        or not all(type(n) in (int, float) and math.isfinite(n) for n in (x1, x2, y))):
                    continue
                x1, x2 = max(left, float(x1)), min(left + width, float(x2))
                low = edge_id in requested_low and top-LOW_HEADROOM_TOLERANCE <= y <= top+LOW_HEADROOM_TOLERANCE
                if x2 - x1 < SPRITE_WIDTH or not (low or top + SPRITE_HEIGHT <= y <= top + height):
                    continue
                cleaned[edge_id] = (x1, x2, float(y))
                if low: cleaned_low.add(edge_id)
                if len(cleaned) >= 64:
                    break
            except (TypeError, ValueError):
                continue
        previous, self.window_edges = self.window_edges, cleaned
        previous_low, self.low_headroom_edges = self.low_headroom_edges, cleaned_low
        old_sides, self.window_climb_sides = self.window_climb_sides, climb_routes.clean(
            climb_sides, cleaned, self.bounds, cleaned_low)
        for visitor in self.visitors.values():
            if visitor.climb_side_id is not None:
                self._preserve_window_segments(visitor, previous, cleaned, previous_low)
            if climb_routes.refresh(self, visitor, old_sides):
                continue
            prior_kind = {name: getattr(visitor, name) in previous_low for name in
                          ("route_edge_id", "window_target_id", "window_edge_id", "takeoff_edge_id")}
            stable = self._preserve_window_segments(visitor, previous, cleaned, previous_low)
            edge_reference = next((name for name in ("route_edge_id", "window_target_id", "window_edge_id")
                                   if getattr(visitor, name) is not None), None)
            edge_id = getattr(visitor, edge_reference) if edge_reference else None
            source_reference = ("takeoff_edge_id" if visitor.takeoff_edge_id is not None else
                                "window_edge_id" if visitor.route_stage in {"approach", "step", "finish"} else None)
            source_id = getattr(visitor, source_reference) if source_reference else None
            source_changed = (source_id is not None and source_reference not in stable
                              and (previous.get(source_id) != cleaned.get(source_id)
                                   or prior_kind[source_reference] != (source_id in cleaned_low)))
            if edge_id is None:
                if source_changed:
                    # A window-to-floor/food route has no destination edge,
                    # but its crouch still depends on the original support.
                    self._begin_jump(visitor, support=self._takeoff_support(visitor),
                                     takeoff_remaining=visitor.takeoff_remaining)
                continue
            if (edge_reference in stable or (previous.get(edge_id) == cleaned.get(edge_id)
                    and prior_kind[edge_reference] == (edge_id in cleaned_low))) and not source_changed:
                continue
            if visitor.state == "top_entering":
                top_entry.abort(self, visitor)
                continue
            arrival = visitor.arrival_state if visitor.window_target_id or visitor.route_stage else "idle"
            final_x = visitor.route_final_x if visitor.route_final_x is not None else visitor.target_x
            final_facing, motion = visitor.arrival_facing, visitor.route_motion
            if visitor.state == "window_peeking" and visitor.peek_after == "purring":
                arrival = "purring"
            # A small move of the very same supporting window is a continuous
            # step, even though the refreshed top no longer equals the old feet.
            intended_x = final_x if visitor.route_stage or visitor.window_target_id else visitor.x
            if self._step_to_shifted_window(visitor, edge_id, previous, intended_x, arrival, motion):
                visitor.arrival_facing = final_facing
                continue
            # Larger moves and genuinely lost support use the existing safe
            # jump/fall route instead of pretending a gap is walkable.
            available = self._reachable_window_edges(visitor)
            if edge_id in available:
                self._move_to_window(visitor, edge_id, final_x if visitor.route_stage or visitor.window_target_id else visitor.x,
                                     arrival=arrival, motion=motion)
                visitor.arrival_facing = final_facing
            elif available:
                nearest = min(available, key=lambda key: self._edge_distance(visitor, key))
                self._move_to_window(visitor, nearest, visitor.x, arrival=arrival, motion=motion)
                visitor.arrival_facing = final_facing
            else:
                self._walk(visitor, visitor.x, self.floor_y, arrival=arrival, arrival_facing=final_facing, motion=motion)

    def set_gift_spots(self, points: Iterable[tuple[float, float]]) -> None:
        """Set permitted icon-adjacent gift top-left positions in desktop pixels.

        These ephemeral coordinates are supplied only after the UI has checked
        user-selected icons. They are clamped for the whole 38px gift, never
        saved, and cleared on a screen change.
        """
        left, top, width, height = self.bounds
        cleaned = []
        for point in points:
            try:
                x, y = point
                if all(isinstance(n, (int, float)) and math.isfinite(n) for n in (x, y)):
                    spot = (min(left + width - GIFT_SIZE, max(left, float(x))),
                            min(top + height - GIFT_SIZE, max(top, float(y))))
                    if spot not in cleaned:
                        cleaned.append(spot)
                        if len(cleaned) >= 32:
                            break
            except (TypeError, ValueError):
                continue
        self.gift_spots = cleaned
        for visitor in self.visitors.values():
            if visitor.gift_spot is not None and visitor.gift_spot not in self.gift_spots:
                visitor.gift_spot = None
                if visitor.state == "peeking":
                    visitor.target_x = self._inside_x(left+width/2-SPRITE_WIDTH/2)
                    visitor.target_y = self.floor_y
                elif visitor.state == "entering":
                    self._walk(visitor, left+width/2-SPRITE_WIDTH/2, self.floor_y, motion="entering")
            self._plan_gift_arrival(visitor)

    def _plan_gift_arrival(self, v: Visitor) -> None:
        # Polling may reveal allowed icons after the visitor has begun entering.
        # Change only its destination: never teleport it or interrupt a pending
        # meal/jump/carry/escape that is already underway.
        if (not v.carrying_gift or not self.gift_spots or v.gift_spot is not None
                or v.state not in {"peeking", "entering"}):
            return
        v.gift_spot = self.rng.choice(self.gift_spots)
        # An icon is a route hint, never an alternative drop location. Invert
        # the shared mouth geometry for the expected approach direction; the
        # actual final facing and any screen clamping still decide the release.
        facing = 1 if v.gift_spot[0] + GIFT_SIZE/2 >= v.x + SPRITE_WIDTH/2 else -1
        mouth_x, mouth_y = mouth_anchor(facing)
        x = self._inside_x(v.gift_spot[0]+GIFT_SIZE/2-mouth_x)
        y = min(self.floor_y, max(self.bounds[1], v.gift_spot[1]-mouth_y))
        v.arrival_facing = facing
        if v.state == "peeking":
            v.target_x, v.target_y = x, y
        else:
            self._walk(v, x, y, arrival_facing=facing, motion="entering")

    def open_gate(self, opened: bool = True) -> bool:
        opened = bool(opened)
        if opened == self.gate_open:
            return False
        self.gate_open = opened
        if opened:
            # After the first meeting, every opening uses the trust cadence.
            # A fresh garden keeps its one remaining welcome wait across close/
            # reopen and restart; a missing resident retains prompt homecoming.
            self._visit_countdown = (self.rng.uniform(2, 5) if self.demo else
                                     self.rng.uniform(12, 30) if self.resident_id else
                                     self._visit_delay())
            if self._welcome_visit_remaining is not None:
                self._welcome_regular_delay = self._visit_countdown
                self._visit_countdown = min(self._visit_countdown, self._welcome_visit_remaining)
            self._emit("gate_open", None, "门开了。也许有一位邻居正路过。")
        else:
            if self._fight_pair:
                self._finish_fight()
            for v in self.visitors.values():
                if v.state != "carried":
                    self._leave(v)
            self._emit("gate_closed", None, "门关好了，访客会从边缘离开。")
        return True

    def _base_visit_weights(self) -> dict[str, float]:
        resident = self.resident_id
        return {cid: (100.0 if cid == resident else 0.2 if resident else 1.0 + c.trust / 20.0)
                for cid, c in self.cats.items() if cid not in self.visitors and not c.is_departed}

    def visit_weights(self) -> dict[str, float]:
        """Candidate weights; absent company preserves single-cat cadence."""
        weights = self._base_visit_weights()
        if self.visitors:
            for cid in weights:
                weights[cid] *= min(self.relations.co_visit_factor(cid, present)
                                    for present in self.visitors)
        return weights

    def _co_visit_chance_factor(self) -> float:
        # Scaling the opportunity as well as the candidates prevents weighted
        # normalization from turning a disliked group into equally many visits.
        base = sum(self._base_visit_weights().values())
        return sum(self.visit_weights().values()) / base if base else 0.0

    def invite(self, cat_id: str | None = None) -> str | None:
        if not self.gate_open or len(self.visitors) >= self.visitor_limit:
            return None
        weights = self.visit_weights()
        if not weights or (cat_id is not None and cat_id not in weights):
            return None
        if cat_id is None:
            cat_id = self.rng.choices(list(weights), weights=list(weights.values()), k=1)[0]
        cat = self.cats[cat_id]
        left, _, width, _ = self.bounds
        from_left = self.rng.random() < 0.5
        x = left - SPRITE_WIDTH + 18 if from_left else left + width - 18
        target_x = self._inside_x(left + width * self.rng.uniform(0.2, 0.8) - SPRITE_WIDTH / 2)
        carrying = self.rng.choice(("feather", "bottle_cap", "toy_mouse")) if cat.trust >= GIFT_TRUST and self.rng.random() < 0.3 else None
        self.visitors[cat_id] = Visitor(cat_id, x, self.floor_y, facing=1 if from_left else -1,
                                       target_x=target_x, target_y=self.floor_y,
                                       stay_remaining=(45 if self.demo else self.rng.uniform(
                                           60 + 1.8*cat.trust, 100 + 2.6*cat.trust)),
                                       carrying_gift=carrying)
        from_top = top_entry.maybe_enter(self, self.visitors[cat_id])
        self._plan_gift_arrival(self.visitors[cat_id])
        cat.visits = min(MAX_VISITS, cat.visits + 1)
        if self._welcome_visit_remaining is not None:
            # A successful manual invitation also completes the first meeting.
            # Restore the already-drawn ordinary interval, so the old welcome
            # deadline cannot create a second accelerated visitor opportunity.
            self._welcome_visit_remaining = None
            self._visit_countdown = self._welcome_regular_delay
        first_meeting = not cat.discovered
        cat.discovered = True
        neighbor_lifecycle.arrived(self, cat)
        if first_meeting:
            self._emit("discovered", cat_id, "你第一次见到了这位访客，名册里多了一张照片。")
        self._emit("arrival", cat_id, f"{cat.display_name}从上方轻轻探了过来。" if from_top else f"{cat.display_name}在门外试探。")
        return cat_id

    def can_eat(self, cat_id: str) -> bool:
        """Read-only meal eligibility; food and safe arrival are checked later."""
        cat = self.cats.get(cat_id) if isinstance(cat_id, str) else None
        return cat is not None and not cat.is_departed and cat.meal_cooldown_remaining <= 1e-9

    def feed(self) -> bool:
        if self.food >= 3:
            return False
        self.food += 1
        if self.gate_open:
            # Food attracts a visitor without turning an unfamiliar cat into
            # an almost permanent presence. Repeated portions do not stack a
            # multiplier or bypass the ordinary low-trust waiting range.
            delay = self.rng.uniform(4, 12) if self.demo or self.resident_id else self._visit_delay()
            self._visit_countdown = min(self._visit_countdown, delay)
            if self._welcome_visit_remaining is not None:
                self._welcome_regular_delay = min(self._welcome_regular_delay, delay)
        for visitor in self.visitors.values():
            if visitor.state == "window_peeking" and self.can_eat(visitor.cat_id):
                self._rise_from_peek(visitor, "eating")
            elif visitor.state == "window_rear" and self.can_eat(visitor.cat_id):
                self._choose_action(visitor)
        self._emit("food", None, "碗里多了一份小鱼干。")
        return True

    def name_cat(self, cat_id: str, name: str) -> str:
        cat = self.cats.get(cat_id)
        if cat is None or not isinstance(name, str):
            return "invalid"
        if not cat.discovered:
            return "undiscovered"
        if not cat.is_neighbor:
            return "guest"
        name = " ".join(name.split())[:24]
        if not normalized_name(name):
            return "invalid"
        cat.nickname = name
        if names_match(name, cat.true_name):
            cat.trust = 100.0
            self._emit("true_name", cat_id, "这个名字带来了熟悉的回应。")
            v = self.visitors.get(cat_id)
            if (v and v.state not in {"carried", "fighting", "fleeing", "leaving"}
                    and v.route_motion not in {"leaving", "fleeing"}
                    and not v.true_name_pending and v.true_name_remaining <= 0):
                v.true_name_pending = True
                self._deliver_true_name(v)
            return "true_name"
        if cat.recognizes_name:
            self._emit("named", cat_id, f"{self.cat_reference(cat_id)}轻轻回应了你的呼唤。")
            return "recognized"
        self._emit("named", cat_id, f"名字记下了。{self.cat_reference(cat_id)}还需要一些时间。")
        return "unfamiliar"

    def call_cat(self, cat_id: str) -> str:
        cat, v = self.cats.get(cat_id), self.visitors.get(cat_id)
        if cat is not None and not cat.discovered:
            return "undiscovered"
        if cat is not None and not cat.is_neighbor:
            return "guest"
        if cat is None or v is None:
            return "absent"
        if not cat.nickname:
            self._emit("call", cat_id, "先为这位访客起一个昵称吧。")
            return "unnamed"
        if cat.recognizes_name:
            if v.tree_trip is not None:
                self._queue_tree_action(v, "called", "called")
            if v.depth_trip is not None:
                self._queue_depth_action(v, "called", "called")
            if v.posture_remaining <= 0 and v.state in {"idle", "sleeping", "playing", "window_peeking", "window_rear"}:
                self._affection(v)
            self._emit("call", cat_id, f"{cat.nickname}的耳朵朝你转了过来。")
            return "recognized"
        self._emit("call", cat_id, f"{self.cat_reference(cat_id)}疑惑地看了一眼，又忙自己的事去了。")
        return "ignored"

    def pet(self, cat_id: str) -> bool:
        cat, v = self.cats.get(cat_id), self.visitors.get(cat_id)
        if (cat is None or v is None or v.posture_remaining > 0
                or v.state in {"carried", "fighting", "fleeing", "leaving", "peeking", "entering", "top_entering", "top_leaving", "eating", "jumping", "climbing", "teasing", "swatting"}):
            return False
        if v.tree_trip is not None:
            self.request_tree_return(cat_id, "pet")
            if cat.trust < PET_TRUST or v.pet_cooldown > 0 or v.tree_pending_action in {"leaving", "fleeing"}:
                return False
            self._queue_tree_action(v, "pet", "pet")
            return True
        if v.depth_trip is not None:
            self.request_depth_return(cat_id, "pet")
            if cat.trust < PET_TRUST or v.pet_cooldown > 0 or v.depth_pending_action in {"leaving", "fleeing"}:
                return False
            self._queue_depth_action(v, "pet", "pet")
            return True
        if scratch_on_pet(self, cat, v):
            return False
        if cat.trust < PET_TRUST:
            if v.pet_cooldown <= 0:
                self._emit("boundary", cat_id, f"{self.cat_reference(cat_id)}还不习惯触碰。先让{self.cat_reference(cat_id)}安心地待一会儿。")
                v.pet_cooldown = 4
            return False
        if v.pet_cooldown > 0:
            return False
        cat.trust = min(100.0, cat.trust + 2.0)
        v.pet_cooldown = 8.0
        self._affection(v)
        self._emit("purr", cat_id, f"呼噜……{self.cat_reference(cat_id)}慢慢眯起了眼睛。")
        return True

    def startle(self, cat_id: str) -> bool:
        cat, v = self.cats.get(cat_id), self.visitors.get(cat_id)
        if (cat is None or v is None or cat.trust >= PET_TRUST
                or v.state in {"carried", "fleeing", "leaving", "fighting"}
                or v.route_motion in {"fleeing", "leaving"}
                or v.depth_pending_action in {"fleeing", "leaving"}
                or v.tree_pending_action in {"fleeing", "leaving"}):
            return False
        cat.trust = max(0.0, cat.trust - 1.0)
        self._leave(v, fleeing=True)
        self._emit("startle", cat_id, f"动作太快了，{self.cat_reference(cat_id)}想先躲远一点。")
        return True

    def clear_cursor(self) -> None:
        """Forget a toy target outside the UI's full selected display.

        The caller owns that display geometry. Model bounds describe the work
        area, so they must not reject a feather over its menu bar or Dock.
        Existing airborne motion and the play deadline remain untouched.
        """
        self._cursor = None

    def cursor_moved(self, x: float, y: float, speed: float) -> bool:
        """Remember valid cursor samples; fast nearby motion may still startle.

        This only reads cursor coordinates. The simulation never moves or
        captures the system cursor, and slow samples also guide playful cats.
        """
        if not all(isinstance(n, (int, float)) and math.isfinite(n) for n in (x, y, speed)) or speed < 0:
            return False
        self._cursor = (float(x), float(y))
        if speed < 900:
            return False
        startled = False
        for cid, v in list(self.visitors.items()):
            if cid == self.active_teaser_id:
                continue
            if math.hypot(x - (v.x + SPRITE_WIDTH / 2), y - (v.y + SPRITE_HEIGHT / 2)) < 150:
                startled = self.startle(cid) or startled
        return startled

    def carry(self, cat_id: str) -> bool:
        v = self.visitors.get(cat_id)
        if v is not None and v.tree_trip is not None:
            self.request_tree_return(cat_id, "drag")
            return False
        if v is not None and v.depth_trip is not None:
            # The gesture may end before return. Do not schedule a stale carry
            # or grant the UI permission to drag a cat still behind scenery.
            self.request_depth_return(cat_id, "drag")
            return False
        if v is None or v.state in {"peeking", "entering", "top_entering", "top_leaving", "fighting", "carried", "leaving", "fleeing"}:
            return False
        self._state(v, "carried")
        self._emit("carry", cat_id, f"轻轻抱起，拖到边缘即可送{self.cat_reference(cat_id)}回家。")
        return True

    def release(self, cat_id: str, x: float, y: float) -> bool:
        v = self.visitors.get(cat_id)
        if v is None or v.state != "carried" or not all(isinstance(n, (int, float)) and math.isfinite(n) for n in (x, y)):
            return False
        left, top, width, _ = self.bounds
        v.x = min(left + width - 18, max(left - SPRITE_WIDTH + 18, x))
        # Tail carries originate above the work area. Releasing/cancelling
        # that actual gesture must start its descent at the held position,
        # including pause and display-rebase cleanup. Ordinary carries retain
        # their original inside-screen release constraint.
        rear_origin = v.rear_blend > 0 and v.y < top and y < top
        v.y = min(self.floor_y, y if rear_origin else max(top, y))
        if self.cats[cat_id].is_departed or not self.gate_open or x < left + 32 or x + SPRITE_WIDTH > left + width - 32:
            self._leave(v)
        else:
            self._walk(v, self._inside_x(x), self.floor_y)
        return True

    def adopt(self, cat_id: str) -> bool:
        cat = self.cats.get(cat_id)
        if cat is None or not cat.is_neighbor or not cat.discovered or cat.trust < 100 or (self.resident_id is not None and self.resident_id != cat_id) or cat.adopted:
            return False
        cat.adopted = True
        self._emit("adopted", cat_id, f"{cat.display_name}愿意把这里当作另一个家。")
        return True

    def cancel_residence(self, cat_id: str) -> bool:
        """End accepted residence without moving or abruptly dismissing a cat."""
        cat = self.cats.get(cat_id) if isinstance(cat_id, str) else None
        if cat is None or not cat.adopted:
            return False
        cat.adopted = False
        cat.trust = 50.0
        # Reuse furniture invalidation: sleeping cats wake at their current
        # position, half-finished lie-downs finish safely, airborne arcs remain.
        if self.bed is not None and self.bed.cat_id == cat_id:
            self.remove_bed()
        visitor = self.visitors.get(cat_id)
        if visitor is not None:
            # A resident's visit clock has been frozen, potentially for hours.
            # Start an ordinary trust-50 visit instead of immediately expelling
            # it or retaining a stale, unusually long pre-adoption allowance.
            visitor.stay_remaining = (45 if self.demo else
                self.rng.uniform(60 + 1.8*cat.trust, 100 + 2.6*cat.trust))
        # An absent former resident must lose the short homecoming deadline as
        # well as its privileged weight. Gate closure still forbids arrivals.
        self._visit_countdown = self._visit_delay()
        self._emit("residence_cancelled", cat_id,
                   f"你取消了{cat.display_name}的常住邀请，信任降至50。以后会作为普通访客来往。")
        return True

    def set_gift_held(self, gift_id: str, held: bool) -> bool:
        """Pause/cancel a mouse's automatic escape while it is being dragged.

        Release starts a fresh 8-22 second wait at its current position. UI
        code remains responsible for updating that position during the drag.
        Static gifts and unknown IDs are harmless no-ops. Repeated held/release
        notifications do not continually restart the countdown.
        """
        if type(held) is not bool:
            return False
        gift = next((g for g in self.gifts if g.id == gift_id), None)
        if gift is None or gift.kind != "toy_mouse":
            return False
        if held:
            if gift.state == "held":
                return False
            gift.state = "held"
            gift.escape_direction = 0
            return True
        if gift.state != "held":
            return False
        self._schedule_mouse(gift)
        return True

    def collect_gift(self, gift_id: str) -> bool:
        gift = next((g for g in self.gifts if g.id == gift_id), None)
        if gift is None:
            return False
        self.gifts.remove(gift)
        if gift.kind in self._collection_counts:
            self._collection_counts[gift.kind] = min(MAX_COLLECTION_COUNT, self._collection_counts[gift.kind] + 1)
        self._emit("cleaned" if gift.kind == "fur" else "gift_collected", gift.cat_id,
                   "猫毛收拾好了。" if gift.kind == "fur" else "礼物收好了。")
        if gift.kind in COLLECTIBLE_GIFTS and gift.cat_id in self.cats:
            cat = self.cats[gift.cat_id]
            cat.trust = min(100.0, cat.trust + 3)
            neighbor_lifecycle.promote(self, cat.id)
            v = self.visitors.get(gift.cat_id)
            if (v is not None and self.gate_open
                    and v.state not in {"carried", "fighting", "leaving", "fleeing"}
                    and v.route_motion not in {"leaving", "fleeing"}
                    and not v.true_name_pending and v.true_name_remaining <= 0
                    and v.gift_response_remaining <= 0):
                # One pending bit coalesces collections; no queue or restart.
                v.gift_response_pending = True
                self._deliver_gift_response(v)
        return True

    def discard_gift(self, gift_id: str) -> bool:
        """Remove a virtual gift without rewarding trust or touching real files."""
        gift = next((g for g in self.gifts if g.id == gift_id), None)
        if gift is None:
            return False
        self.gifts.remove(gift)
        self._emit("discarded", gift.cat_id, "桌面上的小物件已清理。")
        return True

    def _fight_candidates(self) -> tuple[Visitor, Visitor] | None:
        available = [v for v in self.visitors.values()
                     if v.state in {"idle", "walking", "running", "playing", "sleeping", "teasing", "resting"}
                     and v.window_edge_id is None and v.window_target_id is None
                     and not self.cats[v.cat_id].is_departed]
        pairs = [(a, b) for i, a in enumerate(available) for b in available[i + 1:]
                 if math.hypot(a.x-b.x, a.y-b.y) <= 160]
        return min(pairs, key=lambda pair: math.hypot(pair[0].x-pair[1].x, pair[0].y-pair[1].y)) if pairs else None

    def trigger_fight(self) -> bool:
        """Start one nearby pair's encounter; a third cat remains independent."""
        if self._fight_pair or self.calm_mode:
            return False
        pair = self._fight_candidates()
        if pair is None:
            return False
        self._fight_pair = (pair[0].cat_id, pair[1].cat_id)
        for v in pair:
            self._state(v, "fighting")
        self._emit("fight_start", pair[0].cat_id, "两位访客争起了地盘。点击烟雾可以拉架。")
        return True

    def demo_scene(self, scene: str) -> bool:
        """Stage a guided scene only in demo mode, preserving edge arrivals.

        Supported scenes: feeding, trust, gift, mouse, fight. A pending fight waits
        until both cats have walked in, then brings them together naturally.
        Demo mode should use a separate save file at the application boundary.
        """
        if not self.demo or scene not in {"feeding", "trust", "gift", "mouse", "fight"}:
            return False
        if scene == "fight" and (self.calm_mode or self.visitor_limit < 2):
            return False
        self.open_gate()
        if not self.visitors:
            self.invite("amber")
        cat_id = next(iter(self.visitors))
        if scene == "feeding":
            self.feed()
        elif scene == "trust":
            self.cats[cat_id].trust = max(75, self.cats[cat_id].trust)
            self._emit("demo", cat_id, "演示：这位访客现在信任你，可以试试抚摸。")
        elif scene in {"gift", "mouse"}:
            kind = "toy_mouse" if scene == "mouse" else "feather"
            self.cats[cat_id].trust = max(80, self.cats[cat_id].trust)
            visitor = self.visitors[cat_id]
            if visitor.state in {"fighting", "fleeing", "leaving"} or visitor.route_motion in {"leaving", "fleeing"}:
                return False
            if visitor.state == "idle":
                self._drop_from_mouth(visitor, kind)
            else:
                visitor.demo_gift_pending = kind
                self._advance_demo_gift(visitor)
        elif scene == "fight":
            if len(self.visitors) < 2:
                self.invite(next(cid for cid in self.cats if cid not in self.visitors))
            self._demo_pending = "fight"
        return True

    def _advance_demo_gift(self, v: Visitor) -> None:
        """Realize a demo request only after the full-body mouth is visible."""
        kind = v.demo_gift_pending
        if kind is None:
            return
        if v.state in {"fighting", "fleeing", "leaving"} or v.route_motion in {"leaving", "fleeing"}:
            v.demo_gift_pending = None
            return
        if v.state == "window_rear":
            self.leave_rear_perch(v)
            return
        if v.state == "window_peeking":
            self._rise_from_peek(v)
            return
        if v.state in {"carried", "eating", "grooming", "purring"} or v.true_name_pending:
            return
        if v.state == "sleeping":
            # Keep the belly-to-standing transition before exposing a gift.
            # The same wake path retains support and remains interruptible.
            begin_daily_wake(self, v)
            return
        if v.state == "playing":
            # A short real step lets the existing rise animation finish before
            # any gift appears. At the display edge step toward the interior.
            x = self._inside_x(v.x + (18 if v.x < self.bounds[0]+self.bounds[2]/2 else -18))
            self._walk(v, x, v.y)
        if v.state in {"peeking", "entering", "walking", "running", "jumping"}:
            if v.rise_remaining > 0:
                return
            v.carrying_gift = kind
            v.demo_gift_pending = None
            self._plan_gift_arrival(v)
        elif v.state == "idle":
            # A completed peek has returned to the full standing canvas. Keep
            # that pose for the release instead of drawing the seated head.
            v.settle_remaining = SETTLE_SECONDS
            v.demo_gift_pending = None
            self._drop_from_mouth(v, kind)

    def intervene(self, cat_id: str) -> bool:
        if not self._fight_pair or cat_id not in self._fight_pair:
            return False
        self.cats[cat_id].trust = min(100.0, self.cats[cat_id].trust + 4)
        self._emit("intervene", cat_id, f"你护住了{self.cat_reference(cat_id)}。两位访客各自跑回了家。")
        self._finish_fight()
        return True

    def tick(self, dt: float) -> None:
        if not isinstance(dt, (int, float)) or not math.isfinite(dt) or dt < 0:
            raise ValueError("Elapsed time must be a finite non-negative number")
        remaining = min(float(dt), 5.0)
        while remaining > 1e-9:
            step = min(0.25, remaining)
            self._tick_step(step)
            remaining -= step

    def drain_events(self) -> list[dict[str, Any]]:
        events, self._events = self._events, []
        return events

    def export(self) -> dict[str, Any]:
        return {
            "version": STATE_VERSION,
            "bounds": list(self.bounds),
            "calm_mode": self.calm_mode,
            "cats": {cid: {"trust": c.trust, "nickname": c.nickname, "adopted": c.adopted, "visits": c.visits, "discovered": c.discovered,
                           "meal_cooldown_remaining": c.meal_cooldown_remaining,
                           **c.identity_record(), "lifecycle": neighbor_lifecycle.record(c)}
                     for cid, c in self.cats.items()},
            "cat_relations": self.relations.export(),
            "neighbor_lifecycle": neighbor_lifecycle.export(self),
            "food": self.food,
            "bowl": list(self.bowl),
            "resident_bed": {"cat_id": self.bed.cat_id, "x": self.bed.x} if self.bed else None,
            "gifts": [{"id": g.id, "kind": g.kind, "x": g.x, "y": g.y, "cat_id": g.cat_id, "facing": g.facing} for g in self.gifts],
            "next_gift_id": self._next_gift_id,
            "gift_collection": self.collection_counts,
            "welcome_visit_remaining": self._welcome_visit_remaining,
        }

    def _restore(self, state: dict[str, Any] | None) -> None:
        if not isinstance(state, dict):
            return
        # Versionless saves are accepted as v0; newer versions are never guessed.
        version = state.get("version", 0)
        if type(version) is not int or not 0 <= version <= STATE_VERSION:
            return
        self.relations.restore(state.get("cat_relations"))
        restored_bounds = state.get("bounds")
        if (isinstance(restored_bounds, (list, tuple)) and len(restored_bounds) == 4
                and all(type(n) in (int, float) and math.isfinite(n) and abs(n) <= 100_000 for n in restored_bounds)
                and restored_bounds[2] >= SPRITE_WIDTH and restored_bounds[3] >= SPRITE_HEIGHT):
            self.bounds = tuple(float(n) for n in restored_bounds)
        self.calm_mode = state.get("calm_mode") is True
        records = state.get("cats", {})
        if isinstance(records, list):  # Early prototypes used an array of profiles.
            records = {r.get("id"): r for r in records if isinstance(r, dict) and isinstance(r.get("id"), str)}
        adopted = False
        if isinstance(records, dict):
            for cid, cat in self.cats.items():
                row = records.get(cid, {})
                if not isinstance(row, dict):
                    continue
                cat.trust = _number(row.get("trust", row.get("trust_level", 0)), 0, 0, 100)
                cooldown = row.get("meal_cooldown_remaining", 0)
                if (type(cooldown) in (int, float) and cooldown >= 0
                        and (type(cooldown) is int or math.isfinite(cooldown))):
                    cat.meal_cooldown_remaining = float(min(MEAL_COOLDOWN_SECONDS, cooldown))
                nickname = row.get("nickname", "")
                cat.nickname = " ".join(nickname.split())[:24] if isinstance(nickname, str) else ""
                visits = row.get("visits", 0)
                cat.visits = min(MAX_VISITS, visits) if type(visits) is int and visits >= 0 else 0
                # Pre-discovery saves already tracked visits. Nicknames, trust,
                # and adopted flags alone are not evidence of an actual visit.
                cat.discovered = row.get("discovered") is True or cat.visits > 0
                cat.adopted = cat.discovered and row.get("adopted") is True and cat.trust >= 100 and not adopted
                adopted = adopted or cat.adopted
        if any(cat.discovered for cat in self.cats.values()) or self.demo:
            self._welcome_visit_remaining = None
        elif "welcome_visit_remaining" in state:
            remaining = state["welcome_visit_remaining"]
            if remaining is None:
                self._welcome_visit_remaining = None
            elif (type(remaining) in (int, float) and 0 <= remaining <= WELCOME_VISIT_DELAY
                  and math.isfinite(remaining)):
                self._welcome_visit_remaining = float(remaining)
            # Invalid values retain the safe fresh-garden default. Legacy saves
            # without a recorded meeting also get their first welcome once.
        bed_model.restore_bed(self, state.get("resident_bed"))
        collection = state.get("gift_collection", {})
        if isinstance(collection, dict):
            for kind in COLLECTIBLE_GIFTS:
                count = collection.get(kind, 0)
                self._collection_counts[kind] = min(MAX_COLLECTION_COUNT, count) if type(count) is int and count >= 0 else 0
        self.food = int(_number(state.get("food", 0), 0, 0, 3))
        bowl = state.get("bowl")
        if isinstance(bowl, (list, tuple)) and len(bowl) == 2:
            self.set_bowl(bowl[0], bowl[1])
        rows = state.get("gifts", [])
        seen = set()
        if isinstance(rows, list):
            for row in rows[:MAX_GIFTS]:
                if not isinstance(row, dict):
                    continue
                gift_id, kind, cat_id = row.get("id"), row.get("kind"), row.get("cat_id")
                if not isinstance(gift_id, str) or not gift_id or len(gift_id) > 64 or gift_id in seen:
                    continue
                if not isinstance(kind, str) or not isinstance(cat_id, str):
                    continue
                if kind not in {"feather", "bottle_cap", "toy_mouse", "fur"} or cat_id not in self.cats:
                    continue
                seen.add(gift_id)
                gift = Gift(gift_id, kind, _number(row.get("x"), 0, -100_000, 100_000),
                            _number(row.get("y"), 0, -100_000, 100_000), cat_id)
                facing = row.get("facing", -1)
                gift.facing = facing if type(facing) is int and facing in (-1, 1) else -1
                if kind == "toy_mouse":
                    left, top, width, height = self.bounds
                    # An old save (or a save made during escape) must restart
                    # visibly inside the screen and wait, never resume mid-run.
                    gift.x = min(left + width - GIFT_SIZE, max(left, gift.x))
                    gift.y = min(top + height - GIFT_SIZE, max(top, gift.y))
                    self._schedule_mouse(gift)
                self.gifts.append(gift)
        self._next_gift_id = int(_number(state.get("next_gift_id"), 1, 1, 1_000_000_000))
        while f"gift-{self._next_gift_id}" in seen:
            self._next_gift_id += 1

    def _visit_delay(self) -> float:
        if self.demo:
            return self.rng.uniform(8, 16)
        if self.resident_id:
            return self.rng.uniform(35, 70)
        # The whole neighbourhood determines how often someone drops by;
        # the selected cat's own trust determines how long it stays. Counting
        # undiscovered neighbours at their saved/default trust keeps a fresh
        # garden quiet, rather than promoting it after one friendly encounter.
        # A newly generated waiting guest must not dilute the established
        # neighbourhood cadence. The original candidate pool still counts at
        # its saved/default trust; a dynamic cat joins this mean on promotion.
        eligible = [cat for cat in self.cats.values() if not cat.is_departed
                    and (cat.is_neighbor or cat.id in {"amber", "mist", "ink", "onyx", *GENERATED_CAT_IDS})]
        trust = sum(cat.trust for cat in eligible) / len(eligible) if eligible else 0
        return self.rng.uniform(240 - 2.3*trust, 360 - 3.4*trust) * (0.65 if self.food else 1)

    def _inside_x(self, x: float) -> float:
        left, _, width, _ = self.bounds
        return max(left, min(left + width - SPRITE_WIDTH, x))

    def _emit(self, kind: str, cat_id: str | None, message: str) -> None:
        if cat_id is not None:
            neighbor_lifecycle.promote(self, cat_id)
        self._events.append({"kind": kind, "cat_id": cat_id, "message": message})
        if len(self._events) > 200:
            del self._events[:-200]

    def _clear_peek(self, v: Visitor) -> None:
        v.peek_progress = 0.0
        v.peek_phase = ""
        v.clip_y = None
        v.peek_elapsed = 0.0
        v.peek_start = 0.0
        v.peek_after = "idle"

    def _clear_window(self, v: Visitor) -> None:
        v.window_edge_id = v.window_target_id = None
        self._clear_peek(v)

    def _clear_route(self, v: Visitor) -> None:
        climb_routes.clear(v)
        v.route_final_x = v.route_final_y = None
        v.route_edge_id = None
        v.route_stage = ""
        v.route_motion = "walking"

    def _clear_posture(self, v: Visitor) -> None:
        v.posture_kind = v.posture_source = v.posture_target = ""
        v.posture_remaining = v.posture_duration = 0.0

    def _begin_posture(self, v: Visitor, kind: str, duration: float,
                       source: str, target: str) -> bool:
        if v.depth_trip is not None or v.tree_trip is not None:
            return False
        if v.state in {"jumping", "climbing", "carried", "fighting", "leaving", "fleeing"}:
            return False
        v.posture_kind, v.posture_source, v.posture_target = kind, source, target
        v.posture_remaining = v.posture_duration = duration
        v.ground_speed = v.turn_remaining = 0.0
        v.turn_target_facing = None
        return True

    def _state(self, v: Visitor, state: str) -> None:
        if v.tree_trip is not None and state != "tree_visiting":
            if state in {"fleeing", "leaving"}:
                self._queue_tree_action(v, state, state)
            else:
                self.request_tree_return(v.cat_id, "called")
            return
        if v.depth_trip is not None and state != "depth_visiting":
            if state in {"fleeing", "leaving"}:
                self._queue_depth_action(v, state, state)
            else:
                self.request_depth_return(v.cat_id, "interrupted")
            return
        if state == "idle" and self._on_rear_support(v):
            state = "window_rear"
        elif state in {"sleeping", "purring", "resting", "playing"} and self._on_rear_support(v):
            self.leave_rear_perch(v, arrival=state if state in {"purring", "resting"} else "idle")
            return
        previous = v.state
        if ((previous == "eating" and state != "eating" and v.cat_id in self._meal_tokens)
                or state in {"carried", "fighting", "leaving", "fleeing", "swatting"}):
            self._cancel_shared_meal(v.cat_id)
        if state not in {"sleeping", "purring"}:
            v.bed_nap = False
        if state not in {"walking", "running", "jumping", "entering", "bed_laying"}:
            v.bed_route_revision = None
        was_sleeping_purr = v.sleeping_purr
        if state == "purring" and v.on_back:
            v.sleeping_purr_age = (v.sleeping_purr_age if was_sleeping_purr else 0.0) + v.state_age
            v.sleeping_purr = True
        else:
            v.sleeping_purr = False
            v.sleeping_purr_age = 0.0
        self._clear_posture(v)
        if state not in {"walking", "running", "jumping", "entering"}:
            v.meal_bowl = None
        if state in {"carried", "fighting", "leaving", "fleeing", "swatting"}:
            cancel_teaser(self, v.cat_id)
        if state in {"carried", "fighting", "leaving", "fleeing", "playing", "purring", "eating", "sleeping", "teasing", "swatting", "resting", "window_rear"}:
            self.cancel_parkour(v)
        if state in {"carried", "fighting", "purring", "sleeping", "idle", "window_peeking", "window_rear", "playing", "teasing", "swatting", "resting"}:
            self._clear_route(v)
        if state in {"walking", "running", "playing", "teasing"}:
            if previous == "idle":
                # Reverse only the seated portion already displayed. During
                # landing, all four feet are still planted in the standing pose.
                seated = 0.0 if v.landing_remaining > 0 else 1-min(1.0, max(0.0, v.settle_remaining/SETTLE_SECONDS))
                v.rise_remaining = RISE_SECONDS * seated
                v.ground_speed = 0.0
            elif previous not in {"walking", "running", "playing", "teasing"}:
                v.rise_remaining = 0.0
        else:
            v.rise_remaining = 0.0
        if state == "carried" and v.carrying_gift:
            v.demo_gift_pending = v.carrying_gift
            v.carrying_gift = None
            v.gift_spot = None
        if state in {"fighting", "fleeing", "leaving"}:
            v.demo_gift_pending = None
        if state in {"carried", "fighting", "fleeing", "leaving"}:
            v.gift_response_pending = False
            v.gift_response_remaining = 0.0
        elif state != "purring":
            # Support changes may interrupt an already visible response. Do
            # not replay it from three seconds every time a window moves.
            v.gift_response_remaining = 0.0
        if state in {"carried", "fighting", "fleeing", "leaving"}:
            v.true_name_pending = False
            v.true_name_remaining = 0.0
        elif state != "purring" and v.true_name_remaining > 0:
            # Standing recognition may wait for safe support. A belly response
            # interrupted by movement has ended and must not restart seated.
            v.true_name_pending = not was_sleeping_purr
            v.true_name_remaining = 0.0
        v.state, v.state_age = state, 0.0
        if state != "jumping":
            v.jump_vy = v.jump_pose_phase = v.jump_vertical_span = 0.0
            v.takeoff_remaining = v.jump_elapsed = 0.0
            v.takeoff_edge_id = None
        if state != "idle":
            v.settle_remaining = 0.0
        if state not in GROUND_STATES:
            v.ground_speed = 0.0
            v.turn_remaining = 0.0
            v.turn_target_facing = None
        if state in {"carried", "jumping", "fighting"}:
            v.landing_remaining = 0.0
        if state in {"leaving", "fleeing"}:
            # Closing the gate and being startled take priority over a turn.
            v.turn_remaining = 0.0
            v.turn_target_facing = None
        if state != "window_peeking":
            self._clear_peek(v)
        if state in {"carried", "fighting", "playing"}:
            self._clear_window(v)
        if state == "playing":
            v.target_x, v.target_y = v.x, v.y
            v.play_retarget_in = 0.0

    def _ground_step(self, v: Visitor, dt: float, max_speed: float,
                     stop_distance: float = 0.0) -> bool:
        """Advance a grounded route without overshoot, preserving limb phase.

        Briefly plant the feet before reversing, then accelerate within the
        original state's speed limit. Braking is limited by remaining route
        length. Airborne arcs, manual carrying and monitor rebases do not call
        this method and cannot contribute to gait_phase.
        """
        if v.posture_remaining > 0:
            return False
        dx, dy = v.target_x - v.x, v.target_y - v.y
        distance = math.hypot(dx, dy)
        remaining = max(0.0, distance - stop_distance)
        if remaining <= 1e-9:
            v.ground_speed = 0.0
            v.turn_remaining = 0.0
            v.turn_target_facing = None
            return True
        if v.rise_remaining > 0:
            rising = min(dt, v.rise_remaining)
            v.rise_remaining = max(0.0, v.rise_remaining-rising)
            v.ground_speed = 0.0
            dt -= rising
            if dt <= 1e-9:
                return False
        facing = (1 if dx > 0 else -1) if abs(dx) >= 1 else v.facing
        if facing != v.facing and v.state not in {"leaving", "fleeing"}:
            if v.turn_target_facing != facing:
                v.turn_target_facing = facing
                v.turn_remaining = TURN_SECONDS
                v.ground_speed = 0.0
            paused = min(dt, v.turn_remaining)
            v.turn_remaining = max(0.0, v.turn_remaining - paused)
            dt -= paused
            if v.turn_remaining > 1e-9:
                return False
            v.facing = facing
            v.turn_target_facing = None
        else:
            # A new target may cancel a turn before the feet start moving.
            v.turn_remaining = 0.0
            v.turn_target_facing = None
            v.facing = facing
        if dt <= 1e-9:
            return False
        target_speed = min(max_speed, math.sqrt(2 * GROUND_DECELERATION * remaining))
        before = min(max_speed, max(0.0, v.ground_speed))
        rate = (780.0 if v.state == "fleeing" else GROUND_ACCELERATION) if target_speed >= before else GROUND_DECELERATION
        ramp_time = min(dt, abs(target_speed - before) / rate)
        after = before + math.copysign(rate * ramp_time, target_speed - before)
        # Integrate the bounded ramp and any remaining cruising time. Counting
        # the actual final displacement also handles truncated arrival steps.
        step = min(remaining, (before + after) * ramp_time / 2 + after * (dt - ramp_time))
        old_x, old_y = v.x, v.y
        v.x += dx / distance * step
        v.y += dy / distance * step
        arrived = step >= remaining - 1e-9
        if arrived and stop_distance == 0:
            v.x, v.y = v.target_x, v.target_y
        traveled = math.hypot(v.x - old_x, v.y - old_y)
        if v.state in {"running", "fleeing", "teasing"}:
            v.run_phase = (v.run_phase+traveled/RUN_STRIDE) % 1.0
        else:
            v.gait_phase = (v.gait_phase+traveled/GAIT_STRIDE) % 1.0
        v.ground_speed = 0.0 if arrived else after
        return arrived

    def _takeoff_support(self, v: Visitor) -> tuple[bool, str | None]:
        """Only a real, still-present supporting surface permits crouching."""
        if v.state == "carried" or (v.state == "jumping" and v.takeoff_remaining <= 0):
            return False, None
        edge_id = v.takeoff_edge_id if v.takeoff_remaining > 0 else v.window_edge_id
        if edge_id is not None:
            edge = self.window_edges.get(edge_id)
            supported = (edge is not None and edge[0] <= v.x <= edge[1]-SPRITE_WIDTH
                         and abs(v.y-(edge[2]-SPRITE_HEIGHT)) <= 0.5)
            return (True, edge_id) if supported else (False, None)
        left, _, width, _ = self.bounds
        return (left <= v.x <= left+width-SPRITE_WIDTH and abs(v.y-self.floor_y) <= 0.5), None

    def _begin_jump(self, v: Visitor, *, support: tuple[bool, str | None] | None = None,
                    takeoff_remaining: float | None = None) -> None:
        support = self._takeoff_support(v) if support is None else support
        if (v.route_stage == "climb_attach" and abs(v.target_y-v.y) < SHALLOW_MOVE_HEIGHT
                and v.climb_side_id in self.window_climb_sides):
            side = self.window_climb_sides[v.climb_side_id]
            self._clear_window(v)
            v.target_x, v.target_y = side.canvas_x, side.stop_y
            v.route_stage = "climb"
            self._state(v, "climbing")
            v.facing = side.facing
            v.landing_remaining = 0.0
            return
        v.jump_start_x, v.jump_start_y = v.x, v.y
        v.jump_arc = 64.0
        v.jump_duration = max(0.5, min(3.5, math.hypot(v.target_x - v.x, v.target_y - v.y) / 190))
        self._state(v, "jumping")
        v.jump_elapsed = 0.0
        v.takeoff_remaining = (TAKEOFF_SECONDS if takeoff_remaining is None else takeoff_remaining) if support[0] else 0.0
        v.takeoff_edge_id = support[1] if v.takeoff_remaining > 0 else None
        if abs(v.target_x-v.x) >= 1:
            v.facing = 1 if v.target_x > v.x else -1
        self._update_jump_pose(v, 0.0)

    @staticmethod
    def _update_jump_pose(v: Visitor, progress: float, actual_vy: float | None = None) -> None:
        """Describe the existing arc; artwork never changes its flight speed."""
        dy, arc = v.target_y-v.jump_start_y, max(0.0, v.jump_arc)
        apex = max(0.0, min(1.0, (4*arc-dy)/(8*arc))) if arc else (0.0 if dy >= 0 else 1.0)
        peak_y = v.jump_start_y + dy*apex - arc*4*apex*(1-apex)
        v.jump_vertical_span = max(abs(v.jump_start_y-peak_y), abs(v.target_y-peak_y))
        derivative = (dy - 4*arc + 8*arc*progress) / max(v.jump_duration, 1e-9)
        v.jump_vy = derivative if actual_vy is None else actual_vy
        if progress <= apex and apex > 1e-9:
            phase = progress/apex
        else:
            phase = (progress-apex)/max(1-apex, 1e-9)
        v.jump_pose_phase = min(1.0, max(0.0, phase))

    def _source_interval(self, v: Visitor) -> tuple[float, float] | None:
        supported, edge_id = self._takeoff_support(v)
        if not supported:
            return None
        if edge_id:
            left, right, _ = self.window_edges[edge_id]
            return left, right-SPRITE_WIDTH
        left, _, width, _ = self.bounds
        return left, left+width-SPRITE_WIDTH

    def _can_walk_to_window(self, v: Visitor, edge_id: str) -> bool:
        """A same-height handoff is grounded only on the target's full width.

        A newly revealed top can replace a vanished window directly under the
        feet. A gap or an already airborne visitor must not become an air walk.
        """
        edge = self.window_edges.get(edge_id)
        if (edge is None or v.state in {"carried", "climbing"}
                or (v.state == "jumping" and v.takeoff_remaining <= 0)):
            return False
        return (abs(edge[2]-SPRITE_HEIGHT-v.y) <= 1e-7
                and edge[0] <= v.x <= edge[1]-SPRITE_WIDTH)

    def _window_launch_x(self, v: Visitor, edge_id: str) -> float | None:
        """Find a supported launch location at most one short hop from an edge."""
        left, right, _ = self.window_edges[edge_id]
        right -= SPRITE_WIDTH
        if left-WINDOW_JUMP_X <= v.x <= right+WINDOW_JUMP_X:
            return v.x
        source = self._source_interval(v)
        if source is None:
            return None
        low, high = max(source[0], left-WINDOW_JUMP_X), min(source[1], right+WINDOW_JUMP_X)
        return min(high, max(low, v.x)) if low <= high else None

    def _step_to_shifted_window(self, v: Visitor, edge_id: str, previous, final_x, arrival, motion) -> bool:
        old, new = previous.get(edge_id), self.window_edges.get(edge_id)
        if (edge_id != v.window_edge_id or old is None or new is None
                or edge_id in self.low_headroom_edges
                or v.state not in {"idle", "walking", "running", "sleeping", "purring", "resting", "window_peeking"}
                or motion in {"leaving", "fleeing"}
                or not 0 < abs(new[2]-old[2]) < SHALLOW_MOVE_HEIGHT
                or abs(v.y-(old[2]-SPRITE_HEIGHT)) > .5
                or not old[0] <= v.x <= old[1]-SPRITE_WIDTH
                or not new[0] <= v.x <= new[1]-SPRITE_WIDTH):
            return False
        self._clear_route(v)
        self._clear_peek(v)
        v.arrival_state, v.arrival_facing = arrival, None
        v.route_edge_id, v.route_motion = edge_id, motion
        v.route_final_x = min(new[1]-SPRITE_WIDTH, max(new[0], final_x))
        v.route_final_y = new[2]-SPRITE_HEIGHT
        v.target_x, v.target_y = v.x, v.route_final_y
        v.window_target_id = edge_id
        v.route_stage = "step"
        self._state(v, motion)
        return True

    def _short_step_launch_x(self, v: Visitor, edge_id: str) -> float | None:
        """A small step needs a body-wide overlap, never an airborne gap."""
        source = self._source_interval(v)
        edge = self.window_edges.get(edge_id)
        if source is None or edge is None:
            return None
        left, right = max(source[0], edge[0]), min(source[1], edge[1]-SPRITE_WIDTH)
        return min(right, max(left, v.x)) if left <= right else None

    def _start_route_jump(self, v: Visitor, *, support: tuple[bool, str | None] | None = None,
                          remaining: float | None = None, landing_x: float | None = None) -> None:
        support = self._takeoff_support(v) if support is None else support
        final_x, final_y = v.route_final_x, v.route_final_y
        assert final_x is not None and final_y is not None
        airborne = v.state == "jumping" and v.takeoff_remaining <= 0
        if (0 < abs(final_y-v.y) < SHALLOW_MOVE_HEIGHT and not airborne
                and v.state != "climbing"):
            if v.route_edge_id in self.window_edges:
                step_x = self._short_step_launch_x(v, v.route_edge_id)
                step_safe = step_x is not None and abs(step_x-v.x) <= 1e-7
            else:
                step_safe = support[0] and (support[1] is None or abs(final_y-self.floor_y) <= 1e-7)
            if step_safe:
                v.target_x, v.target_y = v.x, final_y
                v.window_target_id = v.route_edge_id
                v.route_stage = "step"
                self._state(v, v.route_motion)
                return
        landing_x = min(v.x+WINDOW_JUMP_X, max(v.x-WINDOW_JUMP_X,
                        final_x if landing_x is None else landing_x))
        if v.route_edge_id in self.window_edges:
            left, right, _ = self.window_edges[v.route_edge_id]
            landing_x = min(right-SPRITE_WIDTH, max(left, landing_x))
        self._clear_window(v)
        v.target_x, v.target_y = landing_x, final_y
        v.window_target_id = v.route_edge_id
        v.route_stage = "jump"
        height = v.y-final_y
        self._begin_jump(v, support=support, takeoff_remaining=remaining)
        if support == (True, None) and height > WINDOW_JUMP_Y:
            v.jump_duration = max(2.0, math.hypot(landing_x-v.x, height)/WINDOW_FLOOR_LAUNCH_SPEED)
            v.jump_arc = min(160.0, height*.18)
            self._update_jump_pose(v, 0.0)

    def _walk(self, v: Visitor, x: float, y: float, arrival: str = "idle",
              arrival_facing: int | None = None, *, motion: str = "walking", allow_shallow: bool = True) -> None:
        if v.tree_trip is not None:
            if motion in {"fleeing", "leaving"}:
                self._queue_tree_action(v, motion, motion)
            else:
                self.request_tree_return(v.cat_id, "called")
            return
        if v.depth_trip is not None:
            if motion in {"fleeing", "leaving"}:
                self._queue_depth_action(v, motion, motion)
            else:
                self.request_depth_return(v.cat_id, "interrupted")
            return
        support = self._takeoff_support(v)
        remaining = v.takeoff_remaining if v.takeoff_remaining > 0 else None
        shallow_foreground = (allow_shallow and v.window_edge_id is None and v.window_target_id is None
                              and v.climb_side_id is None and v.state not in
                              {"jumping", "climbing", "carried", "peeking", "entering", "top_entering", "top_leaving"})
        self._clear_route(v)
        v.bed_route_revision = None
        v.arrival_state, v.arrival_facing = arrival, arrival_facing
        x = x if motion in {"leaving", "fleeing"} else self._inside_x(x)
        y = min(self.floor_y, max(self.bounds[1], y))
        if 0 < abs(y-v.y) < SHALLOW_MOVE_HEIGHT and shallow_foreground:
            # Front-layer movement is at scale 1. Depth-scaled travel has its
            # own continuous route and returned above without entering here.
            self._clear_peek(v)
            v.target_x, v.target_y = x, y
            self._state(v, motion)
            return
        if abs(y-v.y) > 1e-7:
            v.route_final_x, v.route_final_y = x, y
            v.route_motion = motion
            inside = self._inside_x(v.x)
            if v.state in {"peeking", "entering"} and abs(inside-v.x) > 1e-7:
                # Enter on the desktop floor before climbing toward an icon.
                v.target_x, v.target_y = inside, v.y
                v.route_stage = "approach"
                self._state(v, "entering")
            else:
                self._start_route_jump(v, support=support, remaining=remaining)
        else:
            # Walking on a native edge retains that actual support.
            if v.window_edge_id:
                edge = self.window_edges.get(v.window_edge_id)
                if not edge or not edge[0] <= x <= edge[1]-SPRITE_WIDTH:
                    self._clear_window(v)
                    self._walk(v, x, self.floor_y, arrival, arrival_facing, motion=motion)
                    return
            self._clear_peek(v)
            v.window_target_id = None
            v.target_x, v.target_y = x, y
            self._state(v, motion)

    def _edge_distance(self, v: Visitor, edge_id: str) -> float:
        left, right, top = self.window_edges[edge_id]
        x = min(right-SPRITE_WIDTH, max(left, v.x))
        return math.hypot(x-v.x, top-SPRITE_HEIGHT-v.y)

    def _can_launch_from_floor(self, v: Visitor) -> bool:
        return (v.window_edge_id is None
                and (v.window_target_id is None or (v.takeoff_remaining > 0 and v.takeoff_edge_id is None))
                and abs(v.y-self.floor_y) <= 1.0)

    def _reachable_window_edges(self, v: Visitor) -> list[str]:
        nearby, initial_climbs = [], []
        floor_launch = self._can_launch_from_floor(v)
        for edge_id, (_, _, top) in self.window_edges.items():
            if abs(top-SPRITE_HEIGHT-v.y) <= 1e-7:
                if self._can_walk_to_window(v, edge_id):
                    nearby.append(edge_id)
                continue
            if (abs(top-SPRITE_HEIGHT-v.y) < SHALLOW_MOVE_HEIGHT
                    and not (v.state == "jumping" and v.takeoff_remaining <= 0)):
                if self._short_step_launch_x(v, edge_id) is not None:
                    nearby.append(edge_id)
                continue
            if (top-SPRITE_HEIGHT < v.y-WINDOW_JUMP_Y
                    and climb_routes.candidate(self, v, edge_id) is not None):
                initial_climbs.append(edge_id)
                continue
            if floor_launch and edge_id in self.low_headroom_edges:
                continue  # Upper arrivals are not giant jumps from the floor.
            if self._window_launch_x(v, edge_id) is None:
                continue
            height = top-SPRITE_HEIGHT-v.y
            if abs(height) <= WINDOW_JUMP_Y:
                nearby.append(edge_id)
            elif floor_launch and height < 0:
                initial_climbs.append(edge_id)
        return nearby or initial_climbs

    def _move_to_window(self, v: Visitor, edge_id: str, x: float | None = None,
                        arrival: str = "idle", *, motion: str = "walking") -> bool:
        edge = self.window_edges.get(edge_id)
        if edge is None or (edge_id in self.low_headroom_edges and self._can_launch_from_floor(v)):
            return False
        launch_x = self._window_launch_x(v, edge_id)
        height = edge[2]-SPRITE_HEIGHT-v.y
        if (1e-7 < abs(height) < SHALLOW_MOVE_HEIGHT
                and not (v.state == "jumping" and v.takeoff_remaining <= 0)):
            launch_x = self._short_step_launch_x(v, edge_id)
            if launch_x is None:
                return False
        if abs(height) <= 1e-7 and not self._can_walk_to_window(v, edge_id):
            return False  # No ordinary same-height hop, nor walking across air.
        side = climb_routes.candidate(self, v, edge_id) if height < -WINDOW_JUMP_Y else None
        if side is not None:
            final_x = min(edge[1]-SPRITE_WIDTH, max(edge[0], v.x if x is None else x))
            climb_routes.begin(self, v, side, final_x, arrival, motion)
            v.next_action = self.rng.uniform(3, 6) if self.demo else self.rng.uniform(5, 11)
            return True
        if launch_x is None or (abs(height)>WINDOW_JUMP_Y and not (self._can_launch_from_floor(v) and height<0)):
            return False
        support = self._takeoff_support(v)
        remaining = v.takeoff_remaining if v.takeoff_remaining>0 else None
        final_x = min(edge[1]-SPRITE_WIDTH, max(edge[0], v.x if x is None else x))
        if edge_id in self.low_headroom_edges:
            # Land where the short hop can actually reach. Do not perform a
            # long side-on finish walk with the whole body above the display.
            final_x = min(edge[1]-SPRITE_WIDTH, max(edge[0],
                          min(launch_x+WINDOW_JUMP_X, max(launch_x-WINDOW_JUMP_X, final_x))))
            if abs(height) <= 1e-7:
                v.window_edge_id = edge_id
                self._begin_window_rear(v)
                return True
        if abs(height) <= 1e-7:
            v.window_edge_id = edge_id
            self._walk(v, final_x, v.y, arrival, motion=motion)
        else:
            self._clear_route(v)
            self._clear_peek(v)
            v.arrival_state, v.arrival_facing = arrival, None
            v.route_final_x, v.route_final_y = final_x, edge[2]-SPRITE_HEIGHT
            v.route_edge_id, v.route_motion = edge_id, motion
            if abs(launch_x-v.x)>1e-7:
                v.target_x, v.target_y = launch_x, v.y
                v.route_stage = "approach"
                v.window_target_id = None
                self._state(v, motion)
            else:
                self._start_route_jump(v, support=support, remaining=remaining)
        v.next_action = self.rng.uniform(3, 6) if self.demo else self.rng.uniform(5, 11)
        return True

    def _continue_route(self, v: Visitor) -> bool:
        """Return true when arrival is an intermediate route milestone."""
        if climb_routes.advance_waypoint(self, v):
            return True
        if v.route_stage == "approach":
            self._start_route_jump(v)
            return True
        if v.route_stage in {"jump", "step"}:
            if v.window_target_id in self.window_edges:
                v.window_edge_id = v.window_target_id
            elif v.route_stage == "step":
                self._clear_window(v)
            v.window_target_id = None
            final_x = v.route_final_x
            if final_x is not None and abs(final_x-v.x)>1e-7:
                v.target_x, v.target_y = final_x, v.y
                v.route_stage = "finish"
                self._state(v, v.route_motion)
                return True
            # A purely vertical exit still needs a horizontal edge departure.
        self._clear_route(v)
        return False

    def _on_rear_support(self, v: Visitor) -> bool:
        edge = self.window_edges.get(v.window_edge_id)
        return bool(v.window_edge_id in self.low_headroom_edges and edge is not None
                    and edge[0] <= v.x <= edge[1]-SPRITE_WIDTH
                    and abs(v.y-(edge[2]-SPRITE_HEIGHT)) <= .5)

    def _begin_window_rear(self, v: Visitor) -> bool:
        if not self._on_rear_support(v):
            return False
        self._state(v, "window_rear")
        v.next_action = self.rng.uniform(6, 12)
        return True

    def leave_rear_perch(self, v: Visitor, *, arrival: str = "idle") -> None:
        """Reuse the continuous descent, retaining the actual offscreen origin."""
        self._walk(v, v.x, self.floor_y, arrival=arrival)

    def _begin_window_peek(self, v: Visitor) -> bool:
        if self._on_rear_support(v):
            return self._begin_window_rear(v)
        edge = self.window_edges.get(v.window_edge_id)
        if edge is None or abs(v.y - (edge[2] - SPRITE_HEIGHT)) > 0.01:
            return False
        # Near the bottom of the screen a full 78px lowering would clip the
        # sprite offscreen. Such low edges still support sitting and walking.
        if self.floor_y - v.y < WINDOW_PEEK_DEPTH:
            return False
        self._state(v, "window_peeking")
        v.peek_phase = "lowering"
        v.peek_progress = v.peek_elapsed = 0.0
        v.peek_hold = self.rng.uniform(2.0, 4.0)
        v.peek_after = "idle"
        v.clip_y = edge[2]
        self._emit("window_peek", v.cat_id, f"{self.cat_reference(v.cat_id)}趴在窗沿，悄悄探出头来。")
        return True

    def _rise_from_peek(self, v: Visitor, after: str = "idle") -> None:
        if v.state != "window_peeking":
            return
        # Feeding takes priority over an affectionate response queued earlier.
        if v.peek_after != "eating" or after == "eating":
            v.peek_after = after
        if v.peek_phase != "rising":
            v.peek_start = v.peek_progress
            v.peek_elapsed = 0.0
            v.peek_phase = "rising"

    def _peek_step(self, v: Visitor, dt: float) -> None:
        edge = self.window_edges.get(v.window_edge_id)
        if edge is None:
            self._walk(v, v.x, self.floor_y)
            return
        if self.food > 0 and self.can_eat(v.cat_id):
            self._rise_from_peek(v, "eating")
        v.peek_elapsed += dt
        if v.peek_phase == "lowering":
            t = min(1.0, v.peek_elapsed / WINDOW_PEEK_SECONDS)
            v.peek_progress = t * t * (3 - 2 * t)
            if t >= 1:
                v.peek_phase, v.peek_elapsed = "holding", 0.0
        elif v.peek_phase == "holding":
            v.peek_progress = 1.0
            if v.peek_elapsed >= v.peek_hold:
                self._rise_from_peek(v)
        elif v.peek_phase == "rising":
            duration = WINDOW_PEEK_SECONDS * max(0.2, v.peek_start)
            t = min(1.0, v.peek_elapsed / duration)
            v.peek_progress = v.peek_start * (1 - t * t * (3 - 2 * t))
            if t >= 1:
                after = v.peek_after
                v.y = edge[2] - SPRITE_HEIGHT
                self._state(v, "purring" if after == "purring" else "idle")
                v.next_action = self.rng.uniform(5, 11)
                if after == "eating" and self.food > 0 and self.can_eat(v.cat_id):
                    self._choose_action(v)
                return
        v.clip_y = edge[2]
        v.y = edge[2] - SPRITE_HEIGHT + WINDOW_PEEK_DEPTH * v.peek_progress

    def _deliver_true_name(self, v: Visitor) -> bool:
        """Realize a remembered response only where it cannot break a route.

        No secret text is stored in runtime feedback or events. Meals include
        their grooming finish; entry/jumps retain their original destination.
        """
        if not v.true_name_pending:
            return False
        # The rarer, longer recognition response subsumes gift gratitude.
        v.gift_response_pending = False
        v.gift_response_remaining = 0.0
        if v.state == "window_rear":
            self.leave_rear_perch(v)
            return False
        if v.state == "window_peeking":
            self._rise_from_peek(v, "purring")
            return False
        if v.state not in {"idle", "sleeping", "playing", "purring"}:
            return False
        v.true_name_pending = False
        self._state(v, "purring")
        v.true_name_remaining = TRUE_NAME_SECONDS
        self._emit("true_name_response", v.cat_id, "熟悉的呼噜声久久没有停下。")
        return True

    def _deliver_gift_response(self, v: Visitor) -> bool:
        """A collected gift can earn one short response once support is safe."""
        if not v.gift_response_pending:
            return False
        if (not self.gate_open or v.state in {"carried", "fighting", "leaving", "fleeing"}
                or v.route_motion in {"leaving", "fleeing"}
                or v.true_name_pending or v.true_name_remaining > 0):
            v.gift_response_pending = False
            return False
        if v.state == "window_rear":
            self.leave_rear_perch(v)
            return False
        if (v.state not in {"idle", "purring"} or v.route_stage
                or v.landing_remaining > 0 or v.settle_remaining > 0
                or v.rise_remaining > 0 or v.posture_remaining > 0
                or v.carrying_gift or v.demo_gift_pending
                or self.active_teaser_id == v.cat_id):
            return False
        v.gift_response_pending = False
        self._state(v, "purring")
        v.gift_response_remaining = GIFT_RESPONSE_SECONDS
        self._emit("gift_response", v.cat_id, f"{self.cat_reference(v.cat_id)}眯起眼，朝你轻轻呼噜。")
        return True

    def _finish_purring(self, v: Visitor) -> None:
        """Resume the same nap, including time spent responding to the user."""
        on_back = v.sleeping_purr
        nap_age = v.sleeping_purr_age + v.state_age
        self._state(v, "sleeping" if on_back else "idle")
        if on_back:
            v.state_age = nap_age

    def _affection(self, v: Visitor) -> None:
        if v.state == "window_rear":
            self.leave_rear_perch(v, arrival="purring")
        elif v.state == "window_peeking":
            self._rise_from_peek(v, "purring")
        elif v.state in {"walking", "running", "jumping", "climbing"}:
            # Recognition must not freeze a cat halfway through a jump, nor
            # interrupt an already committed meal.
            if v.arrival_state != "eating":
                v.arrival_state = "purring"
        else:
            self._state(v, "purring")

    def _leave(self, v: Visitor, fleeing: bool = False) -> None:
        if v.tree_trip is not None:
            action = "fleeing" if fleeing else "leaving"
            self._queue_tree_action(v, action, action)
            return
        if v.depth_trip is not None:
            action = "fleeing" if fleeing else "leaving"
            self._queue_depth_action(v, action, action)
            return
        if v.state in top_entry.TOP_STATES:
            top_entry.abort(self, v, fleeing=fleeing)
            return
        # Airborne departures still use the jumping pose. Repeated fear or
        # gate-close signals must not restart that arc or downgrade an escape.
        if v.state in {"leaving", "fleeing"} or v.route_motion in {"leaving", "fleeing"}:
            return
        self._clear_route(v)
        left, _, width, _ = self.bounds
        v.facing = -1 if v.x + SPRITE_WIDTH/2 < left+width/2 else 1
        target = left-SPRITE_WIDTH-2 if v.facing<0 else left+width+2
        motion = "fleeing" if fleeing else "leaving"
        # Cancel pending social feedback before planning the emergency descent.
        # Remember physical flight/wall contact before replacing that state.
        airborne = v.state in {"jumping", "climbing"}
        self._state(v, motion)
        self._walk(v, target, self.floor_y, motion=motion, allow_shallow=not airborne)
        v.takeoff_remaining = 0.0
        v.takeoff_edge_id = None

    def _drop_from_mouth(self, v: Visitor, kind: str) -> None:
        if self._on_rear_support(v) or v.y < self.bounds[1]:
            v.demo_gift_pending = kind
            return
        surface = WINDOW_SURFACE_OFFSET if v.window_edge_id or v.window_target_id else 0.0
        x, y = drop_position(v.x, v.y, v.facing, self.bounds, surface_offset=surface)
        self._drop(kind, x, y, v.cat_id, facing=v.facing)

    def _drop(self, kind: str, x: float, y: float, cat_id: str, *, facing: int = -1) -> None:
        if len(self.gifts) >= MAX_GIFTS:
            return
        used_ids = {g.id for g in self.gifts}
        while f"gift-{self._next_gift_id}" in used_ids:
            self._next_gift_id += 1
        left, top, width, height = self.bounds
        x = min(left + width - GIFT_SIZE, max(left, x))
        y = min(top + height - GIFT_SIZE, max(top, y))
        gift = Gift(f"gift-{self._next_gift_id}", kind, x, y, cat_id, facing=facing)
        if kind == "toy_mouse":
            self._schedule_mouse(gift)
        self.gifts.append(gift)
        self._next_gift_id += 1
        self._emit("fur" if kind == "fur" else "gift", cat_id,
                   "留下一小团猫毛，点一下就能清理。" if kind == "fur" else f"{self.cat_reference(cat_id)}放下了一件郑重其事的小礼物。")

    def _schedule_mouse(self, gift: Gift) -> None:
        gift.state = "idle"
        gift.wait_remaining = self.rng.uniform(MOUSE_WAIT_MIN, MOUSE_WAIT_MAX)
        gift.escape_direction = 0

    def _tick_gifts(self, dt: float) -> None:
        left, _, width, _ = self.bounds
        right = left + width
        for gift in list(self.gifts):
            if gift.kind != "toy_mouse" or gift.state == "held":
                continue
            move_time = dt
            if gift.state == "idle":
                waiting = max(0.0, gift.wait_remaining)
                gift.wait_remaining = max(0.0, waiting - dt)
                if gift.wait_remaining > 1e-9:
                    continue
                gift.wait_remaining = 0.0
                gift.state = "escaping"
                gift.escape_direction = self.rng.choice((-1, 1))
                gift.facing = gift.escape_direction
                # Preserve the exact wait boundary even when a low-frequency
                # timer's tick crosses it; only the leftover interval moves.
                move_time = max(0.0, dt - waiting)
            if gift.state != "escaping":
                continue
            gift.x += gift.escape_direction * MOUSE_ESCAPE_SPEED * move_time
            if ((gift.escape_direction < 0 and gift.x + GIFT_SIZE <= left)
                    or (gift.escape_direction > 0 and gift.x >= right)):
                self.gifts.remove(gift)
                self._emit("gift_escaped", gift.cat_id, "那只小老鼠沿着桌面边缘溜走了。")

    def _finish_fight(self) -> None:
        if not self._fight_pair:
            return
        pair = [self.visitors[cid] for cid in self._fight_pair if cid in self.visitors]
        if pair:
            self._drop("fur", sum(v.x for v in pair) / len(pair) + 65,
                       sum(v.y for v in pair) / len(pair) + 92, pair[0].cat_id)
        for v in pair:
            self._leave(v, fleeing=True)
        self._fight_pair = None
        self._fight_countdown = self.rng.uniform(30, 60)

    def _clear_shared_meal(self) -> None:
        self._shared_meal_tokens.clear()

    def _cancel_shared_meal(self, cat_id: str) -> None:
        self._meal_tokens.pop(cat_id, None)
        for pair in tuple(self._shared_meal_tokens):
            if cat_id in pair:
                del self._shared_meal_tokens[pair]

    def _start_shared_meal(self, v: Visitor) -> None:
        # One independent overlap/completion record per pair. A third meal
        # must neither replace the first pair nor award an interrupted pair.
        self._cancel_shared_meal(v.cat_id)
        self._meal_serial += 1
        token = self._meal_serial
        bowl = tuple(self.bowl)
        self._meal_tokens[v.cat_id] = (token, bowl)
        for cid, (other_token, other_bowl) in self._meal_tokens.items():
            other = self.visitors.get(cid)
            if cid != v.cat_id and other_bowl == bowl and other and other.state == "eating":
                pair = tuple(sorted((cid, v.cat_id)))
                self._shared_meal_tokens[pair] = {
                    "tokens": {cid: other_token, v.cat_id: token},
                    "completed": set(), "overlap": False}

    def _observe_shared_meal(self, dt: float) -> None:
        for cid in tuple(self._meal_tokens):
            v = self.visitors.get(cid)
            if v is None or v.state != "eating":
                self._cancel_shared_meal(cid)
        for pair, shared in tuple(self._shared_meal_tokens.items()):
            valid = True
            for cid, serial in shared["tokens"].items():
                v = self.visitors.get(cid)
                if v is None or v.state in {"carried", "fighting", "leaving", "fleeing", "swatting"}:
                    valid = False
                    break
                if cid not in shared["completed"]:
                    token = self._meal_tokens.get(cid)
                    if token is None or token[0] != serial or v.state != "eating":
                        valid = False
                        break
            if not valid:
                del self._shared_meal_tokens[pair]
            elif dt > 0 and not shared["completed"]:
                shared["overlap"] = True

    def _complete_shared_meal(self, v: Visitor) -> None:
        token = self._meal_tokens.pop(v.cat_id, None)
        if token is None:
            return
        for pair, shared in tuple(self._shared_meal_tokens.items()):
            if shared["tokens"].get(v.cat_id) != token[0]:
                continue
            shared["completed"].add(v.cat_id)
            if len(shared["completed"]) != 2:
                continue
            changes = self.relations.share_meal(*pair) if shared["overlap"] else {}
            del self._shared_meal_tokens[pair]
            if changes and all(self.is_discovered(cid) for cid in pair):
                names = [self.cats[cid].display_name for cid in pair]
                self._emit("shared_meal", v.cat_id, f"{names[0]}和{names[1]}一起吃完了饭，对彼此多了一点好感。")

    def _begin_eating(self, v: Visitor) -> bool:
        # An arrival can be stale (empty bowl, another meal, urgent departure).
        # Retrying it must neither restart an ongoing meal nor interrupt an exit.
        if (v.state in {"eating", "grooming", "carried", "fighting", "fleeing", "leaving"}
                or v.route_motion in {"fleeing", "leaving"}):
            return False
        if (self.food <= 0 or not self.can_eat(v.cat_id)
                or (v.meal_bowl is not None and v.meal_bowl != self.bowl)):
            self._state(v, "idle")
            return False
        self.food -= 1
        self.cats[v.cat_id].meal_cooldown_remaining = MEAL_COOLDOWN_SECONDS
        v.has_eaten = True
        self._state(v, "eating")
        self._start_shared_meal(v)
        self._emit("eating", v.cat_id, f"{self.cat_reference(v.cat_id)}低下头，认真吃起小鱼干。")
        return True

    def _play_step(self, v: Visitor, dt: float) -> None:
        if self.calm_mode or self.cats[v.cat_id].personality != "mischievous":
            self._state(v, "idle")
            return
        v.play_retarget_in -= dt
        if v.play_retarget_in <= 0:
            left, top, width, height = self.bounds
            center_x, center_y = v.x + SPRITE_WIDTH / 2, v.y + SPRITE_HEIGHT / 2
            cursor = self._cursor
            nearby = (cursor is not None
                      and left <= cursor[0] <= left + width
                      and top <= cursor[1] <= top + height
                      and math.hypot(cursor[0] - center_x, cursor[1] - center_y) <= PLAY_RADIUS)
            if nearby:
                target_x, target_y = cursor
                v.play_speed = PLAY_SPEED
            else:
                # When the mouse is elsewhere, lounge near screen center
                # instead of chasing it across screens or moving the pointer.
                target_x, target_y = left + width / 2, top + height / 2
                v.play_speed = 45.0
            v.target_x = self._inside_x(target_x - SPRITE_WIDTH / 2)
            v.target_y = min(self.floor_y, max(top, target_y - SPRITE_HEIGHT / 2))
            v.play_retarget_in = PLAY_RETARGET_INTERVAL
            if abs(v.target_y-v.y)>1e-7:
                self._walk(v, v.target_x, v.target_y)
                return
        if self._ground_step(v, dt, v.play_speed, PLAY_DEAD_ZONE):
            seated = v.rise_remaining / RISE_SECONDS
            self._state(v, "idle")
            v.settle_remaining = SETTLE_SECONDS * (1-seated)
            v.next_action = max(v.next_action, 3.0)

    def cancel_parkour(self, visitor: Visitor | str) -> None:
        """Cancel autonomous running without changing position or manual play.

        Controllers may call this before clearing a route and beginning a
        deliberate interaction. An airborne route still lands continuously.
        """
        v = self.visitors.get(visitor) if isinstance(visitor, str) else visitor
        if v is None:
            return
        if v.parkour_remaining > 0:
            v.parkour_cooldown = max(v.parkour_cooldown, 35.0)
        v.parkour_remaining = 0.0
        if v.route_motion == "running":
            v.route_motion = "walking"

    def _parkour_action(self, v: Visitor) -> bool:
        if self._on_rear_support(v):
            self.cancel_parkour(v)
            return False
        if self.calm_mode or v.carrying_gift or v.demo_gift_pending or self._source_interval(v) is None:
            self.cancel_parkour(v)
            return False
        if v.parkour_remaining <= 0:
            if v.parkour_cooldown > 0 or self.rng.random() >= PARKOUR_CHANCE[self.cats[v.cat_id].personality]:
                return False
            v.parkour_remaining = self.rng.uniform(7.0, 12.0)
            v.parkour_cooldown = self.rng.uniform(40.0, 65.0)
            self._emit("parkour", v.cat_id, f"{self.cat_reference(v.cat_id)}忽然来了兴致，沿着桌面轻快地跑了一阵。")
        edges = [key for key in self._reachable_window_edges(v) if key != v.window_edge_id]
        if edges and self.rng.random() < .40:
            if self._move_to_window(v, self.rng.choice(edges), motion="running"):
                return True
        source = self._source_interval(v)
        if source is None:
            self.cancel_parkour(v)
            return False
        # Run only where there is a continuous support surface. The next
        # decision may use another reachable window, never a diagonal sprint.
        direction = self.rng.choice((-1, 1))
        target = min(source[1], max(source[0], v.x+direction*self.rng.uniform(170, 330)))
        if abs(target-v.x)<40:
            target = min(source[1], max(source[0], v.x-direction*180))
        if abs(target-v.x)<10:
            self.cancel_parkour(v)
            return False
        self._walk(v, target, v.y, motion="running")
        v.next_action = .6
        return True

    def _choose_action(self, v: Visitor) -> None:
        if begin_daily_wake(self, v):
            return
        cat = self.cats[v.cat_id]
        if self.food > 0 and self.can_eat(v.cat_id):
            self.cancel_parkour(v)
            facing = 1 if self.bowl[0] >= v.x + SPRITE_WIDTH / 2 else -1
            occupied = set()
            for cid, other in self.visitors.items():
                if cid == v.cat_id:
                    continue
                token = self._meal_tokens.get(cid)
                if other.state == "eating" and token and token[1] == self.bowl:
                    occupied.add(other.facing)
                elif other.state == "grooming" and other.has_eaten:
                    # A finished meal still occupies its side while the cat
                    # washes. Match both actual anchors, so a moved bowl or
                    # grooming elsewhere cannot reserve this meal location.
                    muzzle = EATING_MUZZLE_RIGHT if other.facing > 0 else EATING_MUZZLE_LEFT
                    if (abs(other.x+muzzle-self.bowl[0]) <= 1e-7
                            and abs(other.y+SPRITE_HEIGHT-15-self.bowl[1]) <= 1e-7):
                        occupied.add(other.facing)
                elif (other.meal_bowl == self.bowl and other.arrival_state == "eating"
                      and other.state in {"walking", "running", "jumping", "entering"}
                      and other.route_motion not in {"fleeing", "leaving"}):
                    occupied.add(other.arrival_facing)
            if facing in occupied:
                facing = -facing
                muzzle = EATING_MUZZLE_RIGHT if facing > 0 else EATING_MUZZLE_LEFT
                target_x = self.bowl[0] - muzzle
                if facing in occupied or abs(self._inside_x(target_x)-target_x) > 1e-7:
                    # At a screen edge the other side can be unavailable. Wait
                    # for the occupied side instead of stacking two bodies.
                    self._state(v, "idle")
                    return
            muzzle = EATING_MUZZLE_RIGHT if facing > 0 else EATING_MUZZLE_LEFT
            self._walk(v, self.bowl[0] - muzzle, self.bowl[1] - SPRITE_HEIGHT + 15,
                       "eating", arrival_facing=facing)
            # Recheck at arrival if the user moves the bowl during the route.
            v.meal_bowl = self.bowl
            return
        if bed_model.choose_bed(self, v):
            return
        if self._on_rear_support(v):
            others = [key for key in self._reachable_window_edges(v) if key != v.window_edge_id]
            choice = self.rng.random()
            if choice < .25 and others:
                self._move_to_window(v, others[0])
            elif choice < .70:
                self.leave_rear_perch(v)
            else:
                self._begin_window_rear(v)
            return
        if self._parkour_action(v):
            return
        choice = self.rng.random()
        edges = self._reachable_window_edges(v)
        if v.window_edge_id in self.window_edges:
            edge_id = v.window_edge_id
            left, right, _ = self.window_edges[edge_id]
            others = [key for key in edges if key != edge_id]
            if choice < 0.40 and self._begin_window_peek(v):
                return
            if choice < 0.62:
                self._move_to_window(v, edge_id, min(right - SPRITE_WIDTH, max(left, v.x + self.rng.uniform(-120, 120))))
            elif choice < 0.78 and others:
                self._move_to_window(v, others[0])
            elif choice < 0.90:
                self._walk(v, v.x + self.rng.uniform(-100, 100), self.floor_y)
            else:
                self._state(v, "sleeping" if cat.trust >= PET_TRUST else "idle")
        elif choice < 0.35:
            reachable = [p for p in self.paths if abs(p[0] - v.x) <= 360 and abs(p[1] - v.y) <= 220]
            if edges and cat.personality != "shy" and self.rng.random() < 0.65:
                # Native inputs are front-to-back: favor the foremost usable
                # window, with occasional exploration of another visible top.
                edge_id = edges[0] if self.rng.random() < 0.75 else self.rng.choice(edges)
                self._move_to_window(v, edge_id)
            elif reachable and cat.personality != "shy" and self.rng.random() < 0.45:
                self._walk(v, *self.rng.choice(reachable))
            else:
                self._walk(v, v.x + self.rng.uniform(-160, 160), self.floor_y)
        elif choice < 0.55 and cat.trust >= PET_TRUST:
            self._state(v, "sleeping")
        elif choice < 0.73 and cat.personality == "mischievous" and not self.calm_mode:
            self._state(v, "playing")
        else:
            self._state(v, "idle")
        v.next_action = self.rng.uniform(3, 7) if self.demo else self.rng.uniform(6, 16)

    def _tick_step(self, dt: float) -> None:
        had_visitors = bool(self.visitors)
        self._time += dt
        # Identity-owned cooldown survives departure/reinvitation. No offline
        # clock or separate timer: callers pause by withholding effective ticks.
        for profile in self.cats.values():
            profile.meal_cooldown_remaining = max(0.0, profile.meal_cooldown_remaining-dt)
        current_session(self)
        self._tick_gifts(dt)
        if self.calm_mode:
            self._demo_pending = None
            if self._fight_pair:
                self._finish_fight()
            for visitor in self.visitors.values():
                self.cancel_parkour(visitor)
                if visitor.state in {"playing", "running"}:
                    self._state(visitor, "idle")
        if self.gate_open and len(self.visitors) < self.visitor_limit:
            if self._welcome_visit_remaining is not None:
                self._welcome_visit_remaining = max(0.0, self._welcome_visit_remaining-dt)
            self._visit_countdown -= dt
            if self._visit_countdown <= 0:
                resident = self.resident_id
                if resident is not None and resident not in self.visitors:
                    # A missing household companion returns at this ordinary
                    # opportunity; the rare-neighbour chance is not its gate.
                    self.invite(resident)
                else:
                    # Only additional neighbours use the resident's 5% chance.
                    chance = 1.0 if not self.visitors else (0.05 if resident else 0.12)
                    if self.visitors:
                        chance *= self._co_visit_chance_factor()
                    if self.rng.random() < chance:
                        self.invite()
                self._visit_countdown = self._visit_delay()
        self._observe_shared_meal(dt)
        for cid, v in list(self.visitors.items()):
            cat = self.cats[cid]
            if v.state not in top_entry.TOP_STATES:
                bed_model.account_bed_time(self, v, dt)
            v.age += dt
            v.state_age += dt
            rear_target = (1.0 if v.state == "window_rear" or v.state in top_entry.TOP_STATES else
                           v.rear_blend if v.state == "carried" and v.y < self.bounds[1] else 0.0)
            change = dt/REAR_BLEND_SECONDS
            v.rear_blend = (min(rear_target, v.rear_blend+change) if v.rear_blend < rear_target
                            else max(rear_target, v.rear_blend-change))
            v.pet_cooldown = max(0, v.pet_cooldown - dt)
            v.parkour_cooldown = max(0.0, v.parkour_cooldown-dt)
            if v.parkour_remaining > 0:
                v.parkour_remaining = max(0.0, v.parkour_remaining-dt)
                if v.parkour_remaining <= 0:
                    self.cancel_parkour(v)
                    if v.state == "running":
                        self._state(v, "walking")
            landing_wait = min(dt, v.landing_remaining)
            v.landing_remaining = max(0.0, v.landing_remaining - dt)
            v.settle_remaining = max(0.0, v.settle_remaining - dt)
            was_gift_response = v.gift_response_remaining > 0
            v.gift_response_remaining = max(0.0, v.gift_response_remaining-dt)
            if was_gift_response and v.gift_response_remaining <= 1e-9:
                v.gift_response_remaining = 0.0
                if v.state == "purring" and v.true_name_remaining <= 0:
                    self._finish_purring(v)
            was_true_name = v.true_name_remaining > 0
            v.true_name_remaining = max(0.0, v.true_name_remaining-dt)
            if was_true_name and v.true_name_remaining <= 1e-9 and v.state == "purring":
                v.true_name_remaining = 0.0
                self._finish_purring(v)
            if (v.state not in {"carried", "peeking", "entering", "top_entering", "top_leaving", "leaving", "fleeing", "fighting"}
                    and v.route_motion not in {"leaving", "fleeing"} and not cat.adopted):
                v.stay_remaining -= dt
                # Once food is consumed, finish this meal before an automatic
                # departure; its normal reward extends the visit for grooming.
                # Explicit close/startle/carry still interrupt through their
                # own state transitions, without granting an unfinished meal.
                if v.stay_remaining <= 0 and v.state != "eating":
                    self._leave(v)
            if v.tree_trip is not None:
                self._tick_tree_visit(v, dt)
                continue
            if v.depth_trip is not None:
                self._tick_depth_visit(v, dt)
                continue
            if bed_model.tick_bed_lie(self, v, dt, landing_wait):
                continue
            if tick_interaction(self, v, dt, landing_wait):
                continue
            if v.state == "peeking":
                if v.state_age >= (1.5 if self.demo else 3.0):
                    self._walk(v, v.target_x, v.target_y, arrival_facing=v.arrival_facing, motion="entering")
            elif v.state in top_entry.TOP_STATES:
                top_entry.step(self, v, dt)
            elif v.state == "window_peeking":
                self._peek_step(v, dt)
            elif v.state == "climbing":
                climb_routes.step(self, v, dt)
            elif v.moving:
                speed = RUN_SPEED if v.state in {"running", "fleeing"} else 52 if cat.personality == "shy" else 66
                jumping = v.state == "jumping"
                if jumping:
                    # A coarse tick may cross the crouch boundary. Only its
                    # remaining portion belongs to the flight, never state_age.
                    preparation = min(dt, v.takeoff_remaining)
                    v.takeoff_remaining = max(0.0, v.takeoff_remaining-preparation)
                    if v.takeoff_remaining <= 1e-9:
                        v.takeoff_remaining = 0.0
                        v.takeoff_edge_id = None
                    flight_dt = max(0.0, dt-preparation)
                    old_y = v.y
                    v.jump_elapsed = min(v.jump_duration, v.jump_elapsed + flight_dt)
                    progress = min(1.0, v.jump_elapsed / v.jump_duration)
                    v.x = v.jump_start_x + (v.target_x - v.jump_start_x) * progress
                    raw_y = v.jump_start_y + (v.target_y-v.jump_start_y)*progress - v.jump_arc*4*progress*(1-progress)
                    # A validated low-headroom destination and any subsequent
                    # departure retain their whole arc, including negative y.
                    offscreen_arc = (v.window_target_id in self.low_headroom_edges
                                     or v.jump_start_y < self.bounds[1])
                    v.y = raw_y if offscreen_arc else max(self.bounds[1], raw_y)
                    self._update_jump_pose(v, progress, (v.y-old_y)/flight_dt if flight_dt > 0 else None)
                    if v.target_x != v.jump_start_x:
                        v.facing = 1 if v.target_x > v.jump_start_x else -1
                    arrived = progress >= 1
                else:
                    arrived = self._ground_step(v, max(0.0, dt-landing_wait), speed)
                if arrived:
                    seated = v.rise_remaining / RISE_SECONDS
                    v.x, v.y = v.target_x, v.target_y
                    if jumping:
                        v.landing_remaining = LANDING_SECONDS
                    if self._continue_route(v):
                        continue
                    if v.state in {"leaving", "fleeing"}:
                        del self.visitors[cid]
                        self._emit("departure", cid, f"{cat.display_name}回邻居家去了。")
                    else:
                        # The destination may require turning toward the bowl.
                        # Resolve that facing before releasing anything held in
                        # the mouth, rather than leaving it behind the new pose.
                        if v.arrival_facing is not None:
                            v.facing = v.arrival_facing
                        if v.carrying_gift:
                            self._drop_from_mouth(v, v.carrying_gift)
                            v.carrying_gift = None
                            v.gift_spot = None
                        if v.window_target_id in self.window_edges:
                            v.window_edge_id = v.window_target_id
                        v.window_target_id = None
                        if v.arrival_state == "eating":
                            self._begin_eating(v)
                        elif v.arrival_state == "bed":
                            bed_model.begin_bed_lie(self, v)
                        else:
                            self._state(v, v.arrival_state if v.arrival_state in {"purring", "teasing", "resting"} else "idle")
                            if v.state == "jumping" and v.route_stage:
                                # A response that reached a newly maximized
                                # edge has scheduled its own safe descent. Keep
                                # that arrival intention instead of clearing it.
                                continue
                        if v.state == "idle":
                            # Keep the feet planted briefly before the seated
                            # resting pose. Eating/purring start without delay.
                            v.settle_remaining = SETTLE_SECONDS * (1-seated)
                        v.arrival_state = "idle"
                        v.arrival_facing = None
            elif v.state == "eating" and v.state_age >= 4:
                cat.trust = min(100.0, cat.trust + (18 if cat.personality == "shy" else 22))
                neighbor_lifecycle.completed_meal(self, cat)
                v.stay_remaining += 60
                self._complete_shared_meal(v)
                self._state(v, "grooming")
                self._emit("trust", cid, f"{self.cat_reference(cid)}吃饱了，愿意再多待一会儿。")
            elif v.state == "grooming" and v.state_age >= 5:
                self._state(v, "idle")
            elif v.state == "purring" and v.state_age >= 4 and v.true_name_remaining <= 0 and v.gift_response_remaining <= 0:
                self._finish_purring(v)
            elif v.state in {"idle", "sleeping", "playing", "window_rear"}:
                if bed_model.tick_bed_nap(self, v):
                    pass
                elif self.food > 0 and self.can_eat(v.cat_id) and v.state != "sleeping":
                    self._choose_action(v)
                elif v.state_age >= v.next_action * (3 if v.state == "sleeping" else 1):
                    self._choose_action(v)
                if v.state == "playing":
                    self._play_step(v, dt)
            elif v.state == "fighting" and v.state_age >= 5:
                self._finish_fight()
            if cid in self.visitors:
                self._deliver_true_name(v)
                self._deliver_gift_response(v)
                self._advance_demo_gift(v)
        if self._demo_pending == "fight" and len(self.visitors) == 2:
            pair = list(self.visitors.values())
            if all(v.state in {"idle", "walking", "playing", "sleeping"} for v in pair):
                if self.trigger_fight():
                    self._demo_pending = None
                elif all(v.state != "walking" for v in pair):
                    midpoint = sum(v.x for v in pair) / 2
                    self._walk(pair[0], midpoint - 30, self.floor_y)
                    self._walk(pair[1], midpoint + 30, self.floor_y)
        if len(self.visitors) >= 2 and not self._fight_pair and not self.calm_mode:
            self._fight_countdown -= dt
            if self._fight_countdown <= 0:
                pair = self._fight_candidates()
                if (pair and any(self.cats[v.cat_id].personality == "mischievous" for v in pair)
                        and self.rng.random() < self.relations.fight_chance(*(v.cat_id for v in pair))):
                    self.trigger_fight()
                self._fight_countdown = self.rng.uniform(20, 45)
        if had_visitors and not self.visitors and not self.demo and not self.resident_id:
            # Start a full quiet interval only once the last cat has actually
            # crossed the edge (including depth escapes). Opportunities that
            # elapsed during a visit or a two-cat overlap cannot erase it.
            self._visit_countdown = self._visit_delay()
