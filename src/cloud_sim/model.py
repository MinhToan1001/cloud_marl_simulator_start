"""Bounded FIFO queue with explicit arrival and completion timestamps."""
from __future__ import annotations

from collections import deque
from dataclasses import dataclass

from .config import ClusterConfig, integer


@dataclass(frozen=True)
class Observation:
    name: str
    queue: int
    capacity: int
    max_queue: int
    base_latency_ms: float


class Cluster:
    def __init__(self, config: ClusterConfig):
        self.config = config
        self._arrivals: deque[int] = deque()
        self.second = 0

    @property
    def queue(self) -> int:
        return len(self._arrivals)

    def observe(self) -> Observation:
        c = self.config
        return Observation(c.name, self.queue, c.effective_capacity, c.max_queue, c.base_latency_ms)

    def step(self, assigned: int) -> tuple[dict, tuple[float, ...]]:
        integer("assigned", assigned)
        c = self.config
        before = self.queue
        accepted = min(assigned, c.effective_capacity + c.max_queue - before)
        dropped = assigned - accepted
        self._arrivals.extend([self.second] * accepted)
        processed = min(c.effective_capacity, self.queue)
        latencies = tuple(
            (self.second - self._arrivals.popleft()) * 1000
            + (slot + 1) * 1000 / c.effective_capacity + c.base_latency_ms
            for slot in range(processed)
        )
        after = self.queue
        # Peak RAM before service: all accepted requests and backlog are resident.
        ram = c.idle_memory_mb + (processed + after) * c.memory_per_request_mb
        metrics = {
            "assigned": assigned, "accepted": accepted, "dropped": dropped,
            "processed": processed, "queue_before": before, "queue_after": after,
            "capacity_req_per_s": c.effective_capacity, "max_queue": c.max_queue,
            "cpu_utilization_pct": 100 * processed / c.cpu_capacity,
            "memory_utilization_pct": 100 * ram / c.memory_mb,
            "throughput_req_per_s": processed,
            "error_rate_pct": 100 * dropped / assigned if assigned else 0.0,
            "latency_mean_ms": sum(latencies) / processed if processed else None,
            "latency_sum_ms": sum(latencies),
        }
        if before + assigned != processed + dropped + after:
            raise RuntimeError("Request accounting failed")
        if not (0 <= after <= c.max_queue and 0 <= ram <= c.memory_mb):
            raise RuntimeError("Resource bound failed")
        self.second += 1
        return metrics, latencies
