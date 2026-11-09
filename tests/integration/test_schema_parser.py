import json
from copy import deepcopy
from dataclasses import replace

import pytest

from integration.host import parse_cpu, parse_memory
from integration.parser import AppDeltaParser, kubernetes_records
from integration.quantities import cpu_cores, memory_bytes, quantity
from integration.schema import MetricRecord, digest, read_records, write_records


@pytest.mark.parametrize("raw,expected", [("250m", .25), ("125000000n", .125), ("250u", .00025), ("2", 2), ("1e-3", .001)])
def test_cpu_quantities(raw, expected):
    assert cpu_cores(raw) == pytest.approx(expected)


@pytest.mark.parametrize("raw,expected", [("1Ki", 1024), ("1.5Gi", 1610612736), ("100M", 100000000), ("1e6", 1000000)])
def test_memory_quantities(raw, expected):
    assert memory_bytes(raw) == expected


@pytest.mark.parametrize("raw", ["-1", "NaN", "Inf", "1foo", "1KiMi", ""])
def test_invalid_quantity(raw):
    with pytest.raises(ValueError):
        quantity(raw)


def base(**changes):
    return MetricRecord(**(dict(timestamp_utc="2026-01-01T00:00:00Z", source="simulated", collection_mode="offline_fixture",
                                layer="application", entity_id="fixture", run_id="test", workload_sha256=digest({}), cluster_id="A") | changes))


@pytest.mark.parametrize("changes", [{"source": "simulation"}, {"source": "real"}, {"cpu_usage_cores": float("nan")},
    {"timestamp_utc": "2026-01-01"}, {"layer": "host"}, {"received_total": 2, "completed_total": 0, "errors_total": 0, "inflight": 0},
    {"cpu_capacity_cores": -1}, {"pod_count": 1.2}])
def test_schema_rejects_ambiguous_or_invalid_records(changes):
    with pytest.raises(ValueError):
        base(**changes)


def test_host_never_becomes_cluster_a():
    with pytest.raises(ValueError, match="cannot"):
        base(layer="ubuntu_host")
    assert base(layer="ubuntu_host", cluster_id="").cluster_id == ""


def test_csv_roundtrip_keeps_nulls_and_provenance(tmp_path):
    record = base()
    path = tmp_path / "metrics.csv"
    write_records(path, [record])
    assert read_records(path) == [record]
    write_records(path, [record], append=True)
    assert len(read_records(path)) == 2


def test_parse_nodes_pods_and_missing_metrics(snapshots, context):
    rows = kubernetes_records(snapshots[3], context)
    node, pod = rows
    assert node.layer == "k3s_node" and node.cpu_capacity_cores == 2
    assert pod.layer == "k3s_pod" and pod.cpu_capacity_cores == .25
    assert pod.cpu_usage_cores == .05 and pod.memory_capacity_bytes == 96 * 1024**2
    assert pod.pod_count == pod.ready_pods == 1
    missing = deepcopy(snapshots[0])
    missing["pod_metrics"] = {"items": []}
    row = kubernetes_records(missing, context)[1]
    assert row.cpu_usage_cores is None and row.quality == "missing_resource_metrics"


def test_app_rate_latency_and_lost_events(snapshots, context):
    parser = AppDeltaParser()
    a, b = snapshots[0]["apps"][0], snapshots[1]["apps"][0]
    assert parser.parse(a, a["timestamp_utc"], context).incoming_rps is None
    row = parser.parse(b, b["timestamp_utc"], context)
    assert row.incoming_rps == row.completed_rps == 1
    assert row.latency_mean_ms == row.latency_p95_ms == 5.5
    c = deepcopy(snapshots[2]["apps"][0])
    c["events"] = []
    row = parser.parse(c, c["timestamp_utc"], context)
    assert row.latency_mean_ms == 6 and row.latency_p95_ms is None
    assert row.quality == "latency_events_lost"


def test_counter_restart_and_rejection(snapshots, context):
    parser = AppDeltaParser()
    a = snapshots[3]["apps"][0]
    parser.parse(a, a["timestamp_utc"], context)
    reset = deepcopy(snapshots[0]["apps"][0])
    assert parser.parse(reset, reset["timestamp_utc"], context).quality == "counter_reset_or_nonmonotonic"
    reset["instance_id"] = "new-pod-process"
    assert parser.parse(reset, reset["timestamp_utc"], context).quality == "warmup"
    reset["received_total"] = 123
    with pytest.raises(ValueError, match="conservation"):
        parser.parse(reset, reset["timestamp_utc"], context)


def test_procfs_roles_and_guest_cpu_not_double_counted():
    assert parse_cpu("cpu  100 20 30 400 50 1 2 3 100 20\n") == (606, 450)
    assert parse_memory("MemTotal: 1000 kB\nMemAvailable: 300 kB\n") == (700*1024, 1000*1024)
    with pytest.raises(ValueError):
        parse_memory("MemTotal: 1000 kB\n")
