from pathlib import Path

import pytest

from cloud_sim.config import load_config

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def config():
    return load_config(ROOT / "configs/simulation.json")
