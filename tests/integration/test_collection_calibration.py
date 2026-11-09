import json
from dataclasses import replace
from pathlib import Path
from unittest.mock import patch

import pytest

from cloud_sim.config import load_config
from integration.calibration import calibrate, linear_fit
from integration.collector import KubectlClient, LiveSource, collect, scrub_inventory
from integration.export import export_simulation
from integration.preprocess import aligned_samples
from integration.schema import read_records, write_records

ROOT = Path(__file__).resolve().parents[2]


def test_offline_does_not_contact_cluster(offline_csv):
    records = read_records(offline_csv)
    assert len(records) == 24 and {r.source for r in records} == {"simulated"}
    session = json.loads((offline_csv.parent / "session.json").read_text())
    assert session["connected_to_real_cluster"] is False


def test_fixture_ignores_claimed_real_source_in_config(tmp_path):
    config = json.loads((ROOT / "configs/integration/collector.example.json").read_text())
    config["source"] = "real"
    with patch("integration.collector.LiveSource", side_effect=AssertionError("offline must never call network")):
        metadata = collect(config, tmp_path / "source-test", {}, ROOT / "tests/fixtures/cluster_a.json", samples=1)
    assert metadata["source"] == "simulated" and metadata["connected_to_real_cluster"] is False


def test_existing_reports_cannot_be_overwritten(offline_csv, tmp_path):
    from integration.preprocess import preprocess
    output = tmp_path / "protected-report"
    output.mkdir()
    original = output / "report.md"
    original.write_text("keep existing report")
    with pytest.raises(ValueError, match="new/empty"):
        calibrate(offline_csv, output, ROOT / "configs/simulation.json", True)
    with pytest.raises(ValueError, match="new/empty"):
        preprocess(offline_csv, output, True)
    assert original.read_text() == "keep existing report"


def test_live_requires_known_context_and_node_identity():
    config = json.loads((ROOT / "configs/integration/collector.example.json").read_text())
    with pytest.raises(ValueError):
        LiveSource(config)
    config.update(context="verified", expected_node_names=["expected"])
    with patch.object(KubectlClient, "get", return_value={"items": [{"metadata": {"name": "wrong"}}]}):
        with pytest.raises(ValueError, match="identity"):
            LiveSource(config).snapshot()


def test_inventory_scrubs_secrets():
    data = {"items": [{"metadata": {"name": "api", "annotations": {"secret": "do-not-store"}},
                       "spec": {"containers": [{"name": "api", "env": [{"value": "secret"}]}]}}]}
    assert "secret" not in json.dumps(scrub_inventory(data, "pods"))


def test_no_fixture_masquerades_as_real_and_cpu_is_pod_scoped(offline_csv):
    rows = read_records(offline_csv)
    assert not aligned_samples(rows)[0]
    samples, rejected = aligned_samples(rows, allow_simulated=True)
    assert len(samples) == 7 and rejected[0]["reason"] == "warmup"
    assert samples[0]["cpu_usage_cores"] == .03  # not node's .11 cores
    duplicated = rows + rows
    assert len(aligned_samples(duplicated, allow_simulated=True)[0]) == 7


def test_fixture_calibration_estimates_and_refuses_candidate(offline_csv, tmp_path):
    report = calibrate(offline_csv, tmp_path / "calibration", ROOT / "configs/simulation.json", allow_simulated=True)
    assert report["status"] == "fixture_demonstration_not_real_calibration"
    assert report["cpu_cores_vs_completed_rps"]["slope"] == pytest.approx(.01)
    assert report["observed_healthy_capacity_lower_bound_rps"] == 8
    assert report["overload_onset_observed_rps"] is None
    with pytest.raises(ValueError, match="real/live"):
        calibrate(offline_csv, tmp_path / "reject", ROOT / "configs/simulation.json", True, tmp_path / "candidate.json")
    assert not (tmp_path / "candidate.json").exists()


def test_candidate_changes_only_a_capacity_with_mocked_measurements(offline_csv, tmp_path):
    rows = read_records(offline_csv)
    app_rate = {r.timestamp_utc: r.incoming_rps or 0 for r in rows if r.layer == "application"}
    mocked = []
    for row in rows:
        row = replace(row, source="real", collection_mode="live")
        if row.layer == "k3s_pod":
            cpu = .02 + .028 * app_rate[row.timestamp_utc]
            row = replace(row, cpu_usage_cores=cpu, cpu_utilization_pct=100 * cpu / .25)
        mocked.append(row)
    path = tmp_path / "MOCKED_REAL_RECORDS_UNIT_TEST_ONLY.csv"
    write_records(path, mocked)
    proposed = tmp_path / "candidate.json"
    original = load_config(ROOT / "configs/simulation.json").to_dict()
    calibrate(path, tmp_path / "report", ROOT / "configs/simulation.json", candidate=proposed)
    candidate = load_config(proposed).to_dict()
    assert candidate["clusters"][0]["capacity"] == 8
    assert candidate["clusters"][1:] == original["clusters"][1:]
    actual = dict(candidate["clusters"][0]); actual["capacity"] = original["clusters"][0]["capacity"]
    assert actual == original["clusters"][0]
    assert load_config(ROOT / "configs/simulation.json").to_dict() == original


def test_linear_fit_known_relationship():
    fit = linear_fit([1, 2, 3], [5, 7, 9])
    assert fit["slope"] == 2 and fit["intercept"] == 3 and fit["r_squared"] == 1
    assert linear_fit([1, 1, 1], [1, 2, 3])["status"] != "estimated"


def test_legacy_simulation_export_is_non_destructive(tmp_path):
    output = tmp_path / "common.csv"
    assert export_simulation(ROOT / "results/baseline", output) == 5400
    rows = read_records(output)
    assert {r.source for r in rows} == {"simulated"}
    assert all(r.queue_length == r.inflight for r in rows)
