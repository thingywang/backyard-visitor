"""Pure threshold/command tests; never launch the frozen application."""
from scripts.profile_bundle import assess_rss, scenario_command, summarize


def scenario(label, rss, **overrides):
    result = {
        "label": label, "exit_code": 0, "timed_out": False,
        "termination_requested": False, "sampling_errors": [],
        "samples": [{"rss_bytes": value, "phys_footprint_bytes": 40_000_000,
                     "uss_bytes": 50_000_000, "cpu_percent_one_core": 1.0} for value in rss],
    }
    result.update(overrides)
    return result


def report(closed=90_000_000, single=95_000_000, multi=140_000_000):
    return {"schema_version": 2, "state": "complete", "scenarios": [
        scenario("closed", [closed]), scenario("demo", [single]), scenario("multi", [multi]),
    ]}


def test_single_cat_over_100mb_cannot_borrow_multi_cat_allowance():
    value = assess_rss(report(single=110_000_000, multi=140_000_000))
    assert value["assessment"] == "exceeded_scenario_limit"
    assert value["by_scenario"]["demo"]["assessment"] == "exceeded_limit"
    assert value["by_scenario"]["multi"]["assessment"] == "within_limit_in_observed_samples"


def test_each_scenario_has_its_own_limit_and_inclusive_boundary():
    value = assess_rss(report(closed=100_000_000, single=100_000_000, multi=150_000_000))
    assert value["assessment"] == "within_scenario_limits_in_all_observed_samples"
    assert value["by_scenario"]["closed"]["rss_limit_bytes"] == 100_000_000
    assert value["by_scenario"]["demo"]["rss_limit_bytes"] == 100_000_000
    assert value["by_scenario"]["multi"]["rss_limit_bytes"] == 150_000_000


def test_multi_cat_over_150mb_fails_despite_low_footprint():
    value = assess_rss(report(multi=150_000_001))
    assert value["assessment"] == "exceeded_scenario_limit"
    assert value["by_scenario"]["multi"]["assessment"] == "exceeded_limit"


def test_closed_gate_still_uses_100mb():
    value = assess_rss(report(closed=100_000_001))
    assert value["assessment"] == "exceeded_scenario_limit"
    assert value["by_scenario"]["closed"]["assessment"] == "exceeded_limit"


def test_all_samples_count_including_startup_or_departure_peak():
    value = report()
    value["scenarios"][2] = scenario("multi", [50_000_000, 151_000_000, 120_000_000])
    assert assess_rss(value)["assessment"] == "exceeded_scenario_limit"


def test_missing_multi_scenario_cannot_pass_v2():
    value = report()
    value["scenarios"].pop()
    assert assess_rss(value)["assessment"] == "inconclusive"


def test_empty_null_or_invalid_rss_cannot_be_replaced_by_footprint():
    for values in ([], [None], [float("nan")], [-1], [True]):
        value = report()
        value["scenarios"][1] = scenario("demo", values)
        assert assess_rss(value)["assessment"] == "inconclusive"


def test_sampling_failure_or_timeout_cannot_pass():
    for override in ({"exit_code": 2}, {"timed_out": True},
                     {"termination_requested": True}, {"sampling_errors": [{"type": "AccessDenied"}]}):
        value = report()
        value["scenarios"][1].update(override)
        assert assess_rss(value)["assessment"] == "inconclusive"


def test_duplicate_scenario_cannot_hide_a_missing_or_failed_run():
    value = report()
    value["scenarios"].append(scenario("demo", [80_000_000]))
    assert assess_rss(value)["assessment"] == "inconclusive"


def test_legacy_two_scenario_report_remains_readable():
    value = report()
    value["schema_version"] = 1
    value["scenarios"].pop()
    assert assess_rss(value)["assessment"] == "within_scenario_limits_in_all_observed_samples"
    assert "no enforced one-cat limit" in assess_rss(value)["population_scope_note"]
    value["scenarios"][1]["samples"][0]["rss_bytes"] = 105_000_000
    assert assess_rss(value)["assessment"] == "exceeded_scenario_limit"


