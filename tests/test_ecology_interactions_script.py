"""Pure observer-contract tests; never construct Qt, run a child, or wait 180 s."""
import pytest

from scripts.check_ecology_interactions import coverage_checks, meal_started_model_time, parse_args


def complete_report():
    def response():
        return {"collected": True, "saw_purring": True, "initial_remaining": 2.97,
                "ended": True, "response_model_seconds": 3.02,
                "screenshots": {key: {"path": f"{key}.png", "sha256": "verified-image"}
                                for key in ("before", "during", "after")}}
    ordinary, jump = response(), response()
    jump.update(collected_airborne=True, pending_in_air=True, responded_after_air=True)
    return {"platform_plugin": "cocoa", "model_dt_injected": False,
            "meals": [{"food_after_debit": 0, "saw_eating": True, "saw_grooming": True,
                       "started_model_seconds": t, "observed_real_seconds": t+.1, "mouth_at_bowl": True}
                      for t in (12., 193.)],
            "cooldown_samples": 1500, "cooldown_violations": [], "second_meal_route_samples": [{"state": "walking"}],
            "gifts": {"ordinary": ordinary, "jump": jump,
                      "cancel": {"pending_before_carry": True, "cleared_by_carry": True,
                                 "no_replay_after_release": True}},
            "global_cursor_override_seen": False, "fullscreen_canvas_seen": False,
            "journal_seen": False, "native_key_seen": False, "shutdown_verified": True}


def test_meal_instant_accounts_for_observer_delay_without_injecting_time():
    assert meal_started_model_time(12.3, 179.7) == pytest.approx(12.)
    assert meal_started_model_time(192., 180.) == 192.


@pytest.mark.parametrize("cooldown", [0, -1, 180.01, float("nan"), float("inf")])
def test_meal_instant_rejects_non_debit_or_invalid_countdown(cooldown):
    with pytest.raises(ValueError):
        meal_started_model_time(10, cooldown)


def test_complete_evidence_covers_all_required_checks():
    assert all(coverage_checks(complete_report()).values())


def test_short_meal_interval_cannot_borrow_extra_time_from_total_runtime():
    report = complete_report()
    report["elapsed_real_seconds"] = 300
    report["meals"][1]["started_model_seconds"] = 191.99
    assert not coverage_checks(report)["effective_cooldown_at_least_180_seconds"]


def test_180_model_seconds_with_accelerated_real_time_is_not_native_wait():
    report = complete_report()
    report["meals"][1]["observed_real_seconds"] = 20.
    assert not coverage_checks(report)["real_wait_not_accelerated"]
    report = complete_report()
    report["model_dt_injected"] = True
    assert not coverage_checks(report)["real_wait_not_accelerated"]


def test_elapsed_time_without_actual_debit_and_grooming_cannot_pass():
    report = complete_report()
    report["meals"][1]["food_after_debit"] = 1
    report["meals"][1]["saw_grooming"] = False
    checks = coverage_checks(report)
    assert not checks["two_real_food_debits"] and not checks["both_meals_reached_grooming"]


def test_cooldown_violation_is_retained_even_if_final_meal_interval_is_valid():
    report = complete_report()
    report["cooldown_violations"] = [{"food": 0, "cooldown": 20}]
    assert not coverage_checks(report)["second_portion_preserved_during_cooldown"]


@pytest.mark.parametrize("key", ["collected_airborne", "pending_in_air", "responded_after_air"])
def test_airborne_case_needs_actual_deferred_response_evidence(key):
    report = complete_report()
    report["gifts"]["jump"][key] = False
    assert not coverage_checks(report)["airborne_gift_deferred_until_supported"]


@pytest.mark.parametrize("case", ["ordinary", "jump"])
def test_response_needs_three_captured_phases_and_full_timer(case):
    report = complete_report()
    check = "ordinary_gift_response" if case == "ordinary" else "airborne_gift_deferred_until_supported"
    report["gifts"][case]["screenshots"].pop("during")
    assert not coverage_checks(report)[check]
    report = complete_report()
    report["gifts"][case]["response_model_seconds"] = .3
    assert not coverage_checks(report)[check]


def test_emergency_cancel_must_stay_cancelled_after_release():
    report = complete_report()
    report["gifts"]["cancel"]["no_replay_after_release"] = False
    assert not coverage_checks(report)["emergency_cancelled_pending_response"]


@pytest.mark.parametrize("field,check", [
    ("fullscreen_canvas_seen", "no_global_cursor_override_or_fullscreen_capture"),
    ("global_cursor_override_seen", "no_global_cursor_override_or_fullscreen_capture"),
    ("journal_seen", "journal_never_created"), ("native_key_seen", "captured_cat_window_not_key"),
])
def test_safety_evidence_is_not_masked_by_behavior_success(field, check):
    report = complete_report(); report[field] = True
    assert not coverage_checks(report)[check]


def test_default_deadline_has_no_fast_forward_or_single_cat_memory_scenario():
    options = parse_args(["--platform", "offscreen"])
    assert options.timeout == 330
    assert options.output.parent.name == "v09"
    assert not hasattr(options, "quick_check")


@pytest.mark.parametrize("args", [["--timeout", "209"], ["--timeout", "331"],
                                   ["--timeout", "nan"], ["--child"]])
def test_invalid_deadline_or_unowned_child_rejected(args):
    with pytest.raises(SystemExit):
        parse_args(["--platform", "offscreen", *args])


def test_food_debit_alone_does_not_prove_a_natural_bowl_route():
    report = complete_report()
    report["second_meal_route_samples"] = []
    assert not coverage_checks(report)["second_meal_reached_bowl_naturally"]
    report = complete_report()
    report["meals"][1]["mouth_at_bowl"] = False
    assert not coverage_checks(report)["second_meal_reached_bowl_naturally"]
