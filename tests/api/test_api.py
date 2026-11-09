import json
from dataclasses import replace

import pytest
from fastapi.testclient import TestClient

from cloud_api.app import create_app
from cloud_sim.config import PhaseConfig
from cloud_sim.experiments import run_baselines


@pytest.fixture
def client(config, tmp_path):
    config = replace(config, phases=(PhaseConfig("stable", 3, 90, 90),))
    run_baselines(config, tmp_path, [42])
    with TestClient(create_app(tmp_path)) as client:
        yield client


@pytest.mark.parametrize("endpoint", ["status", "clusters", "metrics", "comparison"])
def test_endpoints_mark_synthetic_data(client, endpoint):
    response = client.get(f"/api/simulation/{endpoint}")
    assert response.status_code == 200
    data = response.json()
    assert data["data_source"] == "simulation" and data["is_simulation"] is True
    assert data["metrics_are_real"] is False


def test_filters_pagination_and_numbers(client):
    response = client.get("/api/simulation/metrics", params={"policy": "round_robin", "cluster": "A", "seed": 42,
                                                           "scenario": "mixed", "limit": 1, "offset": 1})
    data = response.json()
    assert data["total"] == 3 and len(data["rows"]) == 1
    assert data["rows"][0]["second"] == 1
    assert isinstance(data["rows"][0]["cpu_utilization_pct"], float)
    assert isinstance(data["rows"][0]["assigned"], int)
    assert client.get("/api/simulation/metrics?dataset=system&policy=least_loaded").json()["total"] == 3
    assert client.get("/api/simulation/status").json()["ready"] is True
    assert len(client.get("/api/simulation/clusters").json()["clusters"]) == 3


@pytest.mark.parametrize("query", ["limit=0", "limit=10001", "seed=-1", "offset=-1", "policy=real_openstack",
                                  "start_second=3&end_second=2", "dataset=system&cluster=A"])
def test_invalid_queries(client, query):
    assert client.get(f"/api/simulation/metrics?{query}").status_code == 422


def test_no_fake_data_when_results_missing_or_unmarked(tmp_path):
    with TestClient(create_app(tmp_path)) as client:
        assert client.get("/api/simulation/status").json()["status"] == "no_results"
        assert client.get("/api/simulation/metrics").status_code == 404
        (tmp_path / "metadata.json").write_text(json.dumps({"data_source": "openstack"}))
        assert client.get("/api/simulation/status").json()["ready"] is False
        assert client.get("/api/simulation/clusters").status_code == 503


def test_unknown_cluster_and_readonly(client):
    assert client.get("/api/simulation/metrics?cluster=Z").status_code == 404
    assert client.post("/api/simulation/metrics", json={}).status_code == 405


def test_corrupt_csv_returns_service_error(config, tmp_path):
    run_baselines(replace(config, phases=(PhaseConfig("stable", 1, 1, 1),)), tmp_path, [42])
    (tmp_path / "csv/cluster_metrics.csv").write_text("wrong,value\nx,NaN\n")
    with TestClient(create_app(tmp_path)) as client:
        assert client.get("/api/simulation/metrics").status_code == 503


def test_explicit_cors_origin(config, tmp_path, monkeypatch):
    monkeypatch.setenv("CLOUD_SIM_CORS_ORIGINS", "http://localhost:5173")
    with TestClient(create_app(tmp_path)) as client:
        response = client.options("/api/simulation/status", headers={
            "Origin": "http://localhost:5173", "Access-Control-Request-Method": "GET"})
        assert response.headers["access-control-allow-origin"] == "http://localhost:5173"
