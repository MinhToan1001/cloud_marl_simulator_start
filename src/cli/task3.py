"""Task 3: smoke DAG V2 và xác minh, không có trainer hoặc executor Kubernetes."""
import argparse
from collections import defaultdict
from dataclasses import replace
from datetime import datetime,timezone
import json
from pathlib import Path
import platform
import statistics
import subprocess
import sys
import time
from placement.io import new_directory,write_csv,file_hash,fingerprint
from placement_v2.config import Config,provenance
from placement_v2.dag import templates
from placement_v2.env import DAGEnv
from placement_v2.trace import make_trace
from placement_v2.policies import Policy
from placement_v2.schema import manifest,code_fingerprints,save_contract

ROOT=Path(__file__).resolve().parents[2]
SCENARIOS=('stable','ramp_up','spike','recovery','periodic','shift')
METHODS=('round_robin','least_loaded','random_valid','traffic_aware')


def write_json(path,value):
    def native(v):
        if hasattr(v,'item'): return v.item()
        raise TypeError(type(v).__name__)
    Path(path).write_text(json.dumps(value,indent=2,ensure_ascii=False,allow_nan=False,default=native)+'\n',encoding='utf-8')


def smoke(output,seconds=30):
    stage=new_directory(output/'smoke'); traces_dir=new_directory(stage/'traces'); episodes=new_directory(stage/'episodes')
    profile_path=ROOT/'results/task2/placement_v1_20261109_03/calibration/profile.json'
    profile=json.loads(profile_path.read_text(encoding='utf-8'))
    config=replace(Config(),seconds=seconds,dag_arrival_s=min(4.,seconds/4),profile_sha256=file_hash(profile_path))
    write_json(stage/'config.json',config.to_dict()); write_json(stage/'parameter_provenance.json',provenance(config))
    save_contract(stage/'contract.json'); write_json(stage/'schema.json',manifest())
    write_json(stage/'calibration_usage.json',{'profile_path':str(profile_path.relative_to(ROOT)),
        'profile_hash':file_hash(profile_path),'profile_id':profile['profile_id'],'source':'CALIBRATED',
        'latency_reference_ms':profile['response']['service_latency_mean_ms'],
        'memory_reference_bytes':profile['response']['memory_baseline_bytes'],
        'cpu_purged_relative_mae_pct':60.8647,'maximum_capacity_rps':None,
        'mapping':'work_core_s = low-load mean latency seconds × giả định 1 core; baseline RAM/reference request = proxy tỷ lệ. Dùng chung Sample API class cho node DAG là ASSUMPTION(SIMULATED), không suy service demand nhân quả hoặc saturation.',
        'cpu_ols_not_extrapolated':True,'B_C_WAN':'ASSUMPTION(SIMULATED)'})
    rows=[]; started=time.perf_counter()
    for scenario in SCENARIOS:
        for seed in (51001,51002):
            trace=make_trace(config,seed,scenario); write_json(traces_dir/f'{scenario}_{seed}.json',trace)
            for name,dag in templates().items():
                for method in METHODS:
                    env=DAGEnv(config,dag); obs=env.reset(trace=trace); policy=Policy(method,seed); done=False; decisions=[]
                    while not done:
                        actions=policy.actions(env,obs)
                        decisions.append({'time_s':env.now,'agent_mask':obs['agent_mask'].tolist(),'decision_mask':obs['decision_mask'].tolist(),
                                          'action_mask':obs['action_mask'].tolist(),'actions':actions})
                        obs,reward,done,info=env.step(actions)
                    summary=env.summary(); env.assert_conservation()
                    record={'source':'SIMULATED','dag':name,'scenario':scenario,'seed':seed,'method':method,'trace_sha256':trace['sha256'],**summary}
                    rows.append({**record,'errors_by_type':json.dumps(summary['errors_by_type'],sort_keys=True)})
                    workflows=[{'workflow_id':w.id,'arrival_s':w.arrival,'finish_s':w.finish,'status':w.status,'failure':w.failure,
                                'sla_violation':w.sla_violation,'nodes':w.nodes,'bindings':w.bindings,'finishes':w.finishes} for w in env.workflows.values()]
                    write_json(episodes/f'{name}_{scenario}_{seed}_{method}.json',{'metrics':record,'decisions':decisions,'events':env.logs,
                        'resources':env.resource_records,'workflow_ledger':workflows,
                        'invocation_ledger':[vars(j) for j in env.jobs],'traffic_bytes_by_directed_link':env.link_bytes.tolist()})
            print(f'SIMULATED {scenario} seed={seed}: xong 4 DAG × 4 heuristic',flush=True)
    write_csv(stage/'metrics.csv',rows)
    aggregate=[]
    keys=('e2e_mean_s','e2e_p95_s','traffic_bytes','traffic_cost','cpu_usage_std','ram_usage_std','sla_violation_resolved',
          'workflow_arrivals','workflow_completed','workflow_failed','workflow_unfinished','service_invocations')
    for method in METHODS:
        part=[r for r in rows if r['method']==method]
        for key in keys:
            values=[r[key] for r in part]
            aggregate.append({'source':'SIMULATED','method':method,'metric':key,'n':len(values),'mean':statistics.mean(values),'std':statistics.stdev(values)})
    write_csv(stage/'aggregate.csv',aggregate)
    freeze=subprocess.run([sys.executable,'-m','pip','freeze'],capture_output=True,text=True,check=True).stdout
    (stage/'runtime-freeze.txt').write_text(freeze,encoding='utf-8')
    write_json(stage/'metadata.json',{'source':'SIMULATED','utc':datetime.now(timezone.utc).isoformat(),'seed_list':[51001,51002],
        'scenarios':SCENARIOS,'methods':METHODS,'episodes':len(rows),'seconds_per_episode':seconds,'wall_seconds':time.perf_counter()-started,
        'code_fingerprints':code_fingerprints(),'cli_fingerprint':file_hash(__file__),'schema_hash':manifest()['schema_hash'],
        'config_fingerprint':fingerprint(config.to_dict()),'trace_hashes':{p.name:file_hash(p) for p in traces_dir.glob('*.json')},
        'machine':{'python':sys.version,'platform':platform.platform()},'training':False,'closed_loop_ready':False})
    write_json(output/'report_summary.json',{'canonical_report':'docs/task3/REPORT.md','source':'SIMULATED',
        'episodes':len(rows),'aggregate':aggregate,
        'note':'Mỗi task có một báo cáo tổng hợp trong docs; không sinh Markdown lẻ trong results.'})


