from dataclasses import replace
import hashlib
import json
from pathlib import Path

import pytest

from cloud_sim.config import PhaseConfig
from cloud_sim.experiments import aggregate, run_baselines
from cloud_sim.storage import read_csv, read_workload, write_csv

ROOT = Path(__file__).resolve().parents[2]


def test_paired_workload_and_reproducible_exports(config, tmp_path):
    config = replace(config, phases=(PhaseConfig("spike", 4, 600, 600),))
    first, second = tmp_path / "first", tmp_path / "second"
    run_baselines(config, first, [42, 43])
    run_baselines(config, second, [42, 43])
    for name in ("workload", "cluster_metrics", "timeseries", "summary", "comparison", "latency_histogram"):
        assert (first / "csv" / f"{name}.csv").read_bytes() == (second / "csv" / f"{name}.csv").read_bytes()
    rows = read_csv(first / "csv/cluster_metrics.csv")
    for seed in (42, 43):
        for second in range(4):
            part = [r for r in rows if int(r["seed"]) == seed and int(r["second"]) == second]
            assert len({r["traffic_total"] for r in part}) == 1
            assert len(part) == 6
    metadata = json.loads((first / "metadata.json").read_text())
    assert metadata["data_source"] == "simulation"
    assert len(metadata["workload_runs"]) == 2


def test_original_csv_integrity_and_replay(config, tmp_path):
    paths = list((ROOT / "legacy/output").glob("*.csv"))
    before = {p: hashlib.sha256(p.read_bytes()).hexdigest() for p in paths}
    original = read_workload(ROOT / "legacy/output/workload.csv", 42)
    assert len(original) == 180
    assert sum(r["incoming_requests"] for r in original) == 36352
    summaries = run_baselines(config, tmp_path, [42], ROOT / "legacy/output/workload.csv")
    assert all(s["received"] == 36352 for s in summaries)
    assert before == {p: hashlib.sha256(p.read_bytes()).hexdigest() for p in paths}


def test_aggregation_sample_std_and_missing_latency():
    from cloud_sim.experiments import METRICS
    rows = []
    for policy in ("round_robin", "least_loaded"):
        for value in (1, 3):
            rows.append({"policy": policy, **{metric: value for metric in METRICS}})
    result = aggregate(rows)
    assert result[0]["throughput_req_per_s_mean"] == 2
    assert result[0]["throughput_req_per_s_std"] == pytest.approx(2 ** 0.5)


@pytest.mark.parametrize("seeds", [[], [42, 42], [-1]])
def test_invalid_seeds(config, tmp_path, seeds):
    with pytest.raises(ValueError):
        run_baselines(config, tmp_path, seeds)


def test_replay_is_not_pseudoreplicated(config, tmp_path):
    with pytest.raises(ValueError, match="one seed"):
        run_baselines(config, tmp_path, [1, 2], ROOT / "legacy/output/workload.csv")


@pytest.mark.parametrize("rows", [
    [{"second": 1, "incoming_requests": 10}],
    [{"second": 0, "incoming_requests": -1}],
    [{"second": 0, "incoming_requests": 1.5}],
])
def test_reject_malformed_workload(tmp_path, rows):
    path = tmp_path / "bad.csv"
    write_csv(path, rows)
    with pytest.raises(ValueError):
        read_workload(path, 42)


def test_plot_current_and_legacy_and_zero_traffic(config, tmp_path):
    from PIL import Image
    from cloud_sim.plotting import plot_results
    config = replace(config, phases=(PhaseConfig("stable", 2, 0, 0),))
    run_baselines(config, tmp_path / "zero", [42])
    for source, destination in ((tmp_path / "zero/csv", tmp_path / "zero/figures"),
                                 (ROOT / "legacy/output", tmp_path / "legacy")):
        paths = plot_results(source, destination)
        assert len(paths) == 7
        for path in paths:
            with Image.open(path) as img:
                assert img.format == "PNG" and img.width >= 2000 and img.height >= 800
                img.verify()
