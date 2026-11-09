from dataclasses import replace
import json

import pytest

from cloud_sim.config import PhaseConfig
from cloud_sim.storage import read_csv
from marl.config import TrainingConfig
from marl.evaluation import evaluate, scenario_config
from marl.training import train


def test_evaluation_pairing_statistics_seed_split_and_api(config, tmp_path):
    from fastapi.testclient import TestClient
    from cloud_api.app import create_app
    simulation = replace(config, phases=tuple(replace(p, seconds=2) for p in config.phases))
    training = TrainingConfig(episodes=2, hidden_size=8, update_epochs=1, validation_interval=1, validation_seeds=(20001,))
    train_dir = tmp_path / "training"
    train(simulation, training, train_dir, progress=False)
    checkpoint = train_dir / "checkpoints/best.pt"
    result_dir = tmp_path / "evaluation"
    evaluate(simulation, checkpoint, result_dir, [30001, 30002], ["mixed", "stable", "ramp_up", "spike", "recovery"])
    summary = read_csv(result_dir / "csv/summary.csv")
    assert len(summary) == 30
    for scenario in {r["scenario"] for r in summary}:
        for seed in (30001, 30002):
            runs = [r for r in summary if r["scenario"] == scenario and int(r["seed"]) == seed]
            assert len(runs) == 3 and len({r["received"] for r in runs}) == 1
            for row in runs:
                assert int(row["received"]) == int(row["processed"]) + int(row["dropped"]) + int(row["waiting_at_end"])
    assert len(read_csv(result_dir / "csv/comparison.csv")) == 15
    assert len(read_csv(result_dir / "csv/paired_differences.csv")) == 20
    duplicate = tmp_path / "duplicate"
    evaluate(simulation, checkpoint, duplicate, [30001, 30002], ["mixed", "stable", "ramp_up", "spike", "recovery"])
    for path in (result_dir / "csv").glob("*.csv"):
        assert path.read_bytes() == (duplicate / "csv" / path.name).read_bytes()
    for seeds in ([training.seed], [1000], [20001], [30001, 30001]):
        with pytest.raises(ValueError):
            evaluate(simulation, checkpoint, result_dir, seeds, ["mixed"])
    with TestClient(create_app(result_dir)) as client:
        assert client.get("/api/simulation/status").json()["policies"] == ["round_robin", "least_loaded", "marl"]
        assert len(client.get("/api/simulation/comparison?scenario=mixed").json()["rows"]) == 3
        assert client.get("/api/simulation/metrics?scenario=spike&seed=30001&policy=marl").json()["total"] == 6


def test_recovery_includes_preceding_spike(config):
    recovery = scenario_config(config, "recovery")
    assert [p.name for p in recovery.phases] == ["spike", "recovery"]
    with pytest.raises(ValueError):
        scenario_config(config, "unknown")
