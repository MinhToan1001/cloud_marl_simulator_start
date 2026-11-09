"""Paired, held-out evaluation on the same simulator/workload for all policies."""
from __future__ import annotations

import hashlib
import json
import platform
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path

from cloud_sim.config import SimulationConfig, integer
from cloud_sim.engine import simulate
from cloud_sim.experiments import METRICS, aggregate, fingerprint, package_versions
from cloud_sim.storage import write_csv
from cloud_sim.workload import make_workload
from .ippo import load_policy
from .training import run_policy_episode

METHODS = ("round_robin", "least_loaded", "marl")
SCENARIOS = ("mixed", "stable", "ramp_up", "spike", "recovery")


def scenario_config(config: SimulationConfig, scenario: str) -> SimulationConfig:
    if scenario not in SCENARIOS:
        raise ValueError(f"Unknown scenario: {scenario}")
    if scenario == "mixed":
        return config
    names = {"spike", "recovery"} if scenario == "recovery" else {scenario}
    phases = tuple(p for p in config.phases if p.name in names)
    if not phases or not any(p.name == scenario for p in phases):
        raise ValueError(f"Simulation config does not define phase {scenario}")
    return replace(config, phases=phases)


def evaluate(simulation: SimulationConfig, checkpoint_path: Path, output: Path,
             seeds: list[int], scenarios: list[str]) -> dict:
    if not seeds or len(set(seeds)) != len(seeds):
        raise ValueError("Testing seeds must be nonempty and unique")
    if not scenarios or len(set(scenarios)) != len(scenarios):
        raise ValueError("Scenarios must be nonempty and unique")
    for seed in seeds:
        integer("testing seed", seed)
    policy, training, checkpoint = load_policy(checkpoint_path, simulation)
    train_end = training.train_seed_start + training.episodes
    forbidden = {training.seed, *training.validation_seeds}
    if any(seed in forbidden or training.train_seed_start <= seed < train_end for seed in seeds):
        raise ValueError("Testing seeds must be disjoint from policy, training workload, and validation seeds")
    conditions = {name: scenario_config(simulation, name) for name in scenarios}
    workloads, metrics, timeseries, summaries, histograms, manifest = [], [], [], [], [], []
    for scenario, config in conditions.items():
        for seed in seeds:
            workload = make_workload(config, seed)
            values = [r["incoming_requests"] for r in workload]
            workloads.extend({"scenario": scenario, "seed": seed, **r} for r in workload)
            workload_hash = fingerprint(values)
            manifest.append({"scenario": scenario, "seed": seed, "seconds": len(values),
                             "received": sum(values), "workload_sha256": workload_hash,
                             "methods": list(METHODS)})
            for method in METHODS:
                if method == "marl":
                    result, _ = run_policy_episode(policy, config, training, seed, workload=values)
                else:
                    result = simulate(method, values, config)
                prefix = {"scenario": scenario, "seed": seed}
                summaries.append({**prefix, **result.summary})
                metrics.extend({**prefix, **r} for r in result.rows)
                timeseries.extend({**prefix, **r} for r in result.timeseries)
                histograms.extend({**prefix, "policy": method, "latency_ms": latency, "count": count}
                                  for latency, count in sorted(result.latency_histogram.items()))
    comparison = []
    differences = []
    for scenario in scenarios:
        part = [r for r in summaries if r["scenario"] == scenario]
        comparison.extend({"scenario": scenario, **r} for r in aggregate(part, METHODS))
        for seed in seeds:
            by_method = {r["policy"]: r for r in part if r["seed"] == seed}
            for baseline in METHODS[:2]:
                row = {"scenario": scenario, "seed": seed, "comparison": f"marl_minus_{baseline}"}
                for metric in METRICS:
                    a, b = by_method["marl"][metric], by_method[baseline][metric]
                    row[metric] = a - b if a is not None and b is not None else None
                differences.append(row)
    directory = output / "csv"
    for name, rows in (("workload", workloads), ("cluster_metrics", metrics), ("timeseries", timeseries),
                       ("summary", summaries), ("comparison", comparison), ("paired_differences", differences)):
        write_csv(directory / f"{name}.csv", rows)
    if histograms:
        write_csv(directory / "latency_histogram.csv", histograms)
    else:
        (directory / "latency_histogram.csv").write_text("scenario,seed,policy,latency_ms,count\n", encoding="utf-8-sig")
    metadata = {
        "data_source": "simulation", "created_at_utc": datetime.now(timezone.utc).isoformat(),
        "model_version": "fifo-batch-v1", "algorithm": "IPPO", "inference": training.inference_mode,
        "policy_rng": "(policy_seed * 1000003 + workload_seed * 9176) modulo (2**63-1); isolated torch RNG",
        "checkpoint": str(checkpoint_path.resolve()), "checkpoint_episode": checkpoint["episode"],
        "checkpoint_sha256": hashlib.sha256(checkpoint_path.read_bytes()).hexdigest(),
        "policy_seed": training.seed, "training_seed_range": [training.train_seed_start, train_end - 1],
        "validation_seeds": list(training.validation_seeds), "testing_seeds": seeds,
        "scenarios": scenarios, "config": simulation.to_dict(),
        "scenario_configs": {name: cfg.to_dict() for name, cfg in conditions.items()},
        "workload_runs": manifest, "policies": list(METHODS), "drain_at_end": False,
        "tick_seconds": 1, "python": platform.python_version(), "packages": package_versions(),
        "reward_config": training.to_dict()["reward"], "action_step": training.action_step,
    }
    (output / "metadata.json").write_text(json.dumps(metadata, indent=2), encoding="utf-8")
    write_evaluation_report(output, comparison, metadata)
    return metadata


