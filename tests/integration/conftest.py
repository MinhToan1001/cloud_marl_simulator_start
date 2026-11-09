from pathlib import Path
import json

import pytest

from integration.collector import collect

ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture
def snapshots():
    return json.loads((ROOT / "tests/fixtures/cluster_a.json").read_text())["snapshots"]


@pytest.fixture
def context():
    from integration.schema import digest
    return {"source": "simulated", "collection_mode": "offline_fixture", "cluster_id": "A",
            "namespace": "cloud-calibration", "run_id": "test-fixture", "workload_sha256": digest({"test": True})}


@pytest.fixture
def offline_csv(tmp_path):
    config = json.loads((ROOT / "configs/integration/collector.example.json").read_text())
    workload = json.loads((ROOT / "configs/integration/load-safe.json").read_text())
    output = tmp_path / "offline"
    collect(config, output, workload, ROOT / "tests/fixtures/cluster_a.json", samples=8)
    return output / "metrics.csv"
