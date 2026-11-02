"""Persisted neighbour identities and effective, visible guest exposure.

This clock deliberately has no wall-time or simulation-tick connection. The
controller supplies only bright-display, actually visible visitor IDs.
"""
from __future__ import annotations

import hashlib
import math
import random
import re

SCHEMA_VERSION = 1
NEIGHBOR_LIMIT = 7
GUEST_POOL_TARGET = 2
GUEST_EXPOSURE_LIMIT = 7200.0
PROMOTION_MEALS = 3
PROMOTION_TRUST = 40.0
_GENERATED_ID = re.compile(r"generated-([0-9]{6,12})\Z")


def dynamic_ids(rows):
    if not isinstance(rows, dict):
        return ()
    return tuple(sorted(cid for cid in rows if isinstance(cid, str) and _GENERATED_ID.fullmatch(cid)))


def _integer(value, default=0, maximum=10**12):
    return value if type(value) is int and 0 <= value <= maximum else default


def _number(value, default=0.0):
    return float(value) if type(value) in (int, float) and 0 <= value <= GUEST_EXPOSURE_LIMIT and math.isfinite(value) else default


def eligible(cat):
    return cat.meal_count >= PROMOTION_MEALS or cat.trust >= PROMOTION_TRUST


def waiting_for_slot(engine, cid):
    cat = engine.cats.get(cid)
    return bool(cat and cat.discovered and cat.neighbor_status == "guest" and eligible(cat)
                and sum(c.is_neighbor for c in engine.cats.values()) >= NEIGHBOR_LIMIT)


def record(cat):
    return {"status": cat.neighbor_status, "meal_count": cat.meal_count,
            "exposure_seconds": cat.exposure_seconds, "last_seen_order": cat.last_seen_order}


def export(engine):
    return {"schema_version": SCHEMA_VERSION, "next_guest_id": engine._next_guest_id,
            "identity_source": engine._guest_identity_source,
            "last_seen_order": engine._last_seen_order,
            "first_neighbor_assigned": engine._first_neighbor_assigned}


def initialize(engine, state, seed):
    valid = isinstance(state, dict) and type(state.get("version", 0)) is int and 0 <= state.get("version", 0) <= 1
    rows = state.get("cats", {}) if valid else {}
    if isinstance(rows, list):
        rows = {row.get("id"): row for row in rows if isinstance(row, dict) and isinstance(row.get("id"), str)}
    rows = rows if isinstance(rows, dict) else {}
    saved = state.get("neighbor_lifecycle", {}) if valid else {}
    modern = isinstance(saved, dict) and type(saved.get("schema_version")) is int and saved["schema_version"] == SCHEMA_VERSION
    saved = saved if modern else {}
    # No ecology RNG draws: new identities do not alter movement/visit timing.
    default_source = random.Random(None if seed is None else f"guest-source-v1:{seed!r}").getrandbits(32)
    engine._guest_identity_source = _integer(saved.get("identity_source"), default_source, 0xFFFFFFFF)
    largest = max((int(_GENERATED_ID.fullmatch(cid)[1]) for cid in dynamic_ids(rows)), default=0)
    engine._next_guest_id = max(largest + 1, _integer(saved.get("next_guest_id"), 1))
    engine._last_seen_order = _integer(saved.get("last_seen_order"))
    neighbors = 0
    for cid, cat in engine.cats.items():
        row = rows.get(cid, {})
        row = row if isinstance(row, dict) else {}
        life = row.get("lifecycle", {}) if modern else {}
        life = life if isinstance(life, dict) else {}
        if modern and isinstance(life.get("status"), str) and life["status"] in {"guest", "neighbor", "departed"}:
            status = life["status"]
        else:
            # Migration protects every previously met/named/resident identity;
            # an undiscovered nickname still does not unlock its portrait.
            status = "neighbor" if (cat.discovered or cat.nickname or cat.adopted) else "guest"
        if cat.adopted:
            status = "neighbor"
        if status == "neighbor":
            if neighbors >= NEIGHBOR_LIMIT:
                status = "guest"
                cat.adopted = False
            else:
                neighbors += 1
        cat.neighbor_status = status
        cat.meal_count = _integer(life.get("meal_count"), maximum=1_000_000_000)
        cat.exposure_seconds = _number(life.get("exposure_seconds"))
        if cat.neighbor_status == "guest" and cat.meal_count == 0 and cat.exposure_seconds >= GUEST_EXPOSURE_LIMIT:
            cat.neighbor_status = "departed"
        cat.last_seen_order = _integer(life.get("last_seen_order"))
        engine._last_seen_order = max(engine._last_seen_order, cat.last_seen_order)
    engine._first_neighbor_assigned = bool(saved.get("first_neighbor_assigned") is True
        or any(c.is_neighbor or c.discovered for c in engine.cats.values()))
    engine._neighbor_lifecycle_ready = True
    promote(engine)
    replenish(engine)


