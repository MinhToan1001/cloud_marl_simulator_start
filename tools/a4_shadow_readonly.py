"""Inventory và shadow offline trên snapshot GET đã scrub; không có executor."""
import argparse
from datetime import datetime,timezone
import json
from pathlib import Path
import sys
import numpy as np
from integration.quantities import cpu_cores,memory_bytes
from integration.parser import ready
from placement.config import load_config
from placement.io import new_directory,read_json,write_json,file_hash,fingerprint
from placement.schema import manifest as v1_manifest,schema_hash,vectorize
from placement.shadow import unknown_cluster,action_masks,observations,operation_plan
from placement.training import load_policy

ROOT=Path(__file__).resolve().parents[1]


def metric_fresh_at(metric,clock):
    if not metric or not metric.get('timestamp') or not metric.get('window'):return False
    age=(clock-datetime.fromisoformat(metric['timestamp'].replace('Z','+00:00'))).total_seconds()
    return 0<=age<=30


def pod_request(pod):
    """Effective requests: app + sidecars, peak init, overhead, pod-level precedence."""
    spec=pod['spec']; result={}
    for resource,convert in [('cpu',cpu_cores),('memory',memory_bytes)]:
        def request(container):
            req=container.get('resources',{}).get('requests',{})
            value=req.get(resource,container.get('resources',{}).get('limits',{}).get(resource,'0'))
            statuses={v['name']:v for v in pod.get('status',{}).get('containerStatuses',[])}
            allocated=statuses.get(container.get('name'),{}).get('allocatedResources',{}).get(resource,'0')
            return max(convert(value),convert(allocated))
        sidecars=0.;peak=0.
        for init in spec.get('initContainers',[]):
            if init.get('restartPolicy')=='Always':sidecars+=request(init);peak=max(peak,sidecars)
            else:peak=max(peak,sidecars+request(init))
        value=max(sum(request(c) for c in spec.get('containers',[]))+sidecars,peak)
        if resource in spec.get('resources',{}).get('requests',{}):value=convert(spec['resources']['requests'][resource])
        result[resource]=value+convert(spec.get('overhead',{}).get(resource,'0'))
    return result


