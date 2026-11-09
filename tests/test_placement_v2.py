"""Golden physics và invariants V2; không đánh giá hay tune chính sách."""
from dataclasses import replace
from copy import deepcopy
import json
import numpy as np
import pytest
from placement_v2.config import Config
from placement_v2.dag import DAG,Service,Edge,templates,critical_path
from placement_v2.env import DAGEnv
from placement_v2.trace import make_trace
from placement_v2.schema import manifest,save_contract,load_contract
from placement_v2.policies import Policy


def setup(dag=None,arrivals=(.1,),cpu=(10.,10.,10.),lat=0.,band=None,bg=(0.,0.,0.),faults=(),seconds=3.,**kwargs):
    dag=dag or templates()['diamond']
    config=Config(seconds=seconds,dag_arrival_s=0,intra_latency_s=0,workflow_timeout_s=10,
                  cpu_cores=cpu,latency_s=tuple(tuple(0 if i==j else lat for j in range(3)) for i in range(3)),
                  bandwidth_Bps=tuple(tuple(None if i==j else band for j in range(3)) for i in range(3)),**kwargs)
    trace={'source':'SIMULATED','seed':51001,'scenario':'golden','cpu_capacity':list(cpu),'ram_capacity':[2**30]*3,
           'background_ram_bytes':[0]*3,'workflow_arrivals_s':list(arrivals),'faults':list(faults),
           'environment':[{'latency_s':config.latency_s,'bandwidth_Bps':config.bandwidth_Bps,'background_cpu':list(bg)}]}
    return DAGEnv(config,dag),trace


def run(e,t,placement):
    e.reset(trace=t,initial_placement=placement); e.advance(e.config.seconds); e.assert_conservation(); return e.summary()


def test_dag_bounds_cycle_and_second():
    with pytest.raises(ValueError): DAG('six',tuple(Service(str(i)) for i in range(6)),())
    with pytest.raises(ValueError): DAG('cycle',(Service('a'),Service('b')),(Edge('a','b'),Edge('b','a')))
    with pytest.raises(ValueError): DAG('dup',(Service('a'),Service('a')),())
    e,t=setup(); e.reset(trace=t)
    with pytest.raises(ValueError): e.add_controlled_dag(templates()['chain'])


@pytest.mark.parametrize('name',list(templates()))
@pytest.mark.parametrize('lat,band',[(0.,None),(.02,1e6),(.03,1e5)])
def test_exact_critical_path(name,lat,band):
    dag=templates()[name]; p={n:i%3 for i,n in enumerate(dag.topological())}
    e,t=setup(dag,lat=lat,band=band)
    s=run(e,t,p)
    expected=critical_path(dag,p,e.config.latency_s,e.config.bandwidth_Bps)
    assert s['workflow_completed']==1 and s['workflow_failed']==0
    assert s['e2e_mean_s']==pytest.approx(expected,abs=1e-9)
    assert s['service_invocations']==len(dag.services)
    assert s['traffic_bytes']==sum(x.bytes_per_workflow for x in dag.edges if p[x.parent]!=p[x.child])
    assert s['traffic_cost']==pytest.approx(sum(x.bytes_per_workflow*e.config.price_per_byte[p[x.parent]][p[x.child]] for x in dag.edges if p[x.parent]!=p[x.child]))


def test_diamond_max_not_sum():
    dag=templates()['diamond']; p={n:0 for n in dag.topological()}
    w=dag.services[0].work_core_s
    assert critical_path(dag,p,((0,)*3,)*3,((None,)*3,)*3)==pytest.approx(3*w)


def test_contention_and_both_placement_directions():
    dag=templates()['diamond']; coloc={n:0 for n in dag.topological()}; split={'A':0,'B':1,'C':2,'D':0}
    e,t=setup(dag,cpu=(1.,1.,1.)); a=run(e,t,coloc)
    e,t=setup(dag,cpu=(1.,1.,1.)); b=run(e,t,split)
    assert b['e2e_mean_s']<a['e2e_mean_s']
    bound=critical_path(dag,coloc,e.config.latency_s,e.config.bandwidth_Bps)
    assert a['e2e_mean_s']>=bound-1e-9
    e,t=setup(dag,cpu=(1.,1.,1.),lat=.5); c=run(e,t,split)
    e,t=setup(dag,cpu=(1.,1.,1.),lat=.5); d=run(e,t,coloc)
    assert d['e2e_mean_s']<c['e2e_mean_s']
    e,t=setup(dag,cpu=(1.,1.,1.),bg=(.8,0,0)); slow=run(e,t,coloc)
    assert slow['e2e_mean_s']>=a['e2e_mean_s']


