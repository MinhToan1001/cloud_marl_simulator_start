"""Headless plots from exported CSV, with read-only legacy schema support."""
from __future__ import annotations

import math
import statistics
from collections import defaultdict
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt

from .storage import read_csv

COLORS = ("#2563eb", "#e87916", "#059669")
LABELS = {"round_robin": "Round Robin", "least_loaded": "Least Loaded", "marl": "MARL (IPPO)"}


def numeric(row: dict, key: str, fallback: str | None = None) -> float:
    raw = row.get(key, row.get(fallback, "") if fallback else "")
    return float(raw) if raw not in ("", None) else math.nan


def plot_results(csv_dir: Path, figure_dir: Path, scenario: str | None = None) -> list[Path]:
    workload = read_csv(csv_dir / "workload.csv")
    metrics = read_csv(csv_dir / "cluster_metrics.csv")
    summary = read_csv(csv_dir / "summary.csv")
    if workload and "scenario" in workload[0]:
        available = {r["scenario"] for r in workload}
        scenario = scenario or ("mixed" if "mixed" in available else workload[0]["scenario"])
        workload = [r for r in workload if r["scenario"] == scenario]
        metrics = [r for r in metrics if r["scenario"] == scenario]
        summary = [r for r in summary if r["scenario"] == scenario]
    if not workload or not metrics or not summary:
        raise ValueError("Plotting requires nonempty workload, cluster metrics, and summary")
    first_seed = workload[0].get("seed")
    selected = [r for r in metrics if r.get("seed") == first_seed]
    traffic = [r for r in workload if r.get("seed") == first_seed]
    policies = list(dict.fromkeys(r["policy"] for r in selected))
    clusters = list(dict.fromkeys(r["cluster"] for r in selected))
    legacy = "latency_proxy_ms" in metrics[0] and "latency_mean_ms" not in metrics[0]
    latency_label = "Latency proxy (ms)" if legacy else "Completed latency (ms)"
    caption = "SIMULATION ONLY" + (f" | seed {first_seed}" if first_seed is not None else " | legacy data")
    if scenario:
        caption += f" | {scenario}"
    figure_dir.mkdir(parents=True, exist_ok=True)
    paths = []

    def save(fig, name):
        fig.suptitle(caption, fontsize=12)
        fig.tight_layout(rect=(0, 0, 1, 0.95))
        path = figure_dir / f"{name}.png"
        fig.savefig(path, dpi=200, facecolor="white")
        plt.close(fig)
        paths.append(path)

    with plt.rc_context({"font.size": 10, "axes.grid": True, "grid.alpha": 0.2}):
        fig, ax = plt.subplots(figsize=(11, 4))
        ax.plot([int(r["second"]) for r in traffic], [int(r["incoming_requests"]) for r in traffic], color=COLORS[0])
        if "capacity_req_per_s" in selected[0]:
            total_capacity = sum(float(r["capacity_req_per_s"]) for r in selected
                                 if r["policy"] == policies[0] and int(r["second"]) == 0)
            ax.axhline(total_capacity, color="#dc2626", linestyle="--", label="Total service capacity")
            ax.legend(loc="upper left", bbox_to_anchor=(0.01, 0.85))
        last_phase = None
        for r in traffic:
            phase = r.get("phase")
            if phase and phase != last_phase:
                ax.axvline(int(r["second"]), color="#64748b", alpha=0.3)
                ax.text(int(r["second"]), 0.98, phase, transform=ax.get_xaxis_transform(), va="top", fontsize=9)
                last_phase = phase
        ax.set(xlabel="Time (s)", ylabel="Incoming requests/s", title="Workload shared by both policies")
        save(fig, "workload")

        for field, fallback, ylabel, name in (
            ("cpu_utilization_pct", "cpu_proxy_pct", "CPU utilization (%)", "cpu_utilization"),
            ("memory_utilization_pct", "ram_proxy_pct", "RAM utilization (%)", "memory_utilization"),
            ("queue_after", None, "Queued requests", "queue_length"),
        ):
            fig, axes = plt.subplots(len(policies), 1, figsize=(11, 3 * len(policies)), squeeze=False, sharex=True)
            for ax, policy in zip(axes[:, 0], policies):
                for i, cluster in enumerate(clusters):
                    part = [r for r in selected if r["policy"] == policy and r["cluster"] == cluster]
                    ax.plot([int(r["second"]) for r in part], [numeric(r, field, fallback) for r in part],
                            label=f"Cluster {cluster}", color=COLORS[i % len(COLORS)])
                ax.set(ylabel=ylabel, title=LABELS.get(policy, policy))
                if field != "queue_after":
                    ax.set_ylim(0, 105)
                else:
                    ax.set_ylim(bottom=0)
                ax.legend(loc="upper left", ncol=len(clusters))
            axes[-1, 0].set_xlabel("Time (s)")
            save(fig, name)

        grouped = defaultdict(list)
        for row in selected:
            grouped[(row["policy"], int(row["second"]))].append(row)
        series = defaultdict(list)
        for (policy, second), rows in sorted(grouped.items()):
            processed = sum(int(r["processed"]) for r in rows)
            assigned = sum(int(r["assigned"]) for r in rows)
            dropped = sum(int(r["dropped"]) for r in rows)
            latency_sum = sum(int(r["processed"]) * numeric(r, "latency_mean_ms", "latency_proxy_ms")
                              for r in rows if int(r["processed"]) > 0)
            series[policy].append({"second": second, "throughput": processed,
                                   "latency": latency_sum / processed if processed else math.nan,
                                   "error": 100 * dropped / assigned if assigned else 0})
        fig, axes = plt.subplots(2, 1, figsize=(11, 7), sharex=True)
        for i, policy in enumerate(policies):
            part = series[policy]
            for ax, field in zip(axes, ("latency", "throughput")):
                ax.plot([r["second"] for r in part], [r[field] for r in part], color=COLORS[i], label=LABELS.get(policy, policy))
                ax.legend(loc="upper left")
        axes[0].set_ylabel(latency_label)
        axes[1].set(xlabel="Time (s)", ylabel="Throughput (requests/s)")
        save(fig, "latency_throughput")

        fig, ax = plt.subplots(figsize=(11, 4))
        for i, policy in enumerate(policies):
            part = series[policy]
            ax.plot([r["second"] for r in part], [r["error"] for r in part], color=COLORS[i], label=LABELS.get(policy, policy))
        ax.set(xlabel="Time (s)", ylabel="Dropped / incoming (%)", ylim=(0, 105))
        ax.legend(loc="upper left")
        save(fig, "error_rate")

        fig, axes = plt.subplots(2, 3, figsize=(13, 8))
        for ax, (field, fallback, label) in zip(axes.flat, (
            ("throughput_req_per_s", None, "Throughput (req/s)"),
            ("latency_mean_ms", "latency_proxy_ms", latency_label),
            ("latency_p95_ms", None, "Completed latency p95 (ms)"),
            ("error_rate_pct", "drop_rate_pct", "Error rate (%)"),
            ("processed", None, "Completed requests"),
            ("waiting_at_end", None, "Waiting at end"),
        )):
            means, stds = [], []
            for policy in policies:
                values = [numeric(r, field, fallback) for r in summary if r["policy"] == policy]
                values = [v for v in values if math.isfinite(v)]
                means.append(statistics.fmean(values) if values else math.nan)
                stds.append(statistics.stdev(values) if len(values) > 1 else 0)
            if any(math.isfinite(v) for v in means):
                ax.bar(range(len(policies)), means, yerr=stds, capsize=5, color=COLORS[:len(policies)])
                ax.set_ylim(bottom=0)
            else:
                ax.text(0.5, 0.5, "No completed samples / unavailable", ha="center", transform=ax.transAxes, fontsize=9)
            ax.set_xticks(range(len(policies)), [LABELS.get(p, p) for p in policies])
            ax.set_title(label)
        # Comparison is across every exported seed, unlike the time-series panels.
        fig.suptitle("SIMULATION ONLY | mean and sample standard deviation across runs")
        fig.tight_layout(rect=(0, 0, 1, 0.94))
        path = figure_dir / "comparison.png"
        fig.savefig(path, dpi=200, facecolor="white")
        plt.close(fig)
        paths.append(path)
    return paths
