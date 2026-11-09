"""Conservative low-load response profile, grouped hold-out errors and provenance.

Does not infer maximum capacity, queue slots or per-inflight memory from RPS.
The calibrated profile predicts window means in the observed pod budget only.
"""
from collections import Counter
from datetime import timedelta
from pathlib import Path
from dataclasses import replace
import csv
import json
import math
import statistics

import numpy as np

from integration.parser import AppDeltaParser, kubernetes_records
from integration.preprocess import aligned_samples
from integration.schema import digest, read_records, timestamp
from .io import file_hash, fingerprint, new_directory, read_json, write_csv, write_json


def fit_response(samples):
    """CPU OLS with nonnegative coefficients; constants for RAM and latency.

    RAM-vs-RPS is deliberately not used: 8 smoothed observations cannot identify
    memory per queued request. Latency excludes WAN and queuing effects.
    """
    x = np.asarray([s["completed_rps"] for s in samples])
    y = np.asarray([s["cpu_usage_cores"] for s in samples])
    slope, intercept = np.polyfit(x, y, 1) if len(set(x)) > 1 else (0, y.mean())
    slope, intercept = max(0.0, float(slope)), max(0.0, float(intercept))
    return {"cpu_idle_cores": intercept, "cpu_cores_per_rps": slope,
            "memory_baseline_bytes": float(statistics.mean(s["memory_usage_bytes"] for s in samples)),
            "service_latency_mean_ms": float(statistics.mean(s["latency_mean_ms"] for s in samples)),
            "error_fraction": float(statistics.mean(s["error_rate_pct"] / 100 for s in samples))}


def predict_response(profile, rate):
    return {"cpu_usage_cores": profile["cpu_idle_cores"] + profile["cpu_cores_per_rps"] * rate,
            "memory_usage_bytes": profile["memory_baseline_bytes"],
            "latency_mean_ms": profile["service_latency_mean_ms"],
            "completed_rps": rate * (1 - profile["error_fraction"]),
            "error_rate_pct": 100 * profile["error_fraction"]}


def error_metrics(actual, predicted):
    residuals = np.asarray(predicted) - np.asarray(actual)
    return {"n": len(actual), "mae": float(np.abs(residuals).mean()),
            "rmse": float(np.sqrt(np.square(residuals).mean())),
            "relative_mae_pct": float(100 * np.abs(residuals).mean() / np.mean(np.abs(actual))) if np.mean(np.abs(actual)) > 0 else None}


def replay_pod_window(sample, response):
    """Replay integer requests over the observed window, with no fictitious WAN.

    A is a POD budget for this diagnostic, B/C have no arrivals. Partial final
    second contributes only its actual idle time to CPU-window aggregation.
    """
    from .config import PlacementConfig, ClusterConfig
    from .env import PlacementEnv
    from .trace import make_trace
    duration = sample["interval_s"]
    seconds = math.ceil(duration)
    count = round(sample["incoming_rps"]*duration)
    config = PlacementConfig(seconds=seconds, decision_s=1, background_fraction=(0, 0), background_memory_fraction=0,
                             capacity_factor=(1, 1), network_factor=(1, 1), readiness_factor=(1, 1),
                             latency_mean_ms=response["service_latency_mean_ms"], memory_baseline_bytes=response["memory_baseline_bytes"],
                             cpu_idle_cores=response["cpu_idle_cores"], cpu_cores_per_rps=response["cpu_cores_per_rps"])
    config = replace(config, clusters=(ClusterConfig("A", sample["cpu_capacity_cores"], int(sample["memory_capacity_bytes"]), 0), *config.clusters[1:]))
    trace = make_trace(config, 50001)
    trace["demand"] = [[0, 0, 0] for _ in range(seconds)]
    for ordinal in range(count):
        trace["demand"][int((ordinal+.5)*duration/count)][0] += 1
    env = PlacementEnv(config); env.reset(trace=trace)
    while env.second < seconds:
        env.step({a: 0 for a in env.agents})
    summary = env.summary()
    cpu_integral = env.resource_sum[0, 0]*sample["cpu_capacity_cores"] - (seconds-duration)*response["cpu_idle_cores"]
    return {"cpu_usage_cores": float(cpu_integral/duration), "memory_usage_bytes": response["memory_baseline_bytes"],
            "latency_mean_ms": summary["latency_mean_ms"], "completed_rps": summary["processed"]/duration,
            "error_rate_pct": summary["errors"]/max(1, count)*100, "window_s": duration,
            "integer_arrivals": count, "unfinished": summary["unfinished"]}


