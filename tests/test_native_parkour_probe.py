"""Pure contract checks; native GUI processes are mocked or never constructed."""
from types import SimpleNamespace
import json
import pytest
from scripts import probe_native_parkour as probe


@pytest.mark.parametrize('bounds,cat_x', [([0,30,1920,985],800), ([-1200,-300,1200,800],-800), ([0,0,700,540],500)])
def test_layout_fits_one_screen_and_does_not_cover_lower_cat_head_space(bounds,cat_x):
    layout=probe.helper_layout(bounds,cat_x);left,top,w,h=bounds
    for x,y,width,height in layout.values():
        assert left<=x and top+112<=y and x+width<=left+w and y+height<=top+h
    a,b=layout['A'],layout['B']
    assert 0<a[1]-b[1]<=220
    assert a[1]-(b[1]+b[3])>=112
    assert b[0]+b[2]+24<=left+w
    assert b[1]+20+b[3]<=top+h


@pytest.mark.parametrize('bounds', [[0,0,699,800],[0,0,900,539]])
def test_small_screens_fail_explicitly_instead_of_moving_other_windows(bounds):
    with pytest.raises(ValueError):probe.helper_layout(bounds,100)


def route_rows():
    base=dict(state='walking',x=100.,y=500.,takeoff_remaining=0.,jump_elapsed=0.,landing_remaining=0.,
              target_x=264.,jump_start_x=200.,jump_start_y=500.,window_edge_id='A',route_stage='approach')
    rows=[base,dict(base,x=200.)]
    rows.append(dict(base,state='jumping',x=200.,takeoff_remaining=.15,route_stage='jump'))
    rows.append(dict(base,state='jumping',x=230.,y=400.,jump_elapsed=.2,route_stage='jump'))
    rows.append(dict(base,state='idle',x=264.,y=310.,landing_remaining=.2,window_edge_id='B',route_stage=''))
    rows.append(dict(rows[-1],landing_remaining=0.))
    return rows


def test_complete_real_route_observations_pass():
    assert all(probe.route_checks(route_rows(),'B',264).values())


@pytest.mark.parametrize('omitted,check', [('charge','charge_observed'),('air','air_observed'),('landing','landing_observed')])
def test_missing_transition_observation_does_not_pass(omitted,check):
    rows=route_rows()
    if omitted=='charge':rows=[r for r in rows if r['takeoff_remaining']==0]
    if omitted=='air':rows=[r for r in rows if r['jump_elapsed']==0]
    if omitted=='landing':rows=[r for r in rows if r['landing_remaining']==0]
    assert not probe.route_checks(rows,'B',264)[check]


def test_horizontal_hop_limit_and_actual_destination_are_required():
    rows=route_rows();rows[3]['target_x']=265.
    assert not probe.route_checks(rows,'B',264)['short_horizontal_air_route']
    assert not probe.route_checks(route_rows(),'closed-window',264)['arrived_at_native_edge']
    assert not probe.route_checks(route_rows(),'B',300)['arrived_at_native_edge']


def completed_report():
    routes={key:{'samples':route_rows(),'checks':probe.route_checks(route_rows(),'B',264)} for key in ('A','B')}
    return dict(routes=routes,native_poll_count=5,foreign_target_seen=False,moved_rect_matches=True,
        moved_support_reached=True,native_replans=[{'position_unchanged':True}],B_closed_absent=True,returned_to_A=True,
        A_closed_absent=True,returned_to_floor=True,widget_mismatch_seen=False,settled_native_geometry_count=5,
        model_dt_injected=False,clock_acceleration_seen=False,input_capture_seen=False,journal_seen=False,
        key_window_seen=False,shutdown_verified=True,
        dialog_helper=dict(qt_class='QDialog',qt_dialog=True,modal=False,native_level=0,
                           cg_layer=0,native_visible=True,native_style_mask=1))


def test_end_to_end_claim_requires_route_and_cleanup_evidence():
    assert all(probe.final_checks(completed_report()).values())
    value=completed_report();value['routes']['B']['checks']={}
    assert not probe.final_checks(value)['both_native_routes_completed']
    value=completed_report();value['routes']['B']['samples']=[route_rows()[-1]]
    assert not probe.final_checks(value)['second_route_approach_walk_observed']
    value=completed_report();value['native_replans'][0]['position_unchanged']=False
    assert not probe.final_checks(value)['native_replans_do_not_teleport']


