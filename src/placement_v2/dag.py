"""DAG đồng bộ fork-join: mỗi node gọi tối đa một lần cho mỗi workflow ID."""
from dataclasses import dataclass, asdict
import math
from . import MAX_SERVICES


@dataclass(frozen=True)
class Service:
    name: str
    cpu_request: float = .05
    memory_bytes: int = 32*1024**2
    work_core_s: float = .005539729898474358
    cpu_limit: float = 1.
    concurrency: int = 1
    queue_limit: int = 128


@dataclass(frozen=True)
class Edge:
    parent: str
    child: str
    bytes_per_workflow: int = 4096


@dataclass(frozen=True)
class DAG:
    name: str
    services: tuple[Service,...]
    edges: tuple[Edge,...]

    def __post_init__(self):
        names=[s.name for s in self.services]
        if not 1 <= len(names) <= MAX_SERVICES or len(set(names))!=len(names):
            raise ValueError('DAG cần 1–5 service duy nhất')
        for s in self.services:
            if not s.name or any(not math.isfinite(v) or v<=0 for v in (s.cpu_request,s.memory_bytes,s.work_core_s,s.cpu_limit)) or s.cpu_request>s.cpu_limit:
                raise ValueError('Tài nguyên/chi phí service không hợp lệ')
            if type(s.concurrency) is not int or s.concurrency<1 or type(s.queue_limit) is not int or s.queue_limit<0:
                raise ValueError('Concurrency/queue không hợp lệ')
        seen=set()
        for e in self.edges:
            pair=(e.parent,e.child)
            if e.parent not in names or e.child not in names or pair in seen or type(e.bytes_per_workflow) is not int or e.bytes_per_workflow<0:
                raise ValueError('Cạnh DAG không hợp lệ')
            seen.add(pair)
        self.topological()

    def topological(self):
        remaining={s.name for s in self.services}; result=[]
        while remaining:
            ready=sorted(v for v in remaining if all(e.parent in result for e in self.edges if e.child==v))
            if not ready: raise ValueError('DAG có chu trình')
            result.extend(ready); remaining.difference_update(ready)
        return result

    def parents(self,name): return [e.parent for e in self.edges if e.child==name]
    def children(self,name): return [e.child for e in self.edges if e.parent==name]
    def service(self,name): return next(s for s in self.services if s.name==name)
    def to_dict(self): return asdict(self)


def templates():
    def make(name,names,pairs): return DAG(name,tuple(Service(n) for n in names),tuple(Edge(a,b) for a,b in pairs))
    return {
        'chain':make('chain',('gateway','service-a','service-b'),(('gateway','service-a'),('service-a','service-b'))),
        'fan_out':make('fan_out',('gateway','service-a','service-b'),(('gateway','service-a'),('gateway','service-b'))),
        'diamond':make('diamond',('A','B','C','D'),(('A','B'),('A','C'),('B','D'),('C','D'))),
        'five':make('five',('gateway','auth','catalog','billing','response'),(('gateway','auth'),('auth','catalog'),('auth','billing'),('catalog','response'),('billing','response')))}


def critical_path(dag,placement,latency_s,bandwidth_Bps,intra_s=0.,rates=None):
    """Cận không tranh chấp; RTT + bytes/B và max tại join, không cộng nhánh."""
    finish={}
    for name in dag.topological():
        incoming=[]
        for e in dag.edges:
            if e.child!=name: continue
            a,b=placement[e.parent],placement[name]
            bandwidth=bandwidth_Bps[a][b]
            transfer=intra_s if a==b else latency_s[a][b]+(0 if bandwidth is None or e.bytes_per_workflow==0 else e.bytes_per_workflow/bandwidth if bandwidth>0 else math.inf)
            incoming.append(finish[e.parent]+transfer)
        s=dag.service(name)
        rate=s.cpu_limit if rates is None else min(s.cpu_limit,rates[placement[name]])
        finish[name]=max(incoming,default=0)+s.work_core_s/rate if rate>0 else math.inf
    return max(finish.values())