def calibrate_run(run_dir, output):
    run_dir, output = Path(run_dir), new_directory(output)
    session = read_json(run_dir / "collector/session.json")
    client = read_json(run_dir / "client/workload.json")
    records = read_records(run_dir / "collector/metrics.csv")
    identity = ("real", "live", "A", run_dir.name, digest(session["workload"]))
    if any((r.source, r.collection_mode, r.cluster_id, r.run_id, r.workload_sha256) != identity for r in records):
        raise ValueError("Mixed or unverified dataset identity")
    if client["source"] != "real" or client["run_id"] != run_dir.name or client["workload_sha256"] != identity[-1]:
        raise ValueError("Client/collector identity mismatch")
    rebuilt, parser = [], AppDeltaParser()
    snapshots = []
    with (run_dir / "collector/raw.jsonl").open(encoding="utf-8") as stream:
        for line in stream:
            item = json.loads(line)
            if item["source"] != "real" or item["collection_mode"] != "live":
                raise ValueError("Raw source mismatch")
            snap = item["snapshot"]
            snapshots.append(snap)
            context = {"source": "real", "collection_mode": "live", "cluster_id": "A", "namespace": session["namespace"],
                       "run_id": run_dir.name, "workload_sha256": identity[-1]}
            rebuilt.extend(kubernetes_records(snap, context))
            rebuilt.extend(parser.parse(a, snap["observed_at_utc"], context) for a in snap["apps"])
    # CSV schema casts numeric columns to float whereas JSON may contain an int
    # (e.g. 0 vs 0.0). Compare typed values, not their differing JSON spellings.
    if [r.to_dict() for r in rebuilt] != [r.to_dict() for r in records]:
        raise ValueError("Raw replay differs from existing metrics CSV")
    samples, rejected = aligned_samples(records)
    if len(samples) < 4:
        raise ValueError("Insufficient aligned samples for grouped holdout calibration")
    with (run_dir / "processed/aligned.csv").open(encoding="utf-8-sig", newline="") as stream:
        existing = list(csv.DictReader(stream))
    if len(existing) != len(samples) or [r["timestamp_utc"] for r in existing] != [s["timestamp_utc"] for s in samples]:
        raise ValueError("Existing alignment does not reproduce")
    signatures = {(s["pod_uid"], s["app_work_config_json"], s["cpu_capacity_cores"], s["memory_capacity_bytes"]) for s in samples}
    if len(signatures) != 1:
        raise ValueError("Do not pool different pods/configurations/budgets")
    with (run_dir / "client/requests.csv").open(encoding="utf-8-sig", newline="") as stream:
        requests = list(csv.DictReader(stream))
    if any((r["source"], r["run_id"], r["workload_sha256"]) != ("real", run_dir.name, identity[-1]) for r in requests):
        raise ValueError("Mixed request provenance")
    times = [timestamp(s["timestamp_utc"]) for s in samples]
    if times != sorted(set(times)):
        raise ValueError("Duplicate/nonmonotonic application windows")
    # Adjacent smoothed metrics can remain correlated. Group contiguous pairs,
    # not individual rows, and report descriptive errors without confidence claims.
    predictions, replay_predictions = [], []
    for start in range(0, len(samples), 2):
        indices = list(range(start, min(start + 2, len(samples))))
        profile = fit_response([s for i, s in enumerate(samples) if i not in indices])
        for i in indices:
            sample = samples[i]
            predicted = predict_response(profile, sample["incoming_rps"])
            row = {"timestamp_utc": sample["timestamp_utc"], "holdout_block": start // 2, "incoming_rps": sample["incoming_rps"]}
            for key, value in predicted.items():
                row[f"real_{key}"] = sample[key]
                row[f"predicted_{key}"] = value
            predictions.append(row)
            physical = replay_pod_window(sample, profile)
            replay_row = {"timestamp_utc": sample["timestamp_utc"], "holdout_block": start // 2, "window_s": physical["window_s"],
                          "integer_arrivals": physical["integer_arrivals"], "unfinished": physical["unfinished"]}
            for key in predicted:
                replay_row[f"real_{key}"] = sample[key]
                replay_row[f"simulated_{key}"] = physical[key]
            replay_predictions.append(replay_row)
    keys = ("cpu_usage_cores", "memory_usage_bytes", "latency_mean_ms", "completed_rps", "error_rate_pct")
    errors = {key: error_metrics([r[f"real_{key}"] for r in predictions], [r[f"predicted_{key}"] for r in predictions]) for key in keys}
    # Reproduce the previous model at the SAME fractional window rate. Its
    # one-second integer-batch simulator cannot directly replay 0.27 RPS; this
    # comparison is explicitly an analytic low-load expectation, not fake ticks.
    from cloud_sim.config import load_config
    original = load_config(Path(__file__).resolve().parents[2] / "configs/simulation.json").clusters[0]
    prior = {"cpu_usage_cores": [s["completed_rps"] / original.requests_per_core for s in samples],
             "memory_usage_bytes": [(original.idle_memory_mb + s["incoming_rps"] * original.memory_per_request_mb) * 1e6 for s in samples],
             "latency_mean_ms": [original.base_latency_ms + 1000 / original.effective_capacity] * len(samples)}
    prior_errors = {key: error_metrics([s[key] for s in samples], values) for key, values in prior.items()}
    fitted = fit_response(samples)
    profile = {"schema_version": 1, "profile_id": "cluster-a-real-003-low-load-v1", "source": "real",
               "scope": "one Sample API pod, successful_server_work latency; smoothed pod resource metrics",
               "run_id": run_dir.name, "work_config": client["app_work_config"],
               "pod_budget_cpu_cores": samples[0]["cpu_capacity_cores"], "pod_budget_memory_bytes": samples[0]["memory_capacity_bytes"],
               "observed_rps_range": [min(s["incoming_rps"] for s in samples), max(s["incoming_rps"] for s in samples)],
               "samples": len(samples), "response": fitted, "maximum_capacity_rps": None,
               "input_hashes": {p.relative_to(run_dir).as_posix(): file_hash(p) for p in run_dir.rglob('*') if p.is_file()},
               "queue_capacity": None, "memory_per_queued_request": None,
               "calibrated_parameters": ["cpu_idle_cores", "cpu_cores_per_rps", "memory_baseline_bytes", "service_latency_mean_ms"],
               "assumptions": ["CPU OLS describes observed windows, not a causal service demand or saturation estimate",
                               "constant RAM and mean latency apply only in observed low-load domain",
                               "error_fraction=0 observed, not proof zero failure risk", "B/C and additional services are SIMULATED"]}
    report = {"run_id": run_dir.name, "source": "real", "raw_replay_matches_metrics": True,
              "snapshot_count": len(snapshots), "record_count": len(records), "aligned_windows": len(samples),
              "rejected_windows": rejected, "rejection_reasons": dict(Counter(r["reason"] for r in rejected)),
              "request_count": len(requests), "client_failures": sum(int(r["failed"]) for r in requests),
              "client_latency_mean_ms": statistics.mean(float(r["latency_ms"]) for r in requests),
              "client_latency_p95_ms": sorted(float(r["latency_ms"]) for r in requests)[math.ceil(.95 * len(requests)) - 1],
              "first_snapshot_utc": snapshots[0]["observed_at_utc"], "last_snapshot_utc": snapshots[-1]["observed_at_utc"],
              "client_start_utc": client["started_at_utc"], "last_client_request_utc": requests[-1]["timestamp_utc"],
              "observed_rps_range": profile["observed_rps_range"],
              "cpu_usage_cores_range": [min(s["cpu_usage_cores"] for s in samples), max(s["cpu_usage_cores"] for s in samples)],
              "memory_usage_bytes_range": [min(s["memory_usage_bytes"] for s in samples), max(s["memory_usage_bytes"] for s in samples)],
              "server_latency_mean_ms_range": [min(s["latency_mean_ms"] for s in samples), max(s["latency_mean_ms"] for s in samples)],
              "cpu_limit_utilization_max_pct": max(s["cpu_utilization_pct"] for s in samples),
              "errors_grouped_holdout": errors, "old_model_analytic_low_load_errors": prior_errors,
              "full_simulator_low_load_errors": {key: error_metrics([r[f"real_{key}"] for r in replay_predictions], [r[f"simulated_{key}"] for r in replay_predictions]) for key in keys},
              "full_simulator_replay_scope": "A scoped to measured pod limits, zero background/WAN, B/C idle; integer arrivals conserved, fractional window CPU aggregation; no saturation inference",
              "validation_design": "4 contiguous held-out blocks of 2 windows; no independent external run; residuals are descriptive",
              "scope_warning": "Original model A budget differs from measured app pod budget; prior errors quantify mismatch, not whole-cluster accuracy",
              "capacity_identified": False, "live_load_test_executed": False,
              "input_hashes": {p.relative_to(run_dir).as_posix(): file_hash(p) for p in run_dir.rglob('*') if p.is_file()}}
    write_json(output / "profile.json", profile)
    write_json(output / "calibration.json", report)
    write_csv(output / "aligned_reproduced.csv", samples)
    write_csv(output / "heldout_predictions.csv", predictions)
    write_csv(output / "full_simulator_low_load_replay.csv", replay_predictions)
    write_json(output / "rejections.json", rejected)
    return profile, report