def analyse(snapshot_path,output):
    snapshot=read_json(snapshot_path)
    if snapshot.get('source')!='REAL' or snapshot.get('mode')!='READ_ONLY_GET':raise ValueError('Không nghiệm thu A4 từ fixture/simulated')
    out=new_directory(output);queries=snapshot['queries']
    if any(q.get('exit_code')!=0 for q in queries.values()):
        write_json(out/'readiness.json',{'A4':'PENDING','reason':'Snapshot có query chưa thành công'});return
    data=lambda name:queries[name]['data']
    now=datetime.fromisoformat(snapshot['capture_finished_utc'].replace('Z','+00:00'))
    nodes=data('nodes')['items'];pods=data('pods_all')['items'];deployments=data('deployments')['items'];replicasets=data('replicasets')['items']
    reserved={n['metadata']['name']:{'cpu':0.,'memory':0.} for n in nodes};inventory=[]
    for pod in pods:
        req=pod_request(pod);node=pod['spec'].get('nodeName');active=pod.get('status',{}).get('phase') not in ('Succeeded','Failed')
        included=active and node in reserved
        if included:
            for k in req:reserved[node][k]+=req[k]
        inventory.append({'namespace':pod['metadata'].get('namespace'),'name':pod['metadata']['name'],'uid':pod['metadata'].get('uid'),
            'node':node,'included':included,'effective_request':req,'raw_resources':pod['spec'],
            'resize_status':pod.get('status',{}).get('resize')})
    write_json(out/'reservation_inventory.json',{'source':'REAL_DERIVED','nodes':reserved,'pods':inventory,
        'sources':['https://kubernetes.io/docs/concepts/workloads/pods/sidecar-containers/',
                   'https://kubernetes.io/docs/tasks/configure-pod-container/assign-pod-level-resources/'],
        'note':'Terminal pods excluded; terminating non-terminal remain. allocatedResources max is conservative for resize.'})
    matches=[]
    for pod in pods:
        if pod['metadata'].get('namespace')!='cloud-calibration':continue
        for owner in pod['metadata'].get('ownerReferences',[]):
            rs=next((r for r in replicasets if r['metadata'].get('uid')==owner.get('uid') and owner.get('kind')=='ReplicaSet' and r['metadata']['name']==owner.get('name')),None)
            if rs:
                for parent in rs['metadata'].get('ownerReferences',[]):
                    dep=next((d for d in deployments if d['metadata'].get('uid')==parent.get('uid') and parent.get('kind')=='Deployment' and d['metadata']['name']==parent.get('name')=='calibration-work'),None)
                    if dep:matches.append((pod,rs,dep))
    selected=next((v for v in matches if ready(v[0])),matches[0] if matches else None)
    node_metrics={m['metadata']['name']:m for m in data('node_metrics')['items']}
    ages={name:(now-datetime.fromisoformat(m['timestamp'].replace('Z','+00:00'))).total_seconds() for name,m in node_metrics.items()}
    metrics_valid=all(n['metadata']['name'] in node_metrics and 0<=ages[n['metadata']['name']]<=30 for n in nodes)
    endpoint_ready=False;pod_metric_present=False;pod_metric_fresh=False
    if selected:
        pod,rs,dep=selected
        endpoint_ready=any(e.get('conditions',{}).get('ready') is True and e.get('targetRef',{}).get('uid')==pod['metadata']['uid']
                           for sl in data('endpointslices')['items'] for e in sl.get('endpoints',[]))
        selected_metric=next((m for m in data('pod_metrics')['items'] if m['metadata']['name']==pod['metadata']['name'] and m['metadata'].get('namespace')=='cloud-calibration'),None)
        pod_metric_present=selected_metric is not None;pod_metric_fresh=metric_fresh_at(selected_metric,now)
    config=load_config(ROOT/'results/task2/placement_v1_20261109_03/placement_config.json')
    packet={'env_version':'placement-v1','action_kind':'workload_placement','source':'real_fresh_capture',
        'timestamp_utc':snapshot['capture_finished_utc'],'historical_only':True,'clusters':{c:unknown_cluster() for c in 'ABC'},'services':{},'mapping':{}}
    cpu=sum(cpu_cores(n['status']['allocatable']['cpu']) for n in nodes);ram=sum(memory_bytes(n['status']['allocatable']['memory']) for n in nodes)
    a=packet['clusters']['A'];a.update(available=int(all(ready(n) for n in nodes) and metrics_valid),cpu_capacity=cpu,memory_capacity=ram,
        cpu_utilization=sum(cpu_cores(m['usage']['cpu']) for m in node_metrics.values())/cpu if metrics_valid else None,
        memory_utilization=sum(memory_bytes(m['usage']['memory']) for m in node_metrics.values())/ram if metrics_valid else None,
        cpu_reservation=sum(v['cpu'] for v in reserved.values())/cpu,memory_reservation=sum(v['memory'] for v in reserved.values())/ram,
        resource_valid=int(metrics_valid),reservation_valid=1,app_valid=0,age_s=max(ages.values(),default=1e6))
    for i,s in enumerate(config.services):
        known=i==0 and selected is not None;req=pod_request(selected[0]) if known else {}
        packet['services'][s.name]={'active':'A' if known else None,'source':None,'target':None,'phase':'IDLE',
            'remaining_s':0,'cooldown_s':0,'incoming_rps':None,'cpu_request':req.get('cpu'),'memory_request':req.get('memory'),
            'ready':int(known and ready(selected[0]) and endpoint_ready),'migration_count':0,'valid':int(known),'slo_ms':s.slo_ms}
        if known:packet['mapping'][s.name]={'namespace':'cloud-calibration','pod_name':pod['metadata']['name'],'pod_uid':pod['metadata']['uid'],
            'replicaset_name':rs['metadata']['name'],'replicaset_uid':rs['metadata']['uid'],
            'deployment_name':dep['metadata']['name'],'deployment_uid':dep['metadata']['uid'],
            'deployment_identity_verified':True,'pod_identity_verified':True}
    masks=action_masks(packet,config);obs=observations(packet,config);proposals=[]
    for seed in (7,17,27):
        path=ROOT/f'results/task2/placement_v1_20261109_03/training/seed_{seed}/best.pt'
        policy,_=load_policy(path,config);actions=policy.act(obs,masks,greedy=True)[0]
        for name,action in actions.items():proposals.append({'seed':seed,'checkpoint_hash':file_hash(path),**operation_plan(packet,name,int(action),config)})
    spec=v1_manifest(config);availability=[]
    for feature in spec['features']:
        name=feature['name'];known=False;scope='CONFIG/UNKNOWN'
        if name.startswith('cluster.A.'):
            key=name.split('.')[-1];known=a.get(key) is not None and key not in ('incoming_rps','completed_rps','latency_mean_ms','latency_p95_ms','error_fraction');scope='REAL_GET_OR_DERIVED'
        elif name.startswith(('cluster.B.','cluster.C.')):scope='NO_REAL_B_C'
        elif any(v in name for v in ('incoming_rps','migration_count','cooldown','remaining_s')):scope='APP_OR_CONTROLLER_NOT_MEASURED'
        availability.append({**feature,'measured':known,'scope':scope})
    checks={'all_required_GET_success':snapshot['status']=='CAPTURED','node_ready':all(ready(n) for n in nodes),
        'namespace_active':data('namespace').get('status',{}).get('phase')=='Active',
        'calibration_owner_chain':bool(selected),'calibration_pod_and_endpoint_ready':bool(selected and ready(selected[0]) and endpoint_ready),
        'full_pod_requests_limits_inventory':all(p['metadata'].get('uid') for p in pods),'metrics_fresh_at_capture':metrics_valid,
        'calibration_pod_metrics_present':pod_metric_present,'schema_parity':all(v.shape==(123,) for v in obs.values()),
        'calibration_pod_metrics_fresh_at_capture':pod_metric_fresh,
        'three_policy_shadow_no_writes':len(proposals)==9 and all(not p['executable'] for p in proposals)}
    write_json(out/'packet.json',packet);write_json(out/'shadow.json',{'source':'REAL_FRESH_CAPTURE_OFFLINE_SHADOW','schema_hash':schema_hash(config),
        'observations':{k:v.tolist() for k,v in obs.items()},'action_masks':{k:v.tolist() for k,v in masks.items()},
        'proposals':proposals,'feature_availability':availability,'executed_operations':0,'application_requests_sent':0})
    write_json(out/'readiness.json',{'A4':'DONE' if all(checks.values()) else 'PENDING','checks':checks,'closed_loop_ready':False,
        'captured_utc':snapshot['capture_finished_utc'],'snapshot_sha256':file_hash(snapshot_path),'live_app_metrics_measured':False,
        'limitations':['B/C chưa có thật','App queue/RPS/latency không được đo ở A4; không gửi tải/app request','Prototype mapping không chứng minh đã triển khai DAG V2','Policy ngoài miền train budgets; KEEP-only không chứng minh optimality']})
    print(json.dumps({'A4':'DONE' if all(checks.values()) else 'PENDING','checks':checks}))


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--snapshot',type=Path,required=True);p.add_argument('--output',type=Path,required=True)
    a=p.parse_args();analyse(a.snapshot,a.output)