def test_queue_monotonic_same_trace():
    p={n:0 for n in templates()['chain'].topological()}
    e,t=setup(templates()['chain'],arrivals=(.1,.101,.102,.103),cpu=(1.,1.,1.))
    fast=run(e,t,p); reduced=deepcopy(t); reduced['cpu_capacity'][0]=.3
    e2=DAGEnv(e.config,e.dag); slow=run(e2,reduced,p)
    assert slow['queue_mean_s']>=fast['queue_mean_s']
    assert slow['e2e_mean_s']>=fast['e2e_mean_s']


def test_bandwidth_delay_and_shared_link():
    dag=DAG('fork',(Service('a'),Service('b'),Service('c')),(Edge('a','b',10000),Edge('a','c',10000)))
    p={'a':0,'b':1,'c':1}
    e,t=setup(dag,band=1e5); fast=run(e,t,p)
    e,t=setup(dag,band=1e4); slow=run(e,t,p)
    assert slow['e2e_mean_s']>fast['e2e_mean_s']
    assert slow['traffic_bytes']==fast['traffic_bytes']==20000
    assert slow['e2e_mean_s']==pytest.approx(.005539729898474358*2+2.,abs=1e-8)


def test_mid_episode_topo_and_masks():
    e=DAGEnv(Config(seconds=12,dag_arrival_s=2),templates()['diamond']); o=e.reset(seed=51002)
    assert not o['agent_mask'].any() and not o['decision_mask'].any()
    p=Policy('least_loaded'); done=False
    while not done:
        assert np.all(o['action_mask'][o['decision_mask']].any(axis=1))
        assert not o['action_mask'][~o['decision_mask']].any()
        assert not o['obs'][4].any()
        o,_,done,_=e.step(p.actions(e,o))
    places=[r for r in e.logs if r['event']=='PLACE']
    assert len(places)==4
    times={r['service']:r['time_s'] for r in places}
    assert times['A']<times['B'] and times['A']<times['C'] and times['B']<times['D']
    assert e.total['deployments_created']==4
    assert e.total['workflow_arrivals']!=e.total['service_invocations']


def test_pending_reject_and_workflow_error():
    e,t=setup(arrivals=(.1,),pending_retries=2)
    t['ram_capacity']=[1]*3; e.reset(trace=t)
    assert all(s.status=='PENDING' for s in e.states.values())
    o=e.observe(); assert o['agent_mask'].sum()==4 and not o['decision_mask'].any()
    e.step({}); assert all(s.status=='REJECTED' for s in e.states.values())
    assert e.summary()['workflow_failed']==1 and e.summary()['service_invocations']==0


@pytest.mark.parametrize('phase_time',[.1,.35])
def test_migration_serves_during_preparing_and_grace(phase_time):
    dag=DAG('one',(Service('a'),),())
    e,t=setup(dag,arrivals=(phase_time,),readiness_s=.3,grace_s=.5)
    e.reset(trace=t,initial_placement={'a':0}); o=e.observe()
    assert o['action_mask'][0,0] and not o['action_mask'][0,1]
    e.step({'agent_0':2})
    assert e.summary()['workflow_completed']==1
    assert e.summary()['migration_downtime_service_s']==0
    job=e.jobs[0]; assert job.cluster==(0 if phase_time<.3 else 1)
    assert e.summary()['workflow_arrivals']==1 and e.summary()['sla_violations']==0
    assert e.total['deployments_created']==0


def test_migration_rollback_and_source_ready():
    dag=DAG('one',(Service('a'),),())
    e,t=setup(dag,arrivals=(.2,.4),faults=({'time':.1,'kind':'cluster_down','cluster':1},))
    e.reset(trace=t,initial_placement={'a':0}); e.step({'agent_0':2})
    assert e.total['migration_failed']==1 and e.states['a'].active==0
    assert e.summary()['workflow_completed']==2


@pytest.mark.parametrize('kind',['capacity','cluster_down','service_down'])
def test_fault_conservation(kind):
    dag=templates()['diamond']; p={n:0 for n in dag.topological()}
    fault={'time':.108,'kind':kind,'cluster':0,'service':'B','cpu_cores':.01,'memory_bytes':2**20}
    e,t=setup(dag,arrivals=(.1,.2),faults=(fault,)); e.reset(trace=t,initial_placement=p); e.advance(1.)
    s=e.summary(); assert s['workflow_arrivals']==s['workflow_completed']+s['workflow_failed']+s['workflow_unfinished']
    assert s['sla_violations']<=s['workflow_arrivals']
    if kind=='capacity':
        assert e.overloaded[0] and len(e.states['A'].endpoints)==1
        assert not e.validate('A',1)[0] and e.validate('A',2)[0]
        assert e.observe()['obs'][0].max()>1
    else: assert s['workflow_failed']>=1