def test_summaries_keep_distinct_metrics_and_legacy_100mb_diagnostic():
    rows = scenario("multi", [140_000_000])["samples"]
    summary = summarize(rows, rss_target_bytes=150_000_000)
    assert summary["rss_bytes"]["max_bytes"] == 140_000_000
    assert summary["phys_footprint_bytes"]["max_bytes"] == 40_000_000
    assert summary["rss_target_bytes"] == 150_000_000
    assert summary["rss_within_target_in_observed_samples"]
    assert not summary["rss_within_100mb_in_observed_samples"]
    assert summarize(rows, rss_target_bytes=None)["rss_within_target_in_observed_samples"] is None


def test_multi_command_uses_fight_demo_and_isolated_state():
    multi = scenario_command("multi", 40, "/bundle/Visitor", "/tmp/test-state")
    assert multi == ["/bundle/Visitor", "--hidden", "--smoke-seconds", "40",
                     "--state-dir", "/tmp/test-state", "--demo", "--demo-cat-limit", "2", "--demo-scene", "fight"]
    single = scenario_command("demo", 40, "/bundle/Visitor", "/tmp/test-state")
    assert "--demo" in single and "--demo-scene" not in single
    assert single[single.index("--demo-cat-limit") + 1] == "1"
    assert "--demo" not in scenario_command("closed", 12, "/bundle/Visitor", "/tmp/test-state")


import json
from types import SimpleNamespace
import pytest
from scripts import profile_bundle as profiler


def selected_report():
    return {"schema_version": 3, "requested_scenarios": ["multi"], "state": "complete",
            "scenarios": [scenario("multi", [145_000_000])]}


def test_schema3_multi_only_can_pass_without_closed_or_single_measurements():
    value = assess_rss(selected_report())
    assert value["assessment"] == "within_scenario_limits_in_all_observed_samples"
    assert set(value["by_scenario"]) == {"multi"}
    assert value["by_scenario"]["multi"]["rss_limit_bytes"] == 150_000_000


@pytest.mark.parametrize("selection", [None, [], ["multi", "multi"], ["unknown"], [None], [["multi"]]])
def test_bad_or_missing_requested_scope_cannot_pass(selection):
    value = selected_report(); value["requested_scenarios"] = selection
    assert assess_rss(value)["assessment"] == "inconclusive"


def test_extra_or_missing_scenario_cannot_silently_change_selected_scope():
    value = selected_report(); value["scenarios"].append(scenario("closed", [80_000_000]))
    assert assess_rss(value)["assessment"] == "inconclusive"
    value = selected_report(); value["requested_scenarios"] = ["demo", "multi"]
    assert assess_rss(value)["assessment"] == "inconclusive"


def test_selected_multi_still_uses_rss_150_not_footprint_or_single_limit():
    value = selected_report(); value["scenarios"][0] = scenario("multi", [150_000_001])
    assert assess_rss(value)["assessment"] == "exceeded_scenario_limit"
    value["scenarios"][0] = scenario("multi", [150_000_000])
    assert assess_rss(value)["assessment"] == "within_scenario_limits_in_all_observed_samples"


def test_cli_selection_is_explicit_and_default_preserves_all_scenarios(monkeypatch):
    monkeypatch.setattr(profiler.sys, "platform", "darwin")
    defaults = profiler.parse_args([])
    assert profiler.selected_scenarios(defaults) == [("closed", 12), ("demo", 40), ("multi", 40)]
    only = profiler.parse_args(["--scenarios", "multi", "--multi-seconds", "40"])
    assert profiler.selected_scenarios(only) == [("multi", 40)]
    with pytest.raises(SystemExit):
        profiler.parse_args(["--scenarios", "multi", "multi"])


