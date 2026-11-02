"""Observer contract tests, without launching a GUI or touching desktop settings."""
import pytest

from scripts.check_native_lifecycle import (
    final_state, parse_args, resource_growth, watchdog_reason, write_json, read_json,
)


def test_only_completed_checks_clean_exit_and_stable_sources_pass():
    done = {"state": "checks_complete"}
    assert final_state(done, 0, None, True) == "complete"
    for child, code, reason, stable in ((None, 0, None, True),
                                       ({"state": "running"}, 0, None, True),
                                       (done, 1, None, True),
                                       (done, 0, "overall_timeout", True),
                                       (done, 0, None, False)):
        assert final_state(child, code, reason, stable) == "failed"


def test_deadline_and_heartbeat_distinguish_starting_from_hung():
    assert watchdog_reason(1, None, 210) is None
    assert watchdog_reason(21, None, 210) == "startup_without_checkpoint"
    assert watchdog_reason(40, 0.5, 210) is None
    assert watchdog_reason(40, 12.1, 210) == "event_loop_heartbeat_stale"
    assert watchdog_reason(211, 0.1, 210) == "overall_timeout"


def test_resource_growth_detects_orphan_classes_without_net_count_masking():
    assert resource_growth({"QLabel": 5, "Journal": 0}, {"QLabel": 4, "Journal": 1}) == {"Journal": 1}
    assert resource_growth({"QLabel": 5}, {"QLabel": 4}) == {}


def test_quick_check_is_explicitly_offscreen_and_omits_endurance():
    options = parse_args(["--quick-check"])
    assert options.platform == "offscreen"
    assert options.mouse_rounds == 0 and options.minimum_seconds == 0
    assert options.journal_cycles == 2 and options.timeout == 20


@pytest.mark.parametrize("args", [
    ["--timeout", "nan"], ["--timeout", "-1"],
    ["--minimum-seconds", "240"], ["--journal-cycles", "1"],
    ["--mouse-rounds", "-1"], ["--child"],
])
def test_invalid_or_unowned_child_configuration_rejected(args):
    with pytest.raises(SystemExit):
        parse_args(["--platform", "offscreen", *args])


def test_atomic_checkpoints_retain_state_and_missing_or_bad_is_not_success(tmp_path):
    path = tmp_path / "report.json"
    assert read_json(path) is None
    write_json(path, {"state": "running", "heartbeat_count": 12})
    assert read_json(path) == {"state": "running", "heartbeat_count": 12}
    assert not list(tmp_path.glob("*.tmp"))
    path.write_text("partial", encoding="utf-8")
    assert read_json(path) is None


def test_default_duration_covers_three_minutes_without_unbounded_wait():
    options = parse_args(["--platform", "offscreen"])
    assert 180 <= options.minimum_seconds < options.timeout <= 240
    assert options.mouse_rounds == 2
    assert options.output.name == "native-lifecycle.json" and options.output.parent.name == "v08"


def covered_report():
    return {
        "platform_plugin":"cocoa",
        "mouse_rounds":[dict(saw_idle=True,saw_escaping=True,removed=True,active_pause_verified=True,drained=True)],
        "multi_cat":dict(peak_visitors=2,peak_visible=2,simultaneous_observed_seconds=3.5),
        "interaction_rounds":[dict(model_started=True,model_stopped=True,scratch_released=True,
                                   qt_click_through=True,native_ignores_mouse_events=True)],
        "journal_cycles_completed":2,
        "journal_focus_samples":[dict(not_qt_active=True,native_not_key=True) for _ in range(2)],
        "fullscreen_canvas_seen":False,"global_cursor_override_seen":False,
    }


def test_success_needs_complete_resource_and_behavior_coverage():
    from scripts.check_native_lifecycle import coverage_checks
    assert all(coverage_checks(covered_report(), 1).values())
    assert not coverage_checks(covered_report(), 2)["requested_mouse_rounds"]


@pytest.mark.parametrize("failure,check",[
    ("coexistence","two_cats_observed_together"),
    ("escape","all_mice_waited_escaped_and_drained"),
    ("model_stop","all_short_model_interactions_finished"),
    ("scratch_native","scratch_click_through_verified"),
    ("scratch_lifetime","all_short_model_interactions_finished"),
    ("focus","journal_did_not_take_focus"),
    ("missing_focus","journal_did_not_take_focus"),
    ("canvas","no_fullscreen_interaction_canvas"),
    ("global_cursor","no_global_cursor_override"),
])
def test_missing_evidence_never_becomes_endurance_success(failure,check):
    from scripts.check_native_lifecycle import coverage_checks
    report=covered_report()
    if failure=="coexistence": report["multi_cat"]["simultaneous_observed_seconds"]=0
    elif failure=="escape":report["mouse_rounds"][0]["saw_escaping"]=False
    elif failure=="model_stop":report["interaction_rounds"][0]["model_stopped"]=False
    elif failure=="scratch_native":report["interaction_rounds"][0]["native_ignores_mouse_events"]=False
    elif failure=="scratch_lifetime":report["interaction_rounds"][0]["scratch_released"]=False
    elif failure=="focus":report["journal_focus_samples"][1]["native_not_key"]=False
    elif failure=="missing_focus":report["journal_focus_samples"]=[]
    elif failure=="canvas":report["fullscreen_canvas_seen"]=True
    elif failure=="global_cursor":report["global_cursor_override_seen"]=True
    assert coverage_checks(report,1)[check] is False


def test_native_scratch_read_uses_own_nswindow_and_exact_selector():
    import ctypes
    from scripts.check_native_lifecycle import native_ignores_mouse_events
    calls=[]
    class Bridge:
        def window(self, widget):
            assert widget is target
            return 91
        def send(self, pointer, selector, result):
            calls.append((pointer,selector,result))
            return True
    target=object()
    assert native_ignores_mouse_events(Bridge(),target)
    assert calls==[(91,"ignoresMouseEvents",ctypes.c_bool)]


def test_journal_helper_never_activates_or_raises():
    from scripts.check_native_lifecycle import show_without_activating
    from PyQt6.QtCore import Qt
    calls=[]
    class Journal:
        def setAttribute(self,*args):calls.append(("attribute",*args))
        def show(self):calls.append(("show",))
        def lower(self):calls.append(("lower",))
        def activateWindow(self):raise AssertionError("Must not steal focus")
        def raise_(self):raise AssertionError("Must not raise above user's app")
    show_without_activating(Journal())
    assert calls[0]==("attribute",Qt.WidgetAttribute.WA_ShowWithoutActivating,True)
    assert ("show",) in calls and ("lower",) in calls
