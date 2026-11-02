"""Pure acceptance checks. Importing the probe never starts Qt or a process."""
import pytest

from scripts.probe_background_teaser import final_checks, frontmost_role, parse_args, sample_checks, watchdog


def sample(phase="play", stamp=100):
    bounds = {"X":0,"Y":0,"Width":1000,"Height":800}
    names = ("gate","bowl","cat","canvas","hint") if phase=="play" else ("gate","bowl","cat")
    windows = {name:{"qt_visible":True,"native_visible":True,"key":False,"level":8,
        "cg":{"order":i,"alpha":1,"bounds":{"X":i*100,"Y":200,"Width":80,"Height":60}}} for i,name in enumerate(names)}
    return {"phase":phase,"monotonic":stamp,"controller_active":False,"frontmost_role":"helper",
        "helper_active":True,"helper_heartbeat_age":.05,"helper":{"key":True,"level":0,"cg":{"order":10,"layer":0,"alpha":1,"bounds":bounds}},
        "windows":windows,"global_override_none":True,"bin_visible":False,"cat_fully_inside":True,
        "local_cursor_bitmap":True,"pointer_outside_screen":False,"session_active":True,"model_active_id":"amber"}


def report():
    return {"samples":[*(sample("baseline",98+i*.25) for i in range(4)),
                        *(sample("play",100+i*.25) for i in range(60)),
                        *(sample("cleanup",115+i*.25) for i in range(12))],
        "finished_reason":"completed","play_started_monotonic":100,"play_ended_monotonic":115,
        "controller_activated_after_helper":False,"clock_acceleration_seen":False,
        "cleanup":{k:True for k in ("session_removed","model_stopped","canvas_deleted","hint_deleted","timer_stopped","cursor_binding_removed","native_windows_absent","global_override_none")}}


def test_full_real_background_coverage_passes_only_with_cleanup():
    assert all(sample_checks(sample()).values())
    assert all(final_checks(report()).values())


@pytest.mark.parametrize("field,value,check",[
    ("controller_active",True,"controller_inactive"),
    ("frontmost_role","other","helper_frontmost"),
    ("frontmost_role","controller","helper_frontmost"),
    ("helper_active",False,"helper_active_key_and_normal"),
    ("helper_heartbeat_age",1.1,"helper_fresh"),
    ("global_override_none",False,"global_cursor_untouched"),
    ("cat_fully_inside",False,"cat_inside_selected_screen"),
])
def test_actual_inactive_helper_and_fresh_evidence_required(field,value,check):
    row=sample();row[field]=value
    assert sample_checks(row)[check] is False


@pytest.mark.parametrize("name",["gate","bowl","cat","canvas","hint"])
def test_missing_native_window_cannot_pass(name):
    row=sample();row["windows"][name]["cg"]={}
    assert not sample_checks(row)[name+"_visible"]
    assert not sample_checks(row)[name+"_above_helper"]


def test_metadata_visibility_is_not_enough_when_below_or_key():
    row=sample();row["windows"]["canvas"]["cg"]["order"]=11
    row["windows"]["hint"]["key"]=True
    assert not sample_checks(row)["canvas_above_helper"]
    assert not sample_checks(row)["hint_not_key"]


def test_one_focus_steal_cannot_be_erased_by_later_good_samples():
    r=report();r["samples"][8]["controller_active"]=True
    assert not final_checks(r)["all_background_samples_valid"]
    r=report();r["controller_activated_after_helper"]=True
    assert not final_checks(r)["no_controller_reactivation"]


@pytest.mark.parametrize("field",["canvas_deleted","hint_deleted","native_windows_absent","timer_stopped","cursor_binding_removed"])
def test_end_requires_actual_resource_and_native_window_removal(field):
    r=report();r["cleanup"][field]=False
    assert not final_checks(r)["session_canvas_hint_and_timer_removed"]


def test_duration_and_coverage_do_not_accept_an_accelerated_or_sparse_game():
    r=report();r["play_ended_monotonic"]=101
    assert not final_checks(r)["natural_full_15_seconds"]
    r=report();r["samples"]=[s for s in r["samples"] if not(s["phase"]=="play" and 107<s["monotonic"]<109)]
    assert not final_checks(r)["play_sample_coverage"]
    r=report();r["clock_acceleration_seen"]=True
    assert not final_checks(r)["only_normal_ticks"]
    assert not all(final_checks({}).values())