def test_join_fail_once_and_timeout_censor():
    e,t=setup(arrivals=(.1,),lat=1.,faults=({'time':.12,'kind':'service_down','service':'D'},))
    p={'A':0,'B':1,'C':2,'D':0}; e.reset(trace=t,initial_placement=p); e.advance(.2)
    assert e.summary()['workflow_unfinished']==1
    e.advance(3.); assert e.summary()['workflow_unfinished']==1
    # Resolve at explicit timeout; duplicate terminal signals must remain idempotent.
    e.advance(11); wf=e.workflows[0]; e._fail(wf,'again')
    assert e.summary()['workflow_failed']==e.summary()['sla_violations']==1
    e.assert_conservation()


def test_dependency_and_completed_workflow_no_duplicate():
    e,t=setup(arrivals=(.1,.2)); p={'A':0,'B':1,'C':2,'D':0}; s=run(e,t,p)
    assert s['workflow_completed']==2 and s['service_invocations']==8
    assert s['sla_violations']<=2
    for wf in e.workflows.values():
        assert wf.finishes['D']>max(wf.finishes['B'],wf.finishes['C'])
        assert wf.received['D']=={'B','C'}


def test_randomization_range_seed_and_capacity_initial():
    c=Config(); traces=[make_trace(c,s) for s in (51001,51002)]
    assert make_trace(c,51001)==traces[0] and traces[0]['sha256']!=traces[1]['sha256']
    for t in traces:
        assert all(c.cpu_range[0]<=x/base<=c.cpu_range[1] for x,base in zip(t['cpu_capacity'],c.cpu_cores))
        assert all(c.ram_range[0]-1e-7<=x/base<=c.ram_range[1] for x,base in zip(t['ram_capacity'],c.memory_bytes))
        for i in range(3):
            for j in range(3):
                if i!=j: assert c.bandwidth_range[0]<=t['environment'][0]['bandwidth_Bps'][i][j]/c.bandwidth_Bps[i][j]<=c.bandwidth_range[1]
    e,t=setup(); t['cpu_capacity']=[.01]*3
    with pytest.raises(ValueError): e.reset(trace=t,initial_placement={n:0 for n in e.order})


def test_schema_local_global_loader(tmp_path):
    e,t=setup(); e.reset(trace=t,initial_placement={'A':0,'B':1,'C':2,'D':0}); a=e.observe()
    e.states['D'].attempts+=1; b=e.observe()
    # A has B/C as neighbors; D is hidden except from centralized critic.
    np.testing.assert_array_equal(a['obs'][0],b['obs'][0]); assert not np.array_equal(a['global_state'],b['global_state'])
    assert a['obs'].shape==(5,len(manifest()['actor_features']))
    assert len(a['global_state'])==len(manifest()['critic_features'])
    path=tmp_path/'v2.json'; save_contract(path,{'test':1}); assert load_contract(path)=={'test':1}
    data=json.loads(path.read_text()); data['code_fingerprints']['env.py']='changed'; path.write_text(json.dumps(data))
    with pytest.raises(ValueError,match='fingerprint'): load_contract(path)
    path.write_text('{"schema":{"version":"placement-v1"}}')
    with pytest.raises(ValueError): load_contract(path)
    from placement.training import load_policy
    from placement.config import PlacementConfig
    save_contract(path)
    with pytest.raises(Exception): load_policy(path,PlacementConfig())
    from pathlib import Path
    v1=next((Path(__file__).resolve().parents[1]/'results/task2/placement_v1_20261109_03').rglob('best.pt'))
    with pytest.raises(ValueError): load_contract(v1)


def test_initial_capacity_conditional_sample():
    c=Config(seconds=5,dag_arrival_s=0,cpu_cores=(.21,1.,1.),cpu_range=(.8,1.1))
    e=DAGEnv(c,templates()['diamond']); e.reset(seed=51001,initial_placement={n:0 for n in e.order})
    assert e.cpu[0]>=.2
    assert e.trace['initial_reserved']['cpu'][0]==pytest.approx(.2)