@pytest.mark.parametrize('key,check,value', [
    ('foreign_target_seen','only_helper_window_targets',True),
    ('native_poll_count','only_helper_window_targets',0),
    ('model_dt_injected','normal_tick_only',True),
    ('clock_acceleration_seen','normal_tick_only',True),
    ('settled_native_geometry_count','settled_cat_native_geometry_verified',0),
    ('B_closed_absent','closed_B_absent_and_left_safely',False),
    ('returned_to_floor','closed_A_absent_and_returned_to_floor',False),
    ('input_capture_seen','no_input_capture_or_journal',True),
    ('key_window_seen','own_windows_not_key_in_samples',True),
    ('shutdown_verified','clean_controller_shutdown',False),
])
def test_specific_scope_or_lifecycle_failure_is_not_hidden(key,check,value):
    report=completed_report();report[key]=value
    assert not probe.final_checks(report)[check]


def test_controller_startup_failure_stops_only_owned_helper_and_preserves_failure(monkeypatch,tmp_path):
    class OwnHelper:
        pid=123456
        def __init__(self):self.returncode=None;self.stopped=False
        def poll(self):return self.returncode
        def terminate(self):self.stopped=True;self.returncode=-15
        def wait(self,timeout):return self.returncode
    helper=OwnHelper();calls=[]
    def popen(args,**kwargs):
        calls.append(args)
        if '--helper' in args:return helper
        raise OSError('controlled startup failure')
    monkeypatch.setattr(probe.subprocess,'Popen',popen)
    monkeypatch.setattr(probe,'hashes',lambda:{'fixture':'unchanged'})
    options=SimpleNamespace(output=tmp_path/'report.json')
    assert probe.supervise(options)==1
    result=json.loads(options.output.read_text())
    assert helper.stopped and result['state']=='failed'
    assert result['helper_exit_code']==-15 and 'controlled startup failure' in result['error']
    assert len(calls)==2 and not list(tmp_path.glob('backyard-native-parkour-*'))


def test_transition_recording_preserves_each_observed_frame_without_synthesizing():
    report = {}
    rows = route_rows()
    for row in rows:
        probe.record_transition(report, "follow_moved_B", row)
    probe.record_transition(report, "B_closed", rows[-1])
    probe.record_transition(report, "A_closed", None)
    probe.record_transition(report, "entry", rows[0])
    assert report["transition_samples"] == {"follow_moved_B": rows, "B_closed": [rows[-1]]}
    assert report["transition_samples"]["follow_moved_B"][0] is rows[0]


@pytest.mark.parametrize('frame,client,target,expected', [
    ([10,20,400,100],[10,48,400,72],[80,90,400,70],[80,118,400,42]),
    ([-700,-300,400,100],[-699,-272,398,71],[-600,-250,400,70],[-599,-222,398,41]),
    ([10,20,400,70],[10,20,400,70],[30,40,400,70],[30,40,400,70]),
])
def test_frame_command_accounts_for_native_titlebar(frame,client,target,expected):
    assert probe.client_rect_for_frame(frame,client,target)==expected


def test_frame_smaller_than_native_titlebar_is_rejected():
    with pytest.raises(ValueError):
        probe.client_rect_for_frame([0,0,400,70],[0,28,400,42],[0,0,400,20])


@pytest.mark.parametrize('field,value', [
    ('qt_class','QWidget'),('qt_dialog',False),('modal',True),('native_level',3),
    ('cg_layer',None),('native_visible',False),('native_style_mask',0),
])
def test_dialog_coverage_requires_native_decorated_dialog_evidence(field,value):
    report=completed_report(); report['dialog_helper'][field]=value
    assert not probe.final_checks(report)['decorated_nonmodal_dialog_observed']


def test_missing_dialog_metadata_cannot_be_counted_as_dialog_coverage():
    report=completed_report(); report.pop('dialog_helper')
    assert not probe.final_checks(report)['decorated_nonmodal_dialog_observed']
