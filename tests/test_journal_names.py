"""Named visitors are identified in journal events without rewriting user text."""
import json
import pytest
from backyard_visitor.model import Engine


def present(*ids, trust=0):
    e = Engine(seed=3, demo=True, visit_limit=2)
    e.calm_mode = True
    e.open_gate()
    for cid in ids or ('amber',):
        assert e.invite(cid) == cid
        e.cats[cid].trust = trust
        e.cats[cid].neighbor_status = "neighbor"
    for _ in range(1600):
        if all(v.state == 'idle' and v.posture_remaining == 0 for v in e.visitors.values()):
            break
        e.tick(.025)
    else:
        raise AssertionError('Natural entry did not finish')
    e.drain_events()
    return e


def events(e, kind=None):
    result = e.drain_events()
    return [x for x in result if kind is None or x['kind'] == kind]


def test_naming_and_low_trust_call_use_nickname_without_unlocking_recognition():
    e = present()
    assert e.name_cat('amber', '小麦') == 'unfamiliar'
    assert events(e, 'named')[0]['message'] == '名字记下了。小麦还需要一些时间。'
    assert e.call_cat('amber') == 'ignored'
    assert events(e, 'call')[0]['message'] == '小麦疑惑地看了一眼，又忙自己的事去了。'
    assert e.cats['amber'].trust == 0


def test_unnamed_touch_still_uses_original_pronoun():
    e = present()
    assert not e.pet('amber')
    assert events(e, 'boundary')[0]['message'] == '它还不习惯触碰。先让它安心地待一会儿。'


def test_named_boundary_has_no_ambiguous_second_pronoun():
    e = present()
    e.name_cat('amber', '小麦'); e.drain_events()
    assert not e.pet('amber')
    assert events(e, 'boundary')[0]['message'] == '小麦还不习惯触碰。先让小麦安心地待一会儿。'


def test_affection_and_recognized_naming_use_public_name():
    e = present(trust=60)
    assert e.name_cat('amber', '小麦') == 'recognized'
    assert events(e, 'named')[0]['message'] == '小麦轻轻回应了你的呼唤。'
    assert e.pet('amber')
    assert events(e, 'purr')[0]['message'] == '呼噜……小麦慢慢眯起了眼睛。'


def test_two_visitors_are_named_by_event_owner_not_last_renamed_cat():
    e = present('amber', 'mist')
    e.name_cat('amber', '小麦'); e.name_cat('mist', '云团'); e.drain_events()
    e.call_cat('mist'); e.call_cat('amber')
    result = events(e, 'call')
    assert [(x['cat_id'], x['message'].split('疑惑')[0]) for x in result] == [('mist', '云团'), ('amber', '小麦')]


def test_rename_updates_new_events_but_keeps_old_event_snapshot():
    e = present(); e.name_cat('amber', '小麦'); e.drain_events()
    e.call_cat('amber'); before = events(e)[0]
    e.name_cat('amber', '麦麦'); e.call_cat('amber'); after = events(e, 'call')[0]
    assert before['message'].startswith('小麦')
    assert after['message'].startswith('麦麦')


@pytest.mark.parametrize('nickname', ['它它', '{cat}', '小它\"云', '  Little   Cat  '])
def test_user_text_is_inserted_once_and_not_interpreted_as_template(nickname):
    e = present(); e.name_cat('amber', nickname); e.drain_events()
    assert e.call_cat('amber') == 'ignored'
    assert events(e, 'call')[0]['message'] == f'{e.cats["amber"].nickname}疑惑地看了一眼，又忙自己的事去了。'


@pytest.mark.parametrize('action,kind,expected', [
    ('carry', 'carry', '轻轻抱起，拖到边缘即可送小麦回家。'),
    ('startle', 'startle', '动作太快了，小麦想先躲远一点。'),
])
def test_physical_action_events_identify_the_named_cat(action, kind, expected):
    e = present(); e.name_cat('amber', '小麦'); e.drain_events()
    assert getattr(e, action)('amber')
    assert events(e, kind)[0]['message'] == expected


def test_play_start_and_finish_keep_the_right_name():
    e = present(); e.name_cat('amber', '小麦'); e.drain_events()
    assert e.start_teaser('amber')
    assert events(e, 'play_started')[0]['message'] == '逗猫棒轻轻晃起来了。小麦正盯着你手里的小猎物。'
    assert e.stop_teaser('amber')
    assert events(e, 'play_finished')[0]['message'] == '玩累了。小麦慢慢安静下来，找个舒服的地方趴一会儿。'


def test_meal_and_postmeal_events_use_name():
    e = present(); e.name_cat('amber', '小麦'); e.drain_events()
    assert e.feed()
    result = []
    for _ in range(2400):
        e.tick(.025); result.extend(e.drain_events())
        if any(x['kind'] == 'trust' for x in result):
            break
    for kind in ('eating', 'trust'):
        message = next(x['message'] for x in result if x['kind'] == kind)
        assert message.startswith('小麦') and '它' not in message
    assert next(x['message'] for x in result if x['kind'] == 'food') == '碗里多了一份小鱼干。'


def test_saved_nickname_returns_without_using_hidden_name_or_undiscovered_profile():
    e = present(); e.name_cat('amber', '小麦')
    restored = Engine(e.export(), seed=4)
    assert restored.cat_reference('amber') == '小麦'
    assert restored.cat_reference('missing') == '它'
    unseen = next(cid for cid, cat in restored.cats.items() if not cat.discovered)
    restored.cats[unseen].nickname = '未见访客的旧昵称'
    assert restored.cat_reference(unseen) == '它'


def test_secret_response_does_not_echo_secret_or_use_ambiguous_pronoun():
    e = present()
    assert e.name_cat('amber', e.cats['amber'].true_name) == 'true_name'
    result = events(e)
    for item in result:
        assert '它' not in item['message']
    assert e.cats['amber'].true_name not in json.dumps(result, ensure_ascii=False)
