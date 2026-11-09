"""A4 SSH hai hop, kubectl GET, whitelist dữ liệu; không đọc key/kubeconfig vào log."""
import argparse
from datetime import datetime,timezone
import inspect
import json
import os
from pathlib import Path
import subprocess
import sys


def cluster_collect():
    import datetime,json,subprocess,shutil
    now=lambda:datetime.datetime.now(datetime.timezone.utc).isoformat()
    def meta(o):
        m=o.get('metadata',{});r={k:m[k] for k in ('name','namespace','uid','resourceVersion','generation','creationTimestamp','deletionTimestamp') if k in m}
        r['ownerReferences']=[{k:v for k,v in x.items() if k in ('apiVersion','kind','name','uid','controller')} for x in m.get('ownerReferences',[])]
        r['labels']={k:v for k,v in m.get('labels',{}).items() if k in ('app','app.kubernetes.io/name','app.kubernetes.io/instance','kubernetes.io/service-name')}
        return r
    def clean(o,kind):
        if 'items' in o:return {'apiVersion':o.get('apiVersion'),'kind':o.get('kind'),'metadata':meta(o),'items':[clean(x,kind) for x in o['items']]}
        r={'apiVersion':o.get('apiVersion'),'kind':o.get('kind'),'metadata':meta(o)};s=o.get('spec',{});t=o.get('status',{})
        if kind=='metrics':
            r.update({k:o[k] for k in ('timestamp','window','usage') if k in o})
            if 'containers' in o:r['containers']=[{'name':x['name'],'usage':x.get('usage',{})} for x in o['containers']]
        elif kind=='node':r['spec']={k:s[k] for k in ('providerID','unschedulable') if k in s};r['status']={k:t[k] for k in ('capacity','allocatable','nodeInfo') if k in t}
        elif kind=='namespace':r['status']={'phase':t.get('phase')}
        elif kind=='pod':
            r['spec']={k:s[k] for k in ('nodeName','overhead','resources','runtimeClassName') if k in s}
            for key in ('containers','initContainers','ephemeralContainers'):
                r['spec'][key]=[{k:v for k,v in c.items() if k in ('name','resources','restartPolicy')} for c in s.get(key,[])]
            r['status']={k:t[k] for k in ('phase','resize') if k in t}
            r['status']['containerStatuses']=[{k:v for k,v in c.items() if k in ('name','ready','restartCount','started','allocatedResources','resources')} for c in t.get('containerStatuses',[])]
        elif kind in ('deployment','replicaset'):
            r['spec']={k:s[k] for k in ('replicas','selector') if k in s}
            r['spec']['template']={'metadata':meta(s.get('template',{})),'spec':{'containers':[{k:v for k,v in c.items() if k in ('name','resources')} for c in s.get('template',{}).get('spec',{}).get('containers',[])]}}
            r['status']={k:t[k] for k in ('replicas','readyReplicas','availableReplicas','observedGeneration') if k in t}
        elif kind=='service':r['spec']={k:s[k] for k in ('selector','ports','clusterIP','type') if k in s}
        elif kind=='endpoint':
            r.update({k:o[k] for k in ('addressType','ports') if k in o})
            r['endpoints']=[{k:v for k,v in e.items() if k in ('addresses','conditions','nodeName','targetRef')} for e in o.get('endpoints',[])]
        if 'conditions' in t:r.setdefault('status',{})['conditions']=[{k:v for k,v in c.items() if k in ('type','status','reason','lastTransitionTime')} for c in t['conditions']]
        return r
    candidates=[['sudo','-n','k3s','kubectl'],['kubectl']];base=None;probe_errors=[]
    for candidate in candidates:
        p=subprocess.run(candidate+['get','nodes','-o','json'],capture_output=True,text=True,timeout=25) if shutil.which(candidate[0]) else None
        if p and p.returncode==0:base=candidate;break
        probe_errors.append({'candidate':candidate,'exit':p.returncode if p else None,'error':p.stderr[:300] if p else 'command unavailable'})
    result={'source':'REAL','mode':'READ_ONLY_GET','capture_started_utc':now(),'queries':{},'probe_errors':probe_errors}
    if base is None:result['status']='KUBECTL_UNAVAILABLE';print(json.dumps(result));return
    jobs=[('nodes','node',['get','nodes','-o','json']),('namespace','namespace',['get','namespace','cloud-calibration','-o','json']),
          ('pods_all','pod',['get','pods','-A','-o','json']),('deployments','deployment',['get','deployments','-n','cloud-calibration','-o','json']),
          ('replicasets','replicaset',['get','replicasets','-n','cloud-calibration','-o','json']),('services','service',['get','services','-n','cloud-calibration','-o','json']),
          ('endpointslices','endpoint',['get','endpointslices','-n','cloud-calibration','-o','json']),
          ('node_metrics','metrics',['get','--raw','/apis/metrics.k8s.io/v1beta1/nodes']),
          ('pod_metrics','metrics',['get','--raw','/apis/metrics.k8s.io/v1beta1/pods'])]
    for name,kind,args in jobs:
        try:
            p=subprocess.run(base+args,capture_output=True,text=True,timeout=25)
            result['queries'][name]={'captured_at_utc':now(),'exit_code':p.returncode,
                'data':clean(json.loads(p.stdout),kind) if p.returncode==0 else None,
                'error':p.stderr[:300] if p.returncode else None}
        except Exception as e:result['queries'][name]={'captured_at_utc':now(),'exit_code':-1,'error':str(e)[:200]}
    result['capture_finished_utc']=now();result['status']='CAPTURED' if all(q['exit_code']==0 for q in result['queries'].values()) else 'PARTIAL'
    print(json.dumps(result))


