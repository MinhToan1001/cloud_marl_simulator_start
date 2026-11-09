"""One explicit schema for measured, fixture, and simulated observations."""
from __future__ import annotations

import csv
import hashlib
import json
import math
from dataclasses import asdict, dataclass, fields
from datetime import datetime, timezone
from pathlib import Path

LAYERS = {"ubuntu_host", "openstack_hypervisor", "k3s_node", "k3s_pod", "application", "client"}


def utc_now():
    return datetime.now(timezone.utc).isoformat()


def timestamp(value: str) -> datetime:
    if not isinstance(value, str):
        raise ValueError("Timestamp must be a string")
    result = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if result.tzinfo is None:
        raise ValueError("Timestamp must contain a timezone")
    return result.astimezone(timezone.utc)


def digest(data) -> str:
    return hashlib.sha256(json.dumps(data, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


@dataclass(frozen=True)
class MetricRecord:
    timestamp_utc: str
    source: str
    layer: str
    entity_id: str
    run_id: str
    workload_sha256: str
    cluster_id: str = ""
    collection_mode: str = "live"
    origin: str = ""
    observed_at_utc: str = ""
    namespace: str = ""
    node_name: str = ""
    pod_name: str = ""
    pod_uid: str = ""
    instance_id: str = ""
    phase: str = ""
    interval_s: float | None = None
    cpu_usage_cores: float | None = None
    cpu_capacity_cores: float | None = None
    cpu_utilization_pct: float | None = None
    memory_usage_bytes: int | None = None
    memory_capacity_bytes: int | None = None
    memory_utilization_pct: float | None = None
    pod_count: int | None = None
    ready_pods: int | None = None
    pod_status: str = ""
    received_total: int | None = None
    completed_total: int | None = None
    errors_total: int | None = None
    inflight: int | None = None
    incoming_rps: float | None = None
    completed_rps: float | None = None
    error_rps: float | None = None
    error_rate_pct: float | None = None
    latency_mean_ms: float | None = None
    latency_p95_ms: float | None = None
    latency_samples: int | None = None
    latency_population: str = ""
    queue_length: int | None = None
    quality: str = "ok"
    metadata_json: str = "{}"
    schema_version: int = 1

    def __post_init__(self):
        timestamp(self.timestamp_utc)
        if self.observed_at_utc:
            timestamp(self.observed_at_utc)
        if self.source not in {"real", "simulated"} or self.layer not in LAYERS:
            raise ValueError("Invalid source/layer")
        if self.collection_mode not in {"live", "offline_fixture", "simulator_export", "local_test"}:
            raise ValueError("Invalid collection mode")
        if self.source == "real" and self.collection_mode in {"offline_fixture", "simulator_export"}:
            raise ValueError("Fixtures and simulator output cannot be labeled real")
        if self.layer in {"ubuntu_host", "openstack_hypervisor"} and self.cluster_id:
            raise ValueError("Host/hypervisor observations cannot be assigned to Cluster A")
        if self.layer in {"k3s_node", "k3s_pod", "application"} and not self.cluster_id:
            raise ValueError("Cluster-scoped observations require cluster_id")
        if not self.entity_id or not self.run_id or len(self.workload_sha256) != 64:
            raise ValueError("Entity, run ID and SHA-256 workload identity are required")
        try:
            int(self.workload_sha256, 16)
        except ValueError as exc:
            raise ValueError("Invalid SHA-256") from exc
        for name in NUMERIC:
            value = getattr(self, name)
            if value is not None and (type(value) not in (int, float) or not math.isfinite(value) or value < 0):
                raise ValueError(f"{name} must be finite and nonnegative or null")
        for name in INTEGER_FIELDS:
            value = getattr(self, name)
            if value is not None and type(value) is not int:
                raise ValueError(f"{name} must be an integer")
        if self.schema_version != 1 or not isinstance(json.loads(self.metadata_json), dict):
            raise ValueError("Invalid schema version or metadata")
        if all(getattr(self, key) is not None for key in ("received_total", "completed_total", "errors_total", "inflight")):
            if self.received_total != self.completed_total + self.errors_total + self.inflight:
                raise ValueError("Request counters do not conserve requests")

    def to_dict(self):
        return asdict(self)


INTEGER_FIELDS = {"memory_usage_bytes", "memory_capacity_bytes", "pod_count", "ready_pods", "received_total",
                  "completed_total", "errors_total", "inflight", "latency_samples", "queue_length", "schema_version"}
NUMERIC = INTEGER_FIELDS | {"interval_s", "cpu_usage_cores", "cpu_capacity_cores", "cpu_utilization_pct",
                          "memory_utilization_pct", "incoming_rps", "completed_rps", "error_rps", "error_rate_pct",
                          "latency_mean_ms", "latency_p95_ms"}
COLUMNS = [field.name for field in fields(MetricRecord)]


def write_records(path: Path, records, append=False):
    path.parent.mkdir(parents=True, exist_ok=True)
    has_header = append and path.exists() and path.stat().st_size > 0
    if has_header:
        with path.open(encoding="utf-8-sig", newline="") as stream:
            if next(csv.reader(stream)) != COLUMNS:
                raise ValueError("Existing CSV has a different schema")
    with path.open("a" if append else "w", encoding="utf-8", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=COLUMNS)
        if not has_header:
            writer.writeheader()
        writer.writerows(record.to_dict() for record in records)


def read_records(path: Path) -> list[MetricRecord]:
    result = []
    with path.open(encoding="utf-8-sig", newline="") as stream:
        reader = csv.DictReader(stream)
        if reader.fieldnames != COLUMNS:
            raise ValueError("CSV does not match metrics schema v1")
        for row in reader:
            if None in row:
                raise ValueError("Malformed CSV row")
            for name in NUMERIC:
                raw = row[name]
                row[name] = None if raw == "" else int(raw) if name in INTEGER_FIELDS else float(raw)
            result.append(MetricRecord(**row))
    return result
