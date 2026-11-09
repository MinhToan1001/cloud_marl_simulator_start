"""Task 2 lifecycle/accounting, fairness, schema and low-load calibration contracts."""
from copy import deepcopy
from dataclasses import replace
import json
from pathlib import Path

import numpy as np
import pytest
import torch

from placement.calibration import calibrate_run, error_metrics, fit_response, predict_response
from placement.config import PlacementConfig, ClusterConfig
from placement.env import PlacementEnv, quantile
from placement.io import file_hash
from placement.schema import manifest, vectorize
from placement.shadow import action_masks, archived_packets, observations, operation_plan, validate_shadow
from placement.trace import make_trace
from placement.training import TrainingConfig, load_policy, train, finite_episode_gae

ROOT = Path(__file__).resolve().parents[2]


def simple_config(**changes):
    base = PlacementConfig(seconds=40, decision_s=1, readiness_s=3, grace_s=2, cooldown_s=4,
                           background_fraction=(.1, .1), capacity_factor=(1, 1), network_factor=(1, 1), readiness_factor=(1, 1))
    return replace(base, **changes)


def keep(env):
    return {a: 0 for a in env.agents}


def fixed_trace(config, demand=0):
    trace = make_trace(config, 1001)
    trace["demand"] = [[demand]*3 for _ in range(config.seconds)]
    trace["available"] = [[True]*3 for _ in range(config.seconds)]
    return trace


def test_placement_waits_for_readiness_then_overlaps_and_releases():
    config = simple_config()
    env = PlacementEnv(config)
    env.reset(trace=fixed_trace(config, 1))
    action = keep(env); action[env.agents[0]] = 2
    env.step(action)
    assert env.states[0].phase == "PREPARING" and env.states[0].active == 0
    assert env.footprints(0) == {0, 1}
    for _ in range(2):
        env.step(keep(env))
    assert env.states[0].active == 0  # no switch before 3 full seconds
    env.step(keep(env))
    assert env.states[0].active == 1 and env.states[0].phase == "GRACE"
    assert env.footprints(0) == {0, 1}
    for _ in range(2):
        env.step(keep(env))
    assert env.states[0].phase == "IDLE" and env.footprints(0) == {1}
    assert env.total["completed_moves"] == 1
    assert env.validate(0, 3)[1] == "cooldown"


@pytest.mark.parametrize("failure_tick", [1, 4])
def test_target_failure_rolls_back_without_teleporting_backlog(failure_tick):
    config = simple_config()
    trace = fixed_trace(config, 30)
    trace["available"][failure_tick][1] = False
    env = PlacementEnv(config); env.reset(trace=trace)
    action = keep(env); action[env.agents[0]] = 2
    env.step(action)
    for _ in range(failure_tick):
        env.step(keep(env))
    assert env.states[0].active == 0 and env.states[0].phase == "IDLE"
    assert env.total["failed_moves"] == 1 and env.total["completed_moves"] == 0
    env.assert_invariants()


def test_readiness_timeout_and_active_failure_are_accounted():
    config = simple_config(readiness_s=8, timeout_s=2)
    trace = fixed_trace(config, 3)
    trace["available"][0][0] = False
    env = PlacementEnv(config); env.reset(trace=trace)
    action = keep(env); action[env.agents[0]] = 2
    env.step(action)
    assert env.total["unavailable_errors"] == 3
    for _ in range(2):
        env.step(keep(env))
    assert env.total["failed_moves"] == 1
    env.assert_invariants()


def test_joint_arbitration_and_noop_do_not_create_resources_or_moves():
    config = simple_config(max_concurrent_moves=1)
    env = PlacementEnv(config); env.reset(trace=fixed_trace(config))
    action = {env.agents[0]: 2, env.agents[1]: 3, env.agents[2]: 1}
    env.step(action)
    assert env.total["accepted_moves"] == 1
    proposals = [e for e in env.events if e["event"] == "proposal"]
    assert sum(p["reason"] == "concurrency_limit" for p in proposals) == 2
    for i in range(3):
        assert env.validate(i, env.states[i].active+1)[1] == "already_active"
    env.assert_invariants()


def test_resource_rejection_and_invalid_proposal_fallback():
    config = simple_config(clusters=(ClusterConfig("A", .1, 128*1024**2, 1),
                                    ClusterConfig("B", .1, 128*1024**2, 2), ClusterConfig("C", .1, 128*1024**2, 3)))
    env = PlacementEnv(config); env.reset(trace=fixed_trace(config))
    assert env.validate(0, 2)[1] == "insufficient_cpu_headroom"
    actions = keep(env); actions[env.agents[0]] = True
    env.step(actions)
    assert env.events[0]["reason"] == "invalid_action"
    assert not env.total["accepted_moves"]


