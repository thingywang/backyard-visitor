"""Exercise the ordinary source entry before any Controller windows exist."""
from types import SimpleNamespace
import pytest


@pytest.mark.parametrize('platform,expected',[('cocoa',['accessory','controller']),('offscreen',['controller'])])
def test_entry_configures_cocoa_before_controller(platform,expected,monkeypatch):
    import backyard_visitor.app as app
    calls=[]
    qt=SimpleNamespace(setApplicationName=lambda name:None,setOrganizationName=lambda name:None,platformName=lambda:platform)
    monkeypatch.setattr(app.sys,'argv',['visitor','--hidden'])
    monkeypatch.setattr(app.QApplication,'instance',staticmethod(lambda:qt))
    monkeypatch.setattr(app,'configure_cocoa_accessory_application',lambda:calls.append('accessory'))
    def controller(*args,**kwargs):
        calls.append('controller')
        raise RuntimeError('fixture ends before GUI construction')
    monkeypatch.setattr(app,'Controller',controller)
    assert app.main()==2
    assert calls==expected


def test_activation_rejection_fails_cleanly_before_creating_windows(monkeypatch):
    import backyard_visitor.app as app
    qt=SimpleNamespace(setApplicationName=lambda name:None,setOrganizationName=lambda name:None,platformName=lambda:'cocoa')
    monkeypatch.setattr(app.sys,'argv',['visitor','--hidden'])
    monkeypatch.setattr(app.QApplication,'instance',staticmethod(lambda:qt))
    def fail():raise RuntimeError('activation policy refused')
    monkeypatch.setattr(app,'configure_cocoa_accessory_application',fail)
    def forbidden(*args,**kwargs):pytest.fail('Controller must not be created after native mode failure')
    monkeypatch.setattr(app,'Controller',forbidden)
    assert app.main()==2
