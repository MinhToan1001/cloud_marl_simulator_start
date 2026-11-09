"""Audit delivered files, cross-check scientific metrics, and repeat evaluation."""
from __future__ import annotations

import hashlib
import json
import math
import statistics
from collections import defaultdict
from pathlib import Path
from tempfile import TemporaryDirectory

import torch
from PIL import Image

from cloud_sim.config import load_config
from cloud_sim.storage import read_csv
from marl.config import training_config_from_dict
from marl.env import MultiClusterEnv
from marl.evaluation import evaluate
from marl.ippo import IPPO, read_checkpoint, seed_everything

ROOT = Path(__file__).resolve().parents[2]


def verify(output: Path | None = None):
    root = ROOT / "results"
    evaluation = root / "evaluation"
    metadata = json.loads((evaluation / "metadata.json").read_text())
    tables = {path.stem: read_csv(path) for path in (evaluation / "csv").glob("*.csv")}
    workload = defaultdict(dict)
    for row in tables["workload"]:
        workload[(row["scenario"], int(row["seed"]))][int(row["second"])] = int(row["incoming_requests"])
    metrics = defaultdict(list)
    for row in tables["cluster_metrics"]:
        metrics[(row["scenario"], int(row["seed"]), row["policy"])].append(row)
        assert int(row["queue_before"]) + int(row["assigned"]) == int(row["processed"]) + int(row["dropped"]) + int(row["queue_after"])
        assert 0 <= float(row["cpu_utilization_pct"]) <= 100
        assert 0 <= float(row["memory_utilization_pct"]) <= 100
        assert 0 <= int(row["queue_after"]) <= int(row["max_queue"])
    histograms = defaultdict(list)
    for row in tables["latency_histogram"]:
        histograms[(row["scenario"], int(row["seed"]), row["policy"])].append((float(row["latency_ms"]), int(row["count"])))
    for summary in tables["summary"]:
        key = summary["scenario"], int(summary["seed"]), summary["policy"]
        rows = metrics[key]
        trace = workload[key[:2]]
        assert sum(int(r["assigned"]) for r in rows) == int(summary["received"]) == sum(trace.values())
        for second, demand in trace.items():
            tick = [r for r in rows if int(r["second"]) == second]
            assert len(tick) == 3 and sum(int(r["assigned"]) for r in tick) == demand
        assert sum(int(r["processed"]) for r in rows) == int(summary["processed"])
        assert sum(int(r["dropped"]) for r in rows) == int(summary["dropped"])
        waiting = sum(int(r["queue_after"]) for r in rows if int(r["second"]) == len(trace) - 1)
        assert waiting == int(summary["waiting_at_end"])
        assert int(summary["received"]) == int(summary["processed"]) + int(summary["dropped"]) + waiting
        histogram = sorted(histograms[key])
        count = sum(n for _, n in histogram)
        assert count == int(summary["processed"])
        if count:
            assert math.isclose(sum(v * n for v, n in histogram) / count, float(summary["latency_mean_ms"]), abs_tol=1e-8)
            rank, cumulative = math.ceil(0.95 * count), 0
            for value, frequency in histogram:
                cumulative += frequency
                if cumulative >= rank:
                    assert math.isclose(value, float(summary["latency_p95_ms"]), abs_tol=1e-8)
                    break
    for comparison in tables["comparison"]:
        samples = [r for r in tables["summary"] if r["scenario"] == comparison["scenario"] and r["policy"] == comparison["policy"]]
        for key in ("throughput_req_per_s", "latency_mean_ms", "latency_p95_ms", "error_rate_pct", "load_imbalance"):
            values = [float(r[key]) for r in samples if r[key] != ""]
            assert math.isclose(statistics.fmean(values), float(comparison[key + "_mean"]), abs_tol=1e-8)
            assert math.isclose(statistics.stdev(values), float(comparison[key + "_std"]), abs_tol=1e-8)
    with TemporaryDirectory(dir=root, prefix="verification_") as temporary:
        destination = Path(temporary).resolve()
        assert destination.is_relative_to(root.resolve())
        evaluate(load_config(ROOT / "configs/simulation.json"), root / "training/checkpoints/best.pt",
                 destination, metadata["testing_seeds"], metadata["scenarios"])
        for path in (evaluation / "csv").glob("*.csv"):
            assert path.read_bytes() == (destination / "csv" / path.name).read_bytes(), path.name
    checkpoint = read_checkpoint(root / "training/checkpoints/best.pt")
    training = training_config_from_dict(checkpoint["training_config"])
    seed_everything(training.seed)
    initial = IPPO(MultiClusterEnv(load_config(ROOT / "configs/simulation.json")).possible_agents, training)
    for agent, model in initial.models.items():
        assert any(not torch.equal(value, checkpoint["policy_state"]["models"][agent][key]) for key, value in model.state_dict().items())
    history = read_csv(root / "training/csv/training.csv")
    assert len(history) == 300 and int(history[-1]["episode"]) == 300
    assert len(read_csv(root / "training/csv/agent_training.csv")) == 900
    assert all(math.isfinite(float(row[key])) for row in history for key in ("policy_loss", "value_loss", "entropy", "episode_reward"))
    figures = list((root / "baseline/figures").glob("*.png")) + list((root / "training/figures").glob("*.png")) + list((evaluation / "figures").glob("*.png"))
    assert len(figures) == 20
    for path in figures:
        with Image.open(path) as image:
            assert image.width >= 2000 and image.height >= 800
            assert abs(image.info["dpi"][0] - 200) < 0.01
            image.verify()
    legacy_hashes = {
        "legacy/simulate.py": "22551367a0ebffad9e7084d23de6d480a119f82a3f2189dcde1cb6332a275b4f",
        "legacy/README.md": "1ad24ee935b6951946292df17c4c4b1fd189c5d83cabdeb260d5c3c26ba81408",
        "legacy/output/cluster_metrics.csv": "1c8d107a83f25c4c54b5f83315b2b46e460c1f1ff4bc2f1e5f108a25fd633aa9",
        "legacy/output/summary.csv": "c324799aaad868b47fc377ffba621e8f90ee9400738c0436aed57ee35b97ed22",
        "legacy/output/workload.csv": "9e3c3868fcda12e38616a65fdeeb1a28297453f8c79d4d2dd5fad7638f85fc1f",
    }
    for name, expected in legacy_hashes.items():
        assert hashlib.sha256((ROOT / name).read_bytes()).hexdigest() == expected
    report = {"data_source": "simulation", "checked_runs": len(tables["summary"]),
              "csv_rows": {name: len(rows) for name, rows in tables.items()},
              "request_accounting": "passed", "latency_histogram_mean_p95": "passed", "comparison_mean_std": "passed",
              "repeated_evaluation_csv": "all seven files byte-identical", "learned_agents": 3,
              "training_episodes": len(history), "best_episode": checkpoint["episode"],
              "verified_primary_figures": len(figures), "legacy_sha256": legacy_hashes}
    destination = output or ROOT / "data/validation/results.json"
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(json.dumps(report, indent=2))


def main():
    import argparse
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, help="Write verification evidence (default data/validation/results.json)")
    verify(parser.parse_args().output)


if __name__ == "__main__":
    main()
