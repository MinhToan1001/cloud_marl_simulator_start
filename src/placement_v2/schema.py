"""Schema CTDE có thứ tự, đơn vị, fingerprint; actor chỉ thấy DAG lân cận."""
import json
from pathlib import Path
import numpy as np
from placement.io import fingerprint, file_hash, write_json
from . import ENV_VERSION, MAX_SERVICES

STATES=('UNPLACED','PREPARING','READY','MIGRATING','PENDING','REJECTED')


def feature(name,unit='ratio',scale=1.):
    return {'name':name,'unit':unit,'scale':scale}


SERVICE=[feature('exists','bool'),feature('role_id','index',5),
         *[feature('state.'+s,'bool') for s in STATES],
         *[feature('active.'+str(c),'bool') for c in range(3)],
         *[feature('endpoint_ready.'+str(c),'bool') for c in range(3)],
         feature('cpu_request','core'),feature('memory_request','byte',1024**3),
         feature('work','core-second'),feature('cpu_limit','core'),
         feature('cooldown_remaining','second',30),feature('move_count','count',8),
         feature('queue_length','count',128)]
CLUSTER=[feature('cpu_capacity','core',4),feature('ram_capacity','byte',1024**3),
         feature('cpu_usage'),feature('ram_usage'),feature('cpu_reservation'),feature('ram_reservation'),
         feature('available','bool'),feature('overloaded','bool'),feature('oom_risk','bool')]
NEIGHBOR=[feature('parent','bool'),feature('child','bool'),feature('edge_bytes','byte',1024**2),
          *[feature('active.'+str(c),'bool') for c in range(3)],feature('has_ready_endpoint','bool')]


def actor_features():
    return ([feature('episode_time','second',30),feature('dag_present','bool')]
            +[dict(f,name='self.'+f['name']) for f in SERVICE]
            +[dict(f,name=f'cluster.{c}.'+f['name']) for c in range(3) for f in CLUSTER]
            +[dict(f,name=f'neighbor.{i}.'+f['name']) for i in range(5) for f in NEIGHBOR]
            +[feature(f'network.{c}.{k}',unit,scale) for c in range(3)
              for k,unit,scale in [('mean_out_rtt','second',1),('mean_out_bandwidth','byte/second',1e7)]])


def critic_features():
    return ([feature('episode_time','second',30),feature('dag_present','bool')]
            +[dict(f,name=f'service.{i}.'+f['name']) for i in range(5) for f in SERVICE]
            +[dict(f,name=f'cluster.{c}.'+f['name']) for c in range(3) for f in CLUSTER]
            +[feature(f'edge.{i}.{j}.bytes','byte',1024**2) for i in range(5) for j in range(5)]
            +[feature(f'network.{i}.{j}.{k}',unit,scale) for i in range(3) for j in range(3)
              for k,unit,scale in [('rtt','second',1),('bandwidth','byte/second',1e7),('unlimited','bool',1),('price','cost/byte',1e-6)]])


def code_fingerprints():
    return {p.name:file_hash(p) for p in sorted(Path(__file__).parent.glob('*.py'))}


def manifest():
    value={'version':ENV_VERSION,'max_services':MAX_SERVICES,'actions':['KEEP','A','B','C'],
           'actor_features':actor_features(),'critic_features':critic_features(),
           'bandwidth_none_encoding':0,'padding':0,'dtype':'float32'}
    value['schema_hash']=fingerprint(value)
    return value


def save_contract(path,payload=None):
    write_json(path,{'schema':manifest(),'code_fingerprints':code_fingerprints(),'payload':payload})


def load_contract(path):
    try: value=json.loads(Path(path).read_text(encoding='utf-8'))
    except (UnicodeError,ValueError) as e: raise ValueError('Artifact không phải placement-v2 JSON') from e
    if value.get('schema')!=manifest(): raise ValueError('Version/schema V2 không khớp; không chấp nhận V1')
    if value.get('code_fingerprints')!=code_fingerprints(): raise ValueError('Code fingerprint không khớp')
    return value['payload']


def _service(env,name):
    if name is None: return [0.]*len(SERVICE)
    s=env.states[name]; spec=env.dag.service(name)
    return [1.,(env.order.index(name)+1)/5,*[float(s.status==x) for x in STATES],
            *[float(s.active==c) for c in range(3)],*[float(env.endpoint_ready(name,c)) for c in range(3)],
            spec.cpu_request,spec.memory_bytes/1024**3,spec.work_core_s,spec.cpu_limit,
            max(0,s.cooldown_until-env.now)/30,s.attempts/8,
            sum(j.service==name and j.status=='QUEUED' for j in env.jobs)/128]


def encode(env):
    cr,mr,cu,mu,_=env._raw_resources()
    clusters=[]
    for c in range(3):
        clusters += [env.cpu[c]/4,env.ram[c]/1024**3,cu[c]/env.cpu[c],mu[c]/env.ram[c],
                     cr[c]/env.cpu[c],mr[c]/env.ram[c],float(env.available[c]),float(env.overloaded[c]),float(mu[c]>env.ram[c])]
    header=[env.now/30,float(env.present)]
    actor=np.zeros((5,len(actor_features())),dtype=np.float32)
    agent=np.zeros(5,dtype=bool); decision=np.zeros(5,dtype=bool); masks=np.zeros((5,4),dtype=bool)
    for i,name in enumerate(env.order):
        agent[i]=env.present
        if not env.present: continue
        neighbors=[]
        for j in range(5):
            other=env.order[j] if j<len(env.order) else None
            edges=[e for e in env.dag.edges if {e.parent,e.child}=={name,other}]
            neighbors += ([float(other in env.dag.parents(name)),float(other in env.dag.children(name)),
                           sum(e.bytes_per_workflow for e in edges)/1024**2,
                           *[float(env.states[other].active==c) for c in range(3)],float(bool(env.serving_endpoints(other)))] if edges else [0.]*len(NEIGHBOR))
        network=[]
        for c in range(3):
            network += [float(np.mean([env.network['latency_s'][c][j] for j in range(3) if j!=c])),
                        float(np.mean([env.network['bandwidth_Bps'][c][j] or 0 for j in range(3) if j!=c]))/1e7]
        actor[i]=header+_service(env,name)+clusters+neighbors+network
        if env.states[name].status in ('UNPLACED','READY'):
            masks[i]=[env.validate(name,a)[0] for a in range(4)]
            if not masks[i].any(): raise RuntimeError('UNPLACED không có action phải được tự chuyển PENDING')
            decision[i]=True
    services=sum((_service(env,env.order[i] if i<len(env.order) and env.present else None) for i in range(5)),[])
    edges=[]
    for i in range(5):
        for j in range(5):
            edges.append(sum(e.bytes_per_workflow for e in env.dag.edges if i<len(env.order) and j<len(env.order) and e.parent==env.order[i] and e.child==env.order[j])/1024**2)
    net=[]
    for i in range(3):
        for j in range(3):
            b=env.network['bandwidth_Bps'][i][j]
            net += [env.network['latency_s'][i][j],0 if b is None else b/1e7,float(b is None),env.config.price_per_byte[i][j]/1e-6]
    global_state=np.asarray(header+services+clusters+edges+net,dtype=np.float32)
    return {'obs':actor,'global_state':global_state,'agent_mask':agent,'decision_mask':decision,'action_mask':masks,
            'schema_version':ENV_VERSION,'schema_hash':manifest()['schema_hash']}
