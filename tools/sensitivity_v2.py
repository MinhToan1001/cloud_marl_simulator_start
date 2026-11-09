"""Can thiệp một yếu tố, paired seed/arrivals, không train hoặc tune policy."""
import argparse
from copy import deepcopy
from dataclasses import replace
from datetime import datetime,timezone
import json
from pathlib import Path
import sys
import numpy as np
from placement.io import fingerprint,file_hash,new_directory,write_csv
from placement_v2.config import Config
from placement_v2.dag import DAG,Service,Edge
from placement_v2.env import DAGEnv
from placement_v2.schema import code_fingerprints,manifest

ROOT=Path(__file__).resolve().parents[1]
CASES=('base','cpu_high_A','ram_pressure_latency','ram_pressure_errors','bandwidth_low_AB','bandwidth_moderate_AB','rtt_high_AB',
       'payload_large','colocate','migration_slow_keep','migration_slow_move','migration_fast_keep','migration_fast_move',
       'idle_latency','empty_keep','empty_move')


def write_json(path,value):
    Path(path).write_text(json.dumps(value,indent=2,ensure_ascii=False,allow_nan=False,
        default=lambda v:v.item() if hasattr(v,'item') else str(v))+'\n',encoding='utf-8')


def fixture(seed,case):
    rng=np.random.default_rng(seed)
    arrivals=(np.linspace(.2,4.8,24)+rng.uniform(0,.015,24)).tolist()
    c=Config(seconds=6,decision_s=.25,dag_arrival_s=0,workflow_timeout_s=2,workflow_sla_s=.4,
             readiness_s=.2,grace_s=.3,cooldown_s=1,intra_latency_s=0,background_ram_fraction=0)
    services=(Service('a',work_core_s=.05,memory_bytes=8*1024**2,concurrency=2),
              Service('b',work_core_s=.05,memory_bytes=8*1024**2,concurrency=2))
    payload=100000 if case=='payload_large' else 10000
    dag=DAG('sensitivity_chain',services,(Edge('a','b',payload),)); placement={'a':0,'b':1}
    bg=[.1,.1,.1]; bandwidth=1e4 if case=='bandwidth_low_AB' else 1e5 if case=='bandwidth_moderate_AB' else 1e6
    rtt=.2 if case=='rtt_high_AB' else .01
    faults=[]
    if case=='cpu_high_A': bg[0]=1.4
    if case.startswith('ram_pressure'):
        faults=[{'kind':'capacity','time':.1,'cluster':0,'memory_bytes':1024**2}]
        if case=='ram_pressure_latency': c=replace(c,ram_error_coefficient=0)
    if case=='colocate': placement={'a':0,'b':0}
    if case.startswith('migration_') or case.startswith('empty_'):
        dag=DAG('one',(services[0],),()); placement={'a':0}
        if case.startswith('migration_slow'): bg[0]=1.4
        if case.startswith('migration_fast'): bg[1]=1.4
    if case=='idle_latency':
        arrivals=[.2]; c=replace(c,objectives=(1.,0.,0.,0.))
    if case.startswith('empty_'):
        arrivals=[]; c=replace(c,objectives=(0.,0.,0.,0.))  # Isolate explicit migration cost only.
    lat=tuple(tuple(0 if i==j else rtt for j in range(3)) for i in range(3))
    bw=tuple(tuple(None if i==j else bandwidth for j in range(3)) for i in range(3))
    trace={'source':'SIMULATED','seed':seed,'scenario':case,'cpu_capacity':[1.5]*3,'ram_capacity':[1024**3]*3,
           'background_ram_bytes':[0.]*3,'workflow_arrivals_s':arrivals,'faults':faults,
           'environment':[{'latency_s':lat,'bandwidth_Bps':bw,'background_cpu':bg}]}
    return c,dag,trace,placement


def run_case(seed,case):
    c,dag,trace,placement=fixture(seed,case); env=DAGEnv(c,dag); obs=env.reset(trace=trace,initial_placement=placement)
    ticks=[]; done=False
    while not done:
        actions={env.agent_names[i]:0 for i,v in enumerate(obs['decision_mask']) if v}
        if env.now==0 and (case.endswith('_move')): actions['agent_0']=2
        start=env.now; obs,reward,done,info=env.step(actions)
        ticks.append({'start_s':start,'end_s':env.now,'reward':reward,'components':info.get('reward_components'),
                      'interval':info.get('interval_metrics'),'actions':actions})
    s=env.summary(); env.assert_conservation()
    transfers=[]
    for j in env.jobs:
        wf=env.workflows[j.workflow]
        if j.service=='b' and 'a' in wf.finishes: transfers.append(j.ready_at-wf.finishes['a'])
    elapsed=[(wf.finish if wf.finish is not None else env.now)-wf.arrival for wf in env.workflows.values()]
    row={'source':'SIMULATED','seed':seed,'case':case,'arrivals_hash':fingerprint(trace['workflow_arrivals_s']),
         'trace_hash':fingerprint(trace),'dag_hash':fingerprint(dag.to_dict()),**s,
         'workflow_time_mean_s':float(np.mean(elapsed)) if elapsed else 0.,
         'transfer_mean_s':float(np.mean(transfers)) if transfers else None,
         'reward_sum':sum(t['reward'] for t in ticks),'first_tick_reward':ticks[0]['reward'],
         'late_idle_reward':sum(t['reward'] for t in ticks if t['start_s']>=2.)}
    ledger={'config':c.to_dict(),'dag':dag.to_dict(),'trace':trace,'placement':placement,'ticks':ticks,
            'workflow_ledger':[{'id':w.id,'arrival':w.arrival,'finish':w.finish,'status':w.status,'sla':w.sla_violation,'failure':w.failure} for w in env.workflows.values()],
            'events':env.logs,'summary':row}
    return row,ledger


