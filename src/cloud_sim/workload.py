"""Generate a complete workload once, independently of the dispatch policy."""
from __future__ import annotations

import random

from .config import SimulationConfig, integer


def make_workload(config: SimulationConfig, seed: int | None = None) -> list[dict]:
    actual_seed = config.seed if seed is None else seed
    integer("seed", actual_seed)
    rng = random.Random(actual_seed)
    rows = []
    for phase in config.phases:
        for offset in range(phase.seconds):
            fraction = offset / max(1, phase.seconds - 1)
            rate = phase.start_rate + fraction * (phase.end_rate - phase.start_rate)
            rows.append({
                "second": len(rows), "phase": phase.name,
                "incoming_requests": max(0, round(rate * rng.uniform(1 - config.jitter, 1 + config.jitter))),
            })
    return rows
