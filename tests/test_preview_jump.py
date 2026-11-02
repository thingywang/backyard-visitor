"""Preview contracts: actual supported routes, short hops, no coordinate writes."""
import pytest
from backyard_visitor.model import SPRITE_HEIGHT, SPRITE_WIDTH, WINDOW_JUMP_X
from scripts.preview_jump import DT, FRAME_NAMES, render_offset, stage_case


def test_charge_uses_source_support_not_pending_destination():
    row = {'takeoff_remaining': .1, 'takeoff_edge_id': None,
           'window_edge_id': None, 'window_target_id': 'target'}
    assert render_offset(row) == 0
    row['takeoff_edge_id'] = 'source'
    assert render_offset(row) == 6
    row['takeoff_remaining'] = 0
    row['takeoff_edge_id'] = None
    assert render_offset(row) == 6


@pytest.mark.parametrize('kind', ['floor_to_window', 'window_to_window'])
@pytest.mark.parametrize('facing', [-1, 1])
def test_trajectories_reach_each_required_pose_by_ticks(kind, facing):
    case = stage_case(kind, facing)
    assert case['natural_entry_model_seconds'] > 0
    assert [f['label'] for f in case['frames']] == list(FRAME_NAMES)
    frames = {f['label']:f for f in case['frames']}
    path = case['trajectory']
    approach = [r for r in path if r['phase'] == 'approach']
    charge = [r for r in path if r['phase'] == 'charge']
    air = [r for r in path if r['phase'] == 'air']
    landing = [r for r in path if r['phase'] == 'landing']
    finish = [r for r in path if r['phase'] == 'finish']
    assert approach and charge and air and landing and finish
    source, takeoff, contact, target = (case[name] for name in (
        'source_position','takeoff_position','landing_position','target_position'))
    assert (path[0]['x'],path[0]['y']) == tuple(source)
    assert takeoff != source  # The route no longer launches from its initial x.
    assert (takeoff[0]-source[0])*facing > 100
    assert 0 < case['horizontal_jump_distance'] <= WINDOW_JUMP_X
    assert case['horizontal_jump_distance'] == pytest.approx(abs(contact[0]-takeoff[0]))
    assert (target[0]-contact[0])*facing > 0  # Real horizontal finish after contact.

    assert all((r['x'],r['y']) == tuple(takeoff) for r in charge)
    assert all(r['jump_elapsed'] == 0 and r['takeoff_edge_id'] == case['source_edge_id']
               for r in charge)
    assert all(r['facing'] == facing for r in charge+air+landing+finish)
    assert all(r['y'] == source[1] for r in approach)
    assert all(r['y'] == target[1] for r in landing+finish)
    assert all((r['x'],r['y']) == tuple(contact) for r in landing)
    # Ground approach/finish is supported by the actual source/target interval.
    edge_map = {edge[0]:edge[1:] for edge in case['edges']}
    if case['source_edge_id']:
        left,right,top = edge_map[case['source_edge_id']]
        assert all(left <= r['x'] <= right-SPRITE_WIDTH and r['window_edge_id']=='source'
                   and r['y']+SPRITE_HEIGHT == top for r in approach)
    else:
        left,_,width,_ = case['bounds']
        assert all(left <= r['x'] <= left+width-SPRITE_WIDTH and r['window_edge_id'] is None
                   for r in approach)
    left,right,top = edge_map['target']
    assert all(left <= r['x'] <= right-SPRITE_WIDTH and r['window_edge_id']=='target'
               and r['y']+SPRITE_HEIGHT == top for r in landing+finish)

    assert any(r['z_arc_lift'] > 0 for r in air)
    for row in air:
        phase = row['flight_progress']
        assert row['jump_start_x'] == takeoff[0] and row['jump_start_y'] == takeoff[1]
        assert abs(row['target_x']-row['jump_start_x']) <= WINDOW_JUMP_X
        assert row['x'] == pytest.approx(row['jump_start_x']+(row['target_x']-row['jump_start_x'])*phase)
        expected_y = max(case['bounds'][1],row['jump_start_y']+
                         (row['target_y']-row['jump_start_y'])*phase-
                         row['jump_arc']*4*phase*(1-phase))
        assert row['y'] == pytest.approx(expected_y)
    # Consecutive real ticks cannot hide a teleport or diagonal ground slide.
    for a,b in zip(path,path[1:]):
        assert 0 < b['elapsed_seconds']-a['elapsed_seconds'] <= DT+1e-6
        assert abs(b['x']-a['x']) <= 66*DT+1e-6
        assert (b['x']-a['x'])*facing >= -1e-7
        if a['phase'] in {'approach','finish'}:
            assert b['y'] == a['y']
        if a['phase'] != 'air' and b['phase'] != 'air':
            assert b['y'] == a['y']
    assert path[-1]['window_edge_id']=='target' and path[-1]['route_stage']==''
    assert path[-1]['landing_remaining']==0
    assert (path[-1]['x'],path[-1]['y']) == tuple(target)
    assert path[-1]['elapsed_seconds'] > path[-1]['jump_duration']+.2
    source_offset = 0 if kind=='floor_to_window' else 6
    assert frames['approach']['render_offset_y']==frames['charge_start']['render_offset_y']==source_offset
    assert all(frames[name]['render_offset_y']==6 for name in FRAME_NAMES[3:])
    assert .25 <= frames['compressed']['land_progress'] < .5
    assert .5 <= frames['recover']['land_progress'] < .75
    assert .75 <= frames['standing_again']['land_progress'] < 1
