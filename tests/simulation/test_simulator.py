"""Accounting and analytic cases, not snapshots of implementation details."""
from collections import Counter
from dataclasses import replace
import random

import pytest

from cloud_sim.config import ClusterConfig, PhaseConfig, load_config
from cloud_sim.engine import percentile, simulate
from cloud_sim.model import Cluster, Observation
from cloud_sim.policies import Dispatcher, POLICIES
from cloud_sim.workload import make_workload


def tiny(**changes):
    values = dict(name="X", capacity=2, base_latency_ms=10, cpu_cores=1,
                  requests_per_core=2, memory_mb=10, idle_memory_mb=2,
                  memory_per_request_mb=1, queue_limit=2)
    return ClusterConfig(**(values | changes))


def test_fifo_latency_and_drops_by_hand():
    cluster = Cluster(tiny())
    first, latencies = cluster.step(5)
    assert (first["accepted"], first["processed"], first["dropped"], cluster.queue) == (4, 2, 1, 2)
    assert latencies == (510, 1010)
    second, latencies = cluster.step(0)
    assert latencies == (1510, 2010)
    assert second["processed"] == 2
    assert cluster.queue == 0
    idle, latencies = cluster.step(0)
    assert idle["latency_mean_ms"] is None and latencies == ()


def test_memory_limits_service_and_queue():
    c = tiny(capacity=10, requests_per_core=10, memory_mb=5, idle_memory_mb=2, queue_limit=20)
    assert c.effective_capacity == 3 and c.max_queue == 0
    result, _ = Cluster(c).step(100)
    assert result["processed"] == 3 and result["dropped"] == 97
    assert result["memory_utilization_pct"] == 100


def test_memory_limits_backlog_even_with_cpu_headroom():
    c = tiny(memory_mb=5, idle_memory_mb=2, queue_limit=20)
    assert c.max_queue == 1
    result, _ = Cluster(c).step(100)
    assert result["processed"] == 2 and result["queue_after"] == 1
    assert result["dropped"] == 97


def test_cpu_limits_service():
    c = tiny(capacity=20, cpu_cores=0.5, requests_per_core=4)
    assert c.effective_capacity == 2
    result, _ = Cluster(c).step(5)
    assert result["processed"] == 2 and result["cpu_utilization_pct"] == 100


def test_zero_queue_and_idle():
    cluster = Cluster(tiny(queue_limit=0))
    assert cluster.step(3)[0]["dropped"] == 1
    result, _ = cluster.step(0)
    assert result["cpu_utilization_pct"] == 0
    assert result["memory_utilization_pct"] == 20


@pytest.mark.parametrize("changes", [
    {"capacity": 0}, {"capacity": True}, {"capacity": 1.5}, {"cpu_cores": 0},
    {"memory_mb": 1}, {"queue_limit": -1}, {"memory_per_request_mb": 0},
    {"base_latency_ms": -1}, {"memory_mb": float("inf")}, {"cpu_cores": float("nan")},
])
def test_invalid_cluster_config(changes):
    with pytest.raises(ValueError):
        tiny(**changes)


@pytest.mark.parametrize("demand", [-1, 1.5, True])
def test_invalid_request_count(demand):
    with pytest.raises(ValueError):
        Cluster(tiny()).step(demand)


def test_round_robin_continues_across_ticks():
    observations = tuple(Observation(n, 0, 10, 20, 0) for n in "ABC")
    dispatcher = Dispatcher("round_robin")
    assert [dispatcher.allocate(observations, 1) for _ in range(4)] == [
        [1, 0, 0], [0, 1, 0], [0, 0, 1], [1, 0, 0]]
    assert dispatcher.allocate(observations, 0) == [0, 0, 0]


def test_least_loaded_uses_capacity_backlog_and_memory_headroom():
    dispatcher = Dispatcher("least_loaded")
    observations = (Observation("A", 2, 2, 2, 0), Observation("B", 0, 4, 8, 0))
    assert dispatcher.allocate(observations, 2) == [0, 2]
    # Once A has 100 assigned requests it is full; B still has six slots.
    observations = (Observation("A", 0, 100, 0, 0), Observation("B", 0, 1, 5, 0))
    assert dispatcher.allocate(observations, 106) == [100, 6]


