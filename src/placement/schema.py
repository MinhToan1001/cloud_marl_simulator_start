"""One ordered vectorizer for simulation and offline real snapshots."""
import math
import numpy as np

from . import ENV_VERSION
from .io import fingerprint

ACTIONS = {"KEEP": 0, "MOVE_A": 1, "MOVE_B": 2, "MOVE_C": 3}
PHASES = ("IDLE", "PREPARING", "GRACE")
CLUSTER_FIELDS = (
    ("available", "boolean", 1), ("cpu_capacity", "cores", 1), ("memory_capacity", "bytes", 1024**3),
    ("cpu_utilization", "fraction of allocatable", 1), ("memory_utilization", "fraction of allocatable", 1),
    ("cpu_reservation", "fraction of allocatable", 1), ("memory_reservation", "fraction of allocatable", 1),
    ("incoming_rps", "requests/second", 100), ("completed_rps", "requests/second", 100),
    ("latency_mean_ms", "milliseconds", 100), ("latency_p95_ms", "milliseconds", 100),
    ("error_fraction", "fraction", 1), ("resource_valid", "boolean", 1), ("app_valid", "boolean", 1),
    ("age_s", "seconds", 30), ("reservation_valid", "boolean", 1))
SERVICE_FIELDS = (("remaining_s", "seconds", 30), ("cooldown_s", "seconds", 30), ("incoming_rps", "requests/second", 100),
                  ("cpu_request", "cores", 1), ("memory_request", "bytes", 1024**3), ("ready", "boolean", 1),
                  ("migration_count", "completed moves", 20), ("valid", "boolean", 1), ("slo_ms", "milliseconds", 100))


def manifest(config):
    features = []
    for cluster in config.clusters:
        features.extend({"name": f"cluster.{cluster.name}.{key}", "unit": unit, "scale": scale} for key, unit, scale in CLUSTER_FIELDS)
    for service in config.services:
        for location in ("active", "target", "source"):
            features.extend({"name": f"service.{service.name}.{location}.{c}", "unit": "one_hot", "scale": 1} for c in ("NONE", "A", "B", "C"))
        features.extend({"name": f"service.{service.name}.phase.{phase}", "unit": "one_hot", "scale": 1} for phase in PHASES)
        features.extend({"name": f"service.{service.name}.{key}", "unit": unit, "scale": scale} for key, unit, scale in SERVICE_FIELDS)
    features.extend({"name": f"own_workload.{s.name}", "unit": "one_hot", "scale": 1} for s in config.services)
    result = {"env_version": ENV_VERSION, "action_kind": "workload_placement", "agent_ownership": [s.name for s in config.services],
              "cluster_order": [c.name for c in config.clusters], "actions": ACTIONS, "features": features,
              "dtype": "float32", "clip": [0, 10], "missing_value": "zero placeholder plus explicit validity masks; invalid resources disable moves",
              "freshness_max_s": 30, "size": len(features), "horizon_feature": False}
    return result


def vectorize(packet, config, own):
    spec = manifest(config)
    if packet["env_version"] != spec["env_version"] or packet["action_kind"] != "workload_placement":
        raise ValueError("Observation semantic mismatch")
    if set(packet["clusters"]) != set("ABC") or set(packet["services"]) != {s.name for s in config.services} or own not in packet["services"]:
        raise ValueError("Observation identity mismatch")
    values = []
    for c in config.clusters:
        item = packet["clusters"][c.name]
        for key, _, scale in CLUSTER_FIELDS:
            value = item.get(key)
            if value is None:
                value = 0
            if not math.isfinite(value) or value < 0:
                raise ValueError(f"Invalid metric {c.name}.{key}")
            values.append(value / scale)
    for s in config.services:
        item = packet["services"][s.name]
        for location in ("active", "target", "source"):
            if item[location] not in (None, "A", "B", "C"):
                raise ValueError("Invalid location")
            values.extend(float(item[location] == c) for c in (None, "A", "B", "C"))
        if item["phase"] not in PHASES:
            raise ValueError("Invalid phase")
        values.extend(float(item["phase"] == p) for p in PHASES)
        for key, _, scale in SERVICE_FIELDS:
            value = item.get(key)
            if value is None:
                value = 0
            if not math.isfinite(value) or value < 0:
                raise ValueError("Invalid service metric")
            values.append(value / scale)
    values.extend(float(own == s.name) for s in config.services)
    raw = np.asarray(values, dtype=np.float32)
    if len(raw) != spec["size"]:
        raise RuntimeError("Feature count drift")
    return np.clip(raw, 0, 10), int(np.count_nonzero(raw > 10))


def schema_hash(config):
    return fingerprint(manifest(config))