def capture(output,vm_user='nguyenminhtoan'):
    if output.exists():raise FileExistsError(output)
    output.mkdir(parents=True)
    key=Path(os.environ.get('ssh',str(Path.home()/'.ssh')))/'codex_vm_ed25519'
    target_script=inspect.getsource(cluster_collect)+'\ncluster_collect()\n'
    vm_script='''import os,subprocess,json
key='/opt/stack/.ssh/openstack_key'
command=['ssh','-T','-o','BatchMode=yes','-o','StrictHostKeyChecking=yes','-o','ConnectTimeout=7','-i',key,'ubuntu@172.24.4.16','python3','-']
if not os.access(key,os.R_OK):command=['sudo','-n','-u','stack','-H']+command
p=subprocess.run(command,input=PAYLOAD,capture_output=True,timeout=260)
print(json.dumps({'second_hop_exit':p.returncode,'payload':p.stdout.decode(),'error':p.stderr.decode()[:500]}))
'''.replace('PAYLOAD',repr(target_script.encode()))
    start=datetime.now(timezone.utc).isoformat()
    try:
        p=subprocess.run(['ssh','-T','-o','BatchMode=yes','-o','StrictHostKeyChecking=yes','-o','ConnectTimeout=7','-i',str(key),
            vm_user+'@192.168.107.128','python3','-'],input=vm_script.encode(),capture_output=True,timeout=280)
        diagnostic={'source':'OBSERVED_DIAGNOSTIC','start_utc':start,'end_utc':datetime.now(timezone.utc).isoformat(),
                    'first_hop_exit':p.returncode,'error':p.stderr.decode()[:500],'infrastructure_write':False}
        if p.returncode==0:
            hop=json.loads(p.stdout);diagnostic.update({k:v for k,v in hop.items() if k!='payload'})
            if hop['second_hop_exit']==0:
                snapshot=json.loads(hop['payload']);(output/'snapshot.json').write_text(json.dumps(snapshot,indent=2)+'\n',encoding='utf-8')
                diagnostic['snapshot_status']=snapshot['status']
        (output/'connectivity.json').write_text(json.dumps(diagnostic,indent=2)+'\n',encoding='utf-8');print(json.dumps(diagnostic))
    except Exception as e:
        diagnostic={'source':'OBSERVED_DIAGNOSTIC','start_utc':start,'error':str(e),'snapshot_status':'NOT_CAPTURED'}
        (output/'connectivity.json').write_text(json.dumps(diagnostic,indent=2)+'\n',encoding='utf-8');print(json.dumps(diagnostic))


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--output',type=Path,required=True)
    parser.add_argument('--vm-user',choices=('nguyenminhtoan','stack'),default='nguyenminhtoan')
    args=parser.parse_args();capture(args.output,args.vm_user)