@pytest.mark.parametrize("scenario", ["stable", "ramp_up", "spike", "recovery", "periodic", "shift"])
def test_stochastic_long_runs_conserve_every_request_and_resource(scenario):
    config = simple_config(seconds=80)
    env = PlacementEnv(config)
    obs = env.reset(seed=30003, scenario=scenario)
    rng = np.random.default_rng(42)
    while env.second < config.seconds:
        actions = {a: int(rng.choice(np.flatnonzero(mask))) for a, mask in env.masks().items()}
        obs, rewards, _, _ = env.step(actions)
        assert all(np.isfinite(o).all() and len(o) == manifest(config)["size"] for o in obs.values())
        assert all(np.isfinite(v) for v in rewards.values())
    summary = env.summary()
    assert summary["received"] == summary["processed"]+summary["errors"]+summary["unfinished"]
    assert summary["accepted_moves"] == summary["completed_moves"]+summary["failed_moves"]+summary["pending_moves"]
    assert 0 <= summary["slo_violation_fraction_resolved"] <= 1
    assert sum(env.histogram.values()) == summary["processed"]


def test_exogenous_trace_reproducible_independent_of_actions_and_schema_parity():
    config = simple_config()
    trace = fixed_trace(config, 5)
    a, b = PlacementEnv(config), PlacementEnv(config)
    a.reset(trace=deepcopy(trace)); b.reset(trace=deepcopy(trace))
    actions = keep(a); actions[a.agents[0]] = 2
    a.step(actions); b.step(keep(b))
    assert a.trace == b.trace
    for name in a.agents:
        np.testing.assert_array_equal(a.observe()[name], observations(a.packet(), config)[name])
    assert not manifest(config)["horizon_feature"] and manifest(config)["actions"]["MOVE_C"] == 3
    bad = a.packet(); bad["action_kind"] = "request_routing"
    with pytest.raises(ValueError, match="semantic"):
        vectorize(bad, config, a.agents[0])


def test_low_load_latency_and_tail_quantile_populations():
    config = simple_config(latency_mean_ms=5.5)
    env = PlacementEnv(config); env.reset(trace=fixed_trace(config, 1))
    env.step(keep(env))
    assert env.cluster_last[0]["latency_sum"] == pytest.approx(6.5)
    assert quantile({1: 98, 50: 1, 100: 1}, .95) == 1
    assert quantile({1: 98, 50: 1, 100: 1}, .99) == 50
    assert quantile({}, .99) is None


def test_empirical_cpu_response_cannot_exceed_workload_cpu_limit():
    config = simple_config(cpu_cores_per_rps=.02)
    env = PlacementEnv(config); env.reset(trace=fixed_trace(config, 70))
    env.step(keep(env))
    for i, cluster in enumerate(config.clusters):
        # A low-load regression can extrapolate above a pod's hard limit; the
        # simulator must cap the usage response without inventing extra CPU.
        assert env.cluster_last[i]['cpu'] <= cluster.cpu_cores*.1 + config.services[i].cpu_limit + 1e-9


def test_calibration_identifies_response_not_maximum_capacity(tmp_path):
    profile, report = calibrate_run(ROOT/"data/runs/cluster-a-real-003", tmp_path/"calibration")
    assert profile["maximum_capacity_rps"] is None and profile["queue_capacity"] is None
    assert report["raw_replay_matches_metrics"] and report["aligned_windows"] == 8
    assert report["request_count"] == 152 and report["snapshot_count"] == 18
    assert report["errors_grouped_holdout"]["latency_mean_ms"]["n"] == 8
    assert report["cpu_limit_utilization_max_pct"] < 11
    with pytest.raises(FileExistsError):
        calibrate_run(ROOT/"data/runs/cluster-a-real-003", tmp_path/"calibration")


def test_shadow_archive_fails_closed_and_keeps_missing_clusters_unavailable():
    config = simple_config()
    packets = archived_packets(ROOT/"data/runs/cluster-a-real-003", config)
    assert len(packets) == 18
    packet = packets[-1]
    assert packet["historical_only"]
    assert packet["services"][config.services[0].name]["memory_request"] == 32*1024**2
    assert packet["clusters"]["A"]["cpu_capacity"] == 2
    assert not packet["clusters"]["B"]["available"]
    assert not packet["mapping"][config.services[0].name]["deployment_identity_verified"]
    assert all(mask.tolist() == [True, False, False, False] for mask in action_masks(packet, config).values())
    for name in observations(packet, config):
        assert not operation_plan(packet, name, 2, config)["operations"]


