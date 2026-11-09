"""Simulation orchestration and request-weighted aggregation."""
from __future__ import annotations

import math
import statistics
from collections import Counter
from dataclasses import dataclass

from .config import SimulationConfig, integer
from .model import Cluster
from .policies import Dispatcher


def percentile(histogram: Counter, fraction: float = 0.95) -> float | None:
    count = sum(histogram.values())
    if not count:
        return None
    rank = math.ceil(count * fraction)
    seen = 0
    for value, frequency in sorted(histogram.items()):
        seen += frequency
        if seen >= rank:
            return value
    raise RuntimeError("Invalid latency histogram")


@dataclass
class SimulationResult:
    rows: list[dict]
    timeseries: list[dict]
    summary: dict
    latency_histogram: Counter


class SimulationSession:
    """The same state transition is used by baselines, MARL, and evaluation."""

    def __init__(self, config: SimulationConfig, policy: str):
        self.config = config
        self.policy = policy
        self.clusters = [Cluster(c) for c in config.clusters]
        self.rows: list[dict] = []
        self.timeseries: list[dict] = []
        self.histogram: Counter = Counter()
        self.received = self.total_processed = self.total_dropped = 0

    def step(self, demand: int, allocated: list[int]) -> dict:
        integer("incoming_requests", demand)
        if len(allocated) != len(self.clusters):
            raise ValueError("Allocation must contain one count per cluster")
        for value in allocated:
            integer("assigned", value)
        if sum(allocated) != demand:
            raise ValueError("Allocation must sum to current demand")
        second = len(self.timeseries)
        tick_rows, tick_latencies = [], []
        for cluster, incoming in zip(self.clusters, allocated):
            metrics, latencies = cluster.step(incoming)
            row = {"second": second, "policy": self.policy, "cluster": cluster.config.name,
                   "traffic_total": demand, **metrics}
            self.rows.append(row)
            tick_rows.append(row)
            tick_latencies.extend(latencies)
        processed = sum(r["processed"] for r in tick_rows)
        dropped = sum(r["dropped"] for r in tick_rows)
        self.received += demand
        self.total_processed += processed
        self.total_dropped += dropped
        self.histogram.update(tick_latencies)
        waiting = sum(c.queue for c in self.clusters)
        if demand + sum(r["queue_before"] for r in tick_rows) != processed + dropped + waiting:
            raise RuntimeError("Tick accounting failed")
        tick = {
            "second": second, "policy": self.policy, "incoming_requests": demand,
            "processed": processed, "dropped": dropped, "queue_after": waiting,
            "throughput_req_per_s": processed,
            "latency_mean_ms": statistics.fmean(tick_latencies) if tick_latencies else None,
            "latency_p95_ms": percentile(Counter(tick_latencies)),
            "error_rate_pct": 100 * dropped / demand if demand else 0.0,
            "load_imbalance": statistics.pstdev(
                (r["processed"] + r["queue_after"]) / r["capacity_req_per_s"] for r in tick_rows
            ),
        }
        self.timeseries.append(tick)
        return tick

    def result(self) -> SimulationResult:
        if not self.timeseries:
            raise ValueError("Run must contain at least one tick")
        received, processed, dropped = self.received, self.total_processed, self.total_dropped
        waiting = sum(c.queue for c in self.clusters)
        if received != processed + dropped + waiting:
            raise RuntimeError("Run accounting failed")
        summary = {
            "policy": self.policy, "received": received, "processed": processed,
            "dropped": dropped, "waiting_at_end": waiting, "duration_s": len(self.timeseries),
            "throughput_req_per_s": processed / len(self.timeseries),
            "error_rate_pct": 100 * dropped / received if received else 0.0,
            "latency_mean_ms": sum(v * n for v, n in self.histogram.items()) / processed if processed else None,
            "latency_p95_ms": percentile(self.histogram),
            "cpu_utilization_pct": statistics.fmean(r["cpu_utilization_pct"] for r in self.rows),
            "memory_utilization_pct": statistics.fmean(r["memory_utilization_pct"] for r in self.rows),
            "load_imbalance": statistics.fmean(r["load_imbalance"] for r in self.timeseries),
        }
        return SimulationResult(self.rows, self.timeseries, summary, self.histogram)


def simulate(policy: str, workload: list[int], config: SimulationConfig) -> SimulationResult:
    if not workload:
        raise ValueError("Workload must contain at least one tick")
    for value in workload:
        integer("incoming_requests", value)
    session = SimulationSession(config, policy)
    dispatcher = Dispatcher(policy)
    for demand in workload:
        allocated = dispatcher.allocate(tuple(c.observe() for c in session.clusters), demand)
        session.step(demand, allocated)
    return session.result()
