"""Inventory K8s theo init/sidecar/overhead; fixture không được nghiệm thu REAL."""
import importlib.util
import json
from datetime import datetime,timezone
from pathlib import Path
from types import SimpleNamespace
import pytest

ROOT=Path(__file__).parents[1]


def module(name):
    spec=importlib.util.spec_from_file_location(name,ROOT/f'tools/{name}.py')
    m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m);return m


def container(name,cpu,memory='0',sidecar=False):
    r={'name':name,'resources':{'requests':{'cpu':cpu,'memory':memory}}}
    if sidecar:r['restartPolicy']='Always'
    return r


def test_peak_init_and_overhead():
    pod={'spec':{'containers':[container('a','100m','10Mi'),container('b','200m','20Mi')],
                 'initContainers':[container('init','500m','40Mi')],'overhead':{'cpu':'50m','memory':'2Mi'}}}
    req=module('a4_shadow_readonly').pod_request(pod)
    assert req['cpu']==pytest.approx(.55) and req['memory']==42*1024**2


def test_restartable_sidecars_accumulate_in_initialization_order():
    pod={'spec':{'containers':[container('app','400m')],'initContainers':[
        container('side1','100m',sidecar=True),container('setup','600m'),
        container('side2','200m',sidecar=True),container('setup2','800m')], 'overhead':{'cpu':'50m'}}}
    assert module('a4_shadow_readonly').pod_request(pod)['cpu']==pytest.approx(1.15)


def test_pod_level_precedence():
    pod={'spec':{'containers':[container('a','200m')],'resources':{'requests':{'cpu':'1','memory':'100Mi'}},
        'overhead':{'cpu':'50m','memory':'1Mi'}}}
    req=module('a4_shadow_readonly').pod_request(pod)
    assert req['cpu']==pytest.approx(1.05) and req['memory']==101*1024**2


def test_allocated_resize_is_conservative():
    pod={'spec':{'containers':[container('app','100m')]},'status':{'containerStatuses':[{'name':'app','allocatedResources':{'cpu':'300m'}}]}}
    assert module('a4_shadow_readonly').pod_request(pod)['cpu']==pytest.approx(.3)


def test_fixture_cannot_mark_a4_real_done(tmp_path):
    p=tmp_path/'fixture.json';p.write_text('{"source":"SIMULATED","mode":"READ_ONLY_GET"}')
    with pytest.raises(ValueError):module('a4_shadow_readonly').analyse(p,tmp_path/'analysis')
    assert not (tmp_path/'analysis').exists()


@pytest.mark.parametrize('timestamp,expected',[
    ('2026-11-09T12:12:21Z',True),('2026-11-09T12:11:00Z',False),('2026-11-09T12:13:00Z',False)])
def test_pod_metric_must_be_fresh_at_capture(timestamp,expected):
    clock=datetime(2026,11,9,12,12,43,tzinfo=timezone.utc)
    assert module('a4_shadow_readonly').metric_fresh_at({'timestamp':timestamp,'window':'10s'},clock)==expected


def test_capture_scrubs_environment_and_only_calls_get(monkeypatch,capsys):
    import subprocess,shutil
    calls=[]
    secret='fake-token-never-export'
    def fake(args,**kwargs):
        calls.append(args)
        data={'kind':'List','items':[{'metadata':{'name':'test','uid':'id','annotations':{'token':secret}},
            'spec':{'containers':[{'name':'app','env':[{'name':'TOKEN','value':secret}],'resources':{'requests':{'cpu':'100m'}}}]},'status':{}}]}
        return SimpleNamespace(returncode=0,stdout=json.dumps(data),stderr='')
    monkeypatch.setattr(subprocess,'run',fake);monkeypatch.setattr(shutil,'which',lambda name:'/test/bin')
    module('collect_a4_readonly').cluster_collect();output=capsys.readouterr().out
    assert secret not in output
    assert all('get' in args and not set(args)&{'apply','create','delete','patch','exec','scale'} for args in calls)
