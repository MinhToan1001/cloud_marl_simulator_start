"""Validated, immutable configuration for one-second simulation ticks."""
from __future__ import annotations

import json
import math
from dataclasses import asdict, dataclass
from pathlib import Path


def integer(name: str, value: int, minimum: int = 0) -> None:
    if type(value) is not int or value < minimum:
        raise ValueError(f"{name} must be an integer >= {minimum}")


def number(name: str, value: float, minimum: float = 0, positive: bool = False) -> None:
    if (type(value) not in (int, float) or not math.isfinite(value)
            or value < minimum or (positive and value == minimum)):
        raise ValueError(f"{name} must be finite and {'>' if positive else '>='} {minimum}")


@dataclass(frozen=True)
class ClusterConfig:
    name: str
    capacity: int
    base_latency_ms: float
    cpu_cores: float
    requests_per_core: float
    memory_mb: float
    idle_memory_mb: float
    memory_per_request_mb: float
    queue_limit: int

    def __post_init__(self) -> None:
        if not isinstance(self.name, str) or not self.name.strip():
            raise ValueError("cluster name must be a nonempty string")
        integer("capacity", self.capacity, 1)
        integer("queue_limit", self.queue_limit)
        for name in ("cpu_cores", "requests_per_core", "memory_mb", "memory_per_request_mb"):
            number(name, getattr(self, name), positive=True)
        number("idle_memory_mb", self.idle_memory_mb)
        number("base_latency_ms", self.base_latency_ms)
        if self.effective_capacity < 1:
            raise ValueError("CPU/RAM must allow at least one request per tick")

    @property
    def memory_slots(self) -> int:
        return max(0, math.floor((self.memory_mb - self.idle_memory_mb) / self.memory_per_request_mb))

    @property
    def cpu_capacity(self) -> float:
        return self.cpu_cores * self.requests_per_core

    @property
    def effective_capacity(self) -> int:
        return min(self.capacity, math.floor(self.cpu_capacity), self.memory_slots)

    @property
    def max_queue(self) -> int:
        # Reserve RAM for a full service batch; the remainder can hold backlog.
        return min(self.queue_limit, self.memory_slots - self.effective_capacity)


@dataclass(frozen=True)
class PhaseConfig:
    name: str
    seconds: int
    start_rate: float
    end_rate: float

    def __post_init__(self) -> None:
        if self.name not in {"stable", "ramp_up", "spike", "recovery"}:
            raise ValueError(f"Unknown workload phase: {self.name}")
        integer("phase seconds", self.seconds, 1)
        number("start_rate", self.start_rate)
        number("end_rate", self.end_rate)


@dataclass(frozen=True)
class SimulationConfig:
    seed: int
    jitter: float
    clusters: tuple[ClusterConfig, ...]
    phases: tuple[PhaseConfig, ...]

    def __post_init__(self) -> None:
        integer("seed", self.seed)
        number("jitter", self.jitter)
        if self.jitter > 1:
            raise ValueError("jitter must be <= 1")
        if len(self.clusters) != 3 or len({c.name for c in self.clusters}) != 3:
            raise ValueError("Exactly three clusters with unique names are required")
        if not self.phases:
            raise ValueError("At least one workload phase is required")

    @property
    def seconds(self) -> int:
        return sum(p.seconds for p in self.phases)

    def to_dict(self) -> dict:
        return asdict(self)


def load_config(path: str | Path) -> SimulationConfig:
    return config_from_dict(json.loads(Path(path).read_text(encoding="utf-8-sig")))


def config_from_dict(data: dict) -> SimulationConfig:
    if not isinstance(data, dict) or set(data) != {"seed", "jitter", "clusters", "phases"}:
        raise ValueError("Config must contain exactly seed, jitter, clusters, phases")
    try:
        return SimulationConfig(
            seed=data["seed"], jitter=data["jitter"],
            clusters=tuple(ClusterConfig(**c) for c in data["clusters"]),
            phases=tuple(PhaseConfig(**p) for p in data["phases"]),
        )
    except (KeyError, TypeError) as exc:
        raise ValueError(f"Invalid simulation config: {exc}") from exc
