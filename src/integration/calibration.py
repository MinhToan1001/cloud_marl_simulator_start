"""Measured relationships and conservative proposals; never retrain or edit defaults."""
from __future__ import annotations

import json
import math
import statistics
from copy import deepcopy
from pathlib import Path

from cloud_sim.config import config_from_dict, load_config
from .preprocess import aligned_samples
from .schema import digest, read_records


def linear_fit(x, y):
    if len(x) < 3 or len(set(x)) < 3:
        return {"status": "insufficient_distinct_loads", "samples": len(x)}
    mean_x, mean_y = statistics.fmean(x), statistics.fmean(y)
    variance = sum((value - mean_x) ** 2 for value in x)
    slope = sum((a - mean_x) * (b - mean_y) for a, b in zip(x, y)) / variance
    intercept = mean_y - slope * mean_x
    residual = sum((b - (intercept + slope * a)) ** 2 for a, b in zip(x, y))
    total = sum((value - mean_y) ** 2 for value in y)
    return {"status": "estimated", "samples": len(x), "slope": slope, "intercept": intercept,
            "r_squared": 1 - residual / total if total else (1.0 if residual < 1e-12 else 0.0),
            "observed_load_range_rps": [min(x), max(x)]}


def calibrate(input_csv: Path, output: Path, simulator_config: Path, allow_simulated=False,
              candidate: Path | None = None):
    if output.exists() and any(output.iterdir()):
        raise ValueError("Calibration output must be new/empty; never overwrite prior reports")
    records = read_records(input_csv)
    samples, rejected = aligned_samples(records, allow_simulated=allow_simulated)
    if len(samples) < 3:
        raise ValueError("Need >=3 aligned windows at >=3 load levels; inspect quality/freshness/source first")
    # Different work cost or resource limits are different experiments; never pool them silently.
    signatures = {(r["app_work_config_json"], r["cpu_capacity_cores"], r["memory_capacity_bytes"]) for r in samples}
    if len(signatures) != 1:
        raise ValueError("Mixed app work settings or pod limits; calibrate homogeneous experiments separately")
    loads = [r["incoming_rps"] for r in samples]
    cpu = linear_fit([r["completed_rps"] for r in samples], [r["cpu_usage_cores"] for r in samples])
    memory = linear_fit(loads, [r["memory_usage_bytes"] / 1_000_000 for r in samples])
    latency = linear_fit(loads, [r["latency_mean_ms"] for r in samples])
    overloaded = [r for r in samples if (r["cpu_utilization_pct"] or 0) >= 85 or r["error_rate_pct"] >= 1]
    healthy = [r for r in samples if r["error_rate_pct"] < 1]
    capacity_lower_bound = max((r["completed_rps"] for r in healthy), default=None)
    measured_sources = sorted({r["source"] for r in samples})
    real = measured_sources == ["real"] and all(r["collection_mode"] == "live" for r in samples)
    limit = samples[0]["cpu_capacity_cores"]
    cpu_capacity_estimate = (limit - cpu["intercept"]) / cpu["slope"] if (
        limit and cpu.get("slope", 0) > 0 and cpu.get("r_squared", 0) >= 0.5) else None
    report = {
        "status": "preliminary_real_measurements" if real else "fixture_demonstration_not_real_calibration",
        "source": measured_sources, "cluster_id": "A", "samples": len(samples), "rejected_windows": rejected,
        "run_ids": sorted({r["run_id"] for r in samples}), "input_sha256": __import__("hashlib").sha256(input_csv.read_bytes()).hexdigest(),
        "cpu_cores_vs_completed_rps": cpu, "memory_MB_vs_incoming_rps": memory,
        "latency_ms_vs_incoming_rps": latency, "observed_healthy_capacity_lower_bound_rps": capacity_lower_bound,
        "cpu_limit_extrapolated_capacity_rps": cpu_capacity_estimate if cpu_capacity_estimate and cpu_capacity_estimate > 0 else None,
        "overload_onset_observed_rps": min((r["incoming_rps"] for r in overloaded), default=None),
        "capacity_interpretation": "lower bound only" if not overloaded else "short-run observation; saturation/steady state not guaranteed",
        "assumptions": ["Scope is the measured sample-app pod budget, not entire Cluster A hardware",
                        "Node/Ubuntu/hypervisor CPU are not interchangeable with pod CPU",
                        "Resource Metrics API is smoothed and intended for autoscaling; fit is exploratory",
                        "Linear fits are descriptive within observed load range, not proven causal laws",
                        "Server latency excludes Windows/network/SSH; client latency is a separate layer",
                        "Do not map mean/p95 server latency directly to simulator base latency (service time would be double counted)",
                        "RAM-vs-RPS slope does not identify memory per queued request; queue depth/capacity remain unmeasured",
                        "No MARL retraining or live infrastructure changes; B/C remain simulated assumptions"],
        "unchanged_simulator_parameters": ["cpu_cores", "requests_per_core", "memory_mb", "idle_memory_mb", "memory_per_request_mb", "queue_limit", "base_latency_ms"],
    }
    output.mkdir(parents=True, exist_ok=True)
    if candidate:
        if not real or not overloaded or len(samples) < 6 or cpu.get("status") != "estimated" or capacity_lower_bound is None:
            raise ValueError("Candidate requires real/live, >=6 aligned windows, distinct loads and observed overload; fixtures cannot create calibrated config")
        original = load_config(simulator_config).to_dict()
        proposed = deepcopy(original)
        match = [c for c in proposed["clusters"] if c["name"] == "A"]
        if len(match) != 1:
            raise ValueError("Simulator must contain cluster A")
        match[0]["capacity"] = max(1, math.floor(capacity_lower_bound))
        validated = config_from_dict(proposed)
        actual = next(c for c in validated.clusters if c.name == "A")
        if actual.effective_capacity != match[0]["capacity"]:
            raise ValueError("Original CPU/RAM assumptions would cap the proposal; review resource mapping manually")
        if candidate.resolve() == simulator_config.resolve() or candidate.exists():
            raise ValueError("Write candidate to a NEW path, never overwrite original config")
        candidate.parent.mkdir(parents=True, exist_ok=True)
        candidate.write_text(json.dumps(proposed, indent=2), encoding="utf-8")
        report["candidate"] = str(candidate)
        report["candidate_note"] = "Only A.capacity is proposed from a short-run observed lower bound; all other A values and B/C remain original, unverified assumptions. Review before use."
        candidate.with_suffix(".provenance.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
    (output / "calibration.json").write_text(json.dumps(report, indent=2, allow_nan=False), encoding="utf-8")
    (output / "report.md").write_text(
        "# Calibration estimate\n\nStatus: **" + report["status"] + "**\n\n"
        f"Aligned windows: {len(samples)}; observed healthy capacity LOWER BOUND: {capacity_lower_bound} req/s.\n"
        f"Observed overload onset: {report['overload_onset_observed_rps']} req/s (null means not observed).\n\n"
        "See calibration.json for coefficients, units, R-squared, observed range and exclusions.\n\n"
        + "\n".join("- " + value for value in report["assumptions"]) + "\n", encoding="utf-8")
    return report