@pytest.mark.parametrize("policy", POLICIES)
def test_conservation_and_bounds_for_random_loads(config, policy):
    rng = random.Random(731)
    workload = [0, 1, 10000] + [rng.randrange(1000) for _ in range(70)] + [0] * 10
    run = simulate(policy, workload, config)
    summary = run.summary
    assert summary["received"] == summary["processed"] + summary["dropped"] + summary["waiting_at_end"]
    assert summary["waiting_at_end"] == 0
    for row in run.rows:
        assert row["queue_before"] + row["assigned"] == row["processed"] + row["dropped"] + row["queue_after"]
        assert row["accepted"] + row["dropped"] == row["assigned"]
        assert 0 <= row["queue_after"] <= row["max_queue"]
        assert 0 <= row["cpu_utilization_pct"] <= 100
        assert 0 <= row["memory_utilization_pct"] <= 100
        assert row["latency_mean_ms"] is None or row["latency_mean_ms"] >= 0
    for second, demand in enumerate(workload):
        assert sum(r["assigned"] for r in run.rows if r["second"] == second) == demand


@pytest.mark.parametrize("policy", POLICIES)
def test_decisions_do_not_depend_on_future(config, policy):
    prefix = [50, 90, 500, 700, 0]
    a = simulate(policy, prefix + [0] * 5, config)
    b = simulate(policy, prefix + [999] * 5, config)
    assert a.rows[:len(prefix) * 3] == b.rows[:len(prefix) * 3]


@pytest.mark.parametrize("policy", POLICIES)
def test_zero_traffic_and_empty_workload(config, policy):
    run = simulate(policy, [0] * 3, config)
    assert run.summary["received"] == run.summary["processed"] == run.summary["error_rate_pct"] == 0
    assert run.summary["latency_mean_ms"] is None
    assert run.summary["latency_p95_ms"] is None
    with pytest.raises(ValueError, match="at least one"):
        simulate(policy, [], config)


def test_seed_reproducibility_and_rng_isolation(config):
    random.seed(100)
    state = random.getstate()
    a = make_workload(config)
    assert random.getstate() == state
    assert a == make_workload(config)
    assert a != make_workload(config, config.seed + 1)
    assert len(a) == config.seconds == 180
    assert {r["phase"] for r in a} == {"stable", "ramp_up", "spike", "recovery"}


def test_ramp_is_linear_without_noise(config):
    config = replace(config, jitter=0, phases=(PhaseConfig("ramp_up", 5, 10, 50),))
    assert [r["incoming_requests"] for r in make_workload(config)] == [10, 20, 30, 40, 50]


def test_p95_is_request_weighted_nearest_rank():
    assert percentile(Counter({10: 94, 100: 6})) == 100
    assert percentile(Counter({10: 95, 100: 5})) == 10
    assert percentile(Counter()) is None


def test_summary_latency_does_not_average_idle_ticks(config):
    config = replace(config, clusters=tuple(tiny(name=name) for name in "ABC"))
    run = simulate("round_robin", [1, 0], config)
    assert run.summary["latency_mean_ms"] == 510
    assert run.summary["latency_p95_ms"] == 510
    assert run.summary["throughput_req_per_s"] == 0.5
    assert run.summary["received"] == run.summary["processed"] == 1
    assert run.summary["cpu_utilization_pct"] == pytest.approx(50 / 6)


def test_config_rejects_missing_and_unknown_fields(tmp_path, config):
    import json
    path = tmp_path / "bad.json"
    path.write_text(json.dumps(config.to_dict() | {"unknown": 1}))
    with pytest.raises(ValueError):
        load_config(path)
    with pytest.raises(ValueError):
        replace(config, clusters=(config.clusters[0],) * 3)
    with pytest.raises(ValueError):
        replace(config, jitter=1.1)
    path.write_text("[]")
    with pytest.raises(ValueError):
        load_config(path)


def test_unknown_policy(config):
    with pytest.raises(ValueError):
        simulate("unknown", [1], config)
