"""Accepted residence can end without expelling or relocating the visitor."""
import pytest
from backyard_visitor.model import Engine
from backyard_visitor.storage import load_state, save_state


def advance_until(engine, predicate, timeout=360):
    for _ in range(round(timeout/.05)):
        if predicate():
            return
        engine.tick(.05)
    assert predicate(), "Expected ordinary visitor lifecycle did not complete"


def resident(cat_id="mist", bed=True, seed=3):
    engine = Engine(seed=seed, visit_limit=1)
    engine.calm_mode = True
    engine.open_gate(); engine.invite(cat_id)
    engine.cats[cat_id].trust = 100
    assert engine.adopt(cat_id)
    if bed:
        assert engine.place_bed(cat_id, 1100)
    return engine, engine.visitors[cat_id]


def test_cancel_is_ordinary_trust_fifty_without_changing_identity_or_relations():
    e, v = resident(bed=False)
    e.cats[v.cat_id].nickname = "小团"
    relations = e.relations.export()
    before = v.x, v.y, v.state
    assert e.cancel_residence(v.cat_id)
    cat = e.cats[v.cat_id]
    assert e.resident_id is None and not cat.adopted and cat.trust == 50
    assert cat.nickname == "小团" and cat.discovered and cat.visits == 1
    assert e.relations.export() == relations
    assert e.visitors[v.cat_id] is v and (v.x, v.y, v.state) == before
    assert 150 <= v.stay_remaining <= 230
    assert e._visit_countdown >= 240-2.3*(50/len(e.cats))
    assert Engine(e.export())._base_visit_weights()[v.cat_id] == 3.5
    assert not e.adopt(v.cat_id)
    cat.trust = 100
    assert e.adopt(v.cat_id)


@pytest.mark.parametrize("bad_id", [None, [], {}, True, 123, "missing", "amber"])
def test_invalid_or_nonresident_cancellation_is_noop(bad_id):
    e, v = resident()
    before = e.export(), e._visit_countdown, e._bed_revision
    assert not e.cancel_residence(bad_id)
    assert (e.export(), e._visit_countdown, e._bed_revision) == before


def test_repeated_cancellation_does_not_lower_newly_earned_trust_or_reset_timer():
    e, v = resident()
    assert e.cancel_residence(v.cat_id)
    e.cats[v.cat_id].trust = 53
    before = e.export(), v.stay_remaining, e._visit_countdown
    assert not e.cancel_residence(v.cat_id)
    assert (e.export(), v.stay_remaining, e._visit_countdown) == before


def test_napping_resident_wakes_in_place_and_bed_is_removed():
    e, v = resident()
    advance_until(e, lambda: v.bed_nap and v.state == "sleeping")
    before = v.x, v.y
    assert e.cancel_residence(v.cat_id)
    assert e.bed is None and not v.bed_nap and v.bed_route_revision is None
    assert (v.x, v.y) == before and v.state == "waking"
    assert v.posture_kind == "belly_roll"
    advance_until(e, lambda: v.state != "waking", 5)
    assert v.cat_id in e.visitors


@pytest.mark.parametrize("kind", ["crouch_lower", "belly_lie"])
def test_cancel_during_lie_down_finishes_pose_without_stale_nap(kind):
    e, v = resident()
    advance_until(e, lambda: v.state == "bed_laying" and v.posture_kind == kind)
    before = v.x, v.y, v.posture_kind
    assert e.cancel_residence(v.cat_id)
    assert (v.x, v.y, v.posture_kind) == before
    advance_until(e, lambda: v.state == "waking", 3)
    assert not v.bed_nap and e.bed is None


def test_cancel_while_walking_to_bed_clears_arrival_without_teleport():
    e, v = resident()
    advance_until(e, lambda: v.bed_route_revision is not None and v.moving)
    before = v.x, v.y
    assert e.cancel_residence(v.cat_id)
    assert (v.x, v.y) == before
    assert v.bed_route_revision is None and v.arrival_state != "bed"
    for _ in range(400):
        e.tick(.05)
        assert not v.bed_nap
        assert v.state != "bed_laying"


def test_cancel_airborne_bed_route_preserves_current_flight():
    e = Engine(seed=1, visit_limit=1)
    e.calm_mode = True
    e.open_gate(); e.invite("ink")
    e.cats["ink"].trust = 100; e.adopt("ink")
    e.set_window_edges([("low", 150, 1100, 450)])
    v = e.visitors["ink"]
    advance_until(e, lambda: v.window_edge_id == "low" and v.state == "idle")
    e.place_bed("ink", 1100)
    advance_until(e, lambda: v.bed_route_revision is not None)
    assert v.state == "jumping"
    before = v.x, v.y, v.jump_start_x, v.jump_start_y, v.target_x, v.target_y
    assert e.cancel_residence("ink")
    assert (v.x, v.y, v.jump_start_x, v.jump_start_y, v.target_x, v.target_y) == before
    assert v.state == "jumping" and v.arrival_state != "bed"
    advance_until(e, lambda: v.state != "jumping", 10)
    assert not v.bed_nap


def test_cancel_restores_departure_after_old_resident_clock_was_zero():
    e, v = resident(bed=False)
    advance_until(e, lambda: v.state == "idle")
    v.stay_remaining = 0
    e.cancel_residence(v.cat_id)
    e.tick(.05)
    assert v.cat_id in e.visitors and v.route_motion not in {"leaving", "fleeing"}
    assert v.state not in {"leaving", "fleeing"}
    # An ordinary visit really expires again; no resident stay exemption.
    v.stay_remaining = .01
    e.tick(.05)
    assert v.state == "leaving" or v.route_motion == "leaving"


def test_cancelling_does_not_interrupt_explicit_gate_departure():
    e, v = resident(bed=False)
    advance_until(e, lambda: v.state == "idle")
    e.open_gate(False)
    before = v.x, v.y, v.state, v.route_motion, v.target_x, v.target_y
    assert e.cancel_residence(v.cat_id)
    assert (v.x, v.y, v.state, v.route_motion, v.target_x, v.target_y) == before
    advance_until(e, lambda: not e.visitors, 60)
    assert not e.gate_open


def test_absent_resident_loses_short_homecoming_and_persists(tmp_path):
    e, v = resident()
    saved = e.export()
    e = Engine(saved, seed=4)
    assert not e.visitors and not e.gate_open
    e._visit_countdown = .01
    assert e.cancel_residence("mist")
    assert e._visit_countdown > 100 and e.bed is None
    path = tmp_path/"state.json"
    save_state(path, e.export())
    restored = Engine(load_state(path), seed=12)
    assert restored.resident_id is None and restored.bed is None
    assert restored.cats["mist"].trust == 50
    assert restored.cats["mist"].discovered
    restored.open_gate()
    assert restored._visit_countdown > 100
    # Another established neighbour can now accept residence.
    restored.invite("amber"); restored.cats["amber"].trust = 100
    restored.cats["amber"].neighbor_status = "neighbor"
    assert restored.adopt("amber")
