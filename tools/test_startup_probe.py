from pathlib import Path
import startup_probe as probe

class FakeWindows:
    def __init__(self,root):
        self.items=[{'pid':10,'path':str(root/'Weixin.exe'),'startedAt':90},{'pid':20,'path':str(root/'bin/frpc.exe'),'startedAt':90}]
        self.gateway={'pid':30,'path':str(root/'runtime/Scripts/python.exe'),'startedAt':99}
        self.rows=[{'pid':30,'state':2,'localPort':19443},{'pid':20,'state':5,'localPort':50000}]
    def processes(self,names):return self.items
    def process(self,pid):return self.gateway
    def tcp(self):return self.rows

def setup(tmp_path,monkeypatch):
    root=tmp_path;windows=FakeWindows(root)
    monkeypatch.setattr(probe,'endpoint_config',lambda _: {'client':str(root/'Weixin.exe'),'clientTask':'test-client'})
    monkeypatch.setattr(probe,'read_json',lambda *a: {'phase':'running-verified','pid':10})
    accounts=[{'status':'online','currentProcess':True}]
    monkeypatch.setattr(probe,'read_gateway',lambda _:({'port':19443},{'pid':30,'startedAt':100},accounts))
    return root,windows,accounts

def test_all_three_components_and_current_account_are_required(tmp_path,monkeypatch):
    root,windows,accounts=setup(tmp_path,monkeypatch)
    assert probe.probe(root,windows)['ready']
    accounts[0]['status']='loginRequired';accounts[0]['currentProcess']=False
    result=probe.probe(root,windows)
    assert result['gateway']['alive'] and result['gateway']['listening'] and result['relay']['connected']
    assert result['loginRequired'] and not result['ready']

def test_stale_gateway_pid_and_wrong_listener_are_not_ready(tmp_path,monkeypatch):
    root,windows,_=setup(tmp_path,monkeypatch)
    windows.gateway['startedAt']=200
    assert not probe.probe(root,windows)['gateway']['alive']
    windows.gateway['startedAt']=99;windows.rows[0]['pid']=999
    assert not probe.probe(root,windows)['gateway']['listening']

def test_relay_running_without_connection_and_old_online_metadata_do_not_pass(tmp_path,monkeypatch):
    root,windows,accounts=setup(tmp_path,monkeypatch)
    windows.rows.pop();result=probe.probe(root,windows)
    assert result['relay']['alive'] and not result['relay']['connected'] and not result['ready']
    windows.rows.append({'pid':20,'state':5,'localPort':50000});accounts[0]['currentProcess']=False
    assert not probe.probe(root,windows)['ready']

def test_unknown_python_binary_cannot_impersonate_gateway(tmp_path,monkeypatch):
    root,windows,_=setup(tmp_path,monkeypatch)
    windows.gateway['path']=str(root/'unrelated/python.exe')
    assert not probe.probe(root,windows)['gateway']['alive']
