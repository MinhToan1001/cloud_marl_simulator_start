"""Tham số V2 có scope và provenance; không gọi budget giả lập là capacity thật."""
from dataclasses import dataclass,asdict
import math


@dataclass(frozen=True)
class Config:
    seconds: float = 30.
    decision_s: float = 1.
    dag_arrival_s: float = 4.
    cpu_cores: tuple = (1.,1.25,1.5)
    memory_bytes: tuple = (256*1024**2,384*1024**2,512*1024**2)
    latency_s: tuple = ((0,.015,.030),(.015,0,.020),(.030,.020,0))
    bandwidth_Bps: tuple = ((None,2e6,1e6),(2e6,None,1.5e6),(1e6,1.5e6,None))
    price_per_byte: tuple = ((0,1e-7,2e-7),(1e-7,0,1e-7),(2e-7,1e-7,0))
    intra_latency_s: float = .0001
    readiness_s: float = .3
    grace_s: float = .5
    cooldown_s: float = 5.
    migration_timeout_s: float = 3.
    max_moves: int = 8
    max_concurrent_moves: int = 1
    pending_retries: int = 15
    pending_timeout_s: float = 20.
    workflow_queue_limit: int = 256
    workflow_sla_s: float = .25
    workflow_timeout_s: float = 2.
    cpu_range: tuple = (.85,1.15)
    ram_range: tuple = (.85,1.15)
    bandwidth_range: tuple = (.75,1.25)
    latency_range: tuple = (.8,1.2)
    readiness_range: tuple = (.7,1.5)
    background_cpu_range: tuple = (.05,.35)
    background_ram_fraction: float = .1
    ram_latency_penalty: float = 2.
    ram_error_coefficient: float = .05
    invocation_memory_bytes: int = 4096
    memory_baseline_fraction: float = 32378368/33554432
    profile_id: str = 'cluster-a-real-003-low-load-v1'
    profile_sha256: str = ''
    objectives: tuple = (1.,.2,.3,2.)
    reward_mode: str = 'interval-v1'
    migration_start_cost: float = .02
    migration_overlap_cost_per_s: float = .01
    migration_failure_cost: float = .1

    def __post_init__(self):
        for key in ('seconds','decision_s','readiness_s','grace_s','cooldown_s','migration_timeout_s','pending_timeout_s','workflow_sla_s','workflow_timeout_s'):
            if not math.isfinite(getattr(self,key)) or getattr(self,key)<=0: raise ValueError(f'{key} phải dương hữu hạn')
        if not 0<=self.dag_arrival_s<self.seconds: raise ValueError('DAG arrival ngoài episode')
        for key in ('max_moves','max_concurrent_moves','pending_retries','workflow_queue_limit'):
            if type(getattr(self,key)) is not int or getattr(self,key)<1: raise ValueError('Giới hạn phải nguyên dương')
        for seq in (self.cpu_cores,self.memory_bytes):
            if len(seq)!=3 or any(not math.isfinite(v) or v<=0 for v in seq): raise ValueError('Cần budget A/B/C hợp lệ')
        for matrix in (self.latency_s,self.bandwidth_Bps,self.price_per_byte):
            if len(matrix)!=3 or any(len(row)!=3 for row in matrix): raise ValueError('Ma trận mạng 3x3')
            if any(v is not None and (not math.isfinite(v) or v<0) for row in matrix for v in row): raise ValueError('Ma trận không âm hữu hạn; None là bandwidth vô hạn')
        if any(v is None for matrix in (self.latency_s,self.price_per_byte) for row in matrix for v in row): raise ValueError('Chỉ bandwidth được dùng None')
        if not math.isfinite(self.memory_baseline_fraction) or self.memory_baseline_fraction<0: raise ValueError('RAM baseline không hợp lệ')
        for interval in (self.cpu_range,self.ram_range,self.bandwidth_range,self.latency_range,self.readiness_range):
            if len(interval)!=2 or not 0<interval[0]<=interval[1]: raise ValueError('Dải randomization sai')
        if not 0<=self.background_cpu_range[0]<=self.background_cpu_range[1]<1: raise ValueError('Background không hợp lệ')
        if not 0<=self.background_ram_fraction<1 or min(self.ram_latency_penalty,self.ram_error_coefficient)<0: raise ValueError('Mô hình RAM không hợp lệ')
        if len(self.objectives)!=4 or any(v<0 or not math.isfinite(v) for v in self.objectives): raise ValueError('Bốn trọng số mục tiêu không âm')
        if self.reward_mode not in ('interval-v1','legacy-cumulative'): raise ValueError('Reward version không hợp lệ')
        if any(not math.isfinite(v) or v<0 for v in (self.migration_start_cost,self.migration_overlap_cost_per_s,self.migration_failure_cost)): raise ValueError('Migration cost không âm hữu hạn')

    def to_dict(self): return asdict(self)


def from_dict(data):
    return Config(**{k:tuple(tuple(row) if isinstance(row,list) else row for row in v) if isinstance(v,list) else v for k,v in data.items()})


def provenance(config):
    return {key:{'value':value,'source':'CALIBRATED' if key in ('profile_id','profile_sha256') else 'ASSUMPTION(SIMULATED)',
                 'scope':'Profile low-load chỉ cho class Sample API; CPU sai số 60,86%, không xác định saturation' if key.startswith('profile') else 'Chưa xác minh trên A/B/C thật'}
            for key,value in config.to_dict().items()}