def write_evaluation_report(output, comparison, metadata):
    lines = ["# Đánh giá Round Robin, Least Loaded và MARL (IPPO)", "",
             "**Dữ liệu simulation; không phải metrics OpenStack/Kubernetes/AWS.**", "",
             f"Checkpoint episode {metadata['checkpoint_episode']}, chọn bằng validation, inference {metadata['inference']} với policy RNG riêng có seed cố định.",
             f"Policy initialization seed: {metadata['policy_seed']}; training workload seeds: {metadata['training_seed_range']};",
             f"validation: {metadata['validation_seeds']}; testing độc lập: {metadata['testing_seeds']}.",
             "Mỗi cặp scenario/seed dùng cùng workload, tài nguyên, tick và hạch toán cho cả ba phương pháp.",
             "`metadata.json` lưu hash workload/checkpoint và cấu hình từng scenario; không tune bằng tập testing.", "",
             "| Scenario | Thuật toán | Throughput req/s | Latency mean ms | P95 ms | Error % | Thành công |",
             "|---|---|---:|---:|---:|---:|---:|"]
    for row in comparison:
        cells = [row["scenario"], row["policy"]]
        for key in ("throughput_req_per_s", "latency_mean_ms", "latency_p95_ms", "error_rate_pct", "processed"):
            mean, std = row[f"{key}_mean"], row[f"{key}_std"]
            cells.append(f"{mean:.2f} ± {std:.2f}" if mean is not None else "N/A")
        lines.append("| " + " | ".join(cells) + " |")
    lines += ["", "Giá trị mean ± sample std giữa testing seed; p95 tính trên từng request hoàn tất của từng run rồi tổng hợp.",
              "CPU/RAM trung bình, imbalance, số dropped/waiting và số mẫu hợp lệ cũng có trong `csv/comparison.csv`.", "",
              "## Diễn giải kết quả", ""]
    primary = "mixed" if "mixed" in metadata["scenarios"] else metadata["scenarios"][0]
    by_method = {r["policy"]: r for r in comparison if r["scenario"] == primary}
    for baseline in METHODS[:2]:
        parts = []
        for key, unit in (("throughput_req_per_s", "req/s throughput"), ("latency_mean_ms", "ms latency mean"),
                          ("error_rate_pct", "điểm phần trăm error")):
            a, b = by_method["marl"][f"{key}_mean"], by_method[baseline][f"{key}_mean"]
            if a is not None and b is not None:
                parts.append(f"{a - b:+.2f} {unit}")
        lines.append(f"Trong {primary}, MARL − {baseline}: " + "; ".join(parts) + ".")
    lines += ["", "Không coi throughput cao hơn là thắng mọi tiêu chí. Kiểm tra đồng thời latency, drop, imbalance và queue cuối.",
              "Đây là đánh giá trên năm seed tải (mặc định), với một seed huấn luyện chính sách; không phải năm lần train độc lập.",
              "Mean/std mô tả biến thiên, chưa phải chứng minh ý nghĩa thống kê hoặc ưu thế ngoài mô hình.", "",
              "## Ưu điểm và giới hạn", "",
              "Round Robin đơn giản; Least Loaded phản ứng trực tiếp với backlog/capacity và cần telemetry hiện tại.",
              "MARL học ba policy riêng từ reward, có chi phí training và suy luận; có thể kém baseline nếu học chưa đủ hoặc reward lệch mục tiêu.",
              "Không có imitation hay hardcode kết quả MARL. Training hữu hạn, mạng nhỏ chạy CPU và chọn checkpoint bằng validation.",
              "Stable/Ramp-up/Spike được chạy riêng theo phase; Recovery gồm Spike rồi Recovery để quan sát xả backlog.",
              "Mô hình arrival theo lô một giây, capacity cố định, chưa hiệu chỉnh cluster thật, chỉ có lỗi overflow.",
              "Không drain queue cuối run; dropped/waiting không có latency hoàn tất, nên có censoring.",
              "Không dùng kết quả này để khẳng định hiệu năng OpenStack/AWS thật.", "",
              "## Dữ liệu và biểu đồ", "",
              "`csv/summary.csv`: từng run; `comparison.csv`: mean/std; `paired_differences.csv`: chênh lệch paired theo seed;",
              "`cluster_metrics.csv`, `timeseries.csv`, `workload.csv`, `latency_histogram.csv`: dữ liệu để tái kiểm tra.",
              f"`figures/`: bảy hình cho scenario {primary}, seed đầu tiên ở các time-series; comparison dùng tất cả testing seed.",
              "`figures/scenario_comparison.png`: throughput, latency, error và imbalance giữa các scenario.", ""]
    (output / "report.md").write_text("\n".join(lines), encoding="utf-8")