def validate(output,test_python=None):
    stage=new_directory(output/'validation')
    interpreter=str(Path(test_python).resolve()) if test_python else sys.executable
    r=subprocess.run([interpreter,'-m','pytest','tests','-q','--junitxml='+str(stage/'junit.xml')],cwd=ROOT,capture_output=True,text=True,encoding='utf-8',errors='replace')
    (stage/'pytest.txt').write_text(r.stdout+'\n'+r.stderr,encoding='utf-8')
    freeze=subprocess.run([interpreter,'-m','pip','freeze'],capture_output=True,text=True,check=True).stdout
    (stage/'runtime-freeze.txt').write_text(freeze,encoding='utf-8')
    write_json(stage/'status.json',{'exit_code':r.returncode,'interpreter':interpreter,'code_fingerprints':code_fingerprints(),'schema_hash':manifest()['schema_hash']})
    print(r.stdout,flush=True)
    if r.returncode: raise RuntimeError('Kiểm thử thất bại; đọc validation/pytest.txt')


def main():
    sys.stdout.reconfigure(encoding='utf-8')
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output',type=Path,required=True); parser.add_argument('--stage',choices=('all','smoke','validate'),default='all')
    parser.add_argument('--test-python',type=Path,help='Runtime đầy đủ cho regression API/Gymnasium; smoke vẫn dùng runtime hiện tại')
    args=parser.parse_args()
    if args.stage=='all': new_directory(args.output)
    else: args.output.mkdir(parents=True,exist_ok=True)
    if args.stage in ('all','smoke'): smoke(args.output)
    if args.stage in ('all','validate'): validate(args.output,args.test_python)


if __name__=='__main__': main()
