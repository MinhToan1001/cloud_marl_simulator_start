from dataclasses import replace

import numpy as np
import pytest
from gymnasium.utils.env_checker import check_env

from cloud_sim.config import PhaseConfig
from cloud_sim.engine import SimulationSession, simulate
from cloud_sim.policies import Dispatcher
from marl.env import JointGymEnv, MultiClusterEnv, ratios_to_counts


def test_gymnasium_contract(config):
    short = replace(config, phases=(PhaseConfig("stable", 5, 90, 90),))
    check_env(JointGymEnv(short), skip_render_check=True)


def test_env_accounting_reward_and_spaces(config):
    env = MultiClusterEnv(config)
    obs, infos = env.reset(seed=77, options={"workload": [0, 1, 1000, 500, 0]})
    assert len(env.agents) == 3
    for step in range(5):
        assert all(env.observation_space(a).contains(o) for a, o in obs.items())
        obs, rewards, terminated, truncated, infos = env.step({a: step % 3 for a in env.agents})
        assert sum(env.ratios) == pytest.approx(1)
        assert (env.ratios > 0).all()
        info = infos[env.possible_agents[0]]
        assert sum(info["allocation"]) == [0, 1, 1000, 500, 0][step]
        assert len(set(rewards.values())) == 1
        assert next(iter(rewards.values())) == pytest.approx(sum(info["reward_components"].values()))
        assert all(terminated.values()) == (step == 4)
        assert not any(truncated.values())
    summary = env.session.result().summary
    assert summary["received"] == summary["processed"] + summary["dropped"] + summary["waiting_at_end"]
    with pytest.raises(RuntimeError):
        env.step({a: 1 for a in env.possible_agents})


def test_no_lookahead_and_reset_reproducibility(config):
    envs = [MultiClusterEnv(config), MultiClusterEnv(config)]
    for env, future in zip(envs, (0, 999)):
        env.reset(seed=42, options={"workload": [90, 500, 200, future]})
    for _ in range(2):
        a, b = [env.step({agent: 1 for agent in env.agents}) for env in envs]
        for agent in envs[0].possible_agents:
            np.testing.assert_array_equal(a[0][agent], b[0][agent])
            assert a[1][agent] == b[1][agent]
    a, _ = envs[0].reset(seed=42)
    envs[0].step({agent: 0 for agent in envs[0].agents})
    b, _ = envs[0].reset(seed=42)
    for agent in a:
        np.testing.assert_array_equal(a[agent], b[agent])


def test_truncation_has_real_next_observation(config):
    env = MultiClusterEnv(config, max_steps=1)
    env.reset(options={"workload": [90, 270]})
    obs, _, terminated, truncated, _ = env.step({a: 1 for a in env.agents})
    assert not any(terminated.values()) and all(truncated.values())
    assert all(o[-2] == 1 for o in obs.values())


@pytest.mark.parametrize("bad", [0, -1, 3, True, 1.5])
def test_invalid_action_without_mutating_state(config, bad):
    env = MultiClusterEnv(config)
    env.reset()
    actions = {a: 1 for a in env.agents}
    if bad == 0:
        actions.pop(env.agents[0])
    else:
        actions[env.agents[0]] = bad
    with pytest.raises(ValueError):
        env.step(actions)
    assert env.second == 0 and not env.session.rows


@pytest.mark.parametrize("demand", [0, 1, 2, 100, 9999])
def test_apportionment(demand):
    counts = ratios_to_counts(np.array([0.01, 0.2, 0.79]), demand)
    assert sum(counts) == demand and min(counts) >= 0
    assert all(abs(c - r * demand) <= 1 for c, r in zip(counts, (0.01, 0.2, 0.79)))


def test_shared_engine_matches_baselines(config):
    workload = [0, 90, 500, 100, 0]
    session = SimulationSession(config, "round_robin")
    dispatcher = Dispatcher("round_robin")
    for demand in workload:
        session.step(demand, dispatcher.allocate(tuple(c.observe() for c in session.clusters), demand))
    assert session.result().summary == simulate("round_robin", workload, config).summary