def test_cursor_can_leave_selected_screen_without_global_override():
    row=sample();row.update(local_cursor_bitmap=False,pointer_outside_screen=True)
    assert sample_checks(row)["local_cursor_scoped"]
    row["pointer_outside_screen"]=False
    assert not sample_checks(row)["local_cursor_scoped"]


def test_frontmost_metadata_redacts_foreign_pids():
    assert frontmost_role(100,100,101)=="controller"
    assert frontmost_role(101,100,101)=="helper"
    assert frontmost_role(123456,100,101)=="other"


def test_deadline_reserves_cleanup_and_heartbeat_failures_do_not_linger():
    assert watchdog(1,None,None,None) is None
    assert watchdog(9,None,None,None)=="controller_startup_without_checkpoint"
    assert watchdog(12,4.1,.1,1)=="controller_heartbeat_stale"
    assert watchdog(10,.1,None,2)=="helper_startup_without_checkpoint"
    assert watchdog(10,.1,3.1,2)=="helper_heartbeat_stale"
    assert watchdog(50,.1,.1,2)=="overall_deadline"


def test_outputs_cannot_escape_project_output_directory():
    with pytest.raises(SystemExit):parse_args(["--output","/tmp/not-allowed.json"])
    assert parse_args([]).output.name=="background-teaser.json"


@pytest.mark.parametrize("duration", [14.6, 14.99])
def test_wall_clock_early_completed_reason_cannot_replace_full_monotonic_duration(duration):
    r=report();r["play_ended_monotonic"]=100+duration
    r["samples"]=[row for row in r["samples"] if row["phase"]!="play" or row["monotonic"]<=100+duration]
    assert not final_checks(r)["natural_full_15_seconds"]


def test_native_fullscreen_requires_native_mask_active_space_and_full_geometry():
    from scripts.probe_background_teaser import fullscreen_checks
    row=sample();row['fullscreen_required']=True;row['screen_geometry']=[0,0,1000,800]
    row['helper'].update(style_mask=1<<14,on_active_space=True)
    for w in row['windows'].values():w['on_active_space']=True
    assert all(sample_checks(row).values())
    row['windows']['cat']['on_active_space']=False
    assert not sample_checks(row)['cat_on_active_space']
    row['windows']['cat']['on_active_space']=True
    for change in ({'style_mask':0},{'on_active_space':False}):
        helper=dict(row['helper'],**change)
        assert not all(fullscreen_checks(helper,row['screen_geometry']).values())
    # Ordinary maximized/borderless and wrong-monitor windows cannot qualify.
    helper=dict(row['helper']);helper['cg']=dict(helper['cg'],bounds={'X':0,'Y':24,'Width':1000,'Height':776})
    assert not all(fullscreen_checks(helper,row['screen_geometry']).values())
    assert not all(fullscreen_checks(row['helper'],[1000,0,1000,800]).values())


def test_fullscreen_mode_is_explicit_and_keeps_output_scope():
    assert not parse_args([]).fullscreen
    assert parse_args(['--fullscreen']).fullscreen


def test_external_helper_app_cannot_be_launched_by_probe():
    with pytest.raises(SystemExit):
        parse_args(['--helper-app','/Applications/Unrelated.app'])


def test_fullscreen_request_waits_for_owned_active_key_visible_window():
    from scripts.probe_background_teaser import native_fullscreen_request_ready
    window={"key":True,"native_visible":True}
    assert native_fullscreen_request_ready(True,42,42,window,False)
    assert not native_fullscreen_request_ready(False,42,42,window,False)
    assert not native_fullscreen_request_ready(True,43,42,window,False)
    assert not native_fullscreen_request_ready(True,42,42,{"key":False,"native_visible":True},False)
    assert not native_fullscreen_request_ready(True,42,42,{"key":True,"native_visible":False},False)
    assert not native_fullscreen_request_ready(True,42,42,window,True)


def test_activation_requires_exact_popen_helper_identity():
    from scripts.probe_background_teaser import owned_helper_identity
    exe="/own/Helper.app/Contents/MacOS/BackyardFullscreenHelper"
    heartbeat={"pid":42,"frozen":True,"executable":exe,"window":{"window_id":9}}
    assert owned_helper_identity(heartbeat,42,exe)
    for change in ({"pid":43},{"frozen":False},{"executable":"/other/helper"},{"window":{}}):
        assert not owned_helper_identity(dict(heartbeat,**change),42,exe)
