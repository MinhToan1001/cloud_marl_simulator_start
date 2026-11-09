"""DAG rời rạc theo sự kiện; lifecycle độc lập khả năng phục vụ của endpoint.

Workflow ID là đơn vị conservation/SLA. Node invocation và transfer có ledger
riêng; fork-join fail-fast, không retry ngầm và không đếm terminal hai lần.
"""
from collections import Counter,defaultdict,deque
from copy import deepcopy
from dataclasses import dataclass,field
import hashlib
import heapq
import math
import numpy as np

from placement.io import fingerprint
from . import MAX_SERVICES
from .trace import make_trace

EPS=1e-10


@dataclass
class Deployment:
    status: str='UNPLACED'
    phase: str='IDLE'
    active: int|None=None
    source: int|None=None
    target: int|None=None
    endpoints: dict=field(default_factory=dict)
    cooldown_until: float=0.
    pending_since: float|None=None
    retries: int=0
    attempts: int=0
    generation: int=0
    started: float=0.


@dataclass
class Workflow:
    id: int
    arrival: float
    status: str='WAITING'
    nodes: dict=field(default_factory=dict)
    received: dict=field(default_factory=dict)
    bindings: dict=field(default_factory=dict)
    execution: dict=field(default_factory=dict)
    finishes: dict=field(default_factory=dict)
    flows: dict=field(default_factory=dict)
    finish: float|None=None
    failure: str=''
    sla_violation: bool=False


@dataclass
class Job:
    workflow: int
    service: str
    cluster: int
    remaining: float
    ready_at: float
    started: float|None=None
    status: str='QUEUED'


@dataclass
class Transfer:
    workflow: int
    parent: str
    child: str
    payload: int
    origin: int|None=None
    target: int|None=None
    latency_left: float=0.
    bytes_left: float=0.
    status: str='WAIT_ENDPOINT'