def test_target_serves_after_source_loss_in_grace():
    dag=DAG('one',(Service('a'),),())
    e,t=setup(dag,arrivals=(.6,),faults=({'time':.5,'kind':'cluster_down','cluster':0},))
    e.reset(trace=t,initial_placement={'a':0}); e.step({'agent_0':2})
    assert e.summary()['workflow_completed']==1 and e.jobs[0].cluster==1
    assert e.summary()['migration_downtime_service_s']==0


def test_slow_workflow_source_held_until_drained():
    dag=DAG('one',(Service('a',work_core_s=.8),),())
    e,t=setup(dag,arrivals=(.1,),readiness_s=.2,grace_s=.1)
    e.reset(trace=t,initial_placement={'a':0}); e.advance(.1)
    e.step({'agent_0':2})
    assert e.summary()['workflow_completed']==1
    releases=[r for r in e.logs if r['event']=='MIGRATION_COMPLETED']
    assert releases[0]['time_s']>=e.workflows[0].finish


@pytest.mark.parametrize('failure',['queue','ram','unavailable','bandwidth'])
def test_explicit_failures_or_censored(failure):
    dag=DAG('two',(Service('a',work_core_s=.2,queue_limit=0),Service('b')),(Edge('a','b'),)) if failure=='queue' else templates()['chain']
    e,t=setup(dag,arrivals=(.1,.1001),band=0 if failure=='bandwidth' else None,
              ram_error_coefficient=100 if failure=='ram' else .05)
    p={n:i%3 for i,n in enumerate(e.order)}
    if failure=='ram': t['faults']=[{'time':.05,'kind':'capacity','cluster':0,'memory_bytes':1}]
    if failure=='unavailable': t['faults']=[{'time':.05,'kind':'cluster_down','cluster':0}]
    e.reset(trace=t,initial_placement=p); e.advance(11)
    assert e.summary()['workflow_failed']==2 and e.summary()['sla_violations']==2
    e.assert_conservation()


def test_all_unplaced_keep_blocked_and_masks_reject_bad_input():
    e,t=setup(); o=e.reset(trace=t)
    assert not o['action_mask'][:,0].any()
    with pytest.raises(ValueError): e.step({'agent_4':1})
    with pytest.raises(ValueError): e.step({'agent_0':0})


def test_ram_capacity_fault_no_silent_eviction():
    dag=DAG('one',(Service('a',work_core_s=.2),),())
    e,t=setup(dag,arrivals=(.1,),ram_error_coefficient=0,
              faults=({'time':.15,'kind':'capacity','cluster':0,'memory_bytes':2**20},))
    e.reset(trace=t,initial_placement={'a':0}); e.advance(.2)
    assert e.workflows[0].status=='RUNNING' and e.states['a'].endpoints=={0:True}
    assert e._raw_resources()[3][0]>e.ram[0] and e.overloaded[0]
    assert e.summary()['workflow_failed']==0 and e.summary()['workflow_unfinished']==1


@pytest.mark.parametrize('scenario',['stable','ramp-up','spike','recovery','periodic','shift'])
def test_scenario_trace(scenario):
    trace=make_trace(Config(),51001,scenario)
    assert trace['source']=='SIMULATED' and trace['workflow_arrivals_s']


def test_service_loses_readiness_during_transfer_recovers():
    dag=DAG('two',(Service('a'),Service('b')),(Edge('a','b',10000),))
    e,t=setup(dag,band=1e5,faults=({'time':.15,'kind':'service_down','service':'b'},
                                  {'time':.3,'kind':'service_up','service':'b'}))
    s=run(e,t,{'a':0,'b':1})
    assert s['workflow_completed']==1 and s['traffic_bytes']==10000
    assert s['e2e_mean_s']>.2


def test_zero_payload_requires_no_bandwidth():
    dag=DAG('two',(Service('a'),Service('b')),(Edge('a','b',0),))
    e,t=setup(dag,band=0,lat=.02); p={'a':0,'b':1}; s=run(e,t,p)
    assert s['e2e_mean_s']==pytest.approx(critical_path(dag,p,e.config.latency_s,e.config.bandwidth_Bps))
    assert s['workflow_completed']==1 and s['traffic_bytes']==0


@pytest.mark.parametrize('method',['round_robin','least_loaded','random_valid','traffic_aware'])
def test_policies_reproducible_shared_trace(method):
    c=Config(seconds=10,dag_arrival_s=1); trace=make_trace(c,51001)
    results=[]
    for _ in range(2):
        e=DAGEnv(c,templates()['fan_out']); o=e.reset(trace=trace); p=Policy(method,51001); done=False
        while not done: o,_,done,_=e.step(p.actions(e,o))
        results.append(e.summary())
    assert results[0]==results[1]