def plot_evaluation(output):
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    import numpy as np
    from cloud_sim.plotting import COLORS, LABELS, plot_results
    from cloud_sim.storage import read_csv
    paths = plot_results(output / "csv", output / "figures")
    rows = read_csv(output / "csv/comparison.csv")
    scenarios = list(dict.fromkeys(r["scenario"] for r in rows))
    fig, axes = plt.subplots(2, 2, figsize=(13, 8))
    for ax, key, label in zip(axes.flat,
                              ("throughput_req_per_s", "latency_mean_ms", "error_rate_pct", "load_imbalance"),
                              ("Throughput (req/s)", "Completed latency mean (ms)", "Error rate (%)", "Load imbalance")):
        positions = np.arange(len(scenarios))
        for index, policy in enumerate(METHODS):
            part = [next(r for r in rows if r["scenario"] == scenario and r["policy"] == policy) for scenario in scenarios]
            values = [float(r[f"{key}_mean"]) if r[f"{key}_mean"] != "" else float("nan") for r in part]
            errors = [float(r[f"{key}_std"]) if r[f"{key}_std"] != "" else 0 for r in part]
            ax.bar(positions + (index - 1) * 0.25, values, width=0.25, yerr=errors,
                   label=LABELS[policy], color=COLORS[index], capsize=3)
        ax.set_xticks(positions, scenarios, rotation=15)
        ax.set(ylabel=label, ylim=(0, None))
        ax.grid(axis="y", alpha=0.2)
    axes[0, 0].legend(fontsize=9)
    fig.suptitle("SIMULATION | Held-out testing | mean and sample standard deviation")
    fig.tight_layout(rect=(0, 0, 1, 0.96))
    path = output / "figures/scenario_comparison.png"
    fig.savefig(path, dpi=200, facecolor="white")
    plt.close(fig)
    return paths + [path]
