"""Paired placement evaluation, source-labeled plots and archived shadow replay."""
from dataclasses import asdict
from pathlib import Path
import json
import statistics

import numpy as np
import torch

from .env import PlacementEnv
from .io import file_hash, fingerprint, new_directory, read_json, write_csv, write_json
from .policies import PlacementBaseline
from .schema import manifest, schema_hash
from .shadow import archived_packets, action_masks, observations, operation_plan
from .trace import make_trace, SCENARIOS
from .training import load_policy, rollout

METRICS = ("throughput_rps", "latency_mean_ms", "latency_p95_ms", "latency_p99_ms", "error_fraction", "slo_violation_fraction_resolved",
           "cpu_utilization", "memory_utilization", "resource_waste_fraction", "completed_moves", "failed_moves", "downtime_service_s", "mean_dwell_s", "unfinished")


def run_baseline(config, trace, method):
    env, policy = PlacementEnv(config), PlacementBaseline(method, config)
    env.reset(trace=trace)
    done = False
    while not done:
        _, _, done, _ = env.step(policy.act(env))
    return env


def evaluate(config, training_root, output, seeds=(30001, 30002, 30003, 30004, 30005), policy_seeds=(7, 17, 27)):
    if len(set(seeds)) != len(seeds) or not seeds or len(set(policy_seeds)) != len(policy_seeds):
        raise ValueError("Distinct nonempty evaluation seeds required")
    policies, checkpoint_manifest = {}, []
    for seed in policy_seeds:
        path = Path(training_root)/f"seed_{seed}/best.pt"
        policy, checkpoint = load_policy(path, config)
        settings = policy.training
        if any(s in settings.validation_seeds or settings.train_seed_start <= s < settings.train_seed_start+settings.episodes or s == seed for s in seeds):
            raise ValueError("Held-out seeds overlap training or validation")
        policies[seed] = policy
        checkpoint_manifest.append({"policy_seed": seed, "path": str(path), "sha256": file_hash(path), "episode": checkpoint["episode"]})
    output = new_directory(output)
    summaries, traces, ticks, events, histograms = [], [], [], [], []
    for scenario in SCENARIOS:
        for seed in seeds:
            trace = make_trace(config, seed, scenario)
            traces.append(trace)
            runs = []
            for method in ("round_robin", "least_loaded"):
                runs.append((method, None, run_baseline(config, trace, method)))
            for policy_seed, policy in policies.items():
                with torch.random.fork_rng(devices=[]):
                    torch.manual_seed((policy_seed*1000003+seed*9176) % (2**63-1))
                    env, _, _ = rollout(policy, config, seed, scenario, trace=trace)
                runs.append(("ippo", policy_seed, env))
            for method, policy_seed, env in runs:
                if env.trace["sha256"] != trace["sha256"]:
                    raise RuntimeError("Paired trace changed")
                prefix = {"scenario": scenario, "workload_seed": seed, "method": method, "policy_seed": policy_seed}
                summaries.append({**prefix, "trace_sha256": trace["sha256"], **env.summary()})
                ticks.extend({**prefix, **tick} for tick in env.ticks)
                events.extend({**prefix, **{key: event.get(key) for key in ("second", "service", "event", "proposed", "executed", "reason", "target")}} for event in env.events)
                histograms.extend({**prefix, "latency_ms": latency, "count": count} for latency, count in sorted(env.histogram.items()))
            print(f"placement evaluation {scenario} trace={seed} runs={len(runs)}", flush=True)
    comparisons, paired = [], []
    for scenario in SCENARIOS:
        for method in ("round_robin", "least_loaded", "ippo"):
            rows = [r for r in summaries if r["scenario"] == scenario and r["method"] == method]
            for metric in METRICS:
                valid = [r for r in rows if r[metric] is not None]
                values = [r[metric] for r in valid]
                seed_means = [statistics.mean(r[metric] for r in valid if r["policy_seed"] == p) for p in policy_seeds] if method == "ippo" and valid else []
                workload_means = [statistics.mean(r[metric] for r in valid if r["workload_seed"] == s) for s in seeds if any(r["workload_seed"] == s for r in valid)]
                comparisons.append({"scenario": scenario, "method": method, "metric": metric, "mean": statistics.mean(values) if values else None,
                                    "policy_seed_std": statistics.stdev(seed_means) if len(seed_means) > 1 else None,
                                    "workload_seed_std": statistics.stdev(workload_means) if len(workload_means) > 1 else None,
                                    "valid_runs": len(valid), "policy_seeds": len(policy_seeds) if method == "ippo" else 0, "workload_seeds": len(seeds)})
        for row in [r for r in summaries if r["scenario"] == scenario and r["method"] == "ippo"]:
            for method in ("round_robin", "least_loaded"):
                baseline = next(r for r in summaries if r["scenario"] == scenario and r["workload_seed"] == row["workload_seed"] and r["method"] == method)
                paired.append({"scenario": scenario, "workload_seed": row["workload_seed"], "policy_seed": row["policy_seed"], "baseline": method,
                               **{metric: row[metric]-baseline[metric] if row[metric] is not None and baseline[metric] is not None else None for metric in METRICS}})
    write_csv(output/"summary.csv", summaries)
    write_csv(output/"comparison.csv", comparisons)
    write_csv(output/"paired_differences.csv", paired)
    write_csv(output/"timeseries.csv", ticks)
    write_csv(output/"events.csv", events)
    write_csv(output/"latency_histogram.csv", histograms)
    write_json(output/"traces.json", traces)
    result = {"source": "simulated", "action_kind": "workload_placement", "run_count": len(summaries), "unique_trace_count": len(traces),
              "baseline_runs": 2*len(traces), "ippo_runs": len(policy_seeds)*len(traces), "policy_seeds": list(policy_seeds),
              "workload_seeds": list(seeds), "scenarios": list(SCENARIOS), "checkpoints": checkpoint_manifest,
              "schema_hash": schema_hash(config), "config_hash": fingerprint(config.to_dict()),
              "paired_trace_hashes": [t["sha256"] for t in traces], "baseline_duplicate_count": 0,
              "latency_population": "completed requests; unsuccessful/unfinished separately reported",
              "slo_population": "resolved requests; unfinished cohort explicitly censored",
              "uncertainty": "policy-seed SD of workload-averaged scores; workload-seed SD of policy-averaged scores; descriptive, n_policy=3",
              "hysteresis_least_loaded": .1, "inference": "sample; isolated per-run RNG",
              "calibration_warning": "Most experimental loads exceed observed 0.27-3.30 RPS. Capacity, background, WAN, readiness and additional services are simulation assumptions."}
    write_json(output/"metadata.json", result)
    return summaries, comparisons, result


