"""Meaningful update boundaries: no source replacement, signed metadata, state protection."""
import base64
import ctypes
from pathlib import Path
import plistlib
import sys
import threading
import pytest
from backyard_visitor import updates


def make_bundle(tmp_path, **overrides):
    bundle=tmp_path/'Test.app'; (bundle/'Contents').mkdir(parents=True,exist_ok=True)
    info={"SUFeedURL":"https://github.com/thingywang/backyard-visitor/releases/latest/download/appcast.xml",
          "SUPublicEDKey":base64.b64encode(b'x'*32).decode(),"SURequireSignedFeed":True,
          "SUVerifyUpdateBeforeExtraction":True}
    info.update(overrides)
    (bundle/'Contents/Info.plist').write_bytes(plistlib.dumps(info))
    return bundle


def test_valid_signed_https_configuration(tmp_path):
    updates.validate_configuration(make_bundle(tmp_path))


@pytest.mark.parametrize('overrides',[
    {"SUFeedURL":"http://example.com/feed"}, {"SUFeedURL":"file:///tmp/feed"},
    {"SUFeedURL":"https://user:pass@example.com/feed"}, {"SUPublicEDKey":"invalid"},
    {"SUPublicEDKey":base64.b64encode(b'x'*31).decode()},
    {"SURequireSignedFeed":False},{"SUVerifyUpdateBeforeExtraction":False},
])
def test_refuse_unsafe_or_unsigned_configuration(tmp_path,overrides):
    with pytest.raises(updates.UpdateUnavailable): updates.validate_configuration(make_bundle(tmp_path,**overrides))


def test_source_execution_does_not_load_native_library_or_network(monkeypatch):
    monkeypatch.setattr(updates,'app_bundle',lambda:None)
    monkeypatch.setattr(ctypes,'CDLL',lambda *_:pytest.fail('source run must not load installer'))
    with pytest.raises(updates.UpdateUnavailable,match='源码'):
        updates.NativeUpdater(lambda:True,lambda:None).check()


def test_save_failure_never_claims_success_or_shuts_down():
    calls=[]
    updater=updates.NativeUpdater(lambda:False,lambda:calls.append('shutdown'))
    assert updater._state_callback(1)==0
    assert calls==[]


def test_save_and_shutdown_callbacks_and_exception_boundary():
    calls=[]
    updater=updates.NativeUpdater(lambda:calls.append('saved') or True,lambda:calls.append('shutdown'))
    assert updater._state_callback(1)==1
    assert updater._state_callback(2)==1
    assert calls==['saved','shutdown']
    def fail(): raise OSError('disk full')
    assert updates.NativeUpdater(fail,lambda:None)._state_callback(1)==0
    assert updater._state_callback(99)==0


def test_background_thread_cannot_start_cocoa_updater(monkeypatch):
    error=[]
    def work():
        try: updates.NativeUpdater(lambda:True,lambda:None).check()
        except updates.UpdateUnavailable as exc: error.append(str(exc))
    thread=threading.Thread(target=work);thread.start();thread.join(timeout=2)
    assert error and '菜单' in error[0]


def test_only_recognized_frozen_app_path(monkeypatch):
    monkeypatch.setattr(sys,'platform','darwin');monkeypatch.setattr(sys,'frozen',True,raising=False)
    monkeypatch.setattr(sys,'executable','/tmp/Example.app/Contents/MacOS/Example')
    assert updates.app_bundle()==Path('/tmp/Example.app').resolve()
    monkeypatch.setattr(sys,'executable','/usr/local/bin/python')
    assert updates.app_bundle() is None
