"""Normalize Kubernetes snapshots and cumulative app counters into typed rows."""
from __future__ import annotations

import json
import math

from .quantities import cpu_cores, memory_bytes
from .schema import MetricRecord, timestamp


def ready(pod):
    return any(c.get("type") == "Ready" and c.get("status") == "True" for c in pod.get("status", {}).get("conditions", []))


def resource_limit(pod, resource, parser):
    containers = pod.get("spec", {}).get("containers", [])
    values = [c.get("resources", {}).get("limits", {}).get(resource) for c in containers]
    return sum(parser(value) for value in values) if values and all(value is not None for value in values) else None


def kubernetes_records(snapshot: dict, context: dict) -> list[MetricRecord]:
    now = snapshot["observed_at_utc"]
    nodes = snapshot.get("nodes", {}).get("items", [])
    pods = snapshot.get("pods", {}).get("items", [])
    node_metrics = {item["metadata"]["name"]: item for item in snapshot.get("node_metrics", {}).get("items", [])}
    pod_metrics = {(item["metadata"].get("namespace", context["namespace"]), item["metadata"]["name"]): item
                   for item in snapshot.get("pod_metrics", {}).get("items", [])}
    base = {k: context[k] for k in ("source", "collection_mode", "cluster_id", "run_id", "workload_sha256")}
    base.update(observed_at_utc=now, origin="kubernetes_metrics_api", namespace=context["namespace"])
    result = []
    for layer, objects in (("k3s_node", nodes), ("k3s_pod", pods)):
        for obj in objects:
            name = obj["metadata"]["name"]
            if layer == "k3s_node":
                metric = node_metrics.get(name)
                limits = obj.get("status", {}).get("capacity", {})
                cpu_cap = cpu_cores(limits["cpu"]) if "cpu" in limits else None
                memory_cap = memory_bytes(limits["memory"]) if "memory" in limits else None
                selected = [p for p in pods if p.get("spec", {}).get("nodeName") == name]
                extra = dict(node_name=name, pod_count=len(selected), ready_pods=sum(ready(p) for p in selected),
                             pod_status="Ready" if ready(obj) else "NotReady")
                usage = metric.get("usage") if metric else None
            else:
                metric = pod_metrics.get((context["namespace"], name))
                cpu_cap = resource_limit(obj, "cpu", cpu_cores)
                memory_cap = resource_limit(obj, "memory", memory_bytes)
                containers = metric.get("containers", []) if metric else []
                usage = {"cpu": str(sum(cpu_cores(c["usage"]["cpu"]) for c in containers)),
                         "memory": str(sum(memory_bytes(c["usage"]["memory"]) for c in containers))} if containers else None
                extra = dict(node_name=obj.get("spec", {}).get("nodeName", ""), pod_name=name,
                             pod_uid=obj["metadata"].get("uid", ""), pod_count=1, ready_pods=int(ready(obj)),
                             pod_status=obj.get("status", {}).get("phase", "Unknown"))
            cpu = cpu_cores(usage["cpu"]) if usage else None
            memory = memory_bytes(usage["memory"]) if usage else None
            result.append(MetricRecord(
                **base, **extra, timestamp_utc=metric.get("timestamp", now) if metric else now,
                layer=layer, entity_id=name, cpu_usage_cores=cpu, cpu_capacity_cores=cpu_cap,
                cpu_utilization_pct=100 * cpu / cpu_cap if cpu is not None and cpu_cap else None,
                memory_usage_bytes=memory, memory_capacity_bytes=memory_cap,
                memory_utilization_pct=100 * memory / memory_cap if memory is not None and memory_cap else None,
                quality="ok" if usage else "missing_resource_metrics",
                metadata_json=json.dumps({"metrics_window": metric.get("window") if metric else None,
                                          "pod_scope": "selected_namespace_and_label", "denominator": "node_capacity" if layer == "k3s_node" else "sum_container_limits"})))
    return result


class AppDeltaParser:
    def __init__(self):
        self.previous = {}

    def parse(self, app: dict, observed_at: str, context: dict) -> MetricRecord:
        for key in ("received_total", "completed_total", "errors_total", "inflight", "sequence"):
            if type(app[key]) is not int or app[key] < 0:
                raise ValueError(f"Invalid app counter: {key}")
        if app["received_total"] != app["completed_total"] + app["errors_total"] + app["inflight"]:
            raise ValueError("App request conservation failed")
        identity = app["instance_id"]
        old = self.previous.get(identity)
        quality = "warmup"
        values = {}
        if old:
            seconds = app["uptime_s"] - old["uptime_s"]
            deltas = {key: app[key] - old[key] for key in ("received_total", "completed_total", "errors_total", "latency_sum_ms", "sequence")}
            if not math.isfinite(seconds) or seconds <= 0 or any(value < 0 for value in deltas.values()):
                quality = "counter_reset_or_nonmonotonic"
            else:
                received, completed, errors = [deltas[key] for key in ("received_total", "completed_total", "errors_total")]
                events = [e for e in app.get("events", []) if old["sequence"] < e["sequence"] <= app["sequence"]]
                successes = [e["latency_ms"] for e in events if e["success"]]
                complete_events = len(events) == deltas["sequence"] and len(successes) == completed
                quality = "ok" if complete_events else "latency_events_lost"
                values = dict(interval_s=seconds, incoming_rps=received / seconds, completed_rps=completed / seconds,
                              error_rps=errors / seconds, error_rate_pct=100 * errors / (completed + errors) if completed + errors else 0,
                              latency_mean_ms=deltas["latency_sum_ms"] / completed if completed else None,
                              latency_p95_ms=sorted(successes)[math.ceil(0.95 * completed) - 1] if completed and complete_events else None,
                              latency_samples=completed)
        self.previous[identity] = dict(app)
        base = {k: context[k] for k in ("source", "collection_mode", "cluster_id", "run_id", "workload_sha256")}
        return MetricRecord(**base, **values, timestamp_utc=app["timestamp_utc"], observed_at_utc=observed_at,
                            layer="application", entity_id=identity, origin="sample_app", instance_id=identity,
                            namespace=app.get("namespace", context["namespace"]), pod_name=app.get("pod_name", ""),
                            received_total=app["received_total"], completed_total=app["completed_total"],
                            errors_total=app["errors_total"], inflight=app["inflight"], quality=quality,
                            latency_population="successful_server_work", metadata_json=json.dumps({"work_config": app.get("work_config", {}),
                            "window_start_utc": old["timestamp_utc"] if old else None,
                            "server_side_latency": True, "events_retained": len(app.get("events", []))}))
