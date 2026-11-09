"""Strict configuration for a synthetic namespace resource pool, not node capacity."""
from dataclasses import asdict, dataclass, field
import math
from pathlib import Path

from .io import read_json


@dataclass(frozen=True)
class ClusterConfig:
    name: str
    cpu_cores: float
    memory_bytes: int
    network_ms: float


@dataclass(frozen=True)
class ServiceConfig:
    name: str
    cpu_request: float = .1
    memory_request: int = 64 * 1024**2
    cpu_limit: float = .25
    cpu_work_s: float = .005
    slo_ms: float = 100
    queue_limit: int = 80


@dataclass(frozen=True)
class PlacementConfig:
    clusters: tuple[ClusterConfig, ...] = field(default_factory=lambda: (
        ClusterConfig("A", .5, 256 * 1024**2, 1), ClusterConfig("B", .75, 384 * 1024**2, 12),
        ClusterConfig("C", 1, 512 * 1024**2, 25)))
    services: tuple[ServiceConfig, ...] = field(default_factory=lambda: tuple(ServiceConfig(f"service_{i}") for i in range(3)))
    seconds: int = 180
    decision_s: int = 5
    cooldown_s: int = 30
    readiness_s: int = 5
    timeout_s: int = 30
    grace_s: int = 5
    max_moves: int = 20
    max_concurrent_moves: int = 1
    background_fraction: tuple[float, float] = (.1, .65)
    background_memory_fraction: float = .2
    capacity_factor: tuple[float, float] = (.8, 1.2)
    network_factor: tuple[float, float] = (.8, 1.3)
    readiness_factor: tuple[float, float] = (.6, 1.6)
    latency_mean_ms: float = 5.5
    memory_baseline_bytes: float = 32 * 1024**2
    cpu_idle_cores: float = .001
    cpu_cores_per_rps: float = .005
    profile_id: str = "assumed-until-calibration"
    profile_sha256: str = ""
    reward: dict = field(default_factory=lambda: {"success": 1., "latency": .2, "error": 2., "slo": 1., "waste": .1, "move": .1, "failed_move": .5})

    def __post_init__(self):
        if len(self.clusters) != 3 or [c.name for c in self.clusters] != list("ABC"):
            raise ValueError("Exactly ordered A/B/C required")
        if len(self.services) != 3 or len({s.name for s in self.services}) != 3:
            raise ValueError("Exactly three unique workload agents required")
        for key in ("seconds", "decision_s", "cooldown_s", "readiness_s", "timeout_s", "grace_s", "max_moves", "max_concurrent_moves"):
            value = getattr(self, key)
            if type(value) is not int or value < 1:
                raise ValueError(f"{key} must be a positive integer")
        if self.seconds % self.decision_s or self.max_concurrent_moves > 3:
            raise ValueError("Duration must divide by decision interval; at most three simultaneous moves")
        if not 0 <= self.background_fraction[0] <= self.background_fraction[1] < 1:
            raise ValueError("Invalid background range")
        if not 0 <= self.background_memory_fraction < 1:
            raise ValueError("Invalid background memory fraction")
        for pair in (self.capacity_factor, self.network_factor, self.readiness_factor):
            if len(pair) != 2 or not 0 < pair[0] <= pair[1]:
                raise ValueError("Invalid domain range")
        for c in self.clusters:
            if any(not math.isfinite(v) or v <= 0 for v in (c.cpu_cores, c.memory_bytes)) or c.network_ms < 0:
                raise ValueError("Invalid cluster budget")
        for s in self.services:
            if any(not math.isfinite(v) or v <= 0 for v in (s.cpu_request, s.cpu_limit, s.memory_request, s.cpu_work_s, s.slo_ms)) or s.cpu_request > s.cpu_limit or type(s.queue_limit) is not int or s.queue_limit < 0:
                raise ValueError("Invalid service resource/SLO profile")
        expected_reward = {"success", "latency", "error", "slo", "waste", "move", "failed_move"}
        if set(self.reward) != expected_reward or any(not math.isfinite(v) or v < 0 for v in self.reward.values()):
            raise ValueError("Invalid reward coefficients")
        if not math.isfinite(self.latency_mean_ms) or self.latency_mean_ms <= 0 or not 0 < self.memory_baseline_bytes <= min(s.memory_request for s in self.services):
            raise ValueError("Invalid response profile")
        if any(not math.isfinite(v) or v < 0 for v in (self.cpu_idle_cores, self.cpu_cores_per_rps)):
            raise ValueError("Invalid CPU response coefficients")

    def to_dict(self):
        return asdict(self)


def config_from_dict(data):
    values = dict(data)
    if "clusters" in values:
        values["clusters"] = tuple(ClusterConfig(**c) for c in values["clusters"])
    if "services" in values:
        values["services"] = tuple(ServiceConfig(**s) for s in values["services"])
    for name in ("background_fraction", "capacity_factor", "network_factor", "readiness_factor"):
        if name in values:
            values[name] = tuple(values[name])
    return PlacementConfig(**values)


def load_config(path):
    return config_from_dict(read_json(Path(path)))