def shadow_replay(config, run_dir, checkpoint_path, output):
    output = new_directory(output)
    policy, saved = load_policy(checkpoint_path, config)
    packets = archived_packets(run_dir, config)
    rows = []
    with torch.random.fork_rng(devices=[]):
        torch.manual_seed(40001)
        for packet in packets:
            obs, masks = observations(packet, config), action_masks(packet, config)
            actions, _, _ = policy.act(obs, masks, greedy=True)
            rows.extend({"timestamp_utc": packet["timestamp_utc"], **operation_plan(packet, name, action, config)} for name, action in actions.items())
    legacy_rejected = False
    legacy = Path(__file__).resolve().parents[2]/"results/training/checkpoints/best.pt"
    try:
        load_policy(legacy, config)
    except ValueError:
        legacy_rejected = True
    # A fixture verifies the full plan shape. It is explicitly not a live mapping.
    fixture = PlacementEnv(config); fixture.reset(seed=40001)
    packet = fixture.packet()
    packet["mapping"] = {s.name: {"deployment_identity_verified": True, "fixture_only": True} for s in config.services}
    candidates = [(s.name, a) for s in config.services for a in range(1, 4) if operation_plan(packet, s.name, a, config)["validated_action"]]
    fixture_plan = operation_plan(packet, *candidates[0], config) if candidates else None
    write_json(output/"archived_packets.json", packets)
    write_json(output/"proposals.json", rows)
    write_json(output/"synthetic_fixture_plan.json", {"source": "simulated_fixture", "plan": fixture_plan})
    result = {"mode": "offline_archive_shadow", "archive_snapshot_count": len(packets), "workload_proposal_count": len(rows),
              "network_calls": 0, "executed_operations": 0, "nonempty_historical_plans": sum(bool(r["operations"]) for r in rows),
              "legacy_routing_checkpoint_rejected": legacy_rejected, "schema_hash": schema_hash(config),
              "checkpoint_episode": saved["episode"], "synthetic_plan_has_six_operations": fixture_plan is not None and len(fixture_plan["operations"]) == 6,
              "closed_loop_ready": False, "reasons": ["Only historical Cluster A data; current SSH hop unavailable in H1",
                  "B/C not deployed or observed", "Deployment owner identity missing from scrubbed historical inventory",
                  "Cluster-wide resource reservation inventory missing", "Additional workload mappings unverified",
                  "Training load/capacity assumptions exceed low-load calibration domain"],
              "compatibility": "Versioned vector/action interfaces and fail-closed shadow fallback verified; real placement execution unverified"}
    write_json(output/"readiness.json", result)
    return result