def checks(rows):
    result=[]
    for seed in sorted({r['seed'] for r in rows}):
        r={v['case']:v for v in rows if v['seed']==seed}; b=r['base']
        def add(name,condition,detail):result.append({'seed':seed,'check':name,'passed':bool(condition),'detail':detail})
        add('CPU queue/time increases',r['cpu_high_A']['workflow_time_mean_s']>b['workflow_time_mean_s'] and r['cpu_high_A']['queue_mean_s']>=b['queue_mean_s'],'So paired arrivals; đọc failed/censored cùng completed latency')
        add('RAM latency increases without random errors',r['ram_pressure_latency']['workflow_time_mean_s']>b['workflow_time_mean_s'],'Error coefficient=0 chỉ để isolate latency penalty')
        add('RAM pressure produces failure/SLA',r['ram_pressure_errors']['workflow_failed']>b['workflow_failed'] and r['ram_pressure_errors']['sla_violations']>b['sla_violations'],'Hash theo workflow ID, cùng seed')
        add('Moderate bandwidth drop increases observed transfer',r['bandwidth_moderate_AB']['transfer_mean_s'] is not None and r['bandwidth_moderate_AB']['transfer_mean_s']>b['transfer_mean_s'],'Cùng payload/placement/RTT; cohort có transfer hoàn tất')
        add('Severe bandwidth starvation is visible as failures',r['bandwidth_low_AB']['workflow_failed']>b['workflow_failed'] and r['bandwidth_low_AB']['workflow_time_mean_s']>b['workflow_time_mean_s'],'Không gán latency=0 khi không có mẫu hoàn tất; giữ severe case và báo censoring')
        add('RTT increase raises transfer',r['rtt_high_AB']['transfer_mean_s']>b['transfer_mean_s'],'Cùng payload/bandwidth/placement')
        add('Payload raises bytes/cost',r['payload_large']['traffic_bytes']>b['traffic_bytes'] and r['payload_large']['traffic_cost']>b['traffic_cost'],'Giữ arrival cohort và đơn giá')
        add('Helpful move reduces workflow time',r['migration_slow_move']['workflow_time_mean_s']<r['migration_slow_keep']['workflow_time_mean_s'],'Từ CPU nghẽn sang CPU rảnh')
        add('Harmful move increases workflow time',r['migration_fast_move']['workflow_time_mean_s']>r['migration_fast_keep']['workflow_time_mean_s'],'Từ CPU rảnh sang CPU nghẽn')
        add('CPU pressure worsens reward in controlled fixture',r['cpu_high_A']['reward_sum']<b['reward_sum'],'So cùng objective/arrivals, không tune trọng số')
        add('Helpful move improves total reward despite operation cost',r['migration_slow_move']['reward_sum']>r['migration_slow_keep']['reward_sum'],'Chỉ kết luận trong fixture tải CPU chênh lệch rõ')
        add('Harmful move worsens reward',r['migration_fast_move']['reward_sum']<r['migration_fast_keep']['reward_sum'],'Không khẳng định mọi migration đều xấu/tốt')
        add('No stale latency penalty on idle ticks',abs(r['idle_latency']['late_idle_reward'])<1e-9,'Isolate latency objective; sau 2s không còn request')
        add('Explicit migration cost without demand',r['empty_move']['reward_sum']<r['empty_keep']['reward_sum']-1e-9,'Bốn trọng số objective=0 để isolate migration penalty')
    return result


def main():
    parser=argparse.ArgumentParser(description=__doc__); parser.add_argument('--output',type=Path,required=True)
    args=parser.parse_args(); out=new_directory(args.output); ledgers=new_directory(out/'episodes'); rows=[]
    for seed in (70101,70102,70103):
        for case in CASES:
            row,ledger=run_case(seed,case); rows.append(row); write_json(ledgers/f'{seed}_{case}.json',ledger)
        print(f'SIMULATED sensitivity seed={seed}: {len(CASES)} interventions',flush=True)
    write_json(out/'metrics.json',rows)
    write_csv(out/'metrics.csv',({**r,'errors_by_type':json.dumps(r['errors_by_type'],sort_keys=True)} for r in rows))
    result=checks(rows);write_json(out/'checks.json',result)
    write_json(out/'metadata.json',{'source':'SIMULATED','utc':datetime.now(timezone.utc).isoformat(),
        'seeds':[70101,70102,70103],'code_fingerprints':code_fingerprints(),'schema_hash':manifest()['schema_hash'],
        'script_sha256':file_hash(__file__),'training':False,'tuned':False,'cases':CASES,
        'checks_passed':sum(r['passed'] for r in result),'checks_total':len(result)})
    print(json.dumps({'passed':sum(r['passed'] for r in result),'total':len(result),'failed':[r for r in result if not r['passed']]},indent=2,ensure_ascii=False))


if __name__=='__main__':
    sys.stdout.reconfigure(encoding='utf-8'); main()
