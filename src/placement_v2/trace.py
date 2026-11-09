"""Workflow arrivals riêng; chỉ tái sử dụng shape cột gateway V1, không cộng invocations."""
from dataclasses import replace
import math
import numpy as np
from placement.config import PlacementConfig
from placement.trace import make_trace as v1_trace
from placement.io import fingerprint


def make_trace(config,seed,scenario='stable',scale=.1,faults=(),initial_reserved=None):
    scenario=scenario.replace('-','_')
    rng=np.random.default_rng(seed)
    duration=max(1,math.ceil(config.seconds))
    old=v1_trace(replace(PlacementConfig(),seconds=duration,decision_s=1),seed,scenario)
    arrivals=[]
    for second,counts in enumerate(old['demand']):
        n=int(rng.poisson(counts[0]*scale))
        arrivals.extend(second+(i+.5)/n for i in range(n) if config.dag_arrival_s<=second+(i+.5)/n<config.seconds)
    allocated=initial_reserved or {'cpu':[0.]*3,'ram':[0.]*3}
    def capacity(base,interval,required,integer=False):
        low=np.maximum(np.asarray(base)*interval[0],required); high=np.asarray(base)*interval[1]
        if integer: low=np.ceil(low); high=np.floor(high)
        if np.any(low>high): raise ValueError('Dải capacity không thể chứa allocation ban đầu')
        sampled=rng.uniform(low,high)
        return np.maximum(np.asarray(required),np.floor(sampled)).astype(int).tolist() if integer else sampled.tolist()
    cpu=capacity(config.cpu_cores,config.cpu_range,allocated['cpu'])
    ram=capacity(config.memory_bytes,config.ram_range,allocated['ram'],True)
    network=[]
    for t in range(duration):
        lat=[[config.latency_s[i][j]*float(rng.uniform(*config.latency_range)) for j in range(3)] for i in range(3)]
        bw=[[None if config.bandwidth_Bps[i][j] is None else config.bandwidth_Bps[i][j]*float(rng.uniform(*config.bandwidth_range)) for j in range(3)] for i in range(3)]
        background=(np.array(cpu)*rng.uniform(*config.background_cpu_range,3)).tolist()
        network.append({'second':t,'latency_s':lat,'bandwidth_Bps':bw,'background_cpu':background})
    result={'source':'SIMULATED','seed':seed,'scenario':scenario,'workflow_arrivals_s':arrivals,'cpu_capacity':cpu,'ram_capacity':ram,
            'background_ram_bytes':[config.background_ram_fraction*m for m in ram],'environment':network,'faults':list(faults),
            'initial_reserved':allocated,'readiness_factors':rng.uniform(*config.readiness_range,5).tolist(),
            'arrival_semantics':'Một arrival là một workflow ID, không phải tổng service calls; reuse shape cột gateway V1.'}
    result['sha256']=fingerprint(result)
    return result
