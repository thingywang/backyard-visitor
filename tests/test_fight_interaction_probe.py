"""Pure evidence contracts: no GUI, model simulation, native process or input."""
from copy import deepcopy
import sys
import pytest
from scripts.probe_fight_interaction import case_checks, main


CLICK = ['MouseButtonPress', 'MouseButtonRelease']


def complete(name):
    native = {'native_visible': True, 'qt_visible': True, 'key': False}
    event_kinds = ['fight_start', 'fur', 'cleaned']
    scene = {**native, 'visible_count': 1, 'cat_ids': ['amber', 'mist'],
             'cats_hidden': {'amber': True, 'mist': True}}
    case = dict(
        name=name, fight_ids=['amber', 'mist'], selected_id='mist',
        expected_menu_labels={'amber': '棕纹访客', 'mist': '灰色访客'},
        trust_before={'amber': 60, 'mist': 60, 'ink': 60},
        trust_after={'amber': 60, 'mist': 60, 'ink': 60},
        captures=[{'kind': 'smoke', 'native': dict(native)}, {'kind': 'fur', 'native': dict(native)}],
        samples=[{'fight_overlay': scene}],
        fight_seconds=5.02, fight_model_seconds=5.0,
        both_fleeing_seen=True, original_cats_absent=True,
        overlay_cleared_after_fight=True, menu_hidden_after_fight=True,
        restored_cat_windows={'amber': True, 'mist': True},
        fur_count=1, fur_click={'event_types': CLICK[:]}, fur_removed=True,
        fur_widget_removed=True, fur_collection_count=0,
        clock_acceleration_seen=False, injected_model_dt=False,
        global_override_seen=False, unexpected_ui_seen=False, shutdown_verified=True,
        source_hashes={'product.py': 'hash-before'}, source_hashes_after={'product.py': 'hash-before'})
    if name == 'click':
        event_kinds.insert(1, 'intervene')
        case['trust_after']['mist'] = 64
        case['smoke_click'] = {'event_types': CLICK[:], 'target': 'shared_fight_overlay'}
        case['menu_observation'] = {
            'native': {**native, 'key': True},  # Popup menus may legitimately become key.
            'scene_native': [dict(native), dict(native), dict(native)],
            'entries': [{'cat_id': cid, 'text': '护住' + label, 'enabled': True}
                        for cid, label in case['expected_menu_labels'].items()]}
        case['captures'].append({'kind': 'menu', 'native': {**native, 'key': True}})
        case['menu_click'] = {'event_types': CLICK[:], 'target_cat_id': 'mist',
                              'action_text': '护住灰色访客', 'inside_action_geometry': True}
        case['fight_seconds'] = case['fight_model_seconds'] = .7
    case['events'] = [{'kind': kind, 'cat_id': 'mist'} for kind in event_kinds]
    return case


@pytest.mark.parametrize('name', ['natural', 'click'])
def test_complete_required_evidence_passes(name):
    assert all(case_checks(complete(name)).values())


@pytest.mark.parametrize('key,value,check', [
    ('fight_ids', ['amber'], 'two_cats_fought'),
    ('fight_seconds', 3, 'natural_five_seconds_or_explicit_ui_click'),
    ('fight_model_seconds', 2, 'natural_five_seconds_or_explicit_ui_click'),
    ('both_fleeing_seen', False, 'both_fleeing_observed'),
    ('original_cats_absent', False, 'both_original_cats_departed'),
    ('overlay_cleared_after_fight', False, 'fight_overlay_and_menu_cleared'),
    ('menu_hidden_after_fight', False, 'fight_overlay_and_menu_cleared'),
    ('restored_cat_windows', {'amber': True, 'mist': False}, 'cat_windows_restored_after_fight'),
    ('restored_cat_windows', {'amber': True}, 'cat_windows_restored_after_fight'),
    ('fur_count', 2, 'one_fur_drop'),
    ('fur_removed', False, 'fur_model_and_widget_removed'),
    ('fur_widget_removed', False, 'fur_model_and_widget_removed'),
    ('fur_collection_count', 1, 'fur_not_added_to_collection'),
    ('clock_acceleration_seen', True, 'no_time_or_position_fixture'),
    ('injected_model_dt', True, 'no_time_or_position_fixture'),
    ('global_override_seen', True, 'no_global_input_or_native_permissions'),
    ('unexpected_ui_seen', True, 'no_global_input_or_native_permissions'),
    ('shutdown_verified', False, 'clean_shutdown'),
    ('source_hashes_after', {'product.py': 'changed'}, 'case_source_fingerprint_unchanged'),
    ('source_hashes', {}, 'case_source_fingerprint_unchanged'),
])
def test_missing_or_wrong_evidence_fails(key, value, check):
    case = complete('natural')
    case[key] = value
    assert not case_checks(case)[check]