def replenish(engine):
    """Keep a finite waiting pool; historical/departed records are never erased."""
    if sum(c.is_neighbor for c in engine.cats.values()) >= NEIGHBOR_LIMIT:
        return
    pending = sum(c.neighbor_status == "guest" for c in engine.cats.values())
    while pending < GUEST_POOL_TARGET:
        serial = engine._next_guest_id
        engine._next_guest_id += 1
        cid = f"generated-{serial:06d}"
        if cid in engine.cats:
            continue
        used = {c.identity_seed for c in engine.cats.values() if c.identity_seed is not None}
        salt = 0
        while True:
            digest = hashlib.sha256(f"guest-v1:{engine._guest_identity_source}:{serial}:{salt}".encode()).digest()
            identity_seed = int.from_bytes(digest[:4], "big")
            if identity_seed not in used:
                break
            salt += 1
        old_relations = engine.relations.export()
        engine._add_generated_cats({"version": 1, "cats": {cid: {"identity_seed": identity_seed}}}, None, (cid,))
        engine.relations = type(engine.relations)(engine.cats, seed=f"guest-relations-v1:{identity_seed}", state=old_relations)
        pending += 1


def promote(engine, cid=None):
    if not getattr(engine, "_neighbor_lifecycle_ready", False):
        return False
    candidates = ([engine.cats[cid]] if cid in engine.cats else []) if cid is not None else list(engine.cats.values())
    changed = False
    count = sum(c.is_neighbor for c in engine.cats.values())
    for cat in candidates:
        if count >= NEIGHBOR_LIMIT:
            break
        if cat.neighbor_status == "guest" and cat.discovered and eligible(cat):
            cat.neighbor_status = "neighbor"
            count += 1
            changed = True
            engine._emit("neighbor_joined", cat.id, f"{cat.display_name}愿意成为这里的正式邻居了。")
    if changed:
        replenish(engine)
    return changed


def arrived(engine, cat):
    engine._last_seen_order += 1
    cat.last_seen_order = engine._last_seen_order
    if not engine._first_neighbor_assigned:
        engine._first_neighbor_assigned = True
        cat.neighbor_status = "neighbor"
        engine._emit("neighbor_joined", cat.id, "第一位来访的猫，成为了这里的第一位正式邻居。")
    promote(engine, cat.id)
    replenish(engine)


def completed_meal(engine, cat):
    cat.meal_count = min(1_000_000_000, cat.meal_count + 1)
    promote(engine, cat.id)


def account_exposure(engine, dt, visible_ids, display_awake=True):
    if display_awake is not True or type(dt) not in (int, float):
        return False
    try:
        dt = float(dt)
    except (OverflowError, ValueError):
        return False
    if not math.isfinite(dt) or dt <= 0:
        return False
    if isinstance(visible_ids, (str, bytes)):
        return False
    try:
        visible = set(visible_ids)
    except (TypeError, ValueError):
        return False
    dt = min(float(dt), 5.0)
    changed = False
    for cid in tuple(engine.visitors):
        cat = engine.cats.get(cid)
        if cat is None or cid not in visible or cat.neighbor_status != "guest":
            continue
        promote(engine, cid)
        if cat.is_neighbor:
            continue
        previous = cat.exposure_seconds
        cat.exposure_seconds = min(GUEST_EXPOSURE_LIMIT, previous + dt)
        changed |= cat.exposure_seconds != previous
        if cat.meal_count == 0 and cat.exposure_seconds >= GUEST_EXPOSURE_LIMIT:
            cat.neighbor_status = "departed"
            visitor = engine.visitors[cid]
            # Keep pointer ownership until release; release then starts its exit.
            if visitor.state != "carried":
                engine._leave(visitor)
            engine._emit("guest_departed", cid, "这位过客还没有在这里吃过饭，悄悄踏上了新的路。")
            replenish(engine)
            changed = True
    return changed


def roster_ids(engine):
    return tuple(sorted((cid for cid, c in engine.cats.items() if c.discovered),
        key=lambda cid: (not engine.cats[cid].adopted, -engine.cats[cid].last_seen_order, cid)))
