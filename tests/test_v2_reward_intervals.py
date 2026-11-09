"""Oracles theo workflow ledger và chi phí operation, tách khỏi reward implementation."""
from dataclasses import replace
import importlib.util
from pathlib import Path
import pytest
from placement_v2.env import DAGEnv

spec=importlib.util.spec_from_file_location('sensitivity_reward_test',Path(__file__).parents[1]/'tools/sensitivity_v2.py')
suite=importlib.util.module_from_spec(spec);spec.loader.exec_module(suite)


def play(case,config_changes=None):
    c,d,t,p=suite.fixture(70101,case)
    c=replace(c,**(config_changes or {})); e=DAGEnv(c,d); o=e.reset(trace=t,initial_placement=p); ticks=[]; done=False
    while not done:
        a={e.agent_names[i]:0 for i,v in enumerate(o['decision_mask']) if v}
        if e.now==0 and case.endswith('_move'):a['agent_0']=2
        start=e.now; o,r,done,info=e.step(a); ticks.append((start,r,info))
        expected=sum((w.finish if w.finish is not None else e.now)-w.arrival for w in e.workflows.values())
        assert e.summary()['workflow_time_s']==pytest.approx(expected,abs=1e-8)
    return e,ticks


@pytest.mark.parametrize('mode',['interval-v1','legacy-cumulative'])
def test_idle_after_completed_has_no_new_latency_cost(mode):
    e,ticks=play('idle_latency',{'reward_mode':mode})
    late=sum(r for start,r,info in ticks if start>=2)
    if mode=='interval-v1': assert late==pytest.approx(0,abs=1e-10)
    else: assert late<0  # Reproduces the reviewed defect; legacy only for audit.


def test_empty_move_charged_once_plus_exact_overlap_time():
    e,ticks=play('empty_move')
    assert e.summary()['migration_started']==1 and e.summary()['migration_completed']==1
    assert sum(info['interval_metrics']['migration_started'] for _,_,info in ticks)==1
    assert sum(info['reward_components']['migration'] for _,_,info in ticks)==pytest.approx(-.02-.01*.5,abs=1e-9)
    assert sum(r for _,r,_ in ticks)==pytest.approx(-.025,abs=1e-9)


def test_failed_move_charged_once():
    c,d,t,p=suite.fixture(70101,'empty_move')
    t['faults']=[{'time':.1,'kind':'cluster_down','cluster':1}]
    e=DAGEnv(c,d);o=e.reset(trace=t,initial_placement=p);total=0;done=False
    while not done:
        a={e.agent_names[i]:0 for i,v in enumerate(o['decision_mask']) if v}
        if e.now==0:a['agent_0']=2
        o,r,done,info=e.step(a);total+=r
    assert e.summary()['migration_failed']==1
    assert total==pytest.approx(-.02-.1-.01*.1,abs=1e-9)


def test_components_equal_shared_reward_and_traffic_not_double_charged():
    e,ticks=play('payload_large')
    for _,r,info in ticks:assert r==pytest.approx(sum(info['reward_components'].values()))
    assert sum(info['reward_components']['traffic'] for _,_,info in ticks)==pytest.approx(-e.config.objectives[1]*e.summary()['traffic_cost'])


def test_sla_failure_resolved_only_once():
    c,d,t,p=suite.fixture(70101,'idle_latency');c=replace(c,objectives=(0.,0.,0.,2.))
    t['faults']=[{'time':.1,'kind':'service_down','service':'a'}]
    e=DAGEnv(c,d);o=e.reset(trace=t,initial_placement=p);total=0;done=False
    while not done:
        o,r,done,info=e.step({e.agent_names[i]:0 for i,v in enumerate(o['decision_mask']) if v});total+=r
    assert e.summary()['workflow_failed']==1 and e.summary()['sla_violations']==1
    assert total==pytest.approx(-2.)


@pytest.mark.parametrize('case',['cpu_high_A','ram_pressure_latency','ram_pressure_errors','bandwidth_low_AB'])
def test_all_present_workflow_time_includes_failed_and_censored(case):
    e,ticks=play(case)
    assert e.summary()['workflow_arrivals']==24
    assert sum(info['interval_metrics']['workflow_time_s'] for _,_,info in ticks)==pytest.approx(e.summary()['workflow_time_s'])


@pytest.mark.parametrize('seed',[70101,70102,70103])
def test_sensitivity_hypotheses(seed):
    rows=[suite.run_case(seed,case)[0] for case in suite.CASES]
    assert all(r['passed'] for r in suite.checks(rows))
