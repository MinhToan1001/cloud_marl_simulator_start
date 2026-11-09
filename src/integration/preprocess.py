"""Conservative alignment of app windows with matching pod resource observations."""
from __future__ import annotations

import csv
import json
from pathlib import Path

from .schema import read_records, timestamp


def aligned_samples(records, cluster_id="A", max_skew_s=20, allow_simulated=False):
    if not 0 <= max_skew_s <= 60:
        raise ValueError("max_skew_s must be 0..60")
    samples, rejected = [], []
    seen = set()
    resources = [r for r in records if r.layer == "k3s_pod" and r.cluster_id == cluster_id
                 and r.cpu_usage_cores is not None and r.memory_usage_bytes is not None and r.quality == "ok"]
    apps = sorted((r for r in records if r.layer == "application" and r.cluster_id == cluster_id), key=lambda r: r.timestamp_utc)
    used_resource = set()
    for app in apps:
        key = (app.source, app.run_id, app.instance_id, app.timestamp_utc)
        if key in seen:
            rejected.append({"timestamp_utc": app.timestamp_utc, "reason": "duplicate_application_sample"})
            continue
        seen.add(key)
        reason = None
        if app.source != "real" and not allow_simulated:
            reason = "not_real"
        elif app.collection_mode == "local_test":
            reason = "local_test_is_not_k3s"
        elif app.quality != "ok" or app.interval_s is None or app.completed_rps is None:
            reason = app.quality
        elif app.interval_s <= 0 or app.latency_p95_ms is None or not app.latency_samples:
            reason = "no_complete_latency_window"
        matching = [r for r in resources if r.pod_name == app.pod_name and r.namespace == app.namespace
                    and r.run_id == app.run_id and r.source == app.source and r.collection_mode == app.collection_mode
                    and r.workload_sha256 == app.workload_sha256
                    and 0 <= (timestamp(app.timestamp_utc) - timestamp(r.timestamp_utc)).total_seconds() <= max_skew_s
                    and -2 <= (timestamp(r.observed_at_utc or r.timestamp_utc) - timestamp(r.timestamp_utc)).total_seconds() <= 30]
        pod = max(matching, key=lambda r: r.timestamp_utc) if matching else None
        if pod is None:
            reason = reason or "missing_fresh_matching_pod_metrics"
        elif (pod.entity_id, pod.timestamp_utc, pod.run_id, pod.source) in used_resource:
            reason = reason or "repeated_cached_resource_sample"
        if reason:
            rejected.append({"timestamp_utc": app.timestamp_utc, "reason": reason})
            continue
        used_resource.add((pod.entity_id, pod.timestamp_utc, pod.run_id, pod.source))
        samples.append({
            "timestamp_utc": app.timestamp_utc, "source": app.source, "collection_mode": app.collection_mode,
            "cluster_id": cluster_id, "run_id": app.run_id, "workload_sha256": app.workload_sha256,
            "pod_name": app.pod_name, "pod_uid": pod.pod_uid, "interval_s": app.interval_s,
            "incoming_rps": app.incoming_rps, "completed_rps": app.completed_rps,
            "error_rate_pct": app.error_rate_pct, "latency_mean_ms": app.latency_mean_ms,
            "latency_p95_ms": app.latency_p95_ms, "cpu_usage_cores": pod.cpu_usage_cores,
            "cpu_capacity_cores": pod.cpu_capacity_cores, "cpu_utilization_pct": pod.cpu_utilization_pct,
            "memory_usage_bytes": pod.memory_usage_bytes, "memory_capacity_bytes": pod.memory_capacity_bytes,
            "inflight": app.inflight, "alignment_skew_s": (timestamp(app.timestamp_utc) - timestamp(pod.timestamp_utc)).total_seconds(),
            "app_work_config_json": json.dumps(json.loads(app.metadata_json).get("work_config", {}), sort_keys=True),
        })
    return samples, rejected


def preprocess(input_csv: Path, output: Path, allow_simulated=False, max_skew_s=20):
    if output.exists() and any(output.iterdir()):
        raise ValueError("Preprocessing output must be new/empty")
    samples, rejected = aligned_samples(read_records(input_csv), allow_simulated=allow_simulated, max_skew_s=max_skew_s)
    output.mkdir(parents=True, exist_ok=True)
    with (output / "aligned.csv").open("w", encoding="utf-8", newline="") as stream:
        columns = list(samples[0]) if samples else ["timestamp_utc", "source"]
        writer = csv.DictWriter(stream, fieldnames=columns)
        writer.writeheader()
        writer.writerows(samples)
    (output / "quality.json").write_text(json.dumps({"accepted": len(samples), "rejected": rejected,
        "allow_simulated": allow_simulated, "max_skew_s": max_skew_s,
        "note": "As-of matching; no interpolation, missing values not filled with zero; one use per resource timestamp"}, indent=2), encoding="utf-8")
    return samples, rejected
