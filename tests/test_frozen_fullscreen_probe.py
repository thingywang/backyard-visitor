from scripts.probe_frozen_fullscreen import checks, own_windows, outcome


def sample():
    bounds={'X':0,'Y':0,'Width':1920,'Height':1080}
    sizes=[(30,30),(62,40),(144,112),(144,112),(1920,1080),(344,56)]
    windows=[{'window_id':i+1,'order':i,'layer':8,'alpha':1,'onscreen':True,
        'bounds':{'X':0,'Y':0,'Width':w,'Height':h}} for i,(w,h) in enumerate(sizes)]
    return {'phase':'play','screen':[0,0,1920,1080],'frontmost_role':'helper','helper_active':True,'helper_age':.05,
        'helper':{'style_mask':1<<14,'on_active_space':True,'key':True,'level':0,'cg':{'order':10,'layer':0,'bounds':bounds}},'app_windows':windows}


def test_real_space_and_all_companions_required():
    row=sample();assert all(checks(row).values())
    row['app_windows']=[]
    assert checks(row)['helper_native_fullscreen']
    assert not checks(row)['gate_onscreen_above_fullscreen']
    assert not checks(row)['cats_onscreen_above_fullscreen']


def test_one_cat_or_below_helper_or_offscreen_is_not_success():
    for field,value in [('order',11),('onscreen',False),('alpha',0),('layer',0)]:
        row=sample();row['app_windows'][2][field]=value
        assert not checks(row)['cats_onscreen_above_fullscreen']


def test_foreign_windows_and_titles_are_not_persisted():
    assert own_windows([{'kCGWindowOwnerPID':99,'kCGWindowNumber':3}],42)==[]
    row=own_windows([{'kCGWindowOwnerPID':42,'kCGWindowNumber':3,'kCGWindowName':'private'}],42)[0]
    assert 'private' not in str(row)


def test_missing_exit_or_identity_cannot_validate_native_failure():
    row=sample();row['app_windows']=[]
    report={'samples':[row],'app':{'shutdown_verified':True},'app_exit_code':0,'helper_exit_code':0}
    for k in ('frozen_identity_verified','helper_identity_verified','bundle_unchanged','sources_unchanged','helper_fullscreen_exit'):report[k]=True
    assert outcome(report)=='failed'
    report['helper_identity_verified']=False
    assert outcome(report)=='inconclusive'
    assert outcome({})=='inconclusive'


def test_entry_does_not_require_cats_before_their_natural_arrival():
    row=sample();row['phase']='entry';row['app_windows']=row['app_windows'][:2]
    assert all(checks(row).values())
    row['pair_ready']=True
    assert not checks(row)['cats_onscreen_above_fullscreen']


def test_game_construction_samples_are_retained_without_uncreated_canvas_requirement():
    row=sample();row['phase']='play_setup';row['pair_ready']=True
    row['app_windows']=row['app_windows'][:4]
    assert all(checks(row).values())
    row['phase']='play'
    assert not checks(row)['canvas_onscreen_above_fullscreen']
