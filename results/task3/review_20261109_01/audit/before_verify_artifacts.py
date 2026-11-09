"""Đối chiếu độc lập ledger/trace/schema với artifact, không chạy lại policy."""
import csv
import argparse
import json
from pathlib import Path
import sys
from placement.io import fingerprint,file_hash,write_json
from placement_v2.schema import load_contract,manifest,code_fingerprints

ROOT=Path(__file__).resolve().parents[2]
sys.path.insert(0,str(ROOT/'tools'))
from retained_documents import retained_location


def read(p): return json.loads(p.read_text(encoding='utf-8'))


def verify(run_id,output=None):
    run=ROOT/'results/task3'/run_id
    meta=read(run/'smoke/metadata.json'); assert meta['code_fingerprints']==code_fingerprints()
    cli_location=retained_location(ROOT,'src/cli/task3.py',meta['cli_fingerprint'])
    assert cli_location, 'CLI đã chạy phải khớp file hiện tại hoặc bản gốc được lưu trữ'
    load_contract(run/'smoke/contract.json')
    assert read(run/'validation/status.json')['exit_code']==0
    assert read(run/'validation/status.json')['code_fingerprints']==code_fingerprints()
    traces={}
    for path in (run/'smoke/traces').glob('*.json'):
        t=read(path); sha=t.pop('sha256'); assert fingerprint(t)==sha
        assert meta['trace_hashes'][path.name]==file_hash(path)
        traces[t['scenario'],t['seed']]=sha
    paths=list((run/'smoke/episodes').glob('*.json')); assert len(paths)==192
    totals={'arrivals':0,'completed':0,'failed':0,'unfinished':0,'invocations':0}
    for p in paths:
        packet=read(p); s=packet['metrics']; workflows=packet['workflow_ledger']; jobs=packet['invocation_ledger']
        assert s['source']=='SIMULATED' and s['trace_sha256']==traces[s['scenario'],s['seed']]
        assert len({w['workflow_id'] for w in workflows})==len(workflows)==s['workflow_arrivals']
        completed=sum(w['status']=='COMPLETED' for w in workflows); failed=sum(w['status']=='FAILED' for w in workflows)
        assert completed==s['workflow_completed'] and failed==s['workflow_failed']
        assert len(workflows)-completed-failed==s['workflow_unfinished']
        assert sum(w['sla_violation'] for w in workflows)==s['sla_violations']<=completed+failed
        assert sum(s['errors_by_type'].values())==failed
        assert s['service_invocations']==len(jobs)  # smoke không có queue-overflow không tạo Job
        assert len({(j['workflow'],j['service']) for j in jobs})==len(jobs)
        for j in jobs: assert j['workflow'] in {w['workflow_id'] for w in workflows}
        for decision in packet['decisions']:
            enabled={f'agent_{i}' for i,v in enumerate(decision['decision_mask']) if v}
            assert set(decision['actions'])==enabled
            for agent,a in decision['actions'].items(): assert decision['action_mask'][int(agent.split('_')[1])][a]
        assert abs(sum(sum(row) for row in packet['traffic_bytes_by_directed_link'])-s['traffic_bytes'])<1e-6
        for k,col in [('arrivals','workflow_arrivals'),('completed','workflow_completed'),('failed','workflow_failed'),('unfinished','workflow_unfinished'),('invocations','service_invocations')]: totals[k]+=s[col]
    spec=manifest()
    with (ROOT/'docs/task3/SCHEMA_REAL_FEASIBILITY.csv').open(encoding='utf-8',newline='') as stream: features=list(csv.DictReader(stream))
    assert len(features)==len(spec['actor_features'])+len(spec['critic_features'])
    for scope,key in [('actor','actor_features'),('critic','critic_features')]:
        selected=[f for f in features if f['scope']==scope]
        assert [f['name'] for f in selected]==[f['name'] for f in spec[key]]
    before=read(ROOT/'results/task2/placement_v1_20261109_03_closeout/protected_before.json')
    locations={name:retained_location(ROOT,name,sha) for name,sha in before.items()}
    changed=[name for name,location in locations.items() if location is None]
    assert not changed,changed
    outcome={'status':'PASS','source':'OFFLINE_ARTIFACT_VERIFICATION','episodes':len(paths),'shared_traces':len(traces),
             'protected_files':len(before),'protected_changes':changed,'feature_rows':len(features),
             'archived_original_documents':sum(v=='archived_original' for v in locations.values()),
             'execution_cli_provenance':cli_location,
             'totals':totals,'code_fingerprints':code_fingerprints(),'schema_hash':spec['schema_hash']}
    target=Path(output) if output else run/'artifact_verification.json'
    if target.exists(): raise FileExistsError(target)
    write_json(target,outcome); print(json.dumps(outcome,indent=2))


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('run_id'); parser.add_argument('--output',type=Path)
    args=parser.parse_args(); verify(args.run_id,args.output)
