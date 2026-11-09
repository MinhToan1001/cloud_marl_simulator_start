"""Adapters add common-schema files without mutating legacy simulation/Locust CSV."""
import csv
import json
import math
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path

from .schema import MetricRecord, digest, timestamp, write_records


def export_simulation(input_dir: Path, output: Path):
    metadata = json.loads((input_dir / "metadata.json").read_text())
    counters = defaultdict(lambda: [0, 0, 0])
    config = {c["name"]: c for c in metadata["config"]["clusters"]}
    records = []
    anchor = timestamp(metadata["created_at_utc"])
    with (input_dir / "csv/cluster_metrics.csv").open(encoding="utf-8-sig", newline="") as stream:
        for row in csv.DictReader(stream):
            scenario, seed, policy, cluster = row.get("scenario", "mixed"), row["seed"], row["policy"], row["cluster"]
            run_id = f"simulation:{scenario}:{seed}:{policy}"
            key = run_id, cluster
            assigned, completed, errors = (int(row[k]) for k in ("assigned", "processed", "dropped"))
            counts = counters[key]
            for index, value in enumerate((assigned, completed, errors)):
                counts[index] += value
            settings = config[cluster]
            records.append(MetricRecord(
                timestamp_utc=(anchor + timedelta(seconds=int(row["second"]) + 1)).isoformat(),
                source="simulated", collection_mode="simulator_export", layer="application", origin="simulator_csv",
                cluster_id=cluster, entity_id=f"simulated-{cluster}", instance_id=f"simulated-{cluster}",
                run_id=run_id, workload_sha256=digest({"scenario": scenario, "seed": seed, "config": metadata["config"]}),
                interval_s=1, cpu_utilization_pct=float(row["cpu_utilization_pct"]),
                memory_utilization_pct=float(row["memory_utilization_pct"]),
                cpu_usage_cores=float(row["cpu_utilization_pct"]) * settings["cpu_cores"] / 100,
                cpu_capacity_cores=settings["cpu_cores"], memory_capacity_bytes=round(settings["memory_mb"] * 1_000_000),
                memory_usage_bytes=round(float(row["memory_utilization_pct"]) * settings["memory_mb"] * 10000),
                received_total=counts[0], completed_total=counts[1], errors_total=counts[2], inflight=int(row["queue_after"]),
                incoming_rps=assigned, completed_rps=completed, error_rps=errors,
                error_rate_pct=float(row["error_rate_pct"]), queue_length=int(row["queue_after"]),
                latency_mean_ms=float(row["latency_mean_ms"]) if row["latency_mean_ms"] else None,
                latency_population="simulator_completed_requests", latency_samples=completed,
                metadata_json=json.dumps({"synthetic_time_axis": True, "policy": policy, "scenario": scenario,
                                          "seed": seed, "p95_note": "Per-cluster p95 unavailable in original CSV; not inferred from means"})))
    if output.exists():
        raise ValueError("Common export must use a new file")
    write_records(output, records)
    return len(records)


def normalize_locust(requests_csv: Path, workload_json: Path, output: Path, window_s=15):
    metadata = json.loads(workload_json.read_text())
    start = timestamp(metadata["started_at_utc"])
    with requests_csv.open(encoding="utf-8-sig", newline="") as stream:
        raw = list(csv.DictReader(stream))
    windows = defaultdict(list)
    for row in raw:
        if row["source"] != metadata["source"] or row["run_id"] != metadata["run_id"] or row["workload_sha256"] != metadata["workload_sha256"]:
            raise ValueError("Locust rows have mixed provenance")
        windows[max(0, int((timestamp(row["timestamp_utc"]) - start).total_seconds() // window_s))].append(row)
    records = []
    for index, rows in sorted(windows.items()):
        successful = [float(r["latency_ms"]) for r in rows if not int(r["failed"])]
        errors = sum(int(r["failed"]) for r in rows)
        end = start + timedelta(seconds=(index + 1) * window_s)
        records.append(MetricRecord(timestamp_utc=end.isoformat(), source=metadata["source"],
            collection_mode="live" if metadata["source"] == "real" else "local_test", layer="client", origin="locust",
            cluster_id="A" if metadata["source"] == "real" else "", entity_id="locust-driver", run_id=metadata["run_id"],
            workload_sha256=metadata["workload_sha256"], interval_s=window_s, completed_rps=len(successful) / window_s,
            error_rps=errors / window_s, error_rate_pct=100 * errors / len(rows), latency_samples=len(successful),
            latency_mean_ms=sum(successful) / len(successful) if successful else None,
            latency_p95_ms=sorted(successful)[math.ceil(0.95 * len(successful)) - 1] if successful else None,
            latency_population="successful_client_responses", quality="client_window_may_be_partial",
            metadata_json=json.dumps({"network_included": True, "incoming_rps": "not inferred from completed responses",
                                      "last_window_may_be_partial": True})))
    if output.exists():
        raise ValueError("Use a new normalized output file")
    write_records(output, records)
    return len(records)