class DAGEnv:
    def __init__(self,config,dag):
        self.config,self.dag=config,dag
        self.order=dag.topological()
        self.agent_names=[f'agent_{i}' for i in range(MAX_SERVICES)]

    def reset(self,seed=51001,scenario='stable',trace=None,initial_placement=None):
        allocated={'cpu':[0.]*3,'ram':[0.]*3}
        if initial_placement is not None:
            if set(initial_placement)!=set(self.order) or any(type(c) is not int or c not in range(3) for c in initial_placement.values()): raise ValueError('Initial placement không hợp lệ')
            for n,c in initial_placement.items():
                allocated['cpu'][c]+=self.dag.service(n).cpu_request; allocated['ram'][c]+=self.dag.service(n).memory_bytes
        self.trace=deepcopy(trace) if trace is not None else make_trace(self.config,seed,scenario,initial_reserved=allocated)
        if self.trace['source']!='SIMULATED': raise ValueError('V2 physics phải gắn nhãn SIMULATED')
        self.trace['sha256']=fingerprint({k:v for k,v in self.trace.items() if k!='sha256'})
        self.now=0.; self.present=False; self.controlled_dag_registered=True
        self.states={name:Deployment() for name in self.order}
        self.cpu=np.array(self.trace['cpu_capacity'],dtype=float)
        self.ram=np.array(self.trace['ram_capacity'],dtype=float)
        self.available=[True]*3; self.service_health={n:True for n in self.order}
        self.background_ram=np.array(self.trace['background_ram_bytes'],dtype=float)
        self.network=self.trace['environment'][0]
        self.heap=[]; self.sequence=0; self.last_boundary=None
        self.workflows={}; self.next_workflow=0; self.jobs=[]; self.queues=defaultdict(deque)
        self.total=Counter(); self.logs=[]; self.resource_records=[]; self.overloaded=[False]*3
        self.resource_integral=np.zeros((3,4)); self.balance_integral=np.zeros(4); self.waste_integral=0.
        self.link_bytes=np.zeros((3,3)); self.traffic_cost=0.; self.latencies=[]; self.queue_delays=[]
        self.migration_latencies=[]; self.stage_history=[]
        self._schedule(self.config.dag_arrival_s,'dag',None)
        for t in self.trace['workflow_arrivals_s']:
            if 0<=t<self.config.seconds: self._schedule(t,'arrival',None)
        for i,e in enumerate(self.trace['environment'][1:],1): self._schedule(float(i),'environment',e)
        for fault in self.trace['faults']: self._schedule(float(fault['time']),'fault',fault)
        self._process_due(); self._settle()
        if initial_placement is not None:
            if self.config.dag_arrival_s!=0 or set(initial_placement)!=set(self.order): raise ValueError('Initial placement cần DAG hiện diện và đủ service')
            for name,c in initial_placement.items():
                if c not in range(3): raise ValueError('Cluster initial sai')
                self.states[name]=Deployment(status='READY',active=c,endpoints={c:True})
            cpu,ram=self.reservations()
            if (cpu>self.cpu+EPS).any() or (ram>self.ram+EPS).any(): raise ValueError('Capacity lấy mẫu không đủ allocation ban đầu')
        self._boundary(); self._settle(); self.assert_conservation()
        return self.observe()

    def add_controlled_dag(self,dag):
        raise ValueError('Chỉ một controlled DAG trong mỗi episode; nhiều DAG để hướng mở rộng')

    def _schedule(self,time,kind,payload):
        self.sequence+=1; heapq.heappush(self.heap,(float(time),self.sequence,kind,payload))

    def _log(self,event,**fields): self.logs.append({'time_s':self.now,'event':event,**fields})

    def reservations(self):
        cpu=np.zeros(3); ram=np.zeros(3)
        for name,state in self.states.items():
            service=self.dag.service(name)
            for c in state.endpoints:
                cpu[c]+=service.cpu_request; ram[c]+=service.memory_bytes
        return cpu,ram

    def endpoint_ready(self,name,c):
        return c is not None and self.states[name].endpoints.get(c,False) and self.available[c] and self.service_health[name]

    def serving_endpoints(self,name):
        state=self.states[name]
        preferred=[state.active,state.source,state.target,*sorted(state.endpoints)]
        result=[]
        for c in preferred:
            if c not in result and self.endpoint_ready(name,c): result.append(c)
        return result

    def _raw_resources(self):
        cpu_res,ram_res=self.reservations()
        active=[j for j in self.jobs if j.status=='RUNNING']
        memory=self.background_ram.copy()
        for name,state in self.states.items():
            memory+=np.array([self.dag.service(name).memory_bytes*self.config.memory_baseline_fraction if c in state.endpoints else 0 for c in range(3)])
        for j in self.jobs:
            if j.status in ('QUEUED','RUNNING'): memory[j.cluster]+=self.config.invocation_memory_bytes
        background=np.array(self.network['background_cpu'])
        rates={}; cpu_usage=background.copy()
        for c in range(3):
            group=[j for j in active if j.cluster==c]
            free=max(0.,self.cpu[c]-background[c]) if self.available[c] else 0.
            remaining=list(group); grants={id(j):0. for j in group}
            counts=Counter(j.service for j in group)
            caps={id(j):self.dag.service(j.service).cpu_limit/counts[j.service] for j in group}
            while remaining and free>EPS:
                share=free/len(remaining)
                small=[j for j in remaining if caps[id(j)]<=share+EPS]
                if not small:
                    for j in remaining: grants[id(j)]=share
                    break
                for j in small:
                    grant=caps[id(j)]; grants[id(j)]=grant; free-=grant; remaining.remove(j)
            penalty=1+self.config.ram_latency_penalty*max(0.,memory[c]/self.ram[c]-1)
            for j in group:
                cpu_usage[c]+=grants[id(j)]; rates[id(j)]=grants[id(j)]/penalty
        return cpu_res,ram_res,cpu_usage,memory,rates

    def _check_overload(self):
        cr,mr,cu,mu,_=self._raw_resources()
        for c in range(3):
            current=bool(cr[c]>self.cpu[c]+EPS or mr[c]>self.ram[c]+EPS or cu[c]>self.cpu[c]+EPS or mu[c]>self.ram[c]+EPS)
            if current!=self.overloaded[c]: self._log('OVERLOADED' if current else 'OVERLOAD_RECOVERED',cluster=c,cpu_reserved=float(cr[c]),ram_reserved=float(mr[c]),cpu_capacity=float(self.cpu[c]),ram_capacity=float(self.ram[c]))
            self.overloaded[c]=current

    def validate(self,name,action):
        state=self.states[name]
        if type(action) is not int or action not in range(4): return False,'invalid_action'
        unplaced=state.active is None
        if not unplaced and action==0: return True,'KEEP'
        if unplaced and action==0: return False,'KEEP_UNPLACED'
        c=action-1
        if c==state.active: return False,'use_KEEP_for_current_cluster'
        if not self.available[c] or self.overloaded[c]: return False,'unavailable_or_overloaded'
        if not unplaced and (self.now<state.cooldown_until-EPS or state.attempts>=self.config.max_moves): return False,'cooldown_or_limit'
        if state.status not in ('UNPLACED','PENDING','READY'): return False,'lifecycle_busy'
        if unplaced and any(not self.states[p].endpoints for p in self.dag.parents(name)): return False,'topological_dependency'
        if not unplaced and sum(s.status=='MIGRATING' for s in self.states.values())>=self.config.max_concurrent_moves: return False,'migration_concurrency'
        cr,mr,cu,mu,_=self._raw_resources(); service=self.dag.service(name)
        if max(cr[c],cu[c])+service.cpu_request>self.cpu[c]+EPS or max(mr[c],mu[c])+service.memory_bytes>self.ram[c]+EPS: return False,'resource_headroom'
        return True,'PLACE' if unplaced else 'MOVE'

    def _boundary(self):
        if self.last_boundary==self.now: return
        self.last_boundary=self.now; self._check_overload()
        self.total['overloaded_ticks']+=int(any(self.overloaded))
        if not self.present: return
        for name in self.order:
            s=self.states[name]
            if s.status not in ('UNPLACED','PENDING'): continue
            choices=any(self.validate(name,a)[0] for a in (1,2,3))
            if choices:
                s.status='UNPLACED'; continue
            if s.pending_since is None: s.pending_since=self.now
            s.retries+=1; s.status='PENDING'
            if s.retries>=self.config.pending_retries or self.now-s.pending_since>=self.config.pending_timeout_s-EPS:
                s.status='REJECTED'; self._log('REJECTED',service=name,retries=s.retries)
            else: self._log('PENDING',service=name,retries=s.retries)
        self.total['pending_ticks']+=int(any(s.status=='PENDING' for s in self.states.values()))
        cr,mr,cu,mu,_=self._raw_resources()
        self.resource_records.append({'time_s':self.now,'cpu_capacity':self.cpu.tolist(),'ram_capacity':self.ram.tolist(),
            'cpu_usage':cu.tolist(),'ram_usage':mu.tolist(),'cpu_reservation':cr.tolist(),'ram_reservation':mr.tolist(),
            'overloaded':list(self.overloaded),'oom_risk':(mu>self.ram).tolist()})

    def observe(self):
        from .schema import encode
        return encode(self)

    def step(self,actions):
        if self.now>=self.config.seconds-EPS: raise RuntimeError('Episode đã kết thúc')
        observation=self.observe()
        expected={self.agent_names[i] for i,v in enumerate(observation['decision_mask']) if v}
        if set(actions)!=expected: raise ValueError('Chỉ truyền action cho đúng decision_mask; không inference padding/lifecycle busy')
        for i,name in enumerate(self.order):
            if self.agent_names[i] not in actions: continue
            action=actions[self.agent_names[i]]
            if type(action) is not int or action not in range(4) or not observation['action_mask'][i,action]:
                raise ValueError('Action không hợp lệ theo mask trước quyết định')
            valid,kind=self.validate(name,action)
            if not valid:
                if self.states[name].active is None:
                    self.states[name].status='PENDING'; self.states[name].pending_since=self.now
                self._log('proposal_rejected',service=name,action=action,reason=kind); continue
            if action==0: continue
            state=self.states[name]; target=action-1
            state.generation+=1; state.target=target; state.started=self.now; state.endpoints[target]=False
            state.status='PREPARING' if kind=='PLACE' else 'MIGRATING'; state.phase='PREPARING'
            if kind=='MOVE': state.attempts+=1; self.total['migration_started']+=1
            else: self.total['deployments_created']+=1
            self._log(kind,service=name,target=target)
            ready_factor=self.trace.get('readiness_factors',[1.]*MAX_SERVICES)[i]
            self._schedule(self.now+self.config.readiness_s*ready_factor,'ready',(name,state.generation))
            self._schedule(self.now+self.config.migration_timeout_s,'prepare_timeout',(name,state.generation))
        before=self.summary()
        self.advance(min(self.config.seconds,self.now+self.config.decision_s))
        self._boundary(); self._settle(); self.assert_conservation()
        after=self.summary(); w=self.config.objectives
        reward=-(w[0]*after['e2e_mean_s']/self.config.workflow_sla_s+w[1]*(after['traffic_cost']-before['traffic_cost'])
                 +w[2]*(after['cpu_usage_std']+after['ram_usage_std'])+w[3]*after['sla_violation_resolved'])
        return self.observe(),float(reward),self.now>=self.config.seconds-EPS,{'source':'SIMULATED','metrics':after}

    def _process_due(self):
        while self.heap and self.heap[0][0]<=self.now+EPS:
            _,_,kind,payload=heapq.heappop(self.heap)
            if kind=='dag': self.present=True; self._log('DAG_ARRIVAL',dag=self.dag.name)
            elif kind=='arrival': self._arrive()
            elif kind=='environment': self.network=payload
            elif kind=='timeout':
                wf=self.workflows[payload]
                if wf.status not in ('COMPLETED','FAILED'): self._fail(wf,'workflow_timeout')
            elif kind=='fault': self._fault(payload)
            elif kind in ('ready','prepare_timeout','release'): self._lifecycle(kind,payload)
        self._check_overload()

    def _lifecycle(self,kind,payload):
        name,generation=payload; state=self.states[name]
        if state.generation!=generation: return
        if kind=='ready' and state.phase=='PREPARING':
            if not self.available[state.target]: self._rollback(name); return
            state.endpoints[state.target]=True
            if state.active is None:
                state.active=state.target; state.target=None; state.status='READY'; state.phase='IDLE'; self._log('DEPLOYMENT_READY',service=name)
            else:
                state.source=state.active; state.active=state.target; state.phase='GRACE'
                self._log('TARGET_READY',service=name,active=state.active)
                self._schedule(self.now+self.config.grace_s,'release',(name,generation))
        elif kind=='prepare_timeout' and state.phase=='PREPARING': self._rollback(name)
        elif kind=='release' and state.phase=='GRACE':
            if self._referenced(name,state.source): self._schedule(self.now+.01,'release',payload); return
            state.endpoints.pop(state.source,None); state.source=state.target=None; state.status='READY'; state.phase='IDLE'
            state.cooldown_until=self.now+self.config.cooldown_s; self.total['migration_completed']+=1
            self.migration_latencies.append(self.now-state.started); self._log('MIGRATION_COMPLETED',service=name)

    def _rollback(self,name):
        state=self.states[name]; moved=state.status=='MIGRATING'
        if state.phase=='GRACE':
            self._fail_endpoint_jobs(name,state.active,'migration_target_failed')
            state.endpoints.pop(state.active,None); state.active=state.source
        elif state.target is not None: state.endpoints.pop(state.target,None)
        state.target=state.source=None; state.phase='IDLE'; state.generation+=1
        state.status='READY' if state.active is not None else 'PENDING'
        state.cooldown_until=self.now+self.config.cooldown_s
        if moved: self.total['migration_failed']+=1
        self._log('ROLLBACK',service=name,serving_endpoints=self.serving_endpoints(name))

    def _referenced(self,name,cluster):
        for wf in self.workflows.values():
            if wf.status in ('COMPLETED','FAILED'): continue
            if wf.bindings.get(name)==cluster and wf.nodes.get(name) not in ('DONE',None): return True
            if any(f.status not in ('DONE','CANCELLED') and ((f.parent==name and f.origin==cluster) or (f.child==name and f.target==cluster)) for f in wf.flows.values()): return True
        return False

    def _fault(self,fault):
        kind=fault['kind']; c=fault.get('cluster',0)
        if kind=='capacity':
            for key in ('cpu_cores','memory_bytes'):
                if key in fault and (not math.isfinite(float(fault[key])) or float(fault[key])<=0): raise ValueError('Capacity fault phải dương hữu hạn')
            if 'cpu_cores' in fault: self.cpu[c]=float(fault['cpu_cores'])
            if 'memory_bytes' in fault: self.ram[c]=float(fault['memory_bytes'])
        elif kind in ('cluster_down','cluster_up'):
            self.available[c]=kind=='cluster_up'
            if kind=='cluster_down':
                for name in self.order:
                    self._fail_endpoint_jobs(name,c,'cluster_unavailable')
                    state=self.states[name]
                    if state.phase in ('PREPARING','GRACE') and state.target==c: self._rollback(name)
        elif kind in ('service_down','service_up'):
            name=fault['service']; self.service_health[name]=kind=='service_up'
            if kind=='service_down':
                for c in list(self.states[name].endpoints): self._fail_endpoint_jobs(name,c,'service_failed')
        else: raise ValueError('Fault không được hỗ trợ')
        self._log('FAULT',detail=deepcopy(fault)); self._check_overload()

    def _arrive(self):
        wf=Workflow(self.next_workflow,self.now,nodes={n:'WAIT_DEPENDS' for n in self.order},received={n:set() for n in self.order})
        self.next_workflow+=1; self.workflows[wf.id]=wf; self.total['workflow_arrivals']+=1
        self._schedule(self.now+self.config.workflow_timeout_s,'timeout',wf.id)
        waiting=sum(w.status=='WAITING' for w in self.workflows.values())
        if waiting>self.config.workflow_queue_limit: self._fail(wf,'workflow_queue_overflow')

    def _fail(self,wf,reason):
        if wf.status in ('COMPLETED','FAILED'): return
        wf.status='FAILED'; wf.finish=self.now; wf.failure=reason; wf.sla_violation=True
        self.total['workflow_failed']+=1; self.total['workflow_sla_violations']+=1; self.total['error_'+reason]+=1
        for job in self.jobs:
            if job.workflow==wf.id and job.status in ('QUEUED','RUNNING'):
                job.status='CANCELLED'; self.total['invocations_cancelled']+=1; wf.nodes[job.service]='CANCELLED'
        for flow in wf.flows.values():
            if flow.status!='DONE': flow.status='CANCELLED'
        self._log('WORKFLOW_FAILED',workflow_id=wf.id,reason=reason)

    def _fail_endpoint_jobs(self,name,c,reason):
        for job in self.jobs:
            if job.service==name and job.cluster==c and job.status in ('QUEUED','RUNNING'): self._fail(self.workflows[job.workflow],reason)

    def _enqueue(self,wf,name,c):
        if wf.status!='RUNNING' or wf.nodes[name]!='WAIT_DEPENDS': return
        self.total['service_invocations']+=1; wf.nodes[name]='QUEUED'; wf.bindings[name]=c
        service=self.dag.service(name); queue=self.queues[name,c]
        if sum(j.status=='QUEUED' for j in queue)>=service.queue_limit:
            self.total['invocations_failed']+=1; wf.nodes[name]='FAILED'; self._fail(wf,'service_queue_overflow'); return
        job=Job(wf.id,name,c,service.work_core_s,self.now); self.jobs.append(job); queue.append(job)

    def _settle(self):
        for wf in list(self.workflows.values()):
            if wf.status in ('COMPLETED','FAILED'): continue
            if any(s.status=='REJECTED' for s in self.states.values()): self._fail(wf,'deployment_rejected'); continue
            if wf.status=='WAITING':
                if not self.present or not all(self.serving_endpoints(n) for n in self.order): continue
                wf.status='RUNNING'
                for name in self.order:
                    if not self.dag.parents(name): self._enqueue(wf,name,self.serving_endpoints(name)[0])
            for edge in self.dag.edges:
                key=(edge.parent,edge.child)
                if wf.nodes[edge.parent]=='DONE' and key not in wf.flows:
                    wf.flows[key]=Transfer(wf.id,edge.parent,edge.child,edge.bytes_per_workflow)
            for flow in wf.flows.values():
                if flow.status=='WAIT_ENDPOINT':
                    endpoints=self.serving_endpoints(flow.child)
                    if not endpoints: continue
                    target=wf.bindings.setdefault(flow.child,endpoints[0])
                    if not self.endpoint_ready(flow.child,target): continue
                    flow.origin=wf.execution[flow.parent]; flow.target=target
                    flow.latency_left=self.config.intra_latency_s if flow.origin==target else self.network['latency_s'][flow.origin][target]
                    flow.bytes_left=float(flow.payload); flow.status='TRANSFER'
                if (flow.status=='TRANSFER' and self.available[flow.origin] and self.endpoint_ready(flow.child,flow.target)
                    and flow.latency_left<=EPS and (flow.bytes_left<=EPS or flow.origin==flow.target or self.network['bandwidth_Bps'][flow.origin][flow.target] is None)):
                    if flow.origin!=flow.target: self._traffic(flow,flow.bytes_left)
                    flow.bytes_left=0.; flow.status='DONE'; wf.received[flow.child].add(flow.parent)
            for name in self.order:
                if wf.nodes[name]=='WAIT_DEPENDS' and self.dag.parents(name) and set(self.dag.parents(name))<=wf.received[name]:
                    c=wf.bindings[name]
                    if self.endpoint_ready(name,c): self._enqueue(wf,name,c)
        for (name,c),queue in self.queues.items():
            active=sum(j.status=='RUNNING' and j.service==name and j.cluster==c for j in self.jobs)
            while queue and active<self.dag.service(name).concurrency:
                job=queue[0]
                if job.status!='QUEUED': queue.popleft(); continue
                if not self.endpoint_ready(name,c): break
                _,_,_,mem,_=self._raw_resources()
                excess=max(0.,mem[c]/self.ram[c]-1)
                u=int(hashlib.sha256(f'{self.trace["seed"]}:{job.workflow}:{name}'.encode()).hexdigest()[:12],16)/16**12
                if u<min(.95,self.config.ram_error_coefficient*excess):
                    queue.popleft(); job.status='FAILED'; self.total['invocations_failed']+=1
                    self.workflows[job.workflow].nodes[name]='FAILED'; self._fail(self.workflows[job.workflow],'ram_pressure'); continue
                queue.popleft(); job.status='RUNNING'; job.started=self.now
                self.workflows[job.workflow].nodes[name]='RUNNING'; self.queue_delays.append(self.now-job.ready_at); active+=1

    def _traffic(self,flow,amount):
        if amount<=0: return
        self.link_bytes[flow.origin,flow.target]+=amount
        self.traffic_cost+=amount*self.config.price_per_byte[flow.origin][flow.target]

    def _flows(self):
        return [f for w in self.workflows.values() if w.status=='RUNNING' for f in w.flows.values() if f.status=='TRANSFER']

    def _flow_rates(self):
        flows=self._flows(); groups=Counter((f.origin,f.target) for f in flows if f.latency_left<=EPS and f.bytes_left>EPS)
        result={}
        for f in flows:
            if f.latency_left>EPS or f.bytes_left<=EPS: continue
            a,b=f.origin,f.target; bandwidth=self.network['bandwidth_Bps'][a][b]
            result[id(f)]=(bandwidth/groups[a,b] if bandwidth is not None else math.inf) if self.available[a] and self.endpoint_ready(f.child,b) else 0.
        return result

    def _complete_jobs(self):
        for job in self.jobs:
            if job.status!='RUNNING' or job.remaining>EPS: continue
            job.status='DONE'; wf=self.workflows[job.workflow]
            if wf.status!='RUNNING': continue
            wf.nodes[job.service]='DONE'; wf.execution[job.service]=job.cluster; wf.finishes[job.service]=self.now
            self.total['invocations_completed']+=1
            if all(value=='DONE' for value in wf.nodes.values()):
                wf.status='COMPLETED'; wf.finish=self.now
                latency=self.now-wf.arrival; self.latencies.append(latency); self.total['workflow_completed']+=1
                wf.sla_violation=latency>self.config.workflow_sla_s+EPS
                self.total['workflow_sla_violations']+=int(wf.sla_violation)

    def advance(self,until):
        if until<self.now-EPS: raise ValueError('Thời gian không thể lùi')
        iterations=0
        while self.now<until-EPS:
            iterations+=1
            if iterations>1000000: raise RuntimeError('Vòng sự kiện không hội tụ')
            self._settle(); self._check_overload()
            cr,mr,cu,mu,rates=self._raw_resources(); flow_rates=self._flow_rates()
            stop=min(until,self.heap[0][0] if self.heap else until)
            for j in self.jobs:
                if j.status=='RUNNING' and rates.get(id(j),0)>EPS: stop=min(stop,self.now+j.remaining/rates[id(j)])
            for f in self._flows():
                if f.latency_left>EPS: stop=min(stop,self.now+f.latency_left)
                elif flow_rates.get(id(f),0)>EPS: stop=min(stop,self.now+f.bytes_left/flow_rates[id(f)])
            dt=max(0.,stop-self.now)
            if dt<=EPS:
                self._complete_jobs(); self._process_due(); self._settle()
                if self.heap and self.heap[0][0]<=self.now+EPS: continue
                # Positive residuals below tolerance resolve at the same timestamp.
                for j in self.jobs:
                    if j.status=='RUNNING' and j.remaining<=EPS: j.remaining=0.
                dt=min(until-self.now,1e-9)
            normalized=np.array([cu/self.cpu,mu/self.ram,cr/self.cpu,mr/self.ram]).T
            self.resource_integral+=normalized*dt
            self.balance_integral+=np.std(normalized,axis=0)*dt
            self.waste_integral+=float(np.maximum(0,normalized[:,2:4]-normalized[:,:2]).mean())*dt
            self.total['overloaded_cluster_s']+=sum(self.overloaded)*dt
            self.total['pending_service_s']+=sum(s.status=='PENDING' for s in self.states.values())*dt
            self.total['migration_downtime_service_s']+=sum(s.status=='MIGRATING' and not self.serving_endpoints(n) for n,s in self.states.items())*dt
            for j in self.jobs:
                if j.status=='RUNNING': j.remaining-=rates.get(id(j),0)*dt
            for f in self._flows():
                if f.latency_left>EPS: f.latency_left=max(0.,f.latency_left-dt)
                else:
                    sent=min(f.bytes_left,flow_rates.get(id(f),0)*dt)
                    if f.origin!=f.target: self._traffic(f,sent)
                    f.bytes_left-=sent
            self.now=stop if stop>self.now+EPS else self.now+dt
            self._complete_jobs(); self._process_due(); self._settle(); self.assert_conservation()

    def assert_conservation(self):
        complete=sum(w.status=='COMPLETED' for w in self.workflows.values())
        failed=sum(w.status=='FAILED' for w in self.workflows.values())
        unfinished=sum(w.status not in ('COMPLETED','FAILED') for w in self.workflows.values())
        if self.total['workflow_arrivals']!=complete+failed+unfinished or complete!=self.total['workflow_completed'] or failed!=self.total['workflow_failed']:
            raise RuntimeError('Workflow conservation bị vi phạm')
        if self.total['workflow_sla_violations']!=sum(w.sla_violation for w in self.workflows.values()): raise RuntimeError('Đếm trùng SLA')
        live=sum(j.status in ('RUNNING','QUEUED') for j in self.jobs)
        if self.total['service_invocations']!=self.total['invocations_completed']+self.total['invocations_failed']+self.total['invocations_cancelled']+live:
            raise RuntimeError('Invocation conservation bị vi phạm')

    def summary(self):
        t=max(self.now,EPS); resolved=self.total['workflow_completed']+self.total['workflow_failed']
        def percentile(q): return float(np.quantile(self.latencies,q,method='inverted_cdf')) if self.latencies else 0.
        resources=self.resource_integral/t
        return {'workflow_arrivals':self.total['workflow_arrivals'],'workflow_completed':self.total['workflow_completed'],
                'workflow_failed':self.total['workflow_failed'],'workflow_unfinished':len(self.workflows)-resolved,
                'service_invocations':self.total['service_invocations'],'invocations_completed':self.total['invocations_completed'],
                'invocations_failed':self.total['invocations_failed'],'invocations_cancelled':self.total['invocations_cancelled'],
                'e2e_mean_s':float(np.mean(self.latencies)) if self.latencies else 0.,'e2e_p95_s':percentile(.95),'e2e_p99_s':percentile(.99),
                'completed_latency_samples':len(self.latencies),'traffic_bytes':float(self.link_bytes.sum()),'traffic_cost':float(self.traffic_cost),
                'cpu_usage_std':float(self.balance_integral[0]/t),'ram_usage_std':float(self.balance_integral[1]/t),
                'cpu_reservation_std':float(self.balance_integral[2]/t),'ram_reservation_std':float(self.balance_integral[3]/t),
                'cpu_utilization':float(resources[:,0].mean()),'ram_utilization':float(resources[:,1].mean()),
                'cpu_reservation':float(resources[:,2].mean()),'ram_reservation':float(resources[:,3].mean()),
                'resource_waste':float(self.waste_integral/t),
                'sla_violation_resolved':self.total['workflow_sla_violations']/max(1,resolved),'sla_violations':self.total['workflow_sla_violations'],
                'sla_censored':len(self.workflows)-resolved,'throughput_workflows_s':self.total['workflow_completed']/t,
                'migration_started':self.total['migration_started'],'migration_completed':self.total['migration_completed'],'migration_failed':self.total['migration_failed'],
                'migration_latency_mean_s':float(np.mean(self.migration_latencies)) if self.migration_latencies else 0.,
                'migration_downtime_service_s':self.total['migration_downtime_service_s'],'overloaded_cluster_s':self.total['overloaded_cluster_s'],
                'pending_service_s':self.total['pending_service_s'],'overloaded_ticks':self.total['overloaded_ticks'],
                'pending_ticks':self.total['pending_ticks'],'queue_mean_s':float(np.mean(self.queue_delays)) if self.queue_delays else 0.,
                'errors_by_type':{k[6:]:v for k,v in self.total.items() if k.startswith('error_')}}
