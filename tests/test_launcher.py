from streamlit.web import bootstrap
import launcher


def test_launcher_loads_config_before_start(monkeypatch):
    calls=[]
    class Socket:
        def __enter__(self): return self
        def __exit__(self,*args): pass
        def bind(self,address): assert address==('127.0.0.1',0)
        def getsockname(self): return ('127.0.0.1',12345)
    class Thread:
        def __init__(self,**kwargs): pass
        def start(self): pass
    monkeypatch.setattr(launcher.socket,'socket',Socket)
    monkeypatch.setattr(launcher.threading,'Thread',Thread)
    monkeypatch.setattr(bootstrap,'load_config_options',lambda flags:calls.append(('config',flags)))
    monkeypatch.setattr(bootstrap,'run',lambda *args:calls.append(('run',args)))
    monkeypatch.chdir('.')
    launcher.main()
    assert [c[0] for c in calls]==['config','run']
    flags=calls[0][1]
    assert flags['server.address']=='127.0.0.1'
    assert flags['server.port']==12345
    assert flags['browser.gatherUsageStats'] is False