@pytest.mark.parametrize('key', ['smoke_click', 'menu_click', 'menu_observation'])
def test_model_intervention_alone_cannot_be_claimed_as_ui(key):
    case = complete('click')
    del case[key]
    assert not case_checks(case)['natural_five_seconds_or_explicit_ui_click']


@pytest.mark.parametrize('key', ['smoke_click', 'menu_click'])
def test_both_click_stages_require_press_and_release(key):
    case = complete('click')
    case[key]['event_types'] = ['MouseButtonRelease']
    assert not case_checks(case)['natural_five_seconds_or_explicit_ui_click']


def test_menu_selection_must_hit_correct_visible_action():
    for key, value in [('target_cat_id', 'amber'), ('action_text', '护住棕纹访客'),
                       ('inside_action_geometry', False)]:
        case = complete('click')
        case['menu_click'][key] = value
        assert not case_checks(case)['natural_five_seconds_or_explicit_ui_click']


@pytest.mark.parametrize('mutation', ['missing', 'extra', 'wrong_id', 'wrong_label', 'disabled', 'not_visible'])
def test_menu_must_explicitly_offer_both_display_names(mutation):
    case = complete('click')
    menu = case['menu_observation']
    if mutation == 'missing':
        menu['entries'].pop()
    elif mutation == 'extra':
        menu['entries'].append({'cat_id': 'ink', 'text': '护住第三只', 'enabled': True})
    elif mutation == 'wrong_id':
        menu['entries'][1]['cat_id'] = 'amber'
    elif mutation == 'wrong_label':
        menu['entries'][1]['text'] = '护住另一只'
    elif mutation == 'disabled':
        menu['entries'][1]['enabled'] = False
    else:
        menu['native']['native_visible'] = False
    assert not case_checks(case)['explicit_two_cat_menu']
    assert not case_checks(case)['natural_five_seconds_or_explicit_ui_click']


def test_only_selected_cat_may_gain_trust():
    case = complete('click')
    case['trust_after']['amber'] += 4
    assert not case_checks(case)['trust_changes_only_for_selected_cat']
    case = complete('click')
    case['events'][1]['cat_id'] = 'amber'
    assert not case_checks(case)['natural_five_seconds_or_explicit_ui_click']


def test_fur_release_without_press_is_not_click_evidence():
    case = complete('natural')
    case['fur_click']['event_types'] = ['MouseButtonRelease']
    assert not case_checks(case)['fur_cleaned_through_qt_click']


def test_old_two_smoke_windows_cannot_pass_shared_overlay_contract():
    case = complete('natural')
    case['captures'].append(deepcopy(case['captures'][0]))
    assert not case_checks(case)['one_shared_smoke_native_visible']
    case = complete('natural')
    case['samples'][0]['fight_overlay']['visible_count'] = 2
    assert not case_checks(case)['one_shared_smoke_native_visible']


@pytest.mark.parametrize('key,value', [('native_visible', False), ('qt_visible', False),
                                      ('cat_ids', ['amber']), ('visible_count', 0)])
def test_shared_overlay_is_native_visible_and_has_both_cat_ids(key, value):
    case = complete('natural')
    case['samples'][0]['fight_overlay'][key] = value
    assert not case_checks(case)['one_shared_smoke_native_visible']


def test_cat_windows_must_be_hidden_throughout_fight():
    case = complete('natural')
    second = deepcopy(case['samples'][0])
    second['fight_overlay']['cats_hidden']['mist'] = False
    case['samples'].append(second)
    assert not case_checks(case)['original_cat_windows_hidden_during_fight']


def test_menu_key_exception_never_extends_to_scene_windows():
    case = complete('click')
    assert case_checks(case)['captured_scene_windows_not_key']
    case['menu_observation']['scene_native'][1]['key'] = True
    assert not case_checks(case)['captured_scene_windows_not_key']
    case = complete('click')
    case['captures'][0]['native']['key'] = True
    assert not case_checks(case)['captured_scene_windows_not_key']
    case = complete('natural')
    case['samples'][0]['fight_overlay']['key'] = True
    assert not case_checks(case)['captured_scene_windows_not_key']


def test_existing_report_is_refused_before_process_launch(tmp_path, monkeypatch):
    report = tmp_path / 'old-fight-report.json'
    report.write_text('preserved evidence')
    monkeypatch.setattr(sys, 'platform', 'darwin')
    monkeypatch.setattr(sys, 'argv', ['probe_fight_interaction.py', '--output', str(report)])
    with pytest.raises(SystemExit) as stopped:
        main()
    assert stopped.value.code == 2
    assert report.read_text() == 'preserved evidence'