def test_worker_only_calls_requested_multi_never_closed_or_demo(monkeypatch, tmp_path):
    options = SimpleNamespace(scenarios=["multi"], multi_seconds=40, interval=1,
                              output=tmp_path/"profile.json", bundle=tmp_path/"app")
    called = []
    details = {"version": "0.9.0"}
    def fake_run(label, seconds, executable, opts, report):
        called.append((label, seconds)); report["scenarios"].append(scenario(label, [145_000_000])); return True
    monkeypatch.setattr(profiler, "run_scenario", fake_run)
    monkeypatch.setattr(profiler, "bundle_details", lambda bundle: ("fake-executable", details))
    assert profiler.worker(options, "fake-executable", details) == 0
    assert called == [("multi", 40)]
    value = json.loads(options.output.read_text())
    assert value["requested_scenarios"] == ["multi"] and value["schema_version"] == 3
    assert value["rss_target_bytes"] == 150_000_000
    assert value["rss_target_assessment"] == "within_scenario_limits_in_all_observed_samples"


def cg(wid, x=50, y=100, width=144, height=112, pid=42, **extra):
    return {"kCGWindowNumber": wid, "kCGWindowOwnerPID": pid, "kCGWindowIsOnscreen": True,
            "kCGWindowAlpha": 1, "kCGWindowName": "must not appear in report",
            "kCGWindowBounds": {"X": x, "Y": y, "Width": width, "Height": height}, **extra}


def test_metadata_only_keeps_own_numeric_candidate_window_fields():
    rows = [cg(1), cg(2, x=300), cg(3, pid=99), cg(4, width=30, height=30)]
    value = profiler.own_cat_window_metadata(rows, 42, [[0,0,1000,800]])
    assert value["max_count_on_one_display"] == 2
    assert value["fully_contained_window_ids_by_display"] == {"0": [1,2]}
    assert "must not appear" not in json.dumps(value)
    assert "kCGWindowName" not in json.dumps(value)


def test_metadata_excludes_partial_hidden_invalid_and_duplicate_windows():
    rows = [cg(1), cg(1), cg(2, x=950), cg(3, kCGWindowAlpha=0),
            cg(4, kCGWindowIsOnscreen=False), cg(5, x=float("nan"))]
    value = profiler.own_cat_window_metadata(rows, 42, [[0,0,1000,800]])
    assert value["max_count_on_one_display"] == 1
    assert {w["window_id"] for w in value["candidate_windows"]} == {1,2}


def test_negative_screens_supported_but_one_window_per_screen_is_not_coexistence():
    displays = [[-1000,-200,1000,800],[0,0,1000,800]]
    split = profiler.own_cat_window_metadata([cg(1,x=-900,y=-100),cg(2)],42,displays)
    assert split["max_count_on_one_display"] == 1
    same = profiler.own_cat_window_metadata([cg(1,x=-900,y=-100),cg(2,x=-700,y=-100)],42,displays)
    assert same["max_count_on_one_display"] == 2


def test_window_summary_does_not_claim_cat_pixels_and_gaps_break_continuity():
    meta = profiler.own_cat_window_metadata([cg(1),cg(2,x=300)],42,[[0,0,1000,800]])
    rows = [{"elapsed_seconds": n,"window_metadata": meta} for n in (1,2,3,10)]
    value = profiler.summarize_window_evidence(rows)
    assert value["assessment"] == "two_cat_sized_windows_coobserved"
    assert value["coobserved_two_window_samples"] == 4
    assert value["max_consecutive_same_pair_interval_seconds"] == 2
    assert value["cat_pixels_verified"] is False
    assert profiler.summarize_window_evidence([{"elapsed_seconds":0}])["assessment"] == "unavailable"


def test_changed_pair_or_missing_sample_does_not_extend_same_pair_interval():
    first = profiler.own_cat_window_metadata([cg(1),cg(2,x=300)],42,[[0,0,1000,800]])
    second = profiler.own_cat_window_metadata([cg(3),cg(4,x=300)],42,[[0,0,1000,800]])
    rows = [{"elapsed_seconds": n,"window_metadata": m} for n,m in enumerate((first,first,second,{},second))]
    assert profiler.summarize_window_evidence(rows)["max_consecutive_same_pair_interval_seconds"] == 1
