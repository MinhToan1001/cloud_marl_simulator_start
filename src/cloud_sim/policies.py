"""Dispatchers receive only current observations and the current arrival batch."""
from __future__ import annotations

from .config import integer
from .model import Observation

POLICIES = ("round_robin", "least_loaded")


class Dispatcher:
    def __init__(self, policy: str):
        if policy not in POLICIES:
            raise ValueError(f"Unsupported policy: {policy}")
        self.policy = policy
        self.cursor = 0

    def allocate(self, observations: tuple[Observation, ...], demand: int) -> list[int]:
        integer("demand", demand)
        if not observations:
            raise ValueError("At least one cluster is required")
        allocated = [0] * len(observations)
        if self.policy == "round_robin":
            whole, extra = divmod(demand, len(observations))
            allocated = [whole] * len(observations)
            for offset in range(extra):
                allocated[(self.cursor + offset) % len(observations)] += 1
            self.cursor = (self.cursor + demand) % len(observations)
        else:
            for _ in range(demand):
                def score(j: int) -> tuple:
                    o = observations[j]
                    resident = o.queue + allocated[j]
                    full = resident >= o.capacity + o.max_queue
                    finish = (resident + 1) / o.capacity + o.base_latency_ms / 1000
                    return full, finish, j
                target = min(range(len(observations)), key=score)
                allocated[target] += 1
        if sum(allocated) != demand:
            raise RuntimeError("Allocation lost requests")
        return allocated
