"""Acceptance aggregation tests without starting any GUI/controller."""
from types import SimpleNamespace
import pytest
from scripts.probe_teaser_native import (
    final_state, interruption_checks, natural_checks, parse_args, rest_observation, watchdog_reason,
)


def good_case():
    active={'session_active':True,'global_override_none':True,'cursor_pixmap':True,
            'canvas_visible':True,'canvas_is_active_window':False,'model_active_id':'amber','session_error':None}
    final={**active,'session_active':False,'canvas_visible':False,'model_active_id':None,
           'timer_active':False,'rest':{'arrived':True}}
    return {'name':'natural','reason':'completed','active_seconds':15.04,
            'samples':[active.copy() for _ in range(30)]+[final], 'final':final}


def test_natural_completion_needs_real_duration_and_model_rest():
    case=good_case();assert all(natural_checks(case).values())
    case['active_seconds']=1
    assert not natural_checks(case)['real_15_second_completion']
    case=good_case();case['final']['rest']['arrived']=False
    assert not natural_checks(case)['model_rest_arrived']


@pytest.mark.parametrize('field,value,check',[
    ('global_override_none',False,'no_global_cursor_override'),
    ('canvas_is_active_window',True,'canvas_never_qt_active_window'),
    ('model_active_id',None,'model_and_session_agreed_during_play'),
    ('canvas_visible',False,'canvas_visible_during_play'),
    ('session_error','failed','no_session_error'),
])
def test_bad_sample_cannot_be_hidden_by_later_success(field,value,check):
    case=good_case();case['samples'][2][field]=value
    assert not natural_checks(case)[check]


def test_plain_idle_is_not_evidence_of_tired_rest():
    assert not rest_observation(SimpleNamespace(state='idle',arrival_state='idle'))['arrived']
    assert not rest_observation(SimpleNamespace(state='idle',teaser_rest_remaining=5))['arrived']
    assert rest_observation(SimpleNamespace(state='resting'))['arrived']
    assert rest_observation(SimpleNamespace(state='walking',arrival_state='resting'))['pending']
    assert not rest_observation(SimpleNamespace(state='walking',arrival_state='resting'))['arrived']


def test_interruption_must_really_remove_interception():
    case=good_case();case.update(name='right_click',reason='cancelled',active_seconds=.3)
    assert all(interruption_checks(case).values())
    case['final']['canvas_visible']=True
    assert not interruption_checks(case)['canvas_removed']


def test_only_finished_child_clean_exit_and_matching_sources_pass():
    assert final_state({'state':'checks_complete'},0,False,True)=='complete'
    for child,code,timed,stable in [({},0,False,True),({'state':'running'},0,False,True),
                                  ({'state':'checks_complete'},1,False,True),
                                  ({'state':'checks_complete'},0,True,True),
                                  ({'state':'checks_complete'},0,False,False)]:
        assert final_state(child,code,timed,stable)=='failed'


def test_watchdog_limits_owned_click_interceptor_and_startup():
    assert watchdog_reason(1,None) is None
    assert watchdog_reason(13,None)=='startup_without_checkpoint'
    assert watchdog_reason(18,4.1)=='event_loop_heartbeat_stale'
    assert watchdog_reason(39,0)=='overall_timeout'


def test_synthetic_mouse_events_are_offscreen_only():
    with pytest.raises(SystemExit):parse_args(['--interruptions-only'])
    options=parse_args(['--platform','offscreen','--interruptions-only'])
    assert options.interruptions_only and options.platform=='offscreen'


def test_cocoa_requires_actual_native_visibility_without_key_focus():
    case=good_case();case['platform']='cocoa'
    assert not natural_checks(case)['native_canvas_visible_and_not_key']
    for s in case['samples']:
        s['native_canvas']={'native_visible':True,'key':False}
    assert natural_checks(case)['native_canvas_visible_and_not_key']
    case['samples'][1]['native_canvas']['key']=True
    assert not natural_checks(case)['native_canvas_visible_and_not_key']
