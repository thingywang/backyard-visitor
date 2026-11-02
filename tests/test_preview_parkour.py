"""Preview requests real routes instead of staging coordinates or clocks."""
import ast
import math
from pathlib import Path
import pytest
from scripts.preview_parkour import RoutePreview, parse_args


@pytest.mark.parametrize('coat',('ginger','black'))
def test_production_model_completes_both_sides_and_directional_air_poses(coat):
    preview=RoutePreview(coat)
    for _ in range(24*60):
        preview.tick(1/24)
        if preview.phase in {'complete','failed'}:break
    report=preview.report('model_only_test')
    assert report['all_checks_passed'], report['checks']
    assert 20 < preview.completed_at < 40
    assert report['observed_pose_slots']=={'climb':[0,1],'jump_up':[0,1],'jump_down':[0,1]}
    assert not report['failures']
    assert {r['cat_id'] for r in report['samples']}=={'onyx' if coat=='black' else 'amber'}
    assert all(math.isfinite(r['model_seconds']) for r in report['samples'])
    assert 'true_name' not in report and 'nickname' not in report


def test_preview_never_assigns_visitor_coordinates_states_or_animation_clocks():
    path=Path(__file__).resolve().parents[1]/'scripts/preview_parkour.py'
    tree=ast.parse(path.read_text())
    protected={'x','y','target_x','target_y','state','state_age','age','jump_elapsed',
               'jump_start_x','jump_start_y','climb_phase','jump_vy','takeoff_remaining'}
    writes=[node.attr for node in ast.walk(tree) if isinstance(node,ast.Attribute)
            and isinstance(node.ctx,ast.Store) and node.attr in protected]
    assert writes==[]


@pytest.mark.parametrize('value',('0','-1','nan','inf'))
def test_native_duration_must_be_finite_positive(value):
    with pytest.raises(SystemExit):parse_args(['--seconds',value])


def test_both_coats_mode_is_explicitly_offscreen_only():
    assert parse_args(['--offscreen','--coat','both']).coat=='both'
    with pytest.raises(SystemExit):parse_args(['--coat','both'])