def plots(output):
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    import csv
    output = Path(output)
    figures = output/"figures"
    figures.mkdir(exist_ok=True)
    with (output/"evaluation/comparison.csv").open(newline="", encoding="utf-8-sig") as stream:
        comparison = list(csv.DictReader(stream))
    fig, axes = plt.subplots(2, 2, figsize=(13, 8))
    metrics = ("throughput_rps", "latency_p99_ms", "slo_violation_fraction_resolved", "completed_moves")
    labels = ("Throughput (req/s)", "Completed latency p99 (ms)", "SLO violations / resolved", "Completed migrations")
    positions = np.arange(len(SCENARIOS))
    for ax, metric, label in zip(axes.flat, metrics, labels):
        for i, method in enumerate(("round_robin", "least_loaded", "ippo")):
            values = [float(next(r for r in comparison if r["scenario"] == s and r["metric"] == metric and r["method"] == method)["mean"]) for s in SCENARIOS]
            ax.bar(positions+(i-1)*.25, values, .25, label=method)
        ax.set_xticks(positions, SCENARIOS, rotation=25)
        ax.set_ylabel(label); ax.grid(axis="y", alpha=.2)
    axes[0, 0].legend(fontsize=9)
    fig.suptitle("SIMULATED PLACEMENT | 3 policy seeds, 5 paired workload seeds | means")
    fig.tight_layout(rect=(0, 0, 1, .95)); fig.savefig(figures/"placement_comparison.png", dpi=160); plt.close(fig)
    fig, axes = plt.subplots(2, 2, figsize=(12, 8))
    for seed in (7, 17, 27):
        with (output/f"training/seed_{seed}/training.csv").open(encoding="utf-8-sig", newline="") as stream:
            history = list(csv.DictReader(stream))
        validation = list(csv.DictReader((output/f"training/seed_{seed}/validation.csv").open(encoding="utf-8-sig", newline="")))
        for ax, key, label in zip(axes.flat, ("mean_reward", "entropy", "error_fraction", "completed_moves"), ("Train mean reward (20 episode average)", "Masked policy entropy", "Request error fraction", "Completed moves per episode")):
            values = np.asarray([float(r[key]) for r in history])
            if key == "mean_reward" and len(values) >= 20:
                values = np.convolve(values, np.ones(20)/20, "valid")
            ax.plot(np.arange(len(history)-len(values)+1, len(history)+1), values, label=f"seed {seed}", alpha=.8)
            ax.set(xlabel="Episode", ylabel=label); ax.grid(alpha=.2)
    axes[0, 0].legend()
    fig.suptitle("SIMULATED PLACEMENT | Independent IPPO training seeds")
    fig.tight_layout(rect=(0, 0, 1, .95)); fig.savefig(figures/"training_curves.png", dpi=160); plt.close(fig)
    with (output/"calibration/heldout_predictions.csv").open(encoding="utf-8", newline="") as stream:
        predictions = list(csv.DictReader(stream))
    fig, axes = plt.subplots(1, 3, figsize=(13, 4))
    for ax, key, label, scale in zip(axes, ("cpu_usage_cores", "memory_usage_bytes", "latency_mean_ms"), ("Pod CPU (cores)", "Pod RAM (MiB)", "Server mean latency (ms)"), (1, 1024**2, 1)):
        x = [float(r["incoming_rps"]) for r in predictions]
        ax.scatter(x, [float(r[f"real_{key}"])/scale for r in predictions], label="REAL observed", s=30)
        ax.scatter(x, [float(r[f"predicted_{key}"])/scale for r in predictions], marker="x", label="held-out prediction", s=40)
        ax.set(xlabel="Observed incoming RPS", ylabel=label); ax.grid(alpha=.2)
    axes[0].legend(fontsize=8)
    fig.suptitle("Cluster A pod | 8 low-load windows | grouped hold-out; maximum capacity unknown")
    fig.tight_layout(rect=(0, 0, 1, .9)); fig.savefig(figures/"calibration_holdout.png", dpi=160); plt.close(fig)