def test_shadow_plan_is_declarative_and_checks_mapping_staleness_and_headroom():
    config = simple_config()
    env = PlacementEnv(config); env.reset(trace=fixed_trace(config))
    packet = env.packet()
    packet["mapping"] = {env.agents[0]: {"deployment_identity_verified": True}}
    plan = operation_plan(packet, env.agents[0], 2, config)
    assert len(plan["operations"]) == 6 and not plan["executable"]
    packet["clusters"]["B"]["age_s"] = 31
    assert validate_shadow(packet, env.agents[0], 2, config)[0] == 0
    packet["clusters"]["B"]["age_s"] = 0
    packet["clusters"]["B"]["cpu_reservation"] = 1
    assert validate_shadow(packet, env.agents[0], 2, config)[1] == "insufficient_cpu_headroom"


def test_masked_ippo_training_checkpoint_and_legacy_rejection(tmp_path):
    config = simple_config(seconds=20, decision_s=5)
    settings = TrainingConfig(episodes=2, epochs=1, validation_interval=1, validation_seeds=(20001,))
    old = ROOT/"results/training/checkpoints/best.pt"
    before = file_hash(old)
    with pytest.raises(ValueError, match="not a placement"):
        load_policy(old, config)
    report = train(config, tmp_path/"training", 7, settings)
    assert all(report["weights_changed"].values())
    policy, checkpoint = load_policy(tmp_path/"training/last.pt", config)
    env = PlacementEnv(config); obs = env.reset()
    masks = env.masks()
    for _ in range(10):
        actions, _, _ = policy.act(obs, masks)
        assert all(masks[a][actions[a]] for a in actions)
    assert checkpoint["schema"]["size"] == len(obs[env.agents[0]])
    with pytest.raises(ValueError, match="config mismatch"):
        load_policy(tmp_path/"training/last.pt", replace(config, cooldown_s=5))
    modified = torch.load(tmp_path/"training/last.pt", weights_only=True)
    modified["schema"]["features"][0]["unit"] = "wrong unit"
    torch.save(modified, tmp_path/"bad.pt")
    with pytest.raises(ValueError, match="manifest"):
        load_policy(tmp_path/"bad.pt", config)
    assert file_hash(old) == before


def test_finite_horizon_gae_by_hand():
    advantages, returns = finite_episode_gae(torch.tensor([1., 1.]), torch.tensor([.5, .25]), 1, 1)
    torch.testing.assert_close(advantages, torch.tensor([1.5, .75]))
    torch.testing.assert_close(returns, torch.tensor([2., 1.]))


def test_paired_evaluation_reproducible_no_baseline_pseudoreplication(tmp_path):
    from placement.experiments import evaluate, shadow_replay
    config = simple_config(seconds=10, decision_s=5)
    settings = TrainingConfig(episodes=1, epochs=1, validation_interval=1, validation_seeds=(20001,))
    root = tmp_path/'training'; root.mkdir()
    for seed in (7, 17):
        train(config, root/f'seed_{seed}', seed, settings)
    rows, comparison, metadata = evaluate(config, root, tmp_path/'eval1', seeds=(30001,), policy_seeds=(7, 17))
    assert metadata['run_count'] == 24 and metadata['baseline_runs'] == 12
    assert metadata['baseline_duplicate_count'] == 0
    for scenario in {r['scenario'] for r in rows}:
        assert len({r['trace_sha256'] for r in rows if r['scenario'] == scenario}) == 1
    evaluate(config, root, tmp_path/'eval2', seeds=(30001,), policy_seeds=(7, 17))
    assert (tmp_path/'eval1/summary.csv').read_bytes() == (tmp_path/'eval2/summary.csv').read_bytes()
    with pytest.raises(ValueError, match='overlap'):
        evaluate(config, root, tmp_path/'invalid', seeds=(1000,), policy_seeds=(7, 17))
    result = shadow_replay(config, ROOT/'data/runs/cluster-a-real-003', root/'seed_7/best.pt', tmp_path/'shadow')
    assert result['executed_operations'] == 0 and result['legacy_routing_checkpoint_rejected']
