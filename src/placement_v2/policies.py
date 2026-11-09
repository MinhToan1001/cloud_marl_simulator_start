"""Heuristic smoke, không huấn luyện và không phải benchmark Task 5."""
import numpy as np


class Policy:
    def __init__(self,method,seed=0):
        if method not in ('round_robin','least_loaded','random_valid','traffic_aware'): raise ValueError(method)
        self.method=method; self.rng=np.random.default_rng(seed); self.cursor=0

    def actions(self,env,observation):
        result={}
        cr,mr,cu,mu,_=env._raw_resources()
        for i,enabled in enumerate(observation['decision_mask']):
            if not enabled: continue
            valid=np.flatnonzero(observation['action_mask'][i]).tolist()
            name=env.order[i]; state=env.states[name]
            # Stable heuristic deployment: preserve READY unless cluster is overloaded/unavailable.
            if self.method!='random_valid' and 0 in valid and env.available[state.active] and not env.overloaded[state.active]: action=0
            elif self.method=='random_valid': action=int(self.rng.choice(valid))
            else:
                places=[a for a in valid if a>0]
                if not places: action=0
                elif self.method=='round_robin':
                    action=min(places,key=lambda a:(a-1-self.cursor)%3); self.cursor=action%3
                else:
                    def score(a):
                        c=a-1; load=(max(cr[c],cu[c])/env.cpu[c]+max(mr[c],mu[c])/env.ram[c])/2
                        if self.method=='traffic_aware':
                            for e in env.dag.edges:
                                other=e.parent if e.child==name else e.child if e.parent==name else None
                                if other is not None and env.states[other].active is not None and env.states[other].active!=c:
                                    j=env.states[other].active; b=env.network['bandwidth_Bps'][j][c]
                                    load+=env.network['latency_s'][j][c]+(e.bytes_per_workflow/b if b else 1e3)
                        return load
                    action=min(places,key=score)
            result[env.agent_names[i]]=int(action)
        return result
